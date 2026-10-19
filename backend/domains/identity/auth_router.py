"""Authentication and user identity APIs.

This router belongs to the identity and access domain. It manages sessions,
user personnel records, and role/department authorization for the rest of the
clinical and operational modules.
"""

from collections import defaultdict
import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
import jwt
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db, resolve_tenant_context
from backend.core.dependencies import RateLimiter, RequirePermission, get_current_user, validate_pagination, enforce_tenant_resource_quota
from backend.core.mailer import send_email
from backend.domains.identity.models import (
    AuthSession,
    Department,
    DoctorProfile,
    MfaRecoveryCode,
    Permission,
    PasswordResetToken,
    Role,
    User,
    UserRole,
    UserStatusEnum,
)
from backend.domains.identity.passwords import pwd_context
from backend.schemas.common import PaginatedResponse
from backend.domains.identity.schemas import DepartmentCreate, DepartmentResponse, DepartmentUpdate, PermissionResponse, RoleCreate, RoleResponse, RoleUpdate, TokenResponse, UserCreate, UserLogin, UserMeResponse, UserUpdate

from backend.security.envelope import decrypt_tenant_secret, encrypt_tenant_secret
from backend.domains.policies.router import get_or_create_policy
from backend.security.totp import matching_totp_counter, totp_code as _totp_code

router = APIRouter(prefix="/api/auth", tags=["IAM & Auth"])


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_strength(cls, value: str) -> str:
        return _validate_password(value)


class PasswordResetRequest(BaseModel):
    token: str = Field(min_length=32, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_strength(cls, value: str) -> str:
        return _validate_password(value)


class PasswordResetStartRequest(BaseModel):
    email: EmailStr


class MfaTokenRequest(BaseModel):
    mfa_token: str = Field(min_length=32, max_length=4096)


class MfaCodeRequest(MfaTokenRequest):
    code: str = Field(min_length=6, max_length=32)


class MfaDisableRequest(BaseModel):
    password: str
    code: str = Field(min_length=6, max_length=32)


def _validate_password(value: str) -> str:
    classes = (
        any(char.islower() for char in value),
        any(char.isupper() for char in value),
        any(char.isdigit() for char in value),
        any(not char.isalnum() for char in value),
    )
    if sum(classes) < 3:
        raise ValueError("Password must use at least three character classes.")
    return value


def _requires_mfa(user: User) -> bool:
    return user.is_superuser or any(
        role.code in {"ROLE_ADMIN", "ROLE_HOSPITAL_ADMIN"} for role in user.roles
    )


def _matching_totp_counter(secret: str, code: str) -> Optional[int]:
    return matching_totp_counter(secret, code)


def _verify_mfa_code(db: Session, user: User, code: str) -> bool:
    if not user.mfa_secret:
        return False
    secret = decrypt_tenant_secret(user.mfa_secret)
    counter = _matching_totp_counter(secret, code)
    if counter is not None:
        if (
            user.mfa_last_used_counter is not None
            and counter <= user.mfa_last_used_counter
        ):
            return False
        user.mfa_last_used_counter = counter
        return True

    recovery_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    recovery = db.execute(
        select(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.user_id == user.id,
            MfaRecoveryCode.token_hash == recovery_hash,
            MfaRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if not recovery:
        return False
    recovery.used_at = datetime.now(timezone.utc)
    return True


def _issue_mfa_token(user: User, tenant, purpose: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user.id),
            "tenant_slug": tenant.subdomain_slug,
            "branch_code": tenant.branch_code,
            "purpose": purpose,
            "exp": now + timedelta(minutes=5),
        },
        decrypt_tenant_secret(tenant.jwt_secret_key),
        algorithm=settings.ALGORITHM,
    )


def _decode_mfa_token(token: str, tenant, purpose: str) -> int:
    try:
        payload = jwt.decode(
            token,
            decrypt_tenant_secret(tenant.jwt_secret_key),
            algorithms=[settings.ALGORITHM],
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired MFA token.") from exc
    subject = payload.get("sub")
    if (
        payload.get("purpose") != purpose
        or payload.get("tenant_slug") != tenant.subdomain_slug
        or not isinstance(subject, str)
        or not subject.isdecimal()
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired MFA token.")
    return int(subject)


def _issue_access_session(user: User, tenant, db: Session) -> str:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token_id = secrets.token_urlsafe(24)
    token = jwt.encode(
        {
            "sub": str(user.id),
            "tenant_slug": tenant.subdomain_slug,
            "branch_code": tenant.branch_code,
            "jti": token_id,
            "iat": now,
            "exp": expires_at,
        },
        decrypt_tenant_secret(tenant.jwt_secret_key),
        algorithm=settings.ALGORITHM,
    )
    db.add(AuthSession(token_id=token_id, user_id=user.id, expires_at=expires_at))
    return token


def _complete_login(user: User, tenant, db: Session) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    user.last_login_at = now
    if user.first_login_at is None:
        user.first_login_at = now
    user.failed_login_attempts = 0
    user.locked_until = None
    token = _issue_access_session(user, tenant, db)
    db.commit()
    return {"access_token": token, "token_type": "bearer"}


def _record_mfa_failure(db: Session, user: User) -> None:
    user.failed_login_attempts += 1
    policy = get_or_create_policy(db)
    if user.failed_login_attempts >= policy.max_failed_login_attempts:
        user.status = UserStatusEnum.LOCKED
        user.locked_until = datetime.now(timezone.utc) + timedelta(
            minutes=settings.LOGIN_LOCKOUT_MINUTES
        )
    db.commit()


def _revoke_user_sessions(db: Session, user_id: int) -> None:
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )


def _require_role_delegation_authority(
    current_user: User, roles: list[Role]
) -> None:
    """Prevent non-superusers from assigning privileges they cannot personally delegate."""
    if current_user.is_superuser:
        return
    caller_permissions = {
        permission.code
        for assigned_role in current_user.roles
        for permission in assigned_role.permissions
    }
    excessive_permissions = sorted(
        {
            permission.code
            for role in roles
            for permission in role.permissions
            if permission.code not in caller_permissions
        }
    )
    if excessive_permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot assign roles containing permissions you do not possess.",
        )


class CustomPermissionCreate(BaseModel):
    module: str = Field(min_length=2, max_length=50, description="Module name, e.g., Billing or Radiology")
    resource: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9_]+$")
    action: str = Field(min_length=2, max_length=30, pattern=r"^(read|write|delete|verify|void|reverse|discharge|cancel|[a-z_]+)$")
    description: str = Field(min_length=5, max_length=255)


# -----------------------------------------------------------------------------
# Authentication and session domain
# -----------------------------------------------------------------------------
@router.post("/login")
def login(
    payload: UserLogin,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(RateLimiter(scope="auth_login", max_requests=10, window_seconds=60)),
):
    """Authenticates a user against the active hospital database and signs a tenant-isolated JWT."""
    tenant = resolve_tenant_context(request)
    
    # SEC-007: Implement pessimistic lock to prevent distributed brute-force lockout bypass
    from sqlalchemy import select
    user = db.execute(
        select(User).where(User.email == payload.email).with_for_update()
    ).scalar_one_or_none()

    # SEC-003: Timing-Attack Mitigation. Run dummy verify if user is None.
    # Mathematically valid bcrypt hash (matches "password" at cost 12)
    dummy_hash = "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewKd.RvvJ8hYjT.W"
    is_valid_pwd = False
    
    if user:
        is_valid_pwd = pwd_context.verify(payload.password, user.hashed_password)
    else:
        pwd_context.verify(payload.password, dummy_hash)

    now = datetime.now(timezone.utc)
    locked_until = user.locked_until if user else None
    if locked_until and locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    if (
        user
        and user.status == UserStatusEnum.LOCKED
        and locked_until is not None
        and locked_until <= now
    ):
        user.status = UserStatusEnum.ACTIVE
        user.failed_login_attempts = 0
        user.locked_until = None

    if user and user.status == UserStatusEnum.LOCKED:
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
        )

    if not user or not is_valid_pwd:
        if user:
            user.failed_login_attempts += 1
            policy = get_or_create_policy(db)
            if user.failed_login_attempts >= policy.max_failed_login_attempts:
                user.status = UserStatusEnum.LOCKED
                user.locked_until = now + timedelta(
                    minutes=settings.LOGIN_LOCKOUT_MINUTES
                )
            db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if user.status != UserStatusEnum.ACTIVE:
        raise HTTPException(status_code=401, detail=f"Account is {user.status.value}.")

    if user.mfa_enabled:
        db.commit()
        return {
            "mfa_required": True,
            "mfa_token": _issue_mfa_token(user, tenant, "mfa_login"),
        }
    if settings.MFA_REQUIRED_FOR_PRIVILEGED and _requires_mfa(user):
        db.commit()
        return {
            "mfa_setup_required": True,
            "mfa_token": _issue_mfa_token(user, tenant, "mfa_setup"),
        }
    return _complete_login(user, tenant, db)


@router.get("/me", response_model=UserMeResponse)
def get_logged_in_user(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user record without exposing secrets."""
    return current_user


@router.post("/logout")
def logout(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Revokes all active sessions for the authenticated user."""
    _revoke_user_sessions(db, current_user.id)
    db.commit()
    return {"status": "success", "message": "All user sessions have been revoked."}


@router.post("/password/change")
def change_password(
    payload: PasswordChangeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not pwd_context.verify(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    current_user.hashed_password = pwd_context.hash(payload.new_password)
    current_user.password_changed_at = datetime.now(timezone.utc)
    _revoke_user_sessions(db, current_user.id)
    db.commit()
    return {"status": "success", "message": "Password changed; sign in again."}


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(
    payload: PasswordResetStartRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: None = Depends(RateLimiter(scope="auth_password_reset", max_requests=5, window_seconds=300)),
):
    """Sends a single-use password reset token without revealing account existence."""
    tenant = resolve_tenant_context(request)
    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if user and user.status == UserStatusEnum.ACTIVE:
        now = datetime.now(timezone.utc)
        db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        ).update({"used_at": now}, synchronize_session=False)
        raw_token = secrets.token_urlsafe(32)
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
                expires_at=now + timedelta(minutes=30),
            )
        )
        db.commit()
        reset_url = (
            f"{settings.tenant_url(tenant.subdomain_slug)}"
            f"/v1/api/auth/password/reset#token={raw_token}"
        )
        background_tasks.add_task(
            send_email,
            user.email,
            "Avinya HMS password reset",
            f"Reset your password using this one-time link:\n\n{reset_url}\n\nThe link expires in 30 minutes. If you did not request this, ignore this message.",
        )
    return {"message": "If an active account matches that address, password reset instructions will be sent."}


@router.get("/password/reset", response_class=HTMLResponse)
def password_reset_page():
    nonce = base64.b64encode(secrets.token_bytes(18)).decode("ascii")
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Reset your Avinya HMS password</title>
</head>
<body>
  <main>
    <h1>Reset your password</h1>
    <p>Choose a new password with at least 12 characters.</p>
    <form id="reset-form">
      <label for="new-password">New password</label>
      <input id="new-password" type="password" minlength="12" maxlength="128" required autocomplete="new-password">
      <button type="submit">Reset password</button>
    </form>
    <p id="status" role="status" aria-live="polite"></p>
  </main>
  <script nonce="{nonce}">
    const form = document.getElementById("reset-form");
    const status = document.getElementById("status");
    const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
    if (token) history.replaceState(null, "", window.location.pathname);
    if (!token) {{
      form.hidden = true;
      status.textContent = "This reset link is invalid or expired. Request a new one.";
    }}
    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      status.textContent = "Updating password...";
      try {{
        const response = await fetch(window.location.pathname, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            token,
            new_password: document.getElementById("new-password").value
          }})
        }});
        const result = await response.json();
        status.textContent = response.ok
          ? "Password reset. You can sign in with your new password."
          : (result.detail || "The reset link is invalid or expired.");
        if (response.ok) form.hidden = true;
      }} catch (_) {{
        status.textContent = "Unable to contact the server. Try again later.";
      }}
    }});
  </script>
</body>
</html>"""
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'nonce-{nonce}'; "
                "style-src 'none'; connect-src 'self'; form-action 'self'; "
                "base-uri 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )


@router.post("/password/reset")
def reset_password(
    payload: PasswordResetRequest,
    db: Session = Depends(get_db),
    _: None = Depends(RateLimiter(scope="auth_password_reset_confirm", max_requests=10, window_seconds=300)),
):
    now = datetime.now(timezone.utc)
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    reset = db.execute(
        select(PasswordResetToken)
        .where(PasswordResetToken.token_hash == token_hash)
        .with_for_update()
    ).scalar_one_or_none()
    if not reset or reset.used_at is not None:
        raise HTTPException(status_code=400, detail="Password reset token is invalid or expired.")
    expires_at = reset.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        raise HTTPException(status_code=400, detail="Password reset token is invalid or expired.")
    user = db.get(User, reset.user_id)
    if not user or user.status != UserStatusEnum.ACTIVE:
        raise HTTPException(status_code=400, detail="Password reset token is invalid or expired.")
    user.hashed_password = pwd_context.hash(payload.new_password)
    user.password_changed_at = now
    reset.used_at = now
    _revoke_user_sessions(db, user.id)
    db.commit()
    return {"status": "success", "message": "Password reset; sign in with the new password."}


@router.post("/mfa/enroll")
def begin_mfa_enrollment(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    tenant = resolve_tenant_context(request)
    return {
        "mfa_token": _issue_mfa_token(current_user, tenant, "mfa_setup"),
        "expires_in_seconds": 300,
    }


@router.post("/mfa/setup")
def setup_mfa(
    payload: MfaTokenRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    tenant = resolve_tenant_context(request)
    user_id = _decode_mfa_token(payload.mfa_token, tenant, "mfa_setup")
    user = db.get(User, user_id)
    if not user or user.status != UserStatusEnum.ACTIVE:
        raise HTTPException(status_code=401, detail="Account is not active.")
    if user.mfa_enabled:
        raise HTTPException(status_code=409, detail="MFA is already enabled.")
    if user.mfa_secret:
        secret = decrypt_tenant_secret(user.mfa_secret)
    else:
        secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
        user.mfa_secret = encrypt_tenant_secret(secret)
        db.commit()
    label = f"Avinya HMS:{user.email}"
    uri = f"otpauth://totp/{label}?secret={secret}&issuer=Avinya%20HMS&algorithm=SHA1&digits=6&period=30"
    return {"secret": secret, "otpauth_uri": uri}


@router.post(
    "/mfa/confirm",
    dependencies=[
        Depends(
            RateLimiter(scope="auth_mfa_confirm", max_requests=5, window_seconds=300)
        )
    ],
)
def confirm_mfa(
    payload: MfaCodeRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    tenant = resolve_tenant_context(request)
    user_id = _decode_mfa_token(payload.mfa_token, tenant, "mfa_setup")
    user = db.execute(select(User).where(User.id == user_id).with_for_update()).scalar_one_or_none()
    if (
        not user
        or user.status != UserStatusEnum.ACTIVE
        or user.mfa_enabled
        or not user.mfa_secret
    ):
        raise HTTPException(status_code=400, detail="MFA enrollment has not been started.")
    if not _verify_mfa_code(db, user, payload.code):
        _record_mfa_failure(db, user)
        raise HTTPException(status_code=400, detail="Invalid MFA code.")
    user.mfa_enabled = True
    user.failed_login_attempts = 0
    user.locked_until = None
    recovery_codes = [secrets.token_urlsafe(9) for _ in range(10)]
    db.add_all(
        MfaRecoveryCode(
            user_id=user.id,
            token_hash=hashlib.sha256(code.encode("utf-8")).hexdigest(),
        )
        for code in recovery_codes
    )
    result = _complete_login(user, tenant, db)
    return {**result, "recovery_codes": recovery_codes}


@router.post(
    "/mfa/verify-login",
    dependencies=[
        Depends(
            RateLimiter(scope="auth_mfa_verify", max_requests=5, window_seconds=300)
        )
    ],
)
def verify_mfa_login(
    payload: MfaCodeRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    tenant = resolve_tenant_context(request)
    user_id = _decode_mfa_token(payload.mfa_token, tenant, "mfa_login")
    user = db.execute(select(User).where(User.id == user_id).with_for_update()).scalar_one_or_none()
    if (
        not user
        or user.status != UserStatusEnum.ACTIVE
        or not user.mfa_enabled
        or not user.mfa_secret
    ):
        raise HTTPException(status_code=401, detail="Invalid MFA challenge or code.")
    if not _verify_mfa_code(db, user, payload.code):
        _record_mfa_failure(db, user)
        raise HTTPException(status_code=401, detail="Invalid MFA challenge or code.")
    return _complete_login(user, tenant, db)


@router.post("/mfa/disable")
def disable_mfa(
    payload: MfaDisableRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.mfa_enabled or not current_user.mfa_secret:
        raise HTTPException(status_code=409, detail="MFA is not enabled.")
    if not pwd_context.verify(payload.password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Password is incorrect.")
    locked_user = db.execute(
        select(User).where(User.id == current_user.id).with_for_update()
    ).scalar_one()
    if not _verify_mfa_code(db, locked_user, payload.code):
        raise HTTPException(status_code=400, detail="Invalid MFA code.")
    locked_user.mfa_enabled = False
    locked_user.mfa_secret = None
    locked_user.mfa_last_used_counter = None
    _revoke_user_sessions(db, locked_user.id)
    db.commit()
    return {"status": "success", "message": "MFA disabled; sign in again."}


# -----------------------------------------------------------------------------
# User lifecycle domain (Granular IAM: iam:read, iam:write, iam:delete)
# -----------------------------------------------------------------------------
@router.get("/users", response_model=PaginatedResponse[UserMeResponse])
def list_users(
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("iam:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(User).order_by(User.id.asc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/users/doctor", status_code=status.HTTP_201_CREATED)
def create_doctor(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:write")),
):
    enforce_tenant_resource_quota(request, db, resource="users", additional_count=1)
    roles = db.query(Role).filter(Role.id.in_(payload.role_ids)).all()
    if len(roles) != len(set(payload.role_ids)):
        raise HTTPException(status_code=400, detail="One or more role IDs are invalid.")

    if any(r.is_system_role for r in roles) and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Cannot assign system roles without superuser privileges.")
    _require_role_delegation_authority(current_user, roles)

    user = User(
        login_id=payload.login_id,
        email=payload.email,
        hashed_password=pwd_context.hash(payload.password),
        full_name=payload.full_name,
        phone_number=payload.phone_number,
        gender=payload.gender,
    )
    db.add(user)
    db.flush()

    db.add_all(UserRole(user_id=user.id, role_id=role.id) for role in roles)

    if payload.doctor_profile:
        dp = payload.doctor_profile
        db.add(
            DoctorProfile(
                user_id=user.id,
                clinical_id=dp.clinical_id,
                department_id=dp.department_id,
                qualification=dp.qualification,
                specialization=dp.specialization,
                medical_council_name=dp.medical_council_name,
                medical_registration_number=dp.medical_registration_number,
                registration_valid_till=dp.registration_valid_till,
                consultation_fee_paise=dp.consultation_fee_paise,
                consultation_duration_minutes=dp.consultation_duration_minutes,
            )
        )

    db.commit()
    return {"status": "success", "message": "User and clinical profile provisioned successfully"}


@router.patch("/users/{user_id}/status", status_code=200)
def update_user_status(
    user_id: int,
    new_status: UserStatusEnum,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:delete", "iam:write")),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_superuser and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Cannot modify status of a superuser account.")

    user.status = new_status
    if new_status == UserStatusEnum.ACTIVE:
        user.failed_login_attempts = 0
        user.locked_until = None
    db.commit()
    return {"status": "success", "message": f"User {user.email} status updated to {new_status.value}"}


@router.post("/users/{user_id}/departments", status_code=200)
def assign_user_department(
    user_id: int,
    department_id: int,
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:write")),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.is_superuser and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Cannot modify roles of a superuser account.")

    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found.")
    if role.is_system_role and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Cannot assign system roles without superuser privileges.")
    _require_role_delegation_authority(current_user, [role])

    dept = db.get(Department, department_id)
    if not dept or not dept.is_active:
        raise HTTPException(status_code=404, detail="Active department not found.")

    user_role = db.query(UserRole).filter(UserRole.user_id == user_id, UserRole.role_id == role_id).first()
    if user_role:
        user_role.department_id = department_id
    else:
        db.add(UserRole(user_id=user_id, role_id=role_id, department_id=department_id))

    db.commit()
    return {"status": "success", "message": f"User ID {user_id} mapped to Department ID {department_id}"}


@router.get("/users/{user_id}", response_model=UserMeResponse)
def get_user(user_id: int, db: Session = Depends(get_db), _: User = Depends(RequirePermission("iam:read"))):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


@router.put("/users/{user_id}", response_model=UserMeResponse)
def update_user_details(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:write")),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.is_superuser and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Cannot modify a superuser account.")

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.phone_number is not None:
        user.phone_number = payload.phone_number
    if payload.gender is not None:
        user.gender = payload.gender
    user.updated_by_id = current_user.id
    db.commit()
    db.refresh(user)
    return user


# -----------------------------------------------------------------------------
# Role & Granular Read/Write Permission Matrix Domain
# -----------------------------------------------------------------------------
@router.get("/roles", response_model=PaginatedResponse[RoleResponse])
def list_roles(
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("iam:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(Role).order_by(Role.id.asc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:write")),
):
    role_code = f"ROLE_{payload.name.upper().replace(' ', '_')}"
    existing = db.query(Role).filter((Role.name == payload.name) | (Role.code == role_code)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Role name or code already exists.")

    role = Role(
        code=role_code,
        name=payload.name,
        description=payload.description,
        is_system_role=False,
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


@router.put("/roles/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: int,
    payload: RoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:write")),
):
    role = db.get(Role, role_id)
    if not role or role.is_system_role:
        raise HTTPException(status_code=400, detail="Role not found or is a protected system role.")
    if payload.name is not None:
        role.name = payload.name
    if payload.description is not None:
        role.description = payload.description
    role.updated_by_id = current_user.id
    db.commit()
    db.refresh(role)
    return role


@router.delete("/roles/{role_id}", status_code=200)
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("iam:delete")),
):
    role = db.get(Role, role_id)
    if not role or role.is_system_role:
        raise HTTPException(status_code=400, detail="Role not found or is a protected system role.")
    if role.users:
        raise HTTPException(status_code=409, detail="Cannot delete a role that is actively assigned to users.")
    db.delete(role)
    db.commit()
    return {"status": "success", "message": f"Role ID {role_id} permanently removed."}


@router.get("/permissions", response_model=list[PermissionResponse])
def list_permissions(db: Session = Depends(get_db), _: User = Depends(RequirePermission("iam:read"))):
    return db.query(Permission).order_by(Permission.module.asc(), Permission.code.asc()).all()


@router.get("/permissions/matrix")
def get_permissions_matrix(db: Session = Depends(get_db), _: User = Depends(RequirePermission("iam:read"))):
    """
    Returns all permissions organized by Module and Action (read / write / destructive)
    so the Hospital Admin UI can render Read-Only, Write-Only, or Full Access checkboxes.
    """
    perms = db.query(Permission).order_by(Permission.module.asc(), Permission.code.asc()).all()
    matrix: dict[str, dict[str, list[dict]]] = defaultdict(lambda: {"read": [], "write": [], "destructive": []})

    for p in perms:
        action = p.code.split(":")[-1] if ":" in p.code else "other"
        item = {"id": p.id, "code": p.code, "action": action, "description": p.description}
        if action == "read":
            matrix[p.module]["read"].append(item)
        elif action == "write":
            matrix[p.module]["write"].append(item)
        else:
            matrix[p.module]["destructive"].append(item)

    return {"modules": dict(matrix)}


@router.post("/permissions", response_model=PermissionResponse, status_code=status.HTTP_201_CREATED)
def create_custom_permission(
    payload: CustomPermissionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:write")),
):
    """Allows a Hospital Superuser Admin to register new granular `resource:action` permissions."""
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Only the Hospital Superuser can create new permission definitions.")

    code = f"{payload.resource.lower()}:{payload.action.lower()}"
    existing = db.query(Permission).filter(Permission.code == code).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Permission '{code}' already exists.")

    perm = Permission(code=code, description=payload.description, module=payload.module)
    db.add(perm)
    db.commit()
    db.refresh(perm)
    return perm


@router.post("/roles/{role_id}/permissions", status_code=200)
def assign_role_permissions(
    role_id: int,
    permission_ids: list[int],
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:write")),
):
    """Assigns any combination of Read-Only, Write-Only, or Destructive permissions to a role."""
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found.")
    if role.is_system_role and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Cannot modify permissions of a system role.")

    perms = db.query(Permission).filter(Permission.id.in_(permission_ids)).all()
    if len(perms) != len(set(permission_ids)):
        raise HTTPException(status_code=400, detail="One or more permission IDs are invalid.")

    if not current_user.is_superuser:
        caller_perm_ids = {p.id for r in current_user.roles for p in r.permissions}
        if not set(permission_ids).issubset(caller_perm_ids):
            raise HTTPException(status_code=403, detail="Cannot grant permissions that you do not possess.")

    role.permissions = perms
    db.commit()
    return {"status": "success", "message": f"Assigned {len(perms)} granular permissions to role '{role.name}'."}


# -----------------------------------------------------------------------------
# Department Lifecycle (mdm:read, mdm:write, mdm:delete)
# -----------------------------------------------------------------------------
@router.post("/departments", response_model=DepartmentResponse, status_code=status.HTTP_201_CREATED)
def create_department(
    payload: DepartmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("mdm:write", "iam:write")),
):
    existing = db.query(Department).filter(Department.code == payload.code).first()
    if existing:
        raise HTTPException(status_code=409, detail="Department code already exists.")
    dept = Department(**payload.model_dump(), is_active=True, created_by_id=current_user.id, updated_by_id=current_user.id)
    db.add(dept)
    db.commit()
    db.refresh(dept)
    return dept


@router.get("/departments", response_model=PaginatedResponse[DepartmentResponse])
def list_departments(
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:read", "iam:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(Department).filter(Department.is_active == True).order_by(Department.id.asc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.put("/departments/{dept_id}", response_model=DepartmentResponse)
def update_department(
    dept_id: int,
    payload: DepartmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("mdm:write", "iam:write")),
):
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found.")
    if payload.name is not None:
        dept.name = payload.name
    if payload.ndhm_speciality_code is not None:
        dept.ndhm_speciality_code = payload.ndhm_speciality_code
    if payload.is_active is not None:
        dept.is_active = payload.is_active
    dept.updated_by_id = current_user.id
    db.commit()
    db.refresh(dept)
    return dept


@router.delete("/departments/{dept_id}", status_code=200)
def deactivate_department(
    dept_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("mdm:delete", "iam:delete")),
):
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found.")
    dept.is_active = False
    dept.updated_by_id = current_user.id
    db.commit()
    return {"status": "success", "message": f"Department ID {dept_id} deactivated."}