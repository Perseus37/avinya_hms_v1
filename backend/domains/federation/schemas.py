from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from backend.domains.identity.models import BloodGroupEnum, GenderEnum
from backend.domains.patients.models import SeverityEnum


class FederatedClientCreate(BaseModel):
    branch_code: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=3, max_length=150)


class FederatedClientCreatedResponse(BaseModel):
    id: int
    branch_code: str
    name: str
    api_key_prefix: str
    raw_api_key: str
    is_active: bool


class FederatedClientResponse(BaseModel):
    id: int
    branch_code: str
    name: str
    api_key_prefix: str
    is_active: bool
    last_used_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class FederatedAllergySummary(BaseModel):
    allergy_code: str
    allergy_name: str
    severity: SeverityEnum
    reaction: str


class FederatedPatientSummaryResponse(BaseModel):
    source_mrn: str
    abha_number: Optional[str] = None
    full_name: str
    date_of_birth: datetime
    gender: GenderEnum
    blood_group: Optional[BloodGroupEnum] = None
    phone_number: Optional[str] = None
    email: Optional[EmailStr] = None
    address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    active_allergies: list[FederatedAllergySummary] = []
