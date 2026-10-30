from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.audit import AuditMixin
from backend.core.database import Base

from backend.domains.encounters.models import DiagnosisTypeEnum, PatientEncounter
from backend.domains.identity.models import BloodGroupEnum, GenderEnum, User
from backend.domains.patients.models import ComorbidityMaster, DiagnosisMaster, Patient, PhysicalExaminationMaster


class FluidDirectionEnum(str, PyEnum):
    INTAKE = "INTAKE"
    OUTPUT = "OUTPUT"


class TriageAcuityEnum(str, PyEnum):
    RED_RESUSCITATION = "RED_RESUSCITATION"
    ORANGE_EMERGENT = "ORANGE_EMERGENT"
    YELLOW_URGENT = "YELLOW_URGENT"
    GREEN_LESS_URGENT = "GREEN_LESS_URGENT"
    BLACK_DECEASED = "BLACK_DECEASED"


class MLCCauseEnum(str, PyEnum):
    ROAD_TRAFFIC_ACCIDENT = "ROAD_TRAFFIC_ACCIDENT"
    ASSAULT = "ASSAULT"
    POISONING = "POISONING"
    BURNS = "BURNS"
    INDUSTRIAL_ACCIDENT = "INDUSTRIAL_ACCIDENT"
    BROUGHT_DEAD = "BROUGHT_DEAD"
    OTHER = "OTHER"


class DeliveryModeEnum(str, PyEnum):
    NORMAL_VAGINAL = "NORMAL_VAGINAL"
    LSCS_CESAREAN = "LSCS_CESAREAN"
    ASSISTED_FORCEPS_VACUUM = "ASSISTED_FORCEPS_VACUUM"


class MannerOfDeathEnum(str, PyEnum):
    NATURAL = "NATURAL"
    ACCIDENTAL = "ACCIDENTAL"
    SUICIDAL = "SUICIDAL"
    HOMICIDAL = "HOMICIDAL"
    PENDING_INVESTIGATION = "PENDING_INVESTIGATION"


class EncounterVital(AuditMixin, Base):
    """Time-series nursing and clinical vitals recorded during an active PatientEncounter."""
    __tablename__ = "encounter_vitals"
    __table_args__ = (Index("ix_encounter_vitals_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(
        ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False, index=True
    )

    temperature_f: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pulse_bpm: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bp_systolic: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bp_diastolic: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    respiratory_rate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    spo2_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    weight_kg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    height_cm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bmi: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()


class ClinicalNote(AuditMixin, Base):
    """Structured SOAP clinical consultation note with medico-legal sign-off lock."""
    __tablename__ = "clinical_notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(
        ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False, index=True
    )

    subjective_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    objective_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assessment_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    plan_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_signed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    signed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    signed_by: Mapped[Optional["User"]] = relationship(foreign_keys=[signed_by_id])


class EncounterDiagnosis(AuditMixin, Base):
    """Links an active PatientEncounter to ICD-10 DiagnosisMaster entries."""
    __tablename__ = "encounter_diagnoses"
    __table_args__ = (Index("ix_encounter_diagnoses_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(
        ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    diagnosis_id: Mapped[int] = mapped_column(
        ForeignKey("diagnoses_master.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    diagnosis_type: Mapped[DiagnosisTypeEnum] = mapped_column(
        default=DiagnosisTypeEnum.PRIMARY, nullable=False
    )
    clinical_remarks: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    diagnosis: Mapped["DiagnosisMaster"] = relationship()


class FluidBalanceLog(AuditMixin, Base):
    """Tracks inpatient fluid intake (IV, Oral, Blood) and output (Urine, Drain, Emesis) in mL."""
    __tablename__ = "fluid_balance_logs"
    __table_args__ = (
        CheckConstraint("volume_ml > 0", name="ck_fluid_volume_pos"),
        Index("ix_fluid_logs_encounter_id", "encounter_id"),
        Index("ix_fluid_balance_logs_recorded_at", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False)
    recorded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    direction: Mapped[FluidDirectionEnum] = mapped_column(nullable=False)
    fluid_type: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "Normal Saline 0.9%", "Urine (Foley)"
    volume_ml: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    recorded_by: Mapped["User"] = relationship(foreign_keys=[recorded_by_id])


class ERTriageAssessment(AuditMixin, Base):
    """5-Level emergency acuity triage assessment recorded upon ER arrival."""
    __tablename__ = "er_triage_assessments"
    __table_args__ = (
        CheckConstraint("gcs_score IS NULL OR (gcs_score >= 3 AND gcs_score <= 15)", name="ck_triage_gcs_range"),
        CheckConstraint("pain_scale IS NULL OR (pain_scale >= 0 AND pain_scale <= 10)", name="ck_triage_pain_range"),
        Index("ix_er_triage_assessments_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(
        ForeignKey("patient_encounters.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    triaged_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    acuity_level: Mapped[TriageAcuityEnum] = mapped_column(nullable=False)
    chief_complaint: Mapped[str] = mapped_column(String(255), nullable=False)
    gcs_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Glasgow Coma Scale (3-15)
    pain_scale: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 0-10
    airway_status: Mapped[Optional[str]] = mapped_column(String(50), default="CLEAR")
    breathing_status: Mapped[Optional[str]] = mapped_column(String(50), default="NORMAL")
    circulation_status: Mapped[Optional[str]] = mapped_column(String(50), default="STABLE")
    is_mlc_suspected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    triage_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    triaged_by: Mapped["User"] = relationship(foreign_keys=[triaged_by_id])


class MedicoLegalCase(AuditMixin, Base):
    """Official Medico-Legal Case (MLC) register for police intimation and forensic chain-of-custody."""
    __tablename__ = "medico_legal_cases"
    __table_args__ = (Index("ix_medico_legal_cases_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mlc_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    encounter_id: Mapped[int] = mapped_column(
        ForeignKey("patient_encounters.id", ondelete="RESTRICT"), unique=True, nullable=False, index=True
    )
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    registered_by_doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    cause_of_injury: Mapped[MLCCauseEnum] = mapped_column(nullable=False)
    alleged_history: Mapped[str] = mapped_column(Text, nullable=False)
    identification_marks: Mapped[str] = mapped_column(String(255), nullable=False)
    brought_by_name: Mapped[str] = mapped_column(String(100), nullable=False)
    brought_by_relation: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    brought_by_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    police_station_name: Mapped[str] = mapped_column(String(150), nullable=False)
    police_constable_buckle_no: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    police_intimated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    fir_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    patient: Mapped["Patient"] = relationship()
    registered_by_doctor: Mapped["User"] = relationship(foreign_keys=[registered_by_doctor_id])


class PatientComorbidity(AuditMixin, Base):
    """Links a Patient to chronic conditions from ComorbidityMaster across episodes of care."""
    __tablename__ = "patient_comorbidities"
    __table_args__ = (
        UniqueConstraint("patient_id", "comorbidity_id", name="uq_patient_comorbidity"),
        Index("ix_patient_comorbidities_patient_id", "patient_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    comorbidity_id: Mapped[int] = mapped_column(ForeignKey("comorbidities_master.id", ondelete="RESTRICT"), nullable=False, index=True)
    recorded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    clinical_remarks: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    patient: Mapped["Patient"] = relationship()
    comorbidity: Mapped["ComorbidityMaster"] = relationship()
    recorded_by: Mapped["User"] = relationship(foreign_keys=[recorded_by_id])


class EncounterPhysicalExam(AuditMixin, Base):
    """Links an active PatientEncounter to systemic PhysicalExaminationMaster findings."""
    __tablename__ = "encounter_physical_exams"
    __table_args__ = (
        Index("ix_encounter_phys_exams_enc_id", "encounter_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False)
    exam_master_id: Mapped[int] = mapped_column(ForeignKey("physical_examinations_master.id", ondelete="RESTRICT"), nullable=False, index=True)
    recorded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    is_normal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    findings_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    exam_master: Mapped["PhysicalExaminationMaster"] = relationship()
    recorded_by: Mapped["User"] = relationship(foreign_keys=[recorded_by_id])


class BirthRecord(AuditMixin, Base):
    """Statutory Maternity Birth Register that auto-registers live newborns into the Master Patient Index (MPI)."""
    __tablename__ = "birth_records"
    __table_args__ = (
        CheckConstraint("birth_weight_kg > 0.0 AND birth_weight_kg <= 10.0", name="ck_birth_weight_range"),
        CheckConstraint("apgar_1min >= 0 AND apgar_1min <= 10", name="ck_apgar_1_range"),
        CheckConstraint("apgar_5min >= 0 AND apgar_5min <= 10", name="ck_apgar_5_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    certificate_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    mother_encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id"), nullable=False, index=True)
    mother_patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    newborn_patient_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patients.id"), nullable=True, index=True)
    attending_doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    baby_full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    baby_gender: Mapped[GenderEnum] = mapped_column(nullable=False)
    baby_blood_group: Mapped[Optional[BloodGroupEnum]] = mapped_column(nullable=True)
    time_of_birth: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivery_mode: Mapped[DeliveryModeEnum] = mapped_column(nullable=False)
    birth_weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    apgar_1min: Mapped[int] = mapped_column(Integer, nullable=False)
    apgar_5min: Mapped[int] = mapped_column(Integer, nullable=False)
    is_stillborn: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    clinical_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    mother_encounter: Mapped["PatientEncounter"] = relationship()
    mother_patient: Mapped["Patient"] = relationship(foreign_keys=[mother_patient_id])
    newborn_patient: Mapped[Optional["Patient"]] = relationship(foreign_keys=[newborn_patient_id])
    attending_doctor: Mapped["User"] = relationship(foreign_keys=[attending_doctor_id])


class DeathRecord(AuditMixin, Base):
    """Statutory Form 4 Medical Certificate of Cause of Death (MCCD) & Mortality Register."""
    __tablename__ = "death_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    certificate_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id"), unique=True, nullable=False, index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), unique=True, nullable=False, index=True)
    certified_by_doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    immediate_cause_diagnosis_id: Mapped[int] = mapped_column(ForeignKey("diagnoses_master.id"), nullable=False, index=True)

    time_of_death: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    manner_of_death: Mapped[MannerOfDeathEnum] = mapped_column(default=MannerOfDeathEnum.NATURAL, nullable=False)
    antecedent_cause_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_autopsy_advised: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    body_handed_over_to: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    patient: Mapped["Patient"] = relationship()
    certified_by_doctor: Mapped["User"] = relationship(foreign_keys=[certified_by_doctor_id])
    immediate_cause_diagnosis: Mapped["DiagnosisMaster"] = relationship()


class OrderSetItemTypeEnum(str, PyEnum):
    MEDICATION = "MEDICATION"
    DIAGNOSTIC_LAB = "DIAGNOSTIC_LAB"
    RADIOLOGY = "RADIOLOGY"
    NURSING_CARE = "NURSING_CARE"
    DIETARY = "DIETARY"


class OrderSetPriorityEnum(str, PyEnum):
    ROUTINE = "ROUTINE"
    URGENT = "URGENT"
    STAT = "STAT"


class EarlyWarningRiskEnum(str, PyEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class OrderSetMaster(AuditMixin, Base):
    """Standardized CPOE clinical protocol / order set template (e.g. Sepsis Bundle, STEMI Pathway)."""
    __tablename__ = "order_set_masters"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    specialty: Mapped[str] = mapped_column(String(100), nullable=False)
    clinical_indication: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    items: Mapped[List["OrderSetItem"]] = relationship(
        back_populates="order_set", cascade="all, delete-orphan"
    )


class OrderSetItem(AuditMixin, Base):
    """Individual line item within an OrderSetMaster defining medication, lab test, or procedure details."""
    __tablename__ = "order_set_items"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_set_id: Mapped[int] = mapped_column(
        ForeignKey("order_set_masters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_type: Mapped[OrderSetItemTypeEnum] = mapped_column(nullable=False)

    medication_id: Mapped[Optional[int]] = mapped_column(ForeignKey("medication_master.id"), nullable=True, index=True)
    service_id: Mapped[Optional[int]] = mapped_column(ForeignKey("service_catalog.id"), nullable=True, index=True)

    dosage_instruction: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    priority: Mapped[OrderSetPriorityEnum] = mapped_column(default=OrderSetPriorityEnum.ROUTINE, nullable=False)
    clinical_instructions: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    order_set: Mapped[OrderSetMaster] = relationship(back_populates="items")


class ClinicalEarlyWarningScore(AuditMixin, Base):
    """Calculated NEWS2 and qSOFA score based on clinical vitals with Rapid Response Team (RRT) alert status."""
    __tablename__ = "clinical_early_warning_scores"
    __table_args__ = (
        Index("ix_news2_encounter_created", "encounter_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(
        ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vital_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("encounter_vitals.id", ondelete="SET NULL"), nullable=True,
        index=True,
    )
    evaluated_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    news2_score: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_level: Mapped[EarlyWarningRiskEnum] = mapped_column(nullable=False)
    qsofa_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_rrt_alert_triggered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    clinical_recommendation: Mapped[str] = mapped_column(String(500), nullable=False)

    encounter: Mapped["PatientEncounter"] = relationship()
    vital: Mapped[Optional["EncounterVital"]] = relationship()
    evaluated_by: Mapped["User"] = relationship(foreign_keys=[evaluated_by_id])

