"""Pydantic schemas owned by the Avinya HMS domain."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from backend.domains.identity.models import GenderEnum, UserStatusEnum

class UserLogin(BaseModel):
    email: EmailStr
    password: str
    hospital_slug: Optional[str] = Field(
        default=None,
        description="Optional hospital subdomain slug for multi-tenant routing from API docs",
    )

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class RoleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    can_access_dashboard: bool = False
    can_access_patients: bool = False

class RoleResponse(BaseModel):
    id: int
    code: str
    name: str
    description: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None

class PermissionResponse(BaseModel):
    id: int
    code: str
    description: Optional[str]
    module: str
    model_config = ConfigDict(from_attributes=True)

class DoctorProfileCreate(BaseModel):
    clinical_id: str
    department_id: int
    qualification: str
    specialization: str
    medical_council_name: str
    medical_registration_number: str
    registration_valid_till: datetime
    consultation_fee_paise: int
    consultation_duration_minutes: int = 15

class UserCreate(BaseModel):
    login_id: str
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: str
    phone_number: Optional[str] = None
    gender: GenderEnum = GenderEnum.OTHER
    role_ids: List[int]
    doctor_profile: Optional[DoctorProfileCreate] = None

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        character_classes = (
            any(char.islower() for char in value),
            any(char.isupper() for char in value),
            any(char.isdigit() for char in value),
            any(not char.isalnum() for char in value),
        )
        if sum(character_classes) < 3:
            raise ValueError("Password must use at least three character classes.")
        return value

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    phone_number: Optional[str] = None
    gender: Optional[GenderEnum] = None

class UserMeResponse(BaseModel):
    id: int
    login_id: str
    email: str
    full_name: str
    status: UserStatusEnum
    is_superuser: bool
    roles: List[RoleResponse]
    model_config = ConfigDict(from_attributes=True)

class DepartmentCreate(BaseModel):
    code: str
    name: str
    ndhm_speciality_code: Optional[str] = None

class DepartmentUpdate(BaseModel):
    name: Optional[str] = None
    ndhm_speciality_code: Optional[str] = None
    is_active: Optional[bool] = None

class DepartmentResponse(BaseModel):
    id: int
    code: str
    name: str
    ndhm_speciality_code: Optional[str] = None
    is_active: bool
    model_config = ConfigDict(from_attributes=True)
