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

from backend.domains.diagnostics.models import DiagnosticTask
from backend.domains.encounters.models import PatientEncounter
from backend.domains.identity.models import BloodGroupEnum, User
from backend.domains.patients.models import Patient


class BloodComponentTypeEnum(str, PyEnum):
    WHOLE_BLOOD = "WHOLE_BLOOD"
    PRBC = "PRBC"
    FFP = "FFP"
    PLATELETS = "PLATELETS"
    CRYOPRECIPITATE = "CRYOPRECIPITATE"


class BloodBagStatusEnum(str, PyEnum):
    AVAILABLE = "AVAILABLE"
    CROSS_MATCHED = "CROSS_MATCHED"
    ISSUED = "ISSUED"
    REACTION_HALTED = "REACTION_HALTED"
    DISCARDED = "DISCARDED"


class TransfusionReactionTypeEnum(str, PyEnum):
    NONE = "NONE"
    FEBRILE_NON_HEMOLYTIC = "FEBRILE_NON_HEMOLYTIC"
    ALLERGIC_URTICARIA = "ALLERGIC_URTICARIA"
    ACUTE_HEMOLYTIC = "ACUTE_HEMOLYTIC"
    ANAPHYLACTIC = "ANAPHYLACTIC"
    TRALI = "TRALI"


class DicomModalityEnum(str, PyEnum):
    CR = "CR"
    DX = "DX"
    CT = "CT"
    MR = "MR"
    US = "US"
    MG = "MG"
    PT = "PT"


class BloodBagInventory(AuditMixin, Base):
    """Tracks physical blood component bags in the hospital blood bank."""
    __tablename__ = "blood_bag_inventory"
    __table_args__ = (
        CheckConstraint("volume_ml > 0", name="ck_blood_bag_vol_pos"),
        CheckConstraint("processing_fee_paise >= 0", name="ck_blood_bag_fee_non_neg"),
        Index("ix_blood_bags_group_status", "blood_group", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bag_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    component_type: Mapped[BloodComponentTypeEnum] = mapped_column(nullable=False)
    blood_group: Mapped[BloodGroupEnum] = mapped_column(nullable=False)
    volume_ml: Mapped[int] = mapped_column(Integer, nullable=False)
    collection_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expiry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processing_fee_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[BloodBagStatusEnum] = mapped_column(default=BloodBagStatusEnum.AVAILABLE, nullable=False)


class BloodCrossMatchOrder(AuditMixin, Base):
    """Links an active PatientEncounter to a compatible BloodBagInventory unit with transfusion & reaction logs."""
    __tablename__ = "blood_cross_match_orders"
    __table_args__ = (
        Index("ix_cross_match_encounter_id", "encounter_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id"), nullable=False)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    blood_bag_id: Mapped[int] = mapped_column(ForeignKey("blood_bag_inventory.id"), nullable=False)
    ordered_by_doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    cross_matched_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    reaction_type: Mapped[TransfusionReactionTypeEnum] = mapped_column(
        default=TransfusionReactionTypeEnum.NONE, nullable=False
    )
    reaction_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    patient: Mapped["Patient"] = relationship()
    blood_bag: Mapped["BloodBagInventory"] = relationship()
    ordered_by_doctor: Mapped["User"] = relationship(foreign_keys=[ordered_by_doctor_id])
    cross_matched_by: Mapped["User"] = relationship(foreign_keys=[cross_matched_by_id])
    issued_by: Mapped[Optional["User"]] = relationship(foreign_keys=[issued_by_id])


class RadiologyStudyMetadata(AuditMixin, Base):
    """Stores PACS DICOM Study UID, modality, radiation dose, and viewer URL for RADIOLOGY diagnostic tasks."""
    __tablename__ = "radiology_study_metadata"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    diagnostic_task_id: Mapped[int] = mapped_column(
        ForeignKey("diagnostic_tasks.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    study_instance_uid: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    accession_number: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    modality: Mapped[DicomModalityEnum] = mapped_column(nullable=False)
    pacs_viewer_url: Mapped[str] = mapped_column(String(500), nullable=False)
    radiation_dose_mgy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    radiologist_impression: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    diagnostic_task: Mapped["DiagnosticTask"] = relationship()
