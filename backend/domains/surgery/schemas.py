"""Pydantic schemas owned by the Avinya HMS domain."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from backend.domains.surgery.models import AnesthesiaTypeEnum, OTTheatreTypeEnum, SurgeryStatusEnum

class OperationTheatreCreate(BaseModel):
    code: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=2, max_length=100)
    theatre_type: OTTheatreTypeEnum
    floor_id: Optional[int] = None
    hourly_rate_paise: int = Field(default=0, ge=0)

class OperationTheatreResponse(BaseModel):
    id: int
    code: str
    name: str
    theatre_type: OTTheatreTypeEnum
    floor_id: Optional[int] = None
    hourly_rate_paise: int
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

class SurgicalScheduleCreate(BaseModel):
    encounter_id: int
    theatre_id: int
    procedure_service_id: int
    lead_surgeon_id: int
    anesthetist_id: Optional[int] = None
    scheduled_start_time: datetime
    scheduled_end_time: datetime
    pre_op_diagnosis_notes: Optional[str] = None

class WHOChecklistUpsert(BaseModel):
    patient_identity_confirmed: bool = False
    site_marked: bool = False
    consent_signed: bool = False
    pulse_oximeter_functioning: bool = False
    allergy_checked: bool = False
    antibiotic_prophylaxis_given: bool = False
    sponge_instrument_count_verified: bool = False
    specimen_labeled: bool = True

class WHOChecklistResponse(BaseModel):
    id: int
    surgery_id: int
    verified_by_id: int
    patient_identity_confirmed: bool
    site_marked: bool
    consent_signed: bool
    pulse_oximeter_functioning: bool
    allergy_checked: bool
    antibiotic_prophylaxis_given: bool
    sponge_instrument_count_verified: bool
    specimen_labeled: bool
    model_config = ConfigDict(from_attributes=True)

class OperativeRecordUpsert(BaseModel):
    anesthesia_type: AnesthesiaTypeEnum
    asa_physical_status: int = Field(default=1, ge=1, le=6)
    anesthesia_notes: Optional[str] = None
    operative_findings: str = Field(min_length=3)
    post_op_instructions: Optional[str] = None
    estimated_blood_loss_ml: int = Field(default=0, ge=0)

class OperativeRecordResponse(BaseModel):
    id: int
    surgery_id: int
    anesthesia_type: AnesthesiaTypeEnum
    asa_physical_status: int
    anesthesia_notes: Optional[str] = None
    operative_findings: str
    post_op_instructions: Optional[str] = None
    estimated_blood_loss_ml: int
    model_config = ConfigDict(from_attributes=True)

class SurgicalImplantCreate(BaseModel):
    implant_name: str = Field(min_length=2, max_length=150)
    manufacturer: Optional[str] = None
    serial_or_batch_number: str = Field(min_length=2, max_length=100)
    quantity: int = Field(default=1, gt=0)
    unit_price_paise: int = Field(ge=0)

class SurgicalImplantResponse(BaseModel):
    id: int
    surgery_id: int
    implant_name: str
    manufacturer: Optional[str] = None
    serial_or_batch_number: str
    quantity: int
    unit_price_paise: int
    model_config = ConfigDict(from_attributes=True)

class SurgeryStatusUpdateRequest(BaseModel):
    status: SurgeryStatusEnum

class SurgicalScheduleResponse(BaseModel):
    id: int
    case_number: str
    encounter_id: int
    patient_id: int
    theatre_id: int
    procedure_service_id: int
    lead_surgeon_id: int
    anesthetist_id: Optional[int] = None
    scheduled_start_time: datetime
    scheduled_end_time: datetime
    actual_start_time: Optional[datetime] = None
    actual_end_time: Optional[datetime] = None
    status: SurgeryStatusEnum
    pre_op_diagnosis_notes: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)
