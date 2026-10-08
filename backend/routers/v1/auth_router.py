"""Authentication and user identity APIs.

This router belongs to the identity and access domain. It manages sessions,
user personnel records, and role/department authorization for the rest of the
clinical and operational modules.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import settings
from database import get_db, resolve_tenant_context
from dependencies import RateLimiter, RequirePermission, get_current_user, validate_pagination, enforce_tenant_resource_quota
from models import Department, DoctorProfile, Permission, Role, User, UserRole, UserStatusEnum
from schemas.v1.schemas import (
    DepartmentCreate,
    DepartmentResponse,
    DepartmentUpdate,
    PaginatedResponse,
    PermissionResponse,
    RoleCreate,
    RoleResponse,
    RoleUpdate,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserMeResponse,
    UserUpdate,
)

from security.envelope import decrypt_tenant_secret
from routers.v1.tenant_admin_router import get_or_create_policy

router = APIRouter(prefix="/api/auth", tags=["IAM & Auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# MAX_FAILED_LOGIN_ATTEMPTS = get_or_create_policy(get_db).max_failed_login_attempts
MAX_FAILED_LOGIN_ATTEMPTS = 5

class CustomPermissionCreate(BaseModel):
    module: str = Field(min_length=2, max_length=50, description="Module name, e.g., Billing or Radiology")
    resource: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9_]+$")
    action: str = Field(min_length=2, max_length=30, pattern=r"^(read|write|delete|verify|void|reverse|discharge|cancel|[a-z_]+)$")
    description: str = Field(min_length=5, max_length=255)


# -----------------------------------------------------------------------------
# Authentication and session domain
# -----------------------------------------------------------------------------
@router.post("/login")
def login(
    payload: UserLogin,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(RateLimiter(scope="auth_login", max_requests=10, window_seconds=60)),
):
    """Authenticates a user against the active hospital database and signs a tenant-isolated JWT."""
    tenant = resolve_tenant_context(request)
    
    # SEC-007: Implement pessimistic lock to prevent distributed brute-force lockout bypass
    from sqlalchemy import select
    user = db.execute(
        select(User).where(User.email == payload.email).with_for_update()
    ).scalar_one_or_none()

    # SEC-003: Timing-Attack Mitigation. Run dummy verify if user is None.
    # Mathematically valid bcrypt hash (matches "password" at cost 12)
    dummy_hash = "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewKd.RvvJ8hYjT.W"
    is_valid_pwd = False
    
    if user:
        is_valid_pwd = pwd_context.verify(payload.password, user.hashed_password)
    else:
        pwd_context.verify(payload.password, dummy_hash)

    if not user or not is_valid_pwd:
        if user:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= MAX_FAILED_LOGIN_ATTEMPTS:
                user.status = UserStatusEnum.LOCKED
            db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if user.status != UserStatusEnum.ACTIVE:
        raise HTTPException(status_code=401, detail=f"Account is {user.status.value}.")

    user.failed_login_attempts = 0
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()

    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "sub": str(user.id),
        "tenant_slug": tenant.subdomain_slug,
        "branch_code": tenant.branch_code,
        "exp": expire,
    }
    signing_key = decrypt_tenant_secret(tenant.jwt_secret_key)
    token = jwt.encode(to_encode, signing_key, algorithm=settings.ALGORITHM)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me", response_model=UserMeResponse)
def get_logged_in_user(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user record without exposing secrets."""
    return current_user


# -----------------------------------------------------------------------------
# User lifecycle domain (Granular IAM: iam:read, iam:write, iam:delete)
# -----------------------------------------------------------------------------
@router.get("/users", response_model=PaginatedResponse[UserMeResponse])
def list_users(
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("iam:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(User).order_by(User.id.asc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/users/doctor", status_code=status.HTTP_201_CREATED)
def create_doctor(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:write")),
):
    enforce_tenant_resource_quota(request, db, resource="users", additional_count=1)
    roles = db.query(Role).filter(Role.id.in_(payload.role_ids)).all()
    if len(roles) != len(set(payload.role_ids)):
        raise HTTPException(status_code=400, detail="One or more role IDs are invalid.")

    if any(r.is_system_role for r in roles) and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Cannot assign system roles without superuser privileges.")

    user = User(
        login_id=payload.login_id,
        email=payload.email,
        hashed_password=pwd_context.hash(payload.password),
        full_name=payload.full_name,
        phone_number=payload.phone_number,
        gender=payload.gender,
    )
    db.add(user)
    db.flush()

    db.add_all(UserRole(user_id=user.id, role_id=role.id) for role in roles)

    if payload.doctor_profile:
        dp = payload.doctor_profile
        db.add(
            DoctorProfile(
                user_id=user.id,
                clinical_id=dp.clinical_id,
                department_id=dp.department_id,
                qualification=dp.qualification,
                specialization=dp.specialization,
                medical_council_name=dp.medical_council_name,
                medical_registration_number=dp.medical_registration_number,
                registration_valid_till=dp.registration_valid_till,
                consultation_fee_paise=dp.consultation_fee_paise,
                consultation_duration_minutes=dp.consultation_duration_minutes,
            )
        )

    db.commit()
    return {"status": "success", "message": "User and clinical profile provisioned successfully"}


@router.patch("/users/{user_id}/status", status_code=200)
def update_user_status(
    user_id: int,
    new_status: UserStatusEnum,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:delete", "iam:write")),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_superuser and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Cannot modify status of a superuser account.")

    user.status = new_status
    if new_status == UserStatusEnum.ACTIVE:
        user.failed_login_attempts = 0
    db.commit()
    return {"status": "success", "message": f"User {user.email} status updated to {new_status.value}"}


@router.post("/users/{user_id}/departments", status_code=200)
def assign_user_department(
    user_id: int,
    department_id: int,
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:write")),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.is_superuser and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Cannot modify roles of a superuser account.")

    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found.")
    if role.is_system_role and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Cannot assign system roles without superuser privileges.")

    dept = db.get(Department, department_id)
    if not dept or not dept.is_active:
        raise HTTPException(status_code=404, detail="Active department not found.")

    user_role = db.query(UserRole).filter(UserRole.user_id == user_id, UserRole.role_id == role_id).first()
    if user_role:
        user_role.department_id = department_id
    else:
        db.add(UserRole(user_id=user_id, role_id=role_id, department_id=department_id))

    db.commit()
    return {"status": "success", "message": f"User ID {user_id} mapped to Department ID {department_id}"}


@router.get("/users/{user_id}", response_model=UserMeResponse)
def get_user(user_id: int, db: Session = Depends(get_db), _: User = Depends(RequirePermission("iam:read"))):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


@router.put("/users/{user_id}", response_model=UserMeResponse)
def update_user_details(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:write")),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.is_superuser and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Cannot modify a superuser account.")

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.phone_number is not None:
        user.phone_number = payload.phone_number
    if payload.gender is not None:
        user.gender = payload.gender
    user.updated_by_id = current_user.id
    db.commit()
    db.refresh(user)
    return user


# -----------------------------------------------------------------------------
# Role & Granular Read/Write Permission Matrix Domain
# -----------------------------------------------------------------------------
@router.get("/roles", response_model=PaginatedResponse[RoleResponse])
def list_roles(
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("iam:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(Role).order_by(Role.id.asc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:write")),
):
    role_code = f"ROLE_{payload.name.upper().replace(' ', '_')}"
    existing = db.query(Role).filter((Role.name == payload.name) | (Role.code == role_code)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Role name or code already exists.")

    role = Role(
        code=role_code,
        name=payload.name,
        description=payload.description,
        is_system_role=False,
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
    )
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


@router.put("/roles/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: int,
    payload: RoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:write")),
):
    role = db.get(Role, role_id)
    if not role or role.is_system_role:
        raise HTTPException(status_code=400, detail="Role not found or is a protected system role.")
    if payload.name is not None:
        role.name = payload.name
    if payload.description is not None:
        role.description = payload.description
    role.updated_by_id = current_user.id
    db.commit()
    db.refresh(role)
    return role


@router.delete("/roles/{role_id}", status_code=200)
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("iam:delete")),
):
    role = db.get(Role, role_id)
    if not role or role.is_system_role:
        raise HTTPException(status_code=400, detail="Role not found or is a protected system role.")
    if role.users:
        raise HTTPException(status_code=409, detail="Cannot delete a role that is actively assigned to users.")
    db.delete(role)
    db.commit()
    return {"status": "success", "message": f"Role ID {role_id} permanently removed."}


@router.get("/permissions", response_model=list[PermissionResponse])
def list_permissions(db: Session = Depends(get_db), _: User = Depends(RequirePermission("iam:read"))):
    return db.query(Permission).order_by(Permission.module.asc(), Permission.code.asc()).all()


@router.get("/permissions/matrix")
def get_permissions_matrix(db: Session = Depends(get_db), _: User = Depends(RequirePermission("iam:read"))):
    """
    Returns all permissions organized by Module and Action (read / write / destructive)
    so the Hospital Admin UI can render Read-Only, Write-Only, or Full Access checkboxes.
    """
    perms = db.query(Permission).order_by(Permission.module.asc(), Permission.code.asc()).all()
    matrix: dict[str, dict[str, list[dict]]] = defaultdict(lambda: {"read": [], "write": [], "destructive": []})

    for p in perms:
        action = p.code.split(":")[-1] if ":" in p.code else "other"
        item = {"id": p.id, "code": p.code, "action": action, "description": p.description}
        if action == "read":
            matrix[p.module]["read"].append(item)
        elif action == "write":
            matrix[p.module]["write"].append(item)
        else:
            matrix[p.module]["destructive"].append(item)

    return {"modules": dict(matrix)}


@router.post("/permissions", response_model=PermissionResponse, status_code=status.HTTP_201_CREATED)
def create_custom_permission(
    payload: CustomPermissionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:write")),
):
    """Allows a Hospital Superuser Admin to register new granular `resource:action` permissions."""
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Only the Hospital Superuser can create new permission definitions.")

    code = f"{payload.resource.lower()}:{payload.action.lower()}"
    existing = db.query(Permission).filter(Permission.code == code).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Permission '{code}' already exists.")

    perm = Permission(code=code, description=payload.description, module=payload.module)
    db.add(perm)
    db.commit()
    db.refresh(perm)
    return perm


@router.post("/roles/{role_id}/permissions", status_code=200)
def assign_role_permissions(
    role_id: int,
    permission_ids: list[int],
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("iam:write")),
):
    """Assigns any combination of Read-Only, Write-Only, or Destructive permissions to a role."""
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found.")
    if role.is_system_role and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Cannot modify permissions of a system role.")

    perms = db.query(Permission).filter(Permission.id.in_(permission_ids)).all()
    if len(perms) != len(set(permission_ids)):
        raise HTTPException(status_code=400, detail="One or more permission IDs are invalid.")

    if not current_user.is_superuser:
        caller_perm_ids = {p.id for r in current_user.roles for p in r.permissions}
        if not set(permission_ids).issubset(caller_perm_ids):
            raise HTTPException(status_code=403, detail="Cannot grant permissions that you do not possess.")

    role.permissions = perms
    db.commit()
    return {"status": "success", "message": f"Assigned {len(perms)} granular permissions to role '{role.name}'."}


# -----------------------------------------------------------------------------
# Department Lifecycle (mdm:read, mdm:write, mdm:delete)
# -----------------------------------------------------------------------------
@router.post("/departments", response_model=DepartmentResponse, status_code=status.HTTP_201_CREATED)
def create_department(
    payload: DepartmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("mdm:write", "iam:write")),
):
    existing = db.query(Department).filter(Department.code == payload.code).first()
    if existing:
        raise HTTPException(status_code=409, detail="Department code already exists.")
    dept = Department(**payload.model_dump(), is_active=True, created_by_id=current_user.id, updated_by_id=current_user.id)
    db.add(dept)
    db.commit()
    db.refresh(dept)
    return dept


@router.get("/departments", response_model=PaginatedResponse[DepartmentResponse])
def list_departments(
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:read", "iam:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(Department).filter(Department.is_active == True).order_by(Department.id.asc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.put("/departments/{dept_id}", response_model=DepartmentResponse)
def update_department(
    dept_id: int,
    payload: DepartmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("mdm:write", "iam:write")),
):
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found.")
    if payload.name is not None:
        dept.name = payload.name
    if payload.ndhm_speciality_code is not None:
        dept.ndhm_speciality_code = payload.ndhm_speciality_code
    if payload.is_active is not None:
        dept.is_active = payload.is_active
    dept.updated_by_id = current_user.id
    db.commit()
    db.refresh(dept)
    return dept


@router.delete("/departments/{dept_id}", status_code=200)
def deactivate_department(
    dept_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("mdm:delete", "iam:delete")),
):
    dept = db.get(Department, dept_id)
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found.")
    dept.is_active = False
    dept.updated_by_id = current_user.id
    db.commit()
    return {"status": "success", "message": f"Department ID {dept_id} deactivated."}