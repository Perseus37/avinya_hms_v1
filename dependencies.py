from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials,OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy import select
from jose import JWTError, jwt
from models import User, Role
from database import get_db, get_redis

security = HTTPBearer()
# oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
SECRET_KEY = "super_secret_key"
ALGORITHM = "HS256"
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)) -> User:
# def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not isinstance(user_id, str) or not user_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")
    except JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
        
    user = db.execute(select(User).where(User.id == int(user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user

def RequirePermission(permission_code: str):
    def dependency(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        if current_user.is_superuser:
            return True
            
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
        return True
    return dependency