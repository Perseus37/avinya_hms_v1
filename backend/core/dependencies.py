import hashlib
import ipaddress
import json
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
import jwt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.control_plane.models import FederatedNode
from backend.core.database import TenantContext, get_control_db, get_db, get_redis, resolve_tenant_context
from backend.domains.facility.models import Bed
from backend.domains.identity.models import AuthSession, Permission, Role, User, UserStatusEnum

http_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="HTTPBearer",
    description="Paste your JWT Bearer token obtained from /api/auth/login or /api/auth/token (without 'Bearer ' prefix)",
)
tenant_header_scheme = APIKeyHeader(
    name="X-Hospital-Subdomain",
    auto_error=False,
    scheme_name="TenantSubdomain",
    description="Tenant subdomain slug (e.g. 'default' or your newly created hospital slug)",
)
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/auth/token",
    auto_error=False,
    scheme_name="OAuth2PasswordBearer",
    description="OAuth2 password flow. Username = email, Password = password, Client ID = optional hospital slug.",
)


def validate_pagination(page: int = 1, page_size: int = 20) -> tuple[int, int]:
    """Enforces safe pagination boundaries across all list endpoints."""
    if page < 1:
        raise HTTPException(status_code=400, detail="Page must be greater than 0")
    if page_size < 1 or page_size > settings.MAX_PAGE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Page size must be between 1 and {settings.MAX_PAGE_SIZE}",
        )
    return page, page_size


def get_current_user(
    request: Request,
    token: Any = Depends(http_bearer),
    oauth2_token: Optional[str] = Depends(oauth2_scheme),
    _tenant_header: Optional[str] = Depends(tenant_header_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Verifies the JWT using the specific hospital tenant's cryptographic signing key."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials for this hospital instance.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    raw_token = None
    if token and hasattr(token, "credentials") and token.credentials:
        raw_token = token.credentials
    elif token and isinstance(token, str):
        raw_token = token
    elif oauth2_token:
        raw_token = oauth2_token
    else:
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
        if auth_header and auth_header.strip().lower().startswith("bearer "):
            raw_token = auth_header.strip().split(" ", 1)[1].strip()

    if not raw_token:
        raise credentials_exception

    tenant: TenantContext = resolve_tenant_context(request)
    try:
        payload = jwt.decode(raw_token, tenant.jwt_secret_key, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        token_tenant: str = payload.get("tenant_slug")
        token_id = payload.get("jti")
        if (
            not isinstance(user_id, str)
            or not user_id.isdecimal()
            or token_tenant != tenant.subdomain_slug
            or "purpose" in payload
            or not isinstance(token_id, str)
            or not token_id
        ):
            raise credentials_exception
    except jwt.InvalidTokenError:
        raise credentials_exception

    user = db.get(User, int(user_id))
    if not user or user.status != UserStatusEnum.ACTIVE:
        raise credentials_exception
    auth_session = db.get(AuthSession, token_id)
    if (
        not auth_session
        or auth_session.user_id != user.id
        or auth_session.revoked_at is not None
    ):
        raise credentials_exception
    expires_at = auth_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
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

    def __call__(self, request: Request) -> None:
        tenant_ctx = resolve_tenant_context(request)
        try:
            enabled_modules = (
                json.loads(tenant_ctx.enabled_modules_json)
                if tenant_ctx.enabled_modules_json
                else {"all": True}
            )
        except (TypeError, json.JSONDecodeError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Hospital module licensing configuration is invalid.",
            )
        if not isinstance(enabled_modules, dict):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Hospital module licensing configuration is invalid.",
            )

        if enabled_modules.get("all") is True:
            return

        if not enabled_modules.get(self.module_key, False):
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
        self._check(request, redis_client)

    def check_actor(
        self, request: Request, redis_client, actor: str, *, increment: bool = True
    ) -> None:
        self._check(request, redis_client, actor=actor, increment=increment)

    def get_key(self, request: Request, actor: str) -> str:
        tenant_slug = settings.DEFAULT_TENANT_SLUG
        try:
            from backend.core.database import extract_subdomain_slug
            tenant_slug = extract_subdomain_slug(request)
        except Exception:
            hostname = (request.url.hostname or "").lower().rstrip(".")
            base_domain = settings.BASE_DOMAIN.strip(".").lower()
            if hostname.endswith(f".{base_domain}"):
                candidate = hostname[: -(len(base_domain) + 1)]
                if candidate and "." not in candidate:
                    tenant_slug = candidate
        return f"rl:{self.scope}:{tenant_slug}:{actor}"

    def reset_actor(self, request: Request, redis_client, actor: str) -> None:
        if not redis_client:
            return
        key = self.get_key(request, actor)
        try:
            redis_client.delete(key)
        except Exception:
            pass

    def _check(
        self,
        request: Request,
        redis_client,
        actor: str | None = None,
        *,
        increment: bool = True,
    ) -> None:
        sensitive_auth_scope = self.scope.startswith(("auth_login", "auth_mfa"))
        if not redis_client:
            if sensitive_auth_scope:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Authentication temporarily unavailable due to security infrastructure outage.",
                )
            return

        client_ip = request.client.host if request.client else "unknown"
        try:
            trusted_proxies = [
                ipaddress.ip_network(cidr.strip(), strict=False)
                for cidr in settings.TRUSTED_PROXY_IPS.split(",")
                if cidr.strip()
            ]
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Trusted proxy configuration is invalid.",
            ) from exc
        try:
            peer_ip = ipaddress.ip_address(client_ip)
        except ValueError:
            peer_ip = None
        if peer_ip and any(peer_ip in network for network in trusted_proxies):
            forwarded_ips = []
            for value in request.headers.get("X-Forwarded-For", "").split(","):
                try:
                    forwarded_ips.append(ipaddress.ip_address(value.strip()))
                except ValueError:
                    continue
            for candidate in reversed([*forwarded_ips, peer_ip]):
                if not any(candidate in network for network in trusted_proxies):
                    client_ip = str(candidate)
                    break
        actor = actor or client_ip
        key = self.get_key(request, actor)

        try:
            current = redis_client.get(key)
            if current is not None and int(current) >= self.max_requests:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded ({self.max_requests} requests per {self.window_seconds}s). Please retry shortly.",
                )
            if not increment:
                return
            # F-06: Atomic / pipelined rate limit increment with guaranteed TTL recovery.
            # If the process was previously interrupted between INCR and EXPIRE, or if the key
            # has lost its TTL (ttl == -1), the expiration window is immediately restored.
            if hasattr(redis_client, "pipeline"):
                pipe = redis_client.pipeline()
                pipe.incr(key)
                pipe.ttl(key)
                results = pipe.execute()
                count = results[0]
                key_ttl = results[1]
                if count == 1 or key_ttl == -1:
                    if hasattr(redis_client, "expire"):
                        redis_client.expire(key, self.window_seconds)
            elif hasattr(redis_client, "incr"):
                count = redis_client.incr(key)
                key_ttl = redis_client.ttl(key) if hasattr(redis_client, "ttl") else -1
                if count == 1 or key_ttl == -1:
                    if hasattr(redis_client, "expire"):
                        redis_client.expire(key, self.window_seconds)
            else:
                new_val = int(current or 0) + 1
                redis_client.set(key, str(new_val), ex=self.window_seconds)
        except HTTPException:
            raise
        except Exception:
            # SEC-001: Fail-closed for sensitive authentication paths to prevent brute force if Redis drops
            if sensitive_auth_scope:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Authentication temporarily unavailable due to security infrastructure outage."
                )
            # Safe fail-open for generic/non-sensitive rate limits
            return


def enforce_tenant_resource_quota(
    request: Request,
    db: Session,
    resource: str,
    additional_count: int = 1,
) -> None:
    """
    Enforces commercial quota limits (max_beds, max_users) configured in TenantRegistry.quota_limits_json.
    Raises HTTP 403 Forbidden if provisioning additional_count resources would exceed the hospital's contract quota.
    """
    tenant = resolve_tenant_context(request)
    raw_quotas = tenant.quota_limits_json
    if not raw_quotas:
        return

    try:
        quotas = json.loads(raw_quotas) if isinstance(raw_quotas, str) else raw_quotas
    except Exception:
        return

    if resource == "beds":
        max_beds = quotas.get("max_beds")
        if max_beds is not None:
            current_beds = db.scalar(select(func.count(Bed.id))) or 0
            if current_beds + additional_count > int(max_beds):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"Commercial Quota Exceeded: Hospital is licensed for a maximum of {max_beds} bed(s) "
                        f"(currently {current_beds} provisioned). Upgrade your bed quota in the Control Plane."
                    ),
                )

    elif resource == "users":
        max_users = quotas.get("max_users")
        if max_users is not None:
            current_users = db.scalar(select(func.count(User.id))) or 0
            if current_users + additional_count > int(max_users):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"Commercial Quota Exceeded: Hospital is licensed for a maximum of {max_users} staff user(s) "
                        f"(currently {current_users} provisioned). Upgrade your user quota in the Control Plane."
                    ),
                )