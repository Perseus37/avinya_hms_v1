"""Pydantic schemas owned by the Avinya HMS domain."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from backend.domains.clinical.models import DeliveryModeEnum, FluidDirectionEnum, MLCCauseEnum, MannerOfDeathEnum, TriageAcuityEnum
from backend.domains.encounters.models import DiagnosisTypeEnum
from backend.domains.identity.models import BloodGroupEnum, GenderEnum
from backend.domains.pharmacy.models import eMARStatusEnum
from backend.domains.patients.schemas import ComorbidityResponse, DiagnosisResponse, PhysicalExamResponse

class EncounterVitalCreate(BaseModel):
    temperature_f: Optional[float] = Field(default=None, ge=85.0, le=115.0)
    pulse_bpm: Optional[int] = Field(default=None, ge=20, le=300)
    bp_systolic: Optional[int] = Field(default=None, ge=40, le=300)
    bp_diastolic: Optional[int] = Field(default=None, ge=20, le=200)
    respiratory_rate: Optional[int] = Field(default=None, ge=4, le=100)
    spo2_percent: Optional[int] = Field(default=None, ge=30, le=100)
    weight_kg: Optional[float] = Field(default=None, gt=0.0, le=500.0)
    height_cm: Optional[float] = Field(default=None, gt=20.0, le=300.0)
    notes: Optional[str] = Field(default=None, max_length=500)

class EncounterVitalResponse(BaseModel):
    id: int
    encounter_id: int
    temperature_f: Optional[float] = None
    pulse_bpm: Optional[int] = None
    bp_systolic: Optional[int] = None
    bp_diastolic: Optional[int] = None
    respiratory_rate: Optional[int] = None
    spo2_percent: Optional[int] = None
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    bmi: Optional[float] = None
    notes: Optional[str] = None
    created_by_id: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class ClinicalNoteCreate(BaseModel):
    subjective_notes: Optional[str] = None
    objective_notes: Optional[str] = None
    assessment_notes: Optional[str] = None
    plan_notes: Optional[str] = None

class ClinicalNoteUpdate(BaseModel):
    subjective_notes: Optional[str] = None
    objective_notes: Optional[str] = None
    assessment_notes: Optional[str] = None
    plan_notes: Optional[str] = None

class ClinicalNoteResponse(BaseModel):
    id: int
    encounter_id: int
    subjective_notes: Optional[str] = None
    objective_notes: Optional[str] = None
    assessment_notes: Optional[str] = None
    plan_notes: Optional[str] = None
    is_signed: bool
    signed_at: Optional[datetime] = None
    signed_by_id: Optional[int] = None
    created_by_id: Optional[int] = None
    updated_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class EncounterDiagnosisCreate(BaseModel):
    diagnosis_id: int
    diagnosis_type: DiagnosisTypeEnum = DiagnosisTypeEnum.PRIMARY
    clinical_remarks: Optional[str] = Field(default=None, max_length=500)

class EncounterDiagnosisResponse(BaseModel):
    id: int
    encounter_id: int
    diagnosis_id: int
    diagnosis_type: DiagnosisTypeEnum
    clinical_remarks: Optional[str] = None
    created_by_id: Optional[int] = None
    diagnosis: DiagnosisResponse

    model_config = ConfigDict(from_attributes=True)

class eMARCreate(BaseModel):
    prescription_item_id: int
    status: eMARStatusEnum = eMARStatusEnum.ADMINISTERED
    dose_given: str = Field(min_length=1, max_length=100)
    scheduled_time: Optional[datetime] = None
    notes: Optional[str] = Field(default=None, max_length=500)

class eMARResponse(BaseModel):
    id: int
    encounter_id: int
    prescription_item_id: int
    administered_by_id: int
    scheduled_time: Optional[datetime] = None
    administered_at: datetime
    status: eMARStatusEnum
    dose_given: str
    notes: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class FluidLogCreate(BaseModel):
    direction: FluidDirectionEnum
    fluid_type: str = Field(min_length=2, max_length=100)
    volume_ml: int = Field(gt=0, le=10000)
    notes: Optional[str] = Field(default=None, max_length=255)

class FluidLogResponse(BaseModel):
    id: int
    encounter_id: int
    recorded_by_id: int
    direction: FluidDirectionEnum
    fluid_type: str
    volume_ml: int
    recorded_at: datetime
    notes: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class FluidBalanceSummaryResponse(BaseModel):
    encounter_id: int
    total_intake_ml: int
    total_output_ml: int
    net_balance_ml: int
    logs: List[FluidLogResponse]

class ERTriageCreate(BaseModel):
    acuity_level: TriageAcuityEnum
    chief_complaint: str = Field(min_length=2, max_length=255)
    gcs_score: Optional[int] = Field(default=15, ge=3, le=15)
    pain_scale: Optional[int] = Field(default=0, ge=0, le=10)
    airway_status: Optional[str] = "CLEAR"
    breathing_status: Optional[str] = "NORMAL"
    circulation_status: Optional[str] = "STABLE"
    is_mlc_suspected: bool = False
    triage_notes: Optional[str] = None

class ERTriageResponse(BaseModel):
    id: int
    encounter_id: int
    triaged_by_id: int
    acuity_level: TriageAcuityEnum
    chief_complaint: str
    gcs_score: Optional[int] = None
    pain_scale: Optional[int] = None
    airway_status: Optional[str] = None
    breathing_status: Optional[str] = None
    circulation_status: Optional[str] = None
    is_mlc_suspected: bool
    triage_notes: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class MLCCreate(BaseModel):
    encounter_id: int
    cause_of_injury: MLCCauseEnum
    alleged_history: str = Field(min_length=5)
    identification_marks: str = Field(min_length=3, max_length=255)
    brought_by_name: str = Field(min_length=2, max_length=100)
    brought_by_relation: Optional[str] = None
    brought_by_phone: Optional[str] = None
    police_station_name: str = Field(min_length=3, max_length=150)
    police_constable_buckle_no: Optional[str] = None

class MLCPoliceIntimationRequest(BaseModel):
    police_constable_buckle_no: str = Field(min_length=2, max_length=50)
    fir_number: Optional[str] = Field(default=None, max_length=100)

class MLCResponse(BaseModel):
    id: int
    mlc_number: str
    encounter_id: int
    patient_id: int
    registered_by_doctor_id: int
    cause_of_injury: MLCCauseEnum
    alleged_history: str
    identification_marks: str
    brought_by_name: str
    brought_by_relation: Optional[str] = None
    brought_by_phone: Optional[str] = None
    police_station_name: str
    police_constable_buckle_no: Optional[str] = None
    police_intimated_at: Optional[datetime] = None
    fir_number: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class PatientComorbidityCreate(BaseModel):
    comorbidity_id: int
    clinical_remarks: Optional[str] = Field(default=None, max_length=500)

class PatientComorbidityResponse(BaseModel):
    id: int
    patient_id: int
    comorbidity_id: int
    recorded_by_id: int
    clinical_remarks: Optional[str] = None
    is_active: bool
    comorbidity: ComorbidityResponse
    model_config = ConfigDict(from_attributes=True)

class EncounterPhysicalExamCreate(BaseModel):
    exam_master_id: int
    is_normal: bool = True
    findings_notes: Optional[str] = None

class EncounterPhysicalExamResponse(BaseModel):
    id: int
    encounter_id: int
    exam_master_id: int
    recorded_by_id: int
    is_normal: bool
    findings_notes: Optional[str] = None
    exam_master: PhysicalExamResponse
    model_config = ConfigDict(from_attributes=True)

class BirthRecordCreate(BaseModel):
    mother_encounter_id: int
    baby_full_name: Optional[str] = None
    baby_gender: GenderEnum
    baby_blood_group: Optional[BloodGroupEnum] = None
    time_of_birth: datetime
    delivery_mode: DeliveryModeEnum
    birth_weight_kg: float = Field(gt=0.0, le=10.0)
    apgar_1min: int = Field(ge=0, le=10)
    apgar_5min: int = Field(ge=0, le=10)
    is_stillborn: bool = False
    clinical_notes: Optional[str] = None

class BirthRecordResponse(BaseModel):
    id: int
    certificate_number: str
    mother_encounter_id: int
    mother_patient_id: int
    newborn_patient_id: Optional[int] = None
    newborn_mrn: Optional[str] = None
    attending_doctor_id: int
    baby_full_name: str
    baby_gender: GenderEnum
    baby_blood_group: Optional[BloodGroupEnum] = None
    time_of_birth: datetime
    delivery_mode: DeliveryModeEnum
    birth_weight_kg: float
    apgar_1min: int
    apgar_5min: int
    is_stillborn: bool
    clinical_notes: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class DeathRecordCreate(BaseModel):
    encounter_id: int
    immediate_cause_diagnosis_id: int
    time_of_death: datetime
    manner_of_death: MannerOfDeathEnum = MannerOfDeathEnum.NATURAL
    antecedent_cause_notes: Optional[str] = None
    is_autopsy_advised: bool = False
    body_handed_over_to: Optional[str] = None

class DeathRecordResponse(BaseModel):
    id: int
    certificate_number: str
    encounter_id: int
    patient_id: int
    certified_by_doctor_id: int
    immediate_cause_diagnosis_id: int
    time_of_death: datetime
    manner_of_death: MannerOfDeathEnum
    antecedent_cause_notes: Optional[str] = None
    is_autopsy_advised: bool
    body_handed_over_to: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)
