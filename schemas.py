from pydantic import BaseModel, EmailStr, ConfigDict
from typing import Optional, List
from models import BedStatus

# --- AUTH & IAM ---
class RoleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    can_access_dashboard: bool = False
    can_access_patients: bool = False

class RoleResponse(BaseModel):
    id: int
    name: str
    can_access_dashboard: bool
    can_access_patients: bool
    model_config = ConfigDict(from_attributes=True)

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class DoctorProfileCreate(BaseModel):
    department_id: int
    doctor_code: str

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str
    role_id: int
    doctor_profile: Optional[DoctorProfileCreate] = None

class UserMeResponse(BaseModel):
    id: int
    email: str
    name: str
    is_active: bool
    role: RoleResponse
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
    status: BedStatus
    model_config = ConfigDict(from_attributes=True)

class RoomSchema(BaseModel):
    id: int
    room_number: str
    beds: List[BedSchema]
    model_config = ConfigDict(from_attributes=True)

class WardMapResponse(BaseModel):
    id: int
    name: str
    rooms: List[RoomSchema]
    model_config = ConfigDict(from_attributes=True)

class BedStatusUpdate(BaseModel):
    status: BedStatus

# --- CLINICAL TAXONOMY & CPOE ---
class AllergyCreate(BaseModel):
    name: str
    department_id: Optional[int] = 1 # Default scope if needed

class AllergyCheckRequest(BaseModel):
    patient_id: int
    allergen_keywords: List[str]

class TaxonomyResponse(BaseModel):
    items: List[dict]