from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict

from backend.domains.quality.models import (
    ClinicalIncidentSeverityEnum,
    ClinicalIncidentStatusEnum,
    ClinicalIncidentTypeEnum,
)


class ClinicalIncidentCreate(BaseModel):
    incident_type: ClinicalIncidentTypeEnum
    severity: ClinicalIncidentSeverityEnum
    patient_id: Optional[int] = None
    encounter_id: Optional[int] = None
    ward_id: Optional[int] = None
    device_days: Optional[int] = None
    description: str
    root_cause_analysis: Optional[str] = None
    capa_plan: Optional[str] = None


class ClinicalIncidentUpdate(BaseModel):
    severity: Optional[ClinicalIncidentSeverityEnum] = None
    status: Optional[ClinicalIncidentStatusEnum] = None
    root_cause_analysis: Optional[str] = None
    capa_plan: Optional[str] = None
    device_days: Optional[int] = None


class ClinicalIncidentResponse(BaseModel):
    id: int
    incident_code: str
    incident_type: ClinicalIncidentTypeEnum
    severity: ClinicalIncidentSeverityEnum
    patient_id: Optional[int] = None
    encounter_id: Optional[int] = None
    ward_id: Optional[int] = None
    device_days: Optional[int] = None
    description: str
    root_cause_analysis: Optional[str] = None
    capa_plan: Optional[str] = None
    status: ClinicalIncidentStatusEnum
    reported_at: datetime
    resolved_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class QualityBenchmarkCreate(BaseModel):
    indicator_code: str
    name: str
    standard: str = "NABH_5TH_ED"
    unit: str
    target_threshold: float
    warning_threshold: float
    description: Optional[str] = None
    is_active: bool = True


class QualityBenchmarkResponse(QualityBenchmarkCreate):
    id: int
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class QualityIndicatorMetric(BaseModel):
    code: str
    name: str
    numerator_cases: int
    denominator_events: int
    rate: float
    unit: str
    target_threshold: float
    warning_threshold: float
    compliance_status: str


class QualityIndicatorsSummaryResponse(BaseModel):
    start_date: datetime
    end_date: datetime
    hospital_inpatient_bed_days: int
    total_surgeries_performed: int
    total_medication_doses_logged: int
    total_incidents_recorded: int
    indicators: List[QualityIndicatorMetric]
