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
        user_id: str = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")
    except JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
        
    user = db.execute(select(User).where(User.id == int(user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user

class RequirePermission:
    def __init__(self, permission_field: str):
        self.permission_field = permission_field

    def __call__(self, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        role = db.execute(select(Role).where(Role.id == current_user.role_id)).scalar_one_or_none()
        if not role or not getattr(role, self.permission_field, False):
            raise HTTPException(status_code=403, detail=f"Requires {self.permission_field}")
        return True