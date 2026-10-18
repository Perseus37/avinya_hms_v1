"""Pydantic schemas owned by the Avinya HMS domain."""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from backend.domains.encounters.models import AdmissionTypeEnum, AppointmentStatusEnum, AppointmentWaitlistStatusEnum, DischargeDispositionEnum, EncounterStatusEnum, EncounterTypeEnum, ServiceDemandReasonEnum, ServiceDemandSourceEnum, ServiceDemandStatusEnum

class PatientEncounterCreate(BaseModel):
    patient_id: int
    encounter_type: EncounterTypeEnum
    primary_doctor_id: int
    department_id: int
    chief_complaint_notes: Optional[str] = None

class PatientEncounterResponse(BaseModel):
    id: int
    patient_id: int
    encounter_type: EncounterTypeEnum
    status: EncounterStatusEnum
    start_time: datetime
    end_time: Optional[datetime] = None
    primary_doctor_id: int
    department_id: int
    model_config = ConfigDict(from_attributes=True)

class InpatientAdmissionCreate(BaseModel):
    encounter_id: int
    admission_type: AdmissionTypeEnum
    admitting_doctor_id: int
    assigned_bed_id: int

class InpatientAdmissionResponse(BaseModel):
    id: int
    encounter_id: int
    admission_type: AdmissionTypeEnum
    current_bed_id: Optional[int]
    admitting_doctor_id: int
    discharge_time: Optional[datetime] = None
    discharge_disposition: Optional[DischargeDispositionEnum] = None
    model_config = ConfigDict(from_attributes=True)

class BedTransferRequest(BaseModel):
    admission_id: int
    new_bed_id: int
    transfer_reason: str

class BedTransferResponse(BaseModel):
    id: int
    admission_id: int
    from_bed_id: Optional[int]
    to_bed_id: int
    transfer_time: datetime
    transfer_reason: Optional[str]
    model_config = ConfigDict(from_attributes=True)

class ClinicalDischargeRequest(BaseModel):
    discharge_disposition: DischargeDispositionEnum
    discharge_summary: str
    force_financial_override: bool = False

class PatientEncounterUpdate(BaseModel):
    primary_doctor_id: Optional[int] = None
    department_id: Optional[int] = None
    chief_complaint_notes: Optional[str] = None

class InpatientAdmissionUpdate(BaseModel):
    admission_type: Optional[AdmissionTypeEnum] = None
    admitting_doctor_id: Optional[int] = None

class AppointmentCreate(BaseModel):
    patient_id: int
    doctor_id: int
    department_id: int
    appointment_time: datetime
    reason_for_visit: Optional[str] = None

class AppointmentUpdate(BaseModel):
    appointment_time: Optional[datetime] = None
    reason_for_visit: Optional[str] = None

class AppointmentStatusUpdate(BaseModel):
    status: AppointmentStatusEnum
    encounter_id: Optional[int] = None
    reason: Optional[str] = Field(default=None, max_length=500)

class AppointmentResponse(BaseModel):
    id: int
    patient_id: int
    doctor_id: int
    department_id: int
    appointment_time: datetime
    planned_duration_minutes: Optional[int] = None
    status: AppointmentStatusEnum
    token_number: Optional[int] = None
    reason_for_visit: Optional[str]
    encounter_id: Optional[int]
    model_config = ConfigDict(from_attributes=True)

class AppointmentStatusHistoryResponse(BaseModel):
    id: int
    appointment_id: int
    from_status: Optional[AppointmentStatusEnum] = None
    to_status: AppointmentStatusEnum
    changed_at: datetime
    changed_by_id: Optional[int] = None
    reason: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class AppointmentWaitlistCreate(BaseModel):
    patient_id: int
    doctor_id: int
    department_id: int
    requested_start_at: datetime
    requested_end_at: datetime
    reason_for_visit: Optional[str] = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_requested_window(self):
        start = self.requested_start_at
        end = self.requested_end_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if end <= start:
            raise ValueError("requested_end_at must be after requested_start_at.")
        if end - start > timedelta(days=366):
            raise ValueError("Appointment waitlist windows cannot exceed 366 days.")
        return self

class AppointmentWaitlistBook(BaseModel):
    appointment_time: datetime

class AppointmentWaitlistResponse(BaseModel):
    id: int
    patient_id: int
    doctor_id: int
    department_id: int
    requested_start_at: datetime
    requested_end_at: datetime
    reason_for_visit: Optional[str] = None
    status: AppointmentWaitlistStatusEnum
    appointment_id: Optional[int] = None
    created_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

class AppointmentWaitlistHistoryResponse(BaseModel):
    id: int
    waitlist_entry_id: int
    from_status: Optional[AppointmentWaitlistStatusEnum] = None
    to_status: AppointmentWaitlistStatusEnum
    changed_at: datetime
    changed_by_id: int
    reason: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class ServiceDemandCreate(BaseModel):
    requested_service_name: str = Field(min_length=2, max_length=200)
    reason: ServiceDemandReasonEnum
    source: ServiceDemandSourceEnum
    patient_id: Optional[int] = None
    department_id: Optional[int] = None
    service_id: Optional[int] = None
    resolution_notes: Optional[str] = Field(default=None, max_length=1000)

class ServiceDemandUpdate(BaseModel):
    status: ServiceDemandStatusEnum
    external_facility_name: Optional[str] = Field(default=None, max_length=200)
    resolution_notes: Optional[str] = Field(default=None, max_length=1000)
    reason: Optional[str] = Field(default=None, max_length=1000)

class ServiceDemandResponse(BaseModel):
    id: int
    requested_service_name: str
    reason: ServiceDemandReasonEnum
    source: ServiceDemandSourceEnum
    status: ServiceDemandStatusEnum
    patient_id: Optional[int] = None
    department_id: Optional[int] = None
    service_id: Optional[int] = None
    requested_by_id: int
    external_facility_name: Optional[str] = None
    resolution_notes: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

class ServiceDemandStatusHistoryResponse(BaseModel):
    id: int
    request_id: int
    from_status: Optional[ServiceDemandStatusEnum] = None
    to_status: ServiceDemandStatusEnum
    changed_at: datetime
    changed_by_id: int
    reason: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class DoctorLeaveCreate(BaseModel):
    doctor_id: int
    start_time: datetime
    end_time: datetime
    reason: str = Field(min_length=3, max_length=255)

class DoctorLeaveResponse(BaseModel):
    id: int
    doctor_id: int
    start_time: datetime
    end_time: datetime
    reason: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)
