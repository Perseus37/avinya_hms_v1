"""Pydantic schemas owned by the Avinya HMS domain."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from backend.domains.bloodbank.models import BloodBagStatusEnum, BloodComponentTypeEnum, DicomModalityEnum, TransfusionReactionTypeEnum
from backend.domains.identity.models import BloodGroupEnum

class BloodBagCreate(BaseModel):
    bag_number: str = Field(min_length=3, max_length=50)
    component_type: BloodComponentTypeEnum
    blood_group: BloodGroupEnum
    volume_ml: int = Field(gt=0, le=1000)
    collection_date: datetime
    expiry_date: datetime
    processing_fee_paise: int = Field(default=0, ge=0)

class BloodBagResponse(BaseModel):
    id: int
    bag_number: str
    component_type: BloodComponentTypeEnum
    blood_group: BloodGroupEnum
    volume_ml: int
    collection_date: datetime
    expiry_date: datetime
    processing_fee_paise: int
    status: BloodBagStatusEnum
    model_config = ConfigDict(from_attributes=True)

class BloodCrossMatchCreate(BaseModel):
    encounter_id: int
    blood_bag_id: int
    ordered_by_doctor_id: int

class TransfusionReactionRequest(BaseModel):
    reaction_type: TransfusionReactionTypeEnum
    reaction_notes: str = Field(min_length=5, max_length=500)

class BloodCrossMatchResponse(BaseModel):
    id: int
    encounter_id: int
    patient_id: int
    blood_bag_id: int
    ordered_by_doctor_id: int
    cross_matched_by_id: int
    issued_at: Optional[datetime] = None
    issued_by_id: Optional[int] = None
    reaction_type: TransfusionReactionTypeEnum
    reaction_notes: Optional[str] = None
    blood_bag: BloodBagResponse
    model_config = ConfigDict(from_attributes=True)

class RadiologyStudyUpsert(BaseModel):
    study_instance_uid: str = Field(min_length=5, max_length=128)
    accession_number: str = Field(min_length=3, max_length=64)
    modality: DicomModalityEnum
    pacs_viewer_url: str = Field(min_length=5, max_length=500)
    radiation_dose_mgy: Optional[float] = Field(default=None, ge=0.0)
    radiologist_impression: Optional[str] = None

class RadiologyStudyResponse(BaseModel):
    id: int
    diagnostic_task_id: int
    study_instance_uid: str
    accession_number: str
    modality: DicomModalityEnum
    pacs_viewer_url: str
    radiation_dose_mgy: Optional[float] = None
    radiologist_impression: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)
