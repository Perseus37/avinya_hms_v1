"""Authentication and user identity APIs.

This router belongs to the identity and access domain. It manages sessions,
user personnel records, and role/department authorization for the rest of the
clinical and operational modules.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from jose import jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import get_db
from dependencies import ALGORITHM, SECRET_KEY, RequirePermission, get_current_user
from models import DoctorProfile, Role, User, UserRole, UserStatusEnum
from schemas import RoleCreate, RoleResponse, TokenResponse, UserCreate, UserLogin, UserMeResponse

router = APIRouter(prefix="/api/auth", tags=["IAM & Auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# -----------------------------------------------------------------------------
# Authentication and session domain
# -----------------------------------------------------------------------------
@router.post("/login", response_model=TokenResponse)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    """Authenticate a user and return a signed JWT used for subsequent requests."""
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not pwd_context.verify(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = jwt.encode({"sub": str(user.id)}, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me", response_model=UserMeResponse)
def get_logged_in_user(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user record without exposing secrets."""
    return current_user


# -----------------------------------------------------------------------------
# User lifecycle domain
# -----------------------------------------------------------------------------
@router.get("/users", response_model=list[UserMeResponse])
def list_users(db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """List all registered users in the system."""
    return db.query(User).all()


@router.post("/users/doctor", status_code=status.HTTP_201_CREATED)
def create_doctor(payload: UserCreate, db: Session = Depends(get_db)):
    """Create a doctor account and attach the related clinical profile if supplied."""
    hashed_pwd = pwd_context.hash(payload.password)
    user = User(
        login_id=payload.login_id,
        email=payload.email,
        hashed_password=hashed_pwd,
        full_name=payload.full_name,
        phone_number=payload.phone_number,
        gender=payload.gender,
    )
    db.add(user)
    db.flush()

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
            consultation_fee_paise=dp.consultation_fee_paise,
        )
        db.add(doc)

    db.commit()
    return {"status": "success", "message": "User and clinical profile provisioned successfully"}


@router.patch("/users/{user_id}/status", status_code=200)
def update_user_status(
    user_id: int,
    new_status: UserStatusEnum,
    db: Session = Depends(get_db),
    _: bool = Depends(RequirePermission("manage_clinical_mdm")),
):
    """Update a user’s active status such as active, inactive, or suspended."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.status = new_status
    db.commit()
    return {"status": "success", "message": f"User {user.email} status updated to {new_status}"}


@router.post("/users/{user_id}/departments", status_code=200)
def assign_user_department(
    user_id: int,
    department_id: int,
    role_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(RequirePermission("manage_clinical_mdm")),
):
    """Assigns or updates a user's operational department scope."""
    user_role = db.query(UserRole).filter(UserRole.user_id == user_id, UserRole.role_id == role_id).first()
    if user_role:
        user_role.department_id = department_id
    else:
        new_ur = UserRole(user_id=user_id, role_id=role_id, department_id=department_id)
        db.add(new_ur)

    db.commit()
    return {"status": "success", "message": f"User ID {user_id} mapped to Department ID {department_id}"}


# -----------------------------------------------------------------------------
# Role and access-control domain
# -----------------------------------------------------------------------------
@router.get("/roles", response_model=list[RoleResponse])
def list_roles(db: Session = Depends(get_db), _: bool = Depends(get_current_user)):
    """List all available system roles."""
    return db.query(Role).all()


@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
def create_role(payload: RoleCreate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Creates a new enterprise system role."""
    existing = db.query(Role).filter(Role.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Role name already exists.")

    role = Role(
        code=f"ROLE_{payload.name.upper().replace(' ', '_')}",
        name=payload.name,
        description=payload.description,
        is_system_role=False,
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return role
