from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from jose import jwt
from database import get_db
from schemas import UserCreate, UserLogin, TokenResponse
from models import User, DoctorProfile
from dependencies import SECRET_KEY, ALGORITHM

router = APIRouter(prefix="/api/auth", tags=["Auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

@router.post("/login", response_model=TokenResponse)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not pwd_context.verify(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
        
    token = jwt.encode({"sub": str(user.id)}, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": token, "token_type": "bearer"}

@router.post("/users/doctor", status_code=201)
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
    return {"message": "User provisioned"}