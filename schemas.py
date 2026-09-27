from pydantic import BaseModel, EmailStr, ConfigDict
from typing import Optional, List
from models import BedStatus

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

class AllergyCheckRequest(BaseModel):
    patient_id: int
    allergen_keywords: List[str]

class TaxonomyResponse(BaseModel):
    items: List[dict]