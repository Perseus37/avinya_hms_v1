from pydantic import BaseModel, EmailStr, ConfigDict
from datetime import datetime
from typing import Optional, List
from models import GenderEnum, UserStatusEnum, WardTypeEnum, RoomTypeEnum, BedTypeEnum, BedStatusEnum, AllergyCategoryEnum, AllergySeverityEnum, BodySystemEnum, ShiftTypeEnum


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
class FloorCreate(BaseModel):
    floor_number: int
    name: str

class WardCreate(BaseModel):
    floor_id: int
    department_id: int
    code: str
    name: str
    ward_type: WardTypeEnum

class WardUpdate(BaseModel):
    name: Optional[str] = None
    ward_type: Optional[WardTypeEnum] = None
    is_active: Optional[bool] = None

class WardResponse(BaseModel):
    id: int
    name: str
    model_config = ConfigDict(from_attributes=True)

class RoomCreate(BaseModel):
    ward_id: int
    room_number: str
    room_type: RoomTypeEnum
    bed_capacity: int

class BedCreate(BaseModel):
    room_id: int
    bed_number: str
    bed_type: BedTypeEnum
    base_daily_rate_paise: int

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

class ShiftAssignmentCreate(BaseModel):
    ward_id: int
    nurse_user_id: int
    shift_type: ShiftTypeEnum
    shift_date: datetime
    handoff_notes: Optional[str] = None

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

# --- EMR CLINICAL TAXONOMY SCHEMAS ---
class ChiefComplaintCreate(BaseModel):
    department_id: int
    code: str
    name: str

class ComorbidityCreate(BaseModel):
    department_id: int
    code: str
    name: str
    icd10_code: Optional[str] = None

class DiagnosisCreate(BaseModel):
    department_id: int
    icd10_code: str
    short_description: str
    full_description: str
    is_billable: bool = True
    parent_diagnosis_id: Optional[int] = None

class PhysicalExamCreate(BaseModel):
    department_id: int
    body_system: BodySystemEnum
    code: str
    name: str
# --- PATIENT & CLINICAL CHARTING SCHEMAS ---
class PatientCreate(BaseModel):
    mrn: str
    full_name: str
    date_of_birth: datetime
    gender: GenderEnum

class PatientResponse(BaseModel):
    id: int
    mrn: str
    full_name: str
    date_of_birth: datetime
    gender: GenderEnum
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

class PatientAllergyCreate(BaseModel):
    patient_id: int
    allergy_id: int
    severity: AllergySeverityEnum
    reaction_description: Optional[str] = None