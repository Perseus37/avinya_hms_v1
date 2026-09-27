from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from passlib.context import CryptContext
from jose import jwt
from database import get_db
from schemas import UserCreate, UserLogin, TokenResponse, RoleCreate, RoleResponse, UserMeResponse
from models import User, DoctorProfile, Role
from dependencies import SECRET_KEY, ALGORITHM, get_current_user

router = APIRouter(prefix="/api/auth", tags=["IAM & Auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
def create_role(payload: RoleCreate, db: Session = Depends(get_db)):
    existing = db.query(Role).filter(Role.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Role name already exists.")
    
    role = Role(
        name=payload.name,
        description=payload.description,
        can_access_dashboard=payload.can_access_dashboard,
        can_access_patients=payload.can_access_patients
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return role

@router.post("/login", response_model=TokenResponse)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not pwd_context.verify(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
        
    token = jwt.encode({"sub": str(user.id)}, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": token, "token_type": "bearer"}

@router.post("/users/doctor", status_code=status.HTTP_201_CREATED)
def create_doctor(payload: UserCreate, db: Session = Depends(get_db)):
    hashed_pwd = pwd_context.hash(payload.password)
    user = User(email=payload.email, hashed_password=hashed_pwd, name=payload.name, role_id=payload.role_id)
    db.add(user)
    db.flush()
    
    if payload.doctor_profile:
        doc = DoctorProfile(
            user_id=user.id,
            department_id=payload.doctor_profile.department_id,
            doctor_code=payload.doctor_profile.doctor_code
        )
        db.add(doc)
    db.commit()
    return {"message": "User and clinical profile provisioned successfully"}

@router.get("/me", response_model=UserMeResponse)
def get_logged_in_user(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Explicitly load role relationship
    db.refresh(current_user)
    return current_user