"""Shared authentication and authorization dependencies.

These helpers belong to the identity and access domain. They validate bearer
JWTs and enforce permission-based access control before any healthcare workflow
can continue.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from models import Permission, Role, User
from config import settings

# -----------------------------------------------------------------------------
# Identity and security configuration
# -----------------------------------------------------------------------------
security = HTTPBearer()

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """Validate an incoming bearer token and return the active authenticated user."""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = payload.get("sub")
        if not isinstance(user_id, str) or not user_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")
    except JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    user = db.execute(select(User).where(User.id == int(user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or inactive")
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
        )
        perm = db.execute(stmt).scalar_one_or_none()
        if not perm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: Required permission '{permission_code}' missing."
            )
        # Return the user object instead of True for audit tracking
        return current_user
    return dependency