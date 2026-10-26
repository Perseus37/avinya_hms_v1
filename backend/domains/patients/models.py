from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.audit import AuditMixin
from backend.core.database import Base
from backend.domains.identity.models import BloodGroupEnum, GenderEnum, User


class AllergyCategoryEnum(str, PyEnum):
    DRUG = "DRUG"
    FOOD = "FOOD"
    ENVIRONMENTAL = "ENVIRONMENTAL"
    OTHER = "OTHER"


class AllergySeverityEnum(str, PyEnum):
    MILD = "MILD"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"
    ANAPHYLACTIC = "ANAPHYLACTIC"


SeverityEnum = AllergySeverityEnum


class BodySystemEnum(str, PyEnum):
    CARDIOVASCULAR = "CARDIOVASCULAR"
    RESPIRATORY = "RESPIRATORY"
    GASTROINTESTINAL = "GASTROINTESTINAL"
    NEUROLOGICAL = "NEUROLOGICAL"
    MUSCULOSKELETAL = "MUSCULOSKELETAL"
    DERMATOLOGICAL = "DERMATOLOGICAL"


class ChiefComplaintMaster(AuditMixin, Base):
    __tablename__ = "chief_complaints_master"
    __table_args__ = (
        UniqueConstraint("department_id", "code", name="uq_complaint_dept_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class ComorbidityMaster(AuditMixin, Base):
    __tablename__ = "comorbidities_master"
    __table_args__ = (
        UniqueConstraint("department_id", "code", name="uq_comorbidity_dept_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    icd10_code: Mapped[Optional[str]] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(default=True)


class AllergyMaster(AuditMixin, Base):
    __tablename__ = "allergies_master"
    __table_args__ = (
        UniqueConstraint("department_id", "code", name="uq_allergy_dept_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[AllergyCategoryEnum] = mapped_column(
        default=AllergyCategoryEnum.DRUG, nullable=False
    )
    default_severity: Mapped[AllergySeverityEnum] = mapped_column(
        default=AllergySeverityEnum.MODERATE, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(default=True)


class DiagnosisMaster(AuditMixin, Base):
    __tablename__ = "diagnoses_master"
    __table_args__ = (
        UniqueConstraint("department_id", "icd10_code", name="uq_diagnosis_dept_icd10"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    icd10_code: Mapped[str] = mapped_column(String(20), nullable=False)
    short_description: Mapped[str] = mapped_column(String(100), nullable=False)
    full_description: Mapped[str] = mapped_column(Text, nullable=False)
    is_billable: Mapped[bool] = mapped_column(default=True)
    parent_diagnosis_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("diagnoses_master.id")
    )
    is_active: Mapped[bool] = mapped_column(default=True)


class PhysicalExaminationMaster(AuditMixin, Base):
    __tablename__ = "physical_examinations_master"
    __table_args__ = (
        UniqueConstraint("department_id", "code", name="uq_exam_dept_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    body_system: Mapped[BodySystemEnum] = mapped_column(nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class Patient(AuditMixin, Base):
    __tablename__ = "patients"
    __table_args__ = (Index("ix_patients_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mrn: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_of_birth: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    gender: Mapped[GenderEnum] = mapped_column(nullable=False)
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), index=True, nullable=True)
    abha_number: Mapped[Optional[str]] = mapped_column(
        String(50), unique=True, index=True, nullable=True
    )
    abha_address: Mapped[Optional[str]] = mapped_column(
        String(100), unique=True, index=True, nullable=True
    )
    email: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    blood_group: Mapped[Optional[BloodGroupEnum]] = mapped_column(nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    pincode: Mapped[Optional[str]] = mapped_column(String(6), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    district: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    village: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    emergency_contact_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    emergency_contact_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    allow_network_sharing: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )


class BreakGlassGrant(Base):
    __tablename__ = "break_glass_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    review_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    review_outcome: Mapped[Optional[str]] = mapped_column(String(20))
    review_notes: Mapped[Optional[str]] = mapped_column(Text)


class BreakGlassUse(Base):
    __tablename__ = "break_glass_uses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    grant_id: Mapped[int] = mapped_column(
        ForeignKey("break_glass_grants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PatientAllergy(AuditMixin, Base):
    __tablename__ = "patient_allergies"
    __table_args__ = (
        UniqueConstraint("patient_id", "allergy_id", name="uq_patient_allergy"),
        Index("ix_patient_allergies_patient_id", "patient_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    allergy_id: Mapped[int] = mapped_column(
        ForeignKey("allergies_master.id", ondelete="CASCADE"), nullable=False, index=True
    )
    severity: Mapped[AllergySeverityEnum] = mapped_column(nullable=False)
    reaction_description: Mapped[Optional[str]] = mapped_column(Text)
    recorded_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(default=True)

    patient: Mapped["Patient"] = relationship()
    allergy: Mapped["AllergyMaster"] = relationship()
    recorded_by: Mapped["User"] = relationship()

    @property
    def reaction(self) -> Optional[str]:
        return self.reaction_description

    @reaction.setter
    def reaction(self, value: Optional[str]) -> None:
        self.reaction_description = value
