from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.audit import AuditMixin
from backend.core.database import Base


class ClinicalIncidentTypeEnum(str, PyEnum):
    CAUTI = "CAUTI"
    CLABSI = "CLABSI"
    VAP = "VAP"
    SSI = "SSI"
    PATIENT_FALL = "PATIENT_FALL"
    BEDSORE_HAPU = "BEDSORE_HAPU"
    MEDICATION_ERROR = "MEDICATION_ERROR"
    ADVERSE_DRUG_REACTION = "ADVERSE_DRUG_REACTION"
    UNPLANNED_ICU_READMISSION = "UNPLANNED_ICU_READMISSION"
    UNPLANNED_OT_RETURN = "UNPLANNED_OT_RETURN"
    SENTINEL_EVENT = "SENTINEL_EVENT"


class ClinicalIncidentSeverityEnum(str, PyEnum):
    NEAR_MISS = "NEAR_MISS"
    MINOR = "MINOR"
    MODERATE = "MODERATE"
    MAJOR = "MAJOR"
    SENTINEL = "SENTINEL"


class ClinicalIncidentStatusEnum(str, PyEnum):
    REPORTED = "REPORTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    CAPA_ASSIGNED = "CAPA_ASSIGNED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class ClinicalQualityIncident(AuditMixin, Base):
    """
    Patient safety, infection control, and clinical quality adverse event register
    for NABH 5th Edition and JCI 7th Edition accreditation compliance.
    """

    __tablename__ = "clinical_quality_incidents"
    __table_args__ = (
        Index("ix_clinical_quality_incidents_type", "incident_type"),
        Index("ix_clinical_quality_incidents_severity", "severity"),
        Index("ix_clinical_quality_incidents_status", "status"),
        Index("ix_clinical_quality_incidents_reported_at", "reported_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    incident_code: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    incident_type: Mapped[ClinicalIncidentTypeEnum] = mapped_column(
        Enum(ClinicalIncidentTypeEnum), nullable=False
    )
    severity: Mapped[ClinicalIncidentSeverityEnum] = mapped_column(
        Enum(ClinicalIncidentSeverityEnum), nullable=False
    )
    patient_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("patients.id", ondelete="SET NULL"), nullable=True, index=True
    )
    encounter_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("patient_encounters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ward_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("wards.id", ondelete="SET NULL"), nullable=True,
        index=True,
    )
    device_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause_analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    capa_plan: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[ClinicalIncidentStatusEnum] = mapped_column(
        Enum(ClinicalIncidentStatusEnum),
        default=ClinicalIncidentStatusEnum.REPORTED,
        nullable=False,
    )
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    patient = relationship("Patient")
    encounter = relationship("PatientEncounter")
    ward = relationship("Ward")


class QualityIndicatorBenchmark(AuditMixin, Base):
    """
    Hospital clinical quality benchmark targets conforming to NABH / JCI standards.
    """

    __tablename__ = "quality_indicator_benchmarks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    indicator_code: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    standard: Mapped[str] = mapped_column(String(50), default="NABH_5TH_ED", nullable=False)
    unit: Mapped[str] = mapped_column(String(50), nullable=False)
    target_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    warning_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
