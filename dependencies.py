"""Shared authentication and authorization dependencies.

These helpers belong to the identity and access domain. They validate bearer
JWTs and enforce permission-based access control before any healthcare workflow
can continue.
"""

import hashlib
from datetime import datetime, timezone
from fastapi import Depends, HTTPException, status,Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from models import Permission, Role, User, UserStatusEnum,FederatedClient
from config import settings


# -----------------------------------------------------------------------------
# Identity and security configuration
# -----------------------------------------------------------------------------
security = HTTPBearer()

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """Validate token expiration and ensure the user is actively employed."""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = payload.get("sub")
        if not isinstance(user_id, str) or not user_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")
    except JWTError as e:
        # python-jose automatically raises JWTError if 'exp' is missing or past
        raise HTTPException(status_code=401, detail="Token is invalid or expired")

    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid subject identifier")

    user = db.get(User, uid)
    
    # C3 FIX: Explicitly reject suspended or deactivated accounts
    if not user or user.status != UserStatusEnum.ACTIVE:
        raise HTTPException(status_code=401, detail="User account is suspended or inactive")
        
    db.info["audit_user_id"] = user.id
    return user

def RequirePermission(permission_code: str):
    """Verifies a user has the required permission and returns the authorized User object."""
    def dependency(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        if current_user.is_superuser:
            return current_user
                     
        stmt = (
            select(Permission)
            .join(Role.permissions)
            .join(Role.users)
            .where(User.id == current_user.id)
            .where(Permission.code == permission_code)
            .limit(1)  # H1 FIX: Prevents MultipleResultsFound if user has overlapping roles
        )
        perm = db.execute(stmt).scalar_one_or_none()
        if not perm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: Required permission '{permission_code}' missing."
            )
        return current_user
    return dependency

def validate_pagination(page: int = 1, page_size: int = 20) -> tuple[int, int]:
    """Enforces safe pagination boundaries across all list endpoints."""
    if page < 1:
        raise HTTPException(status_code=400, detail="Page must be greater than 0")
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="Page size must be between 1 and 200")
    return page, page_size

def get_federated_client(
    x_federation_api_key: str = Header(..., alias="X-Federation-API-Key"),
    db: Session = Depends(get_db),
) -> FederatedClient:
    """Authenticates external branches/tenants via SHA-256 hashed API keys."""
    if not x_federation_api_key or len(x_federation_api_key) < 16:
        raise HTTPException(status_code=401, detail="Invalid Federation API Key format.")

    key_hash = hashlib.sha256(x_federation_api_key.encode("utf-8")).hexdigest()
    stmt = select(FederatedClient).where(
        FederatedClient.api_key_hash == key_hash,
        FederatedClient.is_active == True,
    )
    client = db.execute(stmt).scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=401, detail="Unauthorized or revoked Federation API Key.")

    client.last_used_at = datetime.now(timezone.utc)
    db.flush()
    return client