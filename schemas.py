from pydantic import BaseModel, EmailStr, ConfigDict
from datetime import datetime
from typing import Optional, List
from models import GenderEnum, UserStatusEnum, WardTypeEnum, RoomTypeEnum, BedTypeEnum, BedStatusEnum, AllergyCategoryEnum, AllergySeverityEnum, BodySystemEnum


# --- AUTH & IAM SCHEMAS ---
class UserLogin(BaseModel):
    email: EmailStr
    password: str

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

class UserCreate(BaseModel):
    login_id: str
    email: EmailStr
    password: str
    full_name: str
    phone_number: Optional[str] = None
    gender: GenderEnum = GenderEnum.OTHER
    role_ids: List[int]
    doctor_profile: Optional[DoctorProfileCreate] = None

class UserMeResponse(BaseModel):
    id: int
    login_id: str
    email: str
    full_name: str
    status: UserStatusEnum
    is_superuser: bool
    roles: List[RoleResponse]
    model_config = ConfigDict(from_attributes=True)

# --- WARD & ASSET MANAGEMENT ---
class WardCreate(BaseModel):
    name: str

class WardResponse(BaseModel):
    id: int
    name: str
    model_config = ConfigDict(from_attributes=True)

class RoomCreate(BaseModel):
    ward_id: int
    room_number: str

class BedCreate(BaseModel):
    room_id: int
    bed_number: str

class BedSchema(BaseModel):
    id: int
    bed_number: str
    status: BedStatusEnum
    model_config = ConfigDict(from_attributes=True)

class RoomSchema(BaseModel):
    id: int
    room_number: str
    beds: List[BedSchema]
    model_config = ConfigDict(from_attributes=True)

class WardMapResponse(BaseModel):
    id: int
    code: str
    name: str
    ward_type: WardTypeEnum
    model_config = ConfigDict(from_attributes=True)

class BedStatusUpdate(BaseModel):
    status: BedStatusEnum

# --- CLINICAL TAXONOMY & CPOE ---
class AllergyMasterCreate(BaseModel):
    department_id: int
    code: str
    name: str
    category: AllergyCategoryEnum
    default_severity: AllergySeverityEnum

class AllergyCheckRequest(BaseModel):
    patient_id: int
    allergen_keywords: List[str]

class TaxonomyResponse(BaseModel):
    items: List[dict]