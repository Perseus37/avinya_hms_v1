from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from jose import jwt
from database import get_db
from schemas import UserCreate, UserLogin, TokenResponse, UserMeResponse
from models import User, DoctorProfile, Role
from dependencies import SECRET_KEY, ALGORITHM, get_current_user

router = APIRouter(prefix="/api/auth", tags=["IAM & Auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

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
    user = User(
        login_id=payload.login_id,
        email=payload.email,
        hashed_password=hashed_pwd,
        full_name=payload.full_name,
        phone_number=payload.phone_number,
        gender=payload.gender
    )
    db.add(user)
    db.flush()
    
    # Assign roles
    roles = db.query(Role).filter(Role.id.in_(payload.role_ids)).all()
    user.roles.extend(roles)
    
    if payload.doctor_profile:
        dp = payload.doctor_profile
        doc = DoctorProfile(
            user_id=user.id,
            clinical_id=dp.clinical_id,
            department_id=dp.department_id,
            qualification=dp.qualification,
            specialization=dp.specialization,
            medical_council_name=dp.medical_council_name,
            medical_registration_number=dp.medical_registration_number,
            registration_valid_till=dp.registration_valid_till,
            consultation_fee_paise=dp.consultation_fee_paise
        )
        db.add(doc)
        
    db.commit()
    return {"status": "success", "message": "User and clinical profile provisioned successfully"}

@router.get("/me", response_model=UserMeResponse)
def get_logged_in_user(current_user: User = Depends(get_current_user)):
    return current_user