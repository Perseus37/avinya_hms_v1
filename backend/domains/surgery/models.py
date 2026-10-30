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

from backend.domains.diagnostics.models import ServiceCatalog
from backend.domains.encounters.models import PatientEncounter
from backend.domains.identity.models import User
from backend.domains.patients.models import Patient


class OTTheatreTypeEnum(str, PyEnum):
    MODULAR_MAJOR = "MODULAR_MAJOR"
    MINOR = "MINOR"
    CARDIOTHORACIC = "CARDIOTHORACIC"
    ORTHO_TRAUMA = "ORTHO_TRAUMA"
    EMERGENCY_OT = "EMERGENCY_OT"


class SurgeryStatusEnum(str, PyEnum):
    SCHEDULED = "SCHEDULED"
    PRE_OP_CLEARED = "PRE_OP_CLEARED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class AnesthesiaTypeEnum(str, PyEnum):
    GENERAL = "GENERAL"
    SPINAL = "SPINAL"
    EPIDURAL = "EPIDURAL"
    REGIONAL_BLOCK = "REGIONAL_BLOCK"
    LOCAL_MAC = "LOCAL_MAC"


class OperationTheatre(AuditMixin, Base):
    """Master catalog of physical Operation Theatres (OT rooms) and their hourly facility rates."""
    __tablename__ = "operation_theatres"
    __table_args__ = (
        CheckConstraint("hourly_rate_paise >= 0", name="ck_ot_hourly_rate_non_neg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    theatre_type: Mapped[OTTheatreTypeEnum] = mapped_column(nullable=False)
    floor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("floors.id"), nullable=True, index=True)
    hourly_rate_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SurgicalSchedule(AuditMixin, Base):
    """Schedules a surgical case in an OperationTheatre with lead surgeon, anesthetist, and procedure link."""
    __tablename__ = "surgical_schedules"
    __table_args__ = (
        Index("ix_surgery_theatre_time", "theatre_id", "scheduled_start_time"),
        Index("ix_surgery_encounter_id", "encounter_id"),
        Index("ix_surgical_schedules_actual_start_time", "actual_start_time"),
        Index("ix_surgical_schedules_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id"), nullable=False)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    theatre_id: Mapped[int] = mapped_column(ForeignKey("operation_theatres.id"), nullable=False)
    procedure_service_id: Mapped[int] = mapped_column(ForeignKey("service_catalog.id"), nullable=False, index=True)

    lead_surgeon_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    anesthetist_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)

    scheduled_start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actual_start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[SurgeryStatusEnum] = mapped_column(default=SurgeryStatusEnum.SCHEDULED, nullable=False)
    pre_op_diagnosis_notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    patient: Mapped["Patient"] = relationship()
    theatre: Mapped["OperationTheatre"] = relationship()
    procedure_service: Mapped["ServiceCatalog"] = relationship()
    lead_surgeon: Mapped["User"] = relationship(foreign_keys=[lead_surgeon_id])
    anesthetist: Mapped[Optional["User"]] = relationship(foreign_keys=[anesthetist_id])
    safety_checklist: Mapped[Optional["WHOSurgicalSafetyChecklist"]] = relationship(
        back_populates="surgery", uselist=False, cascade="all, delete-orphan"
    )
    operative_record: Mapped[Optional["OperativeAnesthesiaRecord"]] = relationship(
        back_populates="surgery", uselist=False, cascade="all, delete-orphan"
    )
    implants: Mapped[List["SurgicalImplantLog"]] = relationship(
        back_populates="surgery", cascade="all, delete-orphan"
    )


class WHOSurgicalSafetyChecklist(AuditMixin, Base):
    """WHO 3-Phase Surgical Safety Checklist (Sign-In, Time-Out, Sign-Out) required for OT transitions."""
    __tablename__ = "who_surgical_safety_checklists"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    surgery_id: Mapped[int] = mapped_column(
        ForeignKey("surgical_schedules.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    verified_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    # Phase 1 & 2: Sign-In & Time-Out (Mandatory before IN_PROGRESS incision)
    patient_identity_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    site_marked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    consent_signed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pulse_oximeter_functioning: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allergy_checked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    antibiotic_prophylaxis_given: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Phase 3: Sign-Out (Mandatory before COMPLETED closure)
    sponge_instrument_count_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    specimen_labeled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    surgery: Mapped["SurgicalSchedule"] = relationship(back_populates="safety_checklist")
    verified_by: Mapped["User"] = relationship(foreign_keys=[verified_by_id])


class OperativeAnesthesiaRecord(AuditMixin, Base):
    """Intra-operative anesthesia & surgical findings record with ASA grading and blood loss tracking."""
    __tablename__ = "operative_anesthesia_records"
    __table_args__ = (
        CheckConstraint("asa_physical_status >= 1 AND asa_physical_status <= 6", name="ck_asa_grade_range"),
        CheckConstraint("estimated_blood_loss_ml >= 0", name="ck_blood_loss_non_neg"),
        Index("ix_operative_anesthesia_records_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    surgery_id: Mapped[int] = mapped_column(
        ForeignKey("surgical_schedules.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    anesthesia_type: Mapped[AnesthesiaTypeEnum] = mapped_column(nullable=False)
    asa_physical_status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # ASA Grade I to VI
    anesthesia_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    operative_findings: Mapped[str] = mapped_column(Text, nullable=False)
    post_op_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    estimated_blood_loss_ml: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    surgery: Mapped["SurgicalSchedule"] = relationship(back_populates="operative_record")


class SurgicalImplantLog(AuditMixin, Base):
    """Tracks prosthetics, stents, and implants used during surgery with serial/lot traceability and auto-billing."""
    __tablename__ = "surgical_implant_logs"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_implant_qty_pos"),
        CheckConstraint("unit_price_paise >= 0", name="ck_implant_price_non_neg"),
        Index("ix_surgical_implant_logs_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    surgery_id: Mapped[int] = mapped_column(
        ForeignKey("surgical_schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    implant_name: Mapped[str] = mapped_column(String(150), nullable=False)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    serial_or_batch_number: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)

    surgery: Mapped["SurgicalSchedule"] = relationship(back_populates="implants")
