import hashlib
import json
from datetime import datetime, timezone
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import settings
from control_models import FederatedNode, TenantRegistry
from database import ControlSessionLocal, TenantContext, get_control_db, get_db, get_redis, resolve_tenant_context
from models import Permission, Role, User, UserStatusEnum

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/api/auth/login")


def validate_pagination(page: int = 1, page_size: int = 20) -> tuple[int, int]:
    """Enforces safe pagination boundaries across all list endpoints."""
    if page < 1:
        raise HTTPException(status_code=400, detail="Page must be greater than 0")
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="Page size must be between 1 and 200")
    return page, page_size


def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Verifies the JWT using the specific hospital tenant's cryptographic signing key."""
    tenant: TenantContext = resolve_tenant_context(request)
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials for this hospital instance.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, tenant.jwt_secret_key, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        token_tenant: str = payload.get("tenant_slug")
        if user_id is None or token_tenant != tenant.subdomain_slug:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.get(User, int(user_id))
    if not user or user.status != UserStatusEnum.ACTIVE:
        raise credentials_exception

    # Populate audit actor ID for the SQLAlchemy before_flush listener in models.py
    db.info["audit_user_id"] = user.id
    return user


def user_has_permission(db: Session, user: User, *permission_codes: str) -> bool:
    """Helper to check if an authenticated user holds at least one of the specified granular permissions."""
    if user.is_superuser:
        return True
    required_set = set(permission_codes)
    stmt = (
        select(Permission.id)
        .join(Role.permissions)
        .join(Role.users)
        .where(User.id == user.id)
        .where(Permission.code.in_(required_set))
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none() is not None


def RequirePermission(*permission_codes: str):
    """
    Strictly verifies that the authenticated user holds AT LEAST ONE of the exact
    `resource:action` permission codes required by the route. No legacy aliases.
    """
    if not permission_codes:
        raise ValueError("At least one permission code must be provided to RequirePermission.")

    def dependency(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        if not user_has_permission(db, current_user, *permission_codes):
            formatted = "' or '".join(permission_codes)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: Required permission '{formatted}' missing.",
            )
        return current_user

    return dependency


class RequireLicensedModule:
    """
    Enforces modular/bundle licensing per hospital tenant.
    Blocks requests with HTTP 403 if the hospital has not licensed the requested module.
    """

    def __init__(self, module_key: str):
        self.module_key = module_key

    def __call__(
        self,
        request: Request,
        control_db: Session = Depends(get_control_db),
    ) -> None:
        tenant_ctx = resolve_tenant_context(request)
        enabled_modules = getattr(request.state, "enabled_modules", None)

        if enabled_modules is None:
            t = control_db.execute(
                select(TenantRegistry).where(TenantRegistry.subdomain_slug == tenant_ctx.subdomain_slug)
            ).scalar_one_or_none()
            try:
                enabled_modules = (
                    json.loads(t.enabled_modules_json)
                    if t and getattr(t, "enabled_modules_json", None)
                    else {"all": True}
                )
            except Exception:
                enabled_modules = {"all": True}
            request.state.enabled_modules = enabled_modules

        if enabled_modules.get("all") is True:
            return

        if not enabled_modules.get(self.module_key, True):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Module '{self.module_key}' is not enabled under your hospital's current license package.",
            )


def get_federated_node(
    x_federation_api_key: str = Header(..., alias="X-Federation-API-Key"),
    control_db: Session = Depends(get_control_db),
) -> FederatedNode:
    """Authenticates cross-branch/cross-tenant requests against the Control Plane."""
    if not x_federation_api_key or len(x_federation_api_key) < 16:
        raise HTTPException(status_code=401, detail="Invalid Federation API Key format.")

    key_hash = hashlib.sha256(x_federation_api_key.encode("utf-8")).hexdigest()
    stmt = select(FederatedNode).where(
        FederatedNode.api_key_hash == key_hash,
        FederatedNode.is_active == True,
    )
    node = control_db.execute(stmt).scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=401, detail="Unauthorized or revoked Federation API Key.")

    node.last_used_at = datetime.now(timezone.utc)
    control_db.commit()
    return node


class RateLimiter:
    """
    Fail-open Redis fixed-window rate limiter.
    Protects sensitive endpoints (login, federation discovery) without breaking
    the application if Redis is temporarily unreachable.
    """

    def __init__(self, scope: str, max_requests: int, window_seconds: int = 60):
        self.scope = scope
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    def __call__(self, request: Request, redis_client=Depends(get_redis)) -> None:
        if not redis_client:
            return

        client_ip = request.client.host if request.client else "unknown"
        fed_prefix = request.headers.get("X-Federation-API-Key", "")[:14]
        actor = fed_prefix if fed_prefix else client_ip
        key = f"rl:{self.scope}:{actor}"

        try:
            current = redis_client.get(key)
            if current is not None and int(current) >= self.max_requests:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded ({self.max_requests} requests per {self.window_seconds}s). Please retry shortly.",
                )
            if hasattr(redis_client, "incr"):
                count = redis_client.incr(key)
                if count == 1 and hasattr(redis_client, "expire"):
                    redis_client.expire(key, self.window_seconds)
            else:
                new_val = int(current or 0) + 1
                redis_client.set(key, str(new_val), ex=self.window_seconds)
        except HTTPException:
            raise
        except Exception:
            # Fail-open if Redis connection drops
            return