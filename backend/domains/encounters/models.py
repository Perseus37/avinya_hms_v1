from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.audit import AuditMixin
from backend.core.database import Base

from backend.domains.facility.models import Bed
from backend.domains.identity.models import Department, User
from backend.domains.patients.models import Patient


class EncounterTypeEnum(str, PyEnum):
    OPD = "OPD"
    IPD = "IPD"
    ER = "ER"


class EncounterStatusEnum(str, PyEnum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    DISCHARGED = "DISCHARGED"
    CANCELLED = "CANCELLED"


class DiagnosisTypeEnum(str, PyEnum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    PROVISIONAL = "PROVISIONAL"


class AdmissionTypeEnum(str, PyEnum):
    ROUTINE = "ROUTINE"
    EMERGENCY = "EMERGENCY"
    MATERNITY = "MATERNITY"
    TRANSFER_IN = "TRANSFER_IN"


class DischargeDispositionEnum(str, PyEnum):
    ROUTINE_DISCHARGE = "ROUTINE_DISCHARGE"
    REFERRED_OUT = "REFERRED_OUT"
    AGAINST_MEDICAL_ADVICE = "AGAINST_MEDICAL_ADVICE"
    DECEASED = "DECEASED"


class AppointmentStatusEnum(str, PyEnum):
    SCHEDULED = "SCHEDULED"
    ARRIVED = "ARRIVED"
    IN_CONSULTATION = "IN_CONSULTATION"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"


class PatientEncounter(AuditMixin, Base):
    __tablename__ = "patient_encounters"
    __table_args__ = (
        Index("ix_encounters_patient_status", "patient_id", "status"),
        Index("ix_patient_encounters_start_time", "start_time"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    encounter_type: Mapped[EncounterTypeEnum] = mapped_column(nullable=False)
    status: Mapped[EncounterStatusEnum] = mapped_column(default=EncounterStatusEnum.ACTIVE, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), nullable=False)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    primary_doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False, index=True)
    chief_complaint_notes: Mapped[Optional[str]] = mapped_column(Text)

    patient: Mapped["Patient"] = relationship()
    primary_doctor: Mapped["User"] = relationship()
    department: Mapped["Department"] = relationship()
    admissions: Mapped[List["InpatientAdmission"]] = relationship(back_populates="encounter")


class InpatientAdmission(AuditMixin, Base):
    __tablename__ = "inpatient_admissions"
    __table_args__ = (Index("ix_inpatient_admissions_discharge_time", "discharge_time"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False, unique=True)
    admission_type: Mapped[AdmissionTypeEnum] = mapped_column(nullable=False)
    current_bed_id: Mapped[Optional[int]] = mapped_column(ForeignKey("beds_master.id"))
    admitting_doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    discharge_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    discharge_disposition: Mapped[Optional[DischargeDispositionEnum]] = mapped_column(default=None)
    discharge_summary: Mapped[Optional[str]] = mapped_column(Text)

    encounter: Mapped[PatientEncounter] = relationship(back_populates="admissions")
    current_bed: Mapped[Optional["Bed"]] = relationship()
    admitting_doctor: Mapped["User"] = relationship()
    transfer_logs: Mapped[List["BedTransferLog"]] = relationship(back_populates="admission")


class BedTransferLog(AuditMixin, Base):
    __tablename__ = "bed_transfer_logs"
    __table_args__ = (Index("ix_bed_transfer_logs_transfer_time", "transfer_time"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    admission_id: Mapped[int] = mapped_column(ForeignKey("inpatient_admissions.id", ondelete="CASCADE"), nullable=False)
    from_bed_id: Mapped[Optional[int]] = mapped_column(ForeignKey("beds_master.id"))
    to_bed_id: Mapped[int] = mapped_column(ForeignKey("beds_master.id"), nullable=False)
    transfer_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), nullable=False)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    transfer_reason: Mapped[Optional[str]] = mapped_column(String(255))

    # FIN-002: Lock in historical bed rates at the exact time of transfer to prevent retroactive overcharging
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    admission: Mapped[InpatientAdmission] = relationship(back_populates="transfer_logs")
    from_bed: Mapped[Optional["Bed"]] = relationship(foreign_keys=[from_bed_id])
    to_bed: Mapped["Bed"] = relationship(foreign_keys=[to_bed_id])
    requested_by: Mapped["User"] = relationship()


class Appointment(AuditMixin, Base):
    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appointments_doctor_time", "doctor_id", "appointment_time"),
        Index("ix_appointments_patient_id", "patient_id"),
        Index("ix_appointments_appointment_time", "appointment_time"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    appointment_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[AppointmentStatusEnum] = mapped_column(default=AppointmentStatusEnum.SCHEDULED, nullable=False)
    token_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason_for_visit: Mapped[Optional[str]] = mapped_column(String(255))
    encounter_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patient_encounters.id"))

    patient: Mapped["Patient"] = relationship()
    doctor: Mapped["User"] = relationship()
    department: Mapped["Department"] = relationship()
    encounter: Mapped[Optional["PatientEncounter"]] = relationship()


class AppointmentStatusHistory(Base):
    __tablename__ = "appointment_status_history"
    __table_args__ = (
        Index(
            "ix_appointment_status_history_appointment_time",
            "appointment_id",
            "changed_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[Optional[str]] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    changed_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reason: Mapped[Optional[str]] = mapped_column(String(500))

    appointment: Mapped[Appointment] = relationship()
    changed_by: Mapped[Optional[User]] = relationship()


class ServiceDemandStatusEnum(str, PyEnum):
    OPEN = "OPEN"
    REFERRED_OUT = "REFERRED_OUT"
    SERVICE_AVAILABLE = "SERVICE_AVAILABLE"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class ServiceDemandReasonEnum(str, PyEnum):
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    NO_APPOINTMENT_CAPACITY = "NO_APPOINTMENT_CAPACITY"
    NO_SPECIALIST = "NO_SPECIALIST"
    EQUIPMENT_UNAVAILABLE = "EQUIPMENT_UNAVAILABLE"
    OTHER = "OTHER"


class ServiceDemandSourceEnum(str, PyEnum):
    OPD = "OPD"
    DIAGNOSTICS = "DIAGNOSTICS"
    REFERRAL = "REFERRAL"
    OTHER = "OTHER"


class ServiceDemandRequest(AuditMixin, Base):
    __tablename__ = "service_demand_requests"
    __table_args__ = (
        Index("ix_service_demand_created_status", "created_at", "status"),
        Index("ix_service_demand_department_created", "department_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    requested_service_name: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[ServiceDemandReasonEnum] = mapped_column(nullable=False)
    source: Mapped[ServiceDemandSourceEnum] = mapped_column(nullable=False)
    status: Mapped[ServiceDemandStatusEnum] = mapped_column(
        default=ServiceDemandStatusEnum.OPEN, nullable=False
    )
    patient_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("patients.id", ondelete="SET NULL")
    )
    department_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL")
    )
    service_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("service_catalog.id", ondelete="SET NULL")
    )
    requested_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    external_facility_name: Mapped[Optional[str]] = mapped_column(String(200))
    resolution_notes: Mapped[Optional[str]] = mapped_column(String(1000))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )


class ServiceDemandStatusHistory(Base):
    __tablename__ = "service_demand_status_history"
    __table_args__ = (
        Index(
            "ix_service_demand_history_request_time",
            "request_id",
            "changed_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("service_demand_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_status: Mapped[Optional[str]] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    changed_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    reason: Mapped[Optional[str]] = mapped_column(String(1000))


class AppointmentWaitlistStatusEnum(str, PyEnum):
    WAITING = "WAITING"
    BOOKED = "BOOKED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class AppointmentWaitlistEntry(AuditMixin, Base):
    __tablename__ = "appointment_waitlist_entries"
    __table_args__ = (
        Index(
            "ix_appointment_waitlist_doctor_window_status",
            "doctor_id",
            "requested_start_at",
            "status",
        ),
        Index(
            "ix_appointment_waitlist_patient_status",
            "patient_id",
            "status",
        ),
        CheckConstraint(
            "requested_end_at > requested_start_at",
            name="ck_appointment_waitlist_valid_window",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id"), nullable=False
    )
    requested_start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    requested_end_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reason_for_visit: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[AppointmentWaitlistStatusEnum] = mapped_column(
        default=AppointmentWaitlistStatusEnum.WAITING, nullable=False
    )
    appointment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL"), unique=True
    )


class AppointmentWaitlistHistory(Base):
    __tablename__ = "appointment_waitlist_history"
    __table_args__ = (
        Index(
            "ix_appointment_waitlist_history_entry_time",
            "waitlist_entry_id",
            "changed_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    waitlist_entry_id: Mapped[int] = mapped_column(
        ForeignKey("appointment_waitlist_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_status: Mapped[Optional[str]] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    changed_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    reason: Mapped[Optional[str]] = mapped_column(String(500))


class DoctorLeave(AuditMixin, Base):
    """Blocks OPD appointment scheduling when a consulting doctor is on leave or emergency OT duty."""
    __tablename__ = "doctor_leaves"
    __table_args__ = (
        Index("ix_doctor_leaves_doc_time", "doctor_id", "start_time", "end_time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    doctor: Mapped["User"] = relationship(foreign_keys=[doctor_id])
