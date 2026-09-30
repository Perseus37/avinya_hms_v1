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
from dependencies import RequirePermission, get_current_user
from models import DoctorProfile, Role, User, UserRole, UserStatusEnum
from schemas import PaginatedResponse, RoleCreate, RoleResponse, TokenResponse, UserCreate, UserLogin, UserMeResponse

from config import settings

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

    token = jwt.encode({"sub": str(user.id)}, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me", response_model=UserMeResponse)
def get_logged_in_user(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user record without exposing secrets."""
    return current_user


# -----------------------------------------------------------------------------
# User lifecycle domain
# -----------------------------------------------------------------------------
@router.get("/users", response_model=PaginatedResponse[UserMeResponse])
def list_users(
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _: bool = Depends(RequirePermission("manage_clinical_mdm")),
):
    """List registered users with pagination support."""
    if page < 1:
        raise HTTPException(status_code=400, detail="Page must be greater than 0")
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="Page size must be between 1 and 200")

    query = db.query(User)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/users/doctor", status_code=status.HTTP_201_CREATED)
def create_doctor(
    payload: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("manage_clinical_mdm")),
):
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
    db.add_all(UserRole(user_id=user.id, role_id=role.id) for role in roles)

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
    current_user: User = Depends(RequirePermission("manage_clinical_mdm")),
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
    current_user: User = Depends(RequirePermission("manage_clinical_mdm")),
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
@router.get("/roles", response_model=PaginatedResponse[RoleResponse])
def list_roles(
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("manage_clinical_mdm")),
):
    """List available system roles with pagination support."""
    if page < 1:
        raise HTTPException(status_code=400, detail="Page must be greater than 0")
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="Page size must be between 1 and 200")

    query = db.query(Role)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
def create_role(payload: RoleCreate, db: Session = Depends(get_db), current_user: User = Depends(RequirePermission("manage_clinical_mdm"))):
    """Creates a new enterprise system role."""
    existing = db.query(Role).filter(Role.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Role name already exists.")

    role = Role(
        code=f"ROLE_{payload.name.upper().replace(' ', '_')}",
        name=payload.name,
        description=payload.description,
        is_system_role=False,
        created_by_id=current_user.id,
        updated_by_id=current_user.id
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return role
from models import Department, Permission
from schemas import DepartmentCreate, DepartmentUpdate, DepartmentResponse, RoleUpdate, UserUpdate, PermissionResponse

# --- Department Lifecycle ---

@router.post("/departments", response_model=DepartmentResponse, status_code=status.HTTP_201_CREATED)
def create_department(payload: DepartmentCreate, db: Session = Depends(get_db), current_user: User = Depends(RequirePermission("manage_clinical_mdm"))):
    existing = db.query(Department).filter(Department.code == payload.code).first()
    if existing:
        raise HTTPException(status_code=409, detail="Department code already exists.")
    dept = Department(**payload.model_dump(), is_active=True, created_by_id=current_user.id, updated_by_id=current_user.id)
    db.add(dept)
    db.commit()
    db.refresh(dept)
    return dept

@router.get("/departments", response_model=PaginatedResponse[DepartmentResponse])
def list_departments(page: int = 1, page_size: int = 50, db: Session = Depends(get_db), _: User = Depends(RequirePermission("manage_clinical_mdm"))):
    query = db.query(Department).filter(Department.is_active == True)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@router.put("/departments/{dept_id}", response_model=DepartmentResponse)
def update_department(dept_id: int, payload: DepartmentUpdate, db: Session = Depends(get_db), current_user: User = Depends(RequirePermission("manage_clinical_mdm"))):
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found.")
    if payload.name is not None: dept.name = payload.name
    if payload.ndhm_speciality_code is not None: dept.ndhm_speciality_code = payload.ndhm_speciality_code
    if payload.is_active is not None: dept.is_active = payload.is_active
    dept.updated_by_id = current_user.id
    db.commit()
    db.refresh(dept)
    return dept

@router.delete("/departments/{dept_id}", status_code=200)
def deactivate_department(dept_id: int, db: Session = Depends(get_db), current_user: User = Depends(RequirePermission("manage_clinical_mdm"))):
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found.")
    dept.is_active = False
    dept.updated_by_id = current_user.id
    db.commit()
    return {"status": "success", "message": f"Department ID {dept_id} deactivated."}


# --- Permissions & Roles Missing CRUD ---

@router.get("/permissions", response_model=list[PermissionResponse])
def list_permissions(db: Session = Depends(get_db), current_user: User = Depends(RequirePermission("manage_clinical_mdm"))):
    """Provides a flat list of all hardcoded system permissions for role assignment UI."""
    return db.query(Permission).all()

@router.put("/roles/{role_id}", response_model=RoleResponse)
def update_role(role_id: int, payload: RoleUpdate, db: Session = Depends(get_db), current_user: User = Depends(RequirePermission("manage_clinical_mdm"))):
    role = db.get(Role, role_id)
    if not role or role.is_system_role:
        raise HTTPException(status_code=400, detail="Role not found or is a protected system role.")
    if payload.name is not None: role.name = payload.name
    if payload.description is not None: role.description = payload.description
    role.updated_by_id = current_user.id
    db.commit()
    db.refresh(role)
    return role

@router.delete("/roles/{role_id}", status_code=200)
def delete_role(role_id: int, db: Session = Depends(get_db), current_user: User = Depends(RequirePermission("manage_clinical_mdm"))):
    role = db.get(Role, role_id)
    if not role or role.is_system_role:
        raise HTTPException(status_code=400, detail="Role not found or is a protected system role.")
    db.delete(role)
    db.commit()
    return {"status": "success", "message": f"Role ID {role_id} permanently removed."}


# --- User Missing CRUD ---

@router.get("/users/{user_id}", response_model=UserMeResponse)
def get_user(user_id: int, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user

@router.put("/users/{user_id}", response_model=UserMeResponse)
def update_user_details(user_id: int, payload: UserUpdate, db: Session = Depends(get_db), current_user: User = Depends(RequirePermission("manage_clinical_mdm"))):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if payload.full_name is not None: user.full_name = payload.full_name
    if payload.phone_number is not None: user.phone_number = payload.phone_number
    if payload.gender is not None: user.gender = payload.gender
    user.updated_by_id = current_user.id
    db.commit()
    db.refresh(user)
    return user