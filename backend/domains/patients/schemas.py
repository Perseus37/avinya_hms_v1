"""Pydantic schemas owned by the Avinya HMS domain."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from backend.domains.identity.models import BloodGroupEnum, GenderEnum
from backend.domains.patients.models import AllergyCategoryEnum, AllergySeverityEnum, BodySystemEnum

class PatientCreate(BaseModel):
    mrn: Optional[str] = None
    full_name: str
    date_of_birth: datetime
    gender: GenderEnum
    phone_number: Optional[str] = "0000000000"
    abha_number: Optional[str] = None
    email: Optional[EmailStr] = None
    blood_group: Optional[BloodGroupEnum] = None
    address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    allow_network_sharing: bool = True

class PatientUpdate(BaseModel):
    full_name: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    gender: Optional[GenderEnum] = None
    phone_number: Optional[str] = None
    abha_number: Optional[str] = None
    email: Optional[EmailStr] = None
    blood_group: Optional[BloodGroupEnum] = None
    address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    is_active: Optional[bool] = None
    allow_network_sharing: Optional[bool] = None

class PatientResponse(BaseModel):
    id: int
    mrn: str
    full_name: str
    date_of_birth: datetime
    gender: GenderEnum
    phone_number: Optional[str] = None
    abha_number: Optional[str] = None
    email: Optional[str] = None
    blood_group: Optional[BloodGroupEnum] = None
    address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    is_active: bool
    allow_network_sharing: bool = True
    model_config = ConfigDict(from_attributes=True)

class PatientAllergyCreate(BaseModel):
    patient_id: Optional[int] = None
    allergy_id: int
    severity: AllergySeverityEnum
    reaction: Optional[str] = None
    reaction_description: Optional[str] = None

class AllergyCreate(BaseModel):
    department_id: int
    code: str
    name: str
    category: AllergyCategoryEnum = AllergyCategoryEnum.DRUG
    default_severity: AllergySeverityEnum = AllergySeverityEnum.MODERATE

class AllergyUpdate(BaseModel):
    department_id: Optional[int] = None
    code: Optional[str] = None
    name: Optional[str] = None
    category: Optional[AllergyCategoryEnum] = None
    default_severity: Optional[AllergySeverityEnum] = None
    is_active: Optional[bool] = None

class AllergyResponse(BaseModel):
    id: int
    department_id: int
    code: str
    name: str
    category: AllergyCategoryEnum
    default_severity: AllergySeverityEnum
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

class PatientAllergyResponse(BaseModel):
    id: int
    allergy_id: int
    severity: AllergySeverityEnum
    reaction_description: Optional[str] = None
    is_active: bool
    allergy: AllergyResponse
    model_config = ConfigDict(from_attributes=True)

class CPOEAllergyCheckRequest(BaseModel):
    patient_id: int
    allergen_keywords: List[str]

class TaxonomyResponse(BaseModel):
    items: List[dict]

class ChiefComplaintCreate(BaseModel):
    department_id: int
    code: str
    name: str

class ChiefComplaintUpdate(BaseModel):
    department_id: Optional[int] = None
    code: Optional[str] = None
    name: Optional[str] = None
    is_active: Optional[bool] = None

class ChiefComplaintResponse(BaseModel):
    id: int
    department_id: int
    code: str
    name: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

class ComorbidityCreate(BaseModel):
    department_id: int
    code: str
    name: str
    icd10_code: Optional[str] = None

class ComorbidityUpdate(BaseModel):
    department_id: Optional[int] = None
    code: Optional[str] = None
    name: Optional[str] = None
    icd10_code: Optional[str] = None
    is_active: Optional[bool] = None

class ComorbidityResponse(BaseModel):
    id: int
    department_id: int
    code: str
    name: str
    icd10_code: Optional[str] = None
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

class DiagnosisCreate(BaseModel):
    department_id: int
    icd10_code: str
    short_description: str
    full_description: str
    is_billable: bool = True
    parent_diagnosis_id: Optional[int] = None

class DiagnosisUpdate(BaseModel):
    department_id: Optional[int] = None
    icd10_code: Optional[str] = None
    short_description: Optional[str] = None
    full_description: Optional[str] = None
    is_billable: Optional[bool] = None
    is_active: Optional[bool] = None

class DiagnosisResponse(BaseModel):
    id: int
    department_id: int
    icd10_code: str
    short_description: str
    full_description: str
    is_billable: bool
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

class PhysicalExamCreate(BaseModel):
    department_id: int
    body_system: BodySystemEnum
    code: str
    name: str

class PhysicalExamUpdate(BaseModel):
    department_id: Optional[int] = None
    body_system: Optional[BodySystemEnum] = None
    code: Optional[str] = None
    name: Optional[str] = None
    is_active: Optional[bool] = None

class PhysicalExamResponse(BaseModel):
    id: int
    department_id: int
    body_system: BodySystemEnum
    code: str
    name: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)
