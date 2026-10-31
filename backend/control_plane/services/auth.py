from contextvars import ContextVar
import base64
from datetime import datetime, timedelta, timezone
import secrets
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.core import database
from backend.core.config import settings
from backend.core.database import get_control_db
from backend.core.dependencies import RateLimiter
from backend.control_plane.models import (
    ControlPlaneOperator,
    ControlPlaneOperatorSession,
)
from backend.domains.identity.passwords import pwd_context
from backend.security.envelope import decrypt_tenant_secret, encrypt_tenant_secret
from backend.security.totp import matching_totp_counter

ControlSessionLocal = database.ControlSessionLocal

control_plane_actor: ContextVar[Optional[str]] = ContextVar(
    "control_plane_actor", default=None
)
control_plane_operator_role: ContextVar[Optional[str]] = ContextVar(
    "control_plane_operator_role", default=None
)


class OperatorLogin(BaseModel):
    email: EmailStr
    password: str


class OperatorMfaToken(BaseModel):
    mfa_token: str = Field(min_length=32, max_length=4096)


class OperatorMfaCode(OperatorMfaToken):
    code: str = Field(pattern=r"^[0-9]{6}$")


def _issue_challenge(operator: ControlPlaneOperator, purpose: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(operator.id),
            "purpose": purpose,
            "exp": now + timedelta(minutes=5),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def _challenge_operator(db: Session, token: str, purpose: str) -> ControlPlaneOperator:
    try:
        claims = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired MFA token.") from exc
    subject = claims.get("sub")
    if claims.get("purpose") != purpose or not isinstance(subject, str) or not subject.isdecimal():
        raise HTTPException(status_code=401, detail="Invalid or expired MFA token.")
    operator = db.execute(
        select(ControlPlaneOperator)
        .where(ControlPlaneOperator.id == int(subject))
        .with_for_update()
    ).scalar_one_or_none()
    if not operator or not operator.is_active:
        raise HTTPException(status_code=401, detail="Operator account is unavailable.")
    return operator


def _complete_operator_login(
    db: Session, operator: ControlPlaneOperator
) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.CONTROL_PLANE_ACCESS_TOKEN_MINUTES)
    token_id = secrets.token_urlsafe(24)
    token = jwt.encode(
        {
            "sub": str(operator.id),
            "purpose": "control_plane_access",
            "jti": token_id,
            "iat": now,
            "exp": expires_at,
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    db.add(
        ControlPlaneOperatorSession(
            token_id=token_id,
            operator_id=operator.id,
            expires_at=expires_at,
        )
    )
    db.commit()
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.CONTROL_PLANE_ACCESS_TOKEN_MINUTES * 60,
    }


def authenticate_operator_token(token: str) -> Optional[dict[str, object]]:
    """Returns the operator identity only for an active MFA-verified session."""
    try:
        claims = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
    except jwt.InvalidTokenError:
        return None
    subject = claims.get("sub")
    token_id = claims.get("jti")
    if (
        claims.get("purpose") != "control_plane_access"
        or not isinstance(subject, str)
        or not subject.isdecimal()
        or not isinstance(token_id, str)
    ):
        return None
    with ControlSessionLocal() as db:
        session = db.get(ControlPlaneOperatorSession, token_id)
        if (
            not session
            or session.operator_id != int(subject)
            or session.revoked_at is not None
        ):
            return None
        expires_at = session.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            return None
        operator = db.get(ControlPlaneOperator, session.operator_id)
        if not operator or not operator.is_active or not operator.mfa_enabled:
            return None
        return {
            "id": operator.id,
            "email": operator.email,
            "role": getattr(operator, "role", "viewer"),
            "token_id": token_id,
        }


def require_operator(request: Request) -> ControlPlaneOperator:
    operator_id = getattr(request.state, "operator_id", None)
    if operator_id is None:
        raise HTTPException(status_code=401, detail="MFA-authenticated operator required.")
    with ControlSessionLocal() as db:
        operator = db.get(ControlPlaneOperator, operator_id)
        if not operator or not operator.is_active:
            raise HTTPException(status_code=401, detail="Operator account is unavailable.")
        return operator


def create_operator_auth_router():
    from fastapi import APIRouter

    router = APIRouter(prefix="/api/auth", tags=["Control Plane Operator Identity"])

    @router.post(
        "/login",
        dependencies=[
            Depends(
                RateLimiter(
                    scope="control_plane_auth_login",
                    max_requests=10,
                    window_seconds=60,
                )
            )
        ],
    )
    def login(payload: OperatorLogin, db: Session = Depends(get_control_db)):
        operator = db.execute(
            select(ControlPlaneOperator)
            .where(ControlPlaneOperator.email == payload.email)
            .with_for_update()
        ).scalar_one_or_none()
        if not operator or not operator.is_active:
            pwd_context.verify(
                payload.password,
                "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewKd.RvvJ8hYjT.W",
            )
            raise HTTPException(status_code=401, detail="Invalid credentials.")
        locked_until = operator.locked_until
        now = datetime.now(timezone.utc)
        if locked_until and locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        if locked_until and locked_until > now:
            raise HTTPException(status_code=401, detail="Invalid credentials.")
        if not pwd_context.verify(payload.password, operator.password_hash):
            operator.failed_login_attempts += 1
            if operator.failed_login_attempts >= 5:
                operator.failed_login_attempts = 0
                operator.locked_until = now + timedelta(minutes=15)
            db.commit()
            raise HTTPException(status_code=401, detail="Invalid credentials.")
        operator.failed_login_attempts = 0
        operator.locked_until = None
        if operator.mfa_enabled:
            db.commit()
            return {
                "mfa_required": True,
                "mfa_token": _issue_challenge(operator, "operator_mfa_login"),
            }
        db.commit()
        return {
            "mfa_setup_required": True,
            "mfa_token": _issue_challenge(operator, "operator_mfa_setup"),
        }

    @router.post("/mfa/setup")
    def setup_mfa(
        payload: OperatorMfaToken, db: Session = Depends(get_control_db)
    ):
        operator = _challenge_operator(db, payload.mfa_token, "operator_mfa_setup")
        if operator.mfa_enabled:
            raise HTTPException(status_code=409, detail="MFA is already enabled.")
        if operator.mfa_secret:
            secret = decrypt_tenant_secret(operator.mfa_secret)
        else:
            secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
            operator.mfa_secret = encrypt_tenant_secret(secret)
            db.commit()
        return {
            "secret": secret,
            "otpauth_uri": (
                f"otpauth://totp/Avinya%20Control%20Plane:{operator.email}"
                f"?secret={secret}&issuer=Avinya%20Control%20Plane&digits=6&period=30"
            ),
        }

    @router.post(
        "/mfa/confirm",
        dependencies=[
            Depends(
                RateLimiter(
                    scope="control_plane_auth_mfa",
                    max_requests=5,
                    window_seconds=300,
                )
            )
        ],
    )
    def confirm_mfa(
        payload: OperatorMfaCode, db: Session = Depends(get_control_db)
    ):
        operator = _challenge_operator(db, payload.mfa_token, "operator_mfa_setup")
        if operator.mfa_enabled or not operator.mfa_secret:
            raise HTTPException(status_code=400, detail="MFA setup has not been started.")
        counter = matching_totp_counter(
            decrypt_tenant_secret(operator.mfa_secret), payload.code
        )
        if counter is None:
            raise HTTPException(status_code=400, detail="Invalid MFA code.")
        operator.mfa_enabled = True
        operator.mfa_last_used_counter = counter
        return _complete_operator_login(db, operator)

    @router.post(
        "/mfa/verify-login",
        dependencies=[
            Depends(
                RateLimiter(
                    scope="control_plane_auth_mfa",
                    max_requests=5,
                    window_seconds=300,
                )
            )
        ],
    )
    def verify_login(
        payload: OperatorMfaCode, db: Session = Depends(get_control_db)
    ):
        operator = _challenge_operator(db, payload.mfa_token, "operator_mfa_login")
        if not operator.mfa_enabled or not operator.mfa_secret:
            raise HTTPException(status_code=401, detail="MFA is not configured.")
        counter = matching_totp_counter(
            decrypt_tenant_secret(operator.mfa_secret), payload.code
        )
        if counter is None or (
            operator.mfa_last_used_counter is not None
            and counter <= operator.mfa_last_used_counter
        ):
            raise HTTPException(status_code=401, detail="Invalid MFA challenge or code.")
        operator.mfa_last_used_counter = counter
        return _complete_operator_login(db, operator)

    @router.post("/logout")
    def logout(
        operator: ControlPlaneOperator = Depends(require_operator),
    ):
        with ControlSessionLocal() as db:
            db.execute(
                update(ControlPlaneOperatorSession)
                .where(
                    ControlPlaneOperatorSession.operator_id == operator.id,
                    ControlPlaneOperatorSession.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(timezone.utc))
            )
            db.commit()
        return {"status": "success", "message": "All operator sessions have been revoked."}

    return router


control_plane_bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="OperatorBearer",
    description="Paste your Operator JWT Bearer token obtained from /api/auth/confirm or /api/auth/verify-login",
)


def verify_control_plane_secret(
    request: Request,
    _bearer: Optional[object] = Depends(control_plane_bearer_scheme),
    x_control_plane_secret: Optional[str] = Header(
        default=None, alias="X-Control-Plane-Secret"
    ),
) -> None:
    if getattr(request.state, "operator_email", None):
        return
    if getattr(request.state, "operator_token_present", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired operator session.",
        )
    if not settings.CONTROL_PLANE_LEGACY_MASTER_KEY_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Static Control Plane credentials are disabled; use a named operator session.",
        )
    expected_key = settings.CONTROL_PLANE_MASTER_KEY
    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Control Plane credentials are not configured.",
        )
    if not x_control_plane_secret or not secrets.compare_digest(x_control_plane_secret, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Control Plane credentials.",
        )


def _verify_vendor_key(provided_key: str) -> None:
    # F-08: Only superadmin operators can bypass vendor key checks; other roles or unauthenticated requests must supply valid master key
    if control_plane_operator_role.get() == "superadmin":
        return
    if not provided_key or not secrets.compare_digest(provided_key, settings.MASTER_ONBOARDING_KEY):
        raise HTTPException(status_code=403, detail="Invalid Master Vendor Setup Key.")


def require_operator_role(*allowed_roles: str):
    """
    R-05: Enforces Role-Based Access Control (RBAC) on control plane endpoints.
    Allowed roles: 'superadmin', 'ops', 'support', 'billing', 'viewer'.
    'superadmin' is always authorized across all role-gated routes.
    """
    def _role_dependency(
        request: Request,
        _: None = Depends(verify_control_plane_secret),
    ) -> str:
        current_role = getattr(request.state, "operator_role", None) or control_plane_operator_role.get()
        if not current_role:
            # If authenticated via legacy static master key
            if getattr(request.state, "operator_email", None) is None:
                return "superadmin"
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operator role is not defined for this session.",
            )

        if current_role == "superadmin" or current_role in allowed_roles:
            return current_role

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access forbidden: role '{current_role}' does not have required permissions. Required: {', '.join(allowed_roles)}.",
        )

    return _role_dependency

