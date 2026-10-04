"""Core SQLAlchemy models for the Avinya HMS domain.

The model layer is grouped by functional responsibility so the healthcare data
model reads like an application domain model instead of a flat list of tables.
"""

from datetime import datetime, timezone
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
    event,
    func,
)
from sqlalchemy.orm import Mapped, Session as SQLAlchemySession, mapped_column, relationship
from database import Base



# -----------------------------------------------------------------------------
# Shared enums
# -----------------------------------------------------------------------------
class GenderEnum(str, PyEnum):
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"


class BloodGroupEnum(str, PyEnum):
    A_POS = "A+"
    A_NEG = "A-"
    B_POS = "B+"
    B_NEG = "B-"
    O_POS = "O+"
    O_NEG = "O-"
    AB_POS = "AB+"
    AB_NEG = "AB-"


class UserStatusEnum(str, PyEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    LOCKED = "LOCKED"
    DEACTIVATED = "DEACTIVATED"


class WardTypeEnum(str, PyEnum):
    GENERAL = "GENERAL"
    PRIVATE = "PRIVATE"
    ICU = "ICU"
    NICU = "NICU"
    EMERGENCY = "EMERGENCY"


class RoomTypeEnum(str, PyEnum):
    SINGLE = "SINGLE"
    DOUBLE = "DOUBLE"
    DELUXE = "DELUXE"
    DORMITORY = "DORMITORY"


class BedTypeEnum(str, PyEnum):
    STANDARD = "STANDARD"
    ICU = "ICU"
    PEDIATRIC = "PEDIATRIC"
    MOTORIZED = "MOTORIZED"


class BedStatusEnum(str, PyEnum):
    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    HOUSEKEEPING = "HOUSEKEEPING"
    MAINTENANCE = "MAINTENANCE"


class ShiftTypeEnum(str, PyEnum):
    MORNING = "MORNING"
    EVENING = "EVENING"
    NIGHT = "NIGHT"


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


# --- PHASE 4 ENUMS ---
class ServiceTypeEnum(str, PyEnum):
    LAB = "LAB"
    RADIOLOGY = "RADIOLOGY"
    PROCEDURE = "PROCEDURE"
    PACKAGE = "PACKAGE"
    CONSULTATION = "CONSULTATION"


class OrderStatusEnum(str, PyEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PARTIAL = "PARTIAL"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TaskStatusEnum(str, PyEnum):
    PENDING = "PENDING"
    SAMPLE_COLLECTED = "SAMPLE_COLLECTED"
    SAMPLE_REJECTED = "SAMPLE_REJECTED"
    IN_PROGRESS = "IN_PROGRESS"
    RESULT_ENTERED = "RESULT_ENTERED"
    VERIFIED = "VERIFIED"
    CANCELLED = "CANCELLED"


# --- PHASE 5 ENUMS ---
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


# --- PHASE 6 ENUMS ---
class InvoiceStatusEnum(str, PyEnum):
    DRAFT = "DRAFT"
    UNPAID = "UNPAID"
    FINALIZED = "UNPAID"  # Alias to UNPAID to match PostgreSQL invoicestatusenum
    PARTIAL = "PARTIAL"
    PAID = "PAID"
    VOID = "VOID"

# --- PHASE 7 ENUMS ---
class AppointmentStatusEnum(str, PyEnum):
    SCHEDULED = "SCHEDULED"
    ARRIVED = "ARRIVED"
    IN_CONSULTATION = "IN_CONSULTATION"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"


# --- PHASE 8 ENUMS ---
class MedicationFormEnum(str, PyEnum):
    TABLET = "TABLET"
    CAPSULE = "CAPSULE"
    SYRUP = "SYRUP"
    INJECTION = "INJECTION"
    OINTMENT = "OINTMENT"
    DROPS = "DROPS"


class PrescriptionStatusEnum(str, PyEnum):
    ACTIVE = "ACTIVE"
    PARTIAL = "PARTIAL"
    DISPENSED = "DISPENSED"
    CANCELLED = "CANCELLED"

# --- PHASE 9 ENUMS ---
class InsuranceProviderTypeEnum(str, PyEnum):
    TPA = "TPA"
    INSURER = "INSURER"
    GOVERNMENT_SCHEME = "GOVERNMENT_SCHEME"
    CORPORATE = "CORPORATE"


class PreAuthStatusEnum(str, PyEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    QUERY_RAISED = "QUERY_RAISED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ClaimStatusEnum(str, PyEnum):
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    SETTLED = "SETTLED"
    REJECTED = "REJECTED"

class PaymentMethodEnum(str, PyEnum):
    CASH = "CASH"
    CREDIT_CARD = "CREDIT_CARD"
    INSURANCE = "INSURANCE"
    UPI = "UPI"
    BANK_TRANSFER = "BANK_TRANSFER"
    ADVANCE_ADJUSTMENT = "ADVANCE_ADJUSTMENT"


class DepositStatusEnum(str, PyEnum):
    ACTIVE = "ACTIVE"
    EXHAUSTED = "EXHAUSTED"
    REFUNDED = "REFUNDED"

# --- PHASE 10 ENUMS ---
class ReportTypeEnum(str, PyEnum):
    OPD_APPOINTMENT_SLIP = "OPD_APPOINTMENT_SLIP"
    INVOICE = "INVOICE"
    PRESCRIPTION = "PRESCRIPTION"
    DIAGNOSTIC_REPORT = "DIAGNOSTIC_REPORT"
    DISCHARGE_SUMMARY = "DISCHARGE_SUMMARY"
    MLC_POLICE_REPORT = "MLC_POLICE_REPORT"
    SURGICAL_OPERATIVE_NOTE = "SURGICAL_OPERATIVE_NOTE"
    BIRTH_CERTIFICATE = "BIRTH_CERTIFICATE"
    DEATH_CERTIFICATE_MCCD = "DEATH_CERTIFICATE_MCCD"

class PageSizeEnum(str, PyEnum):
    A4_PORTRAIT = "A4_PORTRAIT"
    A4_LANDSCAPE = "A4_LANDSCAPE"
    A5_PORTRAIT = "A5_PORTRAIT"
    THERMAL_80MM = "THERMAL_80MM"

# --- PHASE 11 ENUMS ---
class eMARStatusEnum(str, PyEnum):
    ADMINISTERED = "ADMINISTERED"
    HELD = "HELD"
    REFUSED = "REFUSED"
    MISSED = "MISSED"


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

# --- PHASE 12 ENUMS ---
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

# --- PHASE 13 ENUMS ---
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

class FederationPolicyEnum(str, PyEnum):
    FULL_MESH = "FULL_MESH"
    CORPORATE_GROUP_ONLY = "CORPORATE_GROUP_ONLY"
    EMERGENCY_ALLERGIES_ONLY = "EMERGENCY_ALLERGIES_ONLY"
    ISOLATED_SILO = "ISOLATED_SILO"


# -----------------------------------------------------------------------------
# Auditing mixin for timestamp fields
# -----------------------------------------------------------------------------
class AuditMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    created_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


@event.listens_for(SQLAlchemySession, "before_flush")
def stamp_audit_actor(session, flush_context, instances):
    """Attribute audited ORM inserts and updates to the authenticated request user."""
    actor_id = session.info.get("audit_user_id")
    if actor_id is None:
        return

    for entity in session.new:
        if isinstance(entity, AuditMixin):
            if entity.created_by_id is None:
                entity.created_by_id = actor_id
            entity.updated_by_id = actor_id

    for entity in session.dirty:
        if isinstance(entity, AuditMixin) and session.is_modified(entity, include_collections=False):
            entity.updated_by_id = actor_id


# -----------------------------------------------------------------------------
# Federated Client and Transfer Log Models
# -----------------------------------------------------------------------------
class FederatedClient(AuditMixin, Base):
    """Stores authorized external branches/tenants allowed to request patient records."""
    __tablename__ = "federated_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    branch_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    api_key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class FederationTransferLog(Base):
    """Immutable audit log of every cross-branch/cross-tenant patient data lookup."""
    __tablename__ = "federation_transfer_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("federated_clients.id"), nullable=True, index=True)
    requesting_branch_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_identifier: Mapped[str] = mapped_column(String(100), nullable=False)
    purpose: Mapped[str] = mapped_column(String(255), nullable=False)
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# -----------------------------------------------------------------------------
# Identity and IAM domain
# -----------------------------------------------------------------------------
class Department(AuditMixin, Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    ndhm_speciality_code: Mapped[Optional[str]] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(default=True)


class Role(AuditMixin, Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    is_system_role: Mapped[bool] = mapped_column(default=False)

    permissions: Mapped[List["Permission"]] = relationship(secondary="role_permissions", back_populates="roles")
    users: Mapped[List["User"]] = relationship(secondary="user_roles", back_populates="roles")


class Permission(AuditMixin, Base):
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    module: Mapped[str] = mapped_column(String(50), nullable=False)

    roles: Mapped[List[Role]] = relationship(secondary="role_permissions", back_populates="permissions")


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)


class User(AuditMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    login_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone_number: Mapped[Optional[str]] = mapped_column(String(20))
    gender: Mapped[GenderEnum] = mapped_column(default=GenderEnum.OTHER)
    status: Mapped[UserStatusEnum] = mapped_column(default=UserStatusEnum.ACTIVE)
    is_superuser: Mapped[bool] = mapped_column(default=False)
    failed_login_attempts: Mapped[int] = mapped_column(default=0)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    roles: Mapped[List[Role]] = relationship(secondary="user_roles", back_populates="users")
    doctor_profile: Mapped[Optional["DoctorProfile"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserRole(AuditMixin, Base):
    __tablename__ = "user_roles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))


class DoctorProfile(AuditMixin, Base):
    __tablename__ = "doctor_profiles"
    __table_args__ = (
        CheckConstraint("consultation_duration_minutes >= 5", name="ck_doctor_min_duration"),
        CheckConstraint("consultation_fee_paise >= 0", name="ck_doctor_non_neg_fee"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    clinical_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    qualification: Mapped[str] = mapped_column(String(100), nullable=False)
    specialization: Mapped[str] = mapped_column(String(100), nullable=False)
    medical_council_name: Mapped[str] = mapped_column(String(100), nullable=False)
    medical_registration_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    registration_valid_till: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consultation_fee_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consultation_duration_minutes: Mapped[int] = mapped_column(Integer, default=15, nullable=False)

    user: Mapped[User] = relationship(back_populates="doctor_profile")
    department: Mapped[Department] = relationship()


# -----------------------------------------------------------------------------
# Facility and ward management domain
# -----------------------------------------------------------------------------
class Floor(AuditMixin, Base):
    __tablename__ = "floors"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    floor_number: Mapped[int] = mapped_column(unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)

    wards: Mapped[List["Ward"]] = relationship(back_populates="floor")


class Ward(AuditMixin, Base):
    __tablename__ = "wards"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    floor_id: Mapped[int] = mapped_column(ForeignKey("floors.id"), nullable=False)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    ward_type: Mapped[WardTypeEnum] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)

    floor: Mapped[Floor] = relationship(back_populates="wards")
    department: Mapped[Department] = relationship()
    rooms: Mapped[List["PhysicalRoom"]] = relationship(back_populates="ward")


class PhysicalRoom(AuditMixin, Base):
    __tablename__ = "physical_rooms_master"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ward_id: Mapped[int] = mapped_column(ForeignKey("wards.id", ondelete="CASCADE"), nullable=False)
    room_number: Mapped[str] = mapped_column(String(20), nullable=False)
    room_type: Mapped[RoomTypeEnum] = mapped_column(nullable=False)
    bed_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)

    ward: Mapped[Ward] = relationship(back_populates="rooms")
    beds: Mapped[List["Bed"]] = relationship(back_populates="room")


class Bed(AuditMixin, Base):
    __tablename__ = "beds_master"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("physical_rooms_master.id", ondelete="CASCADE"), nullable=False)
    bed_number: Mapped[str] = mapped_column(String(20), nullable=False)
    bed_type: Mapped[BedTypeEnum] = mapped_column(nullable=False)
    status: Mapped[BedStatusEnum] = mapped_column(default=BedStatusEnum.AVAILABLE, nullable=False)
    base_daily_rate_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    current_admission_id: Mapped[Optional[int]] = mapped_column(Integer)

    room: Mapped[PhysicalRoom] = relationship(back_populates="beds")


class NursingShiftAssignment(AuditMixin, Base):
    __tablename__ = "nursing_shift_assignments"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ward_id: Mapped[int] = mapped_column(ForeignKey("wards.id"), nullable=False)
    nurse_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    shift_type: Mapped[ShiftTypeEnum] = mapped_column(nullable=False)
    shift_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    handoff_notes: Mapped[Optional[str]] = mapped_column(Text)
    handed_off_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# -----------------------------------------------------------------------------
# Clinical and EMR master data
# -----------------------------------------------------------------------------
class ChiefComplaintMaster(AuditMixin, Base):
    __tablename__ = "chief_complaints_master"
    __table_args__ = (UniqueConstraint("department_id", "code", name="uq_complaint_dept_code"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class ComorbidityMaster(AuditMixin, Base):
    __tablename__ = "comorbidities_master"
    __table_args__ = (UniqueConstraint("department_id", "code", name="uq_comorbidity_dept_code"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    icd10_code: Mapped[Optional[str]] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(default=True)


class AllergyMaster(AuditMixin, Base):
    __tablename__ = "allergies_master"
    __table_args__ = (UniqueConstraint("department_id", "code", name="uq_allergy_dept_code"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[AllergyCategoryEnum] = mapped_column(default=AllergyCategoryEnum.DRUG, nullable=False)
    default_severity: Mapped[AllergySeverityEnum] = mapped_column(default=AllergySeverityEnum.MODERATE, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class DiagnosisMaster(AuditMixin, Base):
    __tablename__ = "diagnoses_master"
    __table_args__ = (UniqueConstraint("department_id", "icd10_code", name="uq_diagnosis_dept_icd10"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    icd10_code: Mapped[str] = mapped_column(String(20), nullable=False)
    short_description: Mapped[str] = mapped_column(String(100), nullable=False)
    full_description: Mapped[str] = mapped_column(Text, nullable=False)
    is_billable: Mapped[bool] = mapped_column(default=True)
    parent_diagnosis_id: Mapped[Optional[int]] = mapped_column(ForeignKey("diagnoses_master.id"))
    is_active: Mapped[bool] = mapped_column(default=True)


class PhysicalExaminationMaster(AuditMixin, Base):
    __tablename__ = "physical_examinations_master"
    __table_args__ = (UniqueConstraint("department_id", "code", name="uq_exam_dept_code"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    body_system: Mapped[BodySystemEnum] = mapped_column(nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class Patient(AuditMixin, Base):
    __tablename__ = "patients"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mrn: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_of_birth: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    gender: Mapped[GenderEnum] = mapped_column(nullable=False)
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), index=True, nullable=True, default="0000000000")
    abha_number: Mapped[Optional[str]] = mapped_column(String(50), unique=True, index=True, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    blood_group: Mapped[Optional[BloodGroupEnum]] = mapped_column(nullable=True)
    address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    emergency_contact_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    emergency_contact_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    allow_network_sharing: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PatientAllergy(AuditMixin, Base):
    __tablename__ = "patient_allergies"
    __table_args__ = (
        UniqueConstraint("patient_id", "allergy_id", name="uq_patient_allergy"),
        Index("ix_patient_allergies_patient_id", "patient_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    allergy_id: Mapped[int] = mapped_column(ForeignKey("allergies_master.id", ondelete="CASCADE"), nullable=False)
    severity: Mapped[AllergySeverityEnum] = mapped_column(nullable=False)
    reaction_description: Mapped[Optional[str]] = mapped_column(Text)
    recorded_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)

    patient: Mapped[Patient] = relationship()
    allergy: Mapped[AllergyMaster] = relationship()
    recorded_by: Mapped[User] = relationship()

    @property
    def reaction(self) -> Optional[str]:
        return self.reaction_description

    @reaction.setter
    def reaction(self, value: Optional[str]) -> None:
        self.reaction_description = value


# ==========================================
# PHASE 4: DIAGNOSTIC SERVICES & LIS CORE
# ==========================================
class ServiceCategory(AuditMixin, Base):
    __tablename__ = "service_categories"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)

    department: Mapped["Department"] = relationship()
    services: Mapped[List["ServiceCatalog"]] = relationship(back_populates="category")


class ServiceCatalog(AuditMixin, Base):
    __tablename__ = "service_catalog"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("service_categories.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    service_type: Mapped[ServiceTypeEnum] = mapped_column(nullable=False)
    base_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    loinc_code: Mapped[Optional[str]] = mapped_column(String(50))
    cpt_code: Mapped[Optional[str]] = mapped_column(String(50))
    is_order_set: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)

    category: Mapped[ServiceCategory] = relationship(back_populates="services")


class ServiceBundleMapping(AuditMixin, Base):
    __tablename__ = "service_bundle_mappings"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_bundle_mapping_qty_pos"),)
    parent_service_id: Mapped[int] = mapped_column(ForeignKey("service_catalog.id", ondelete="CASCADE"), primary_key=True)
    child_service_id: Mapped[int] = mapped_column(ForeignKey("service_catalog.id", ondelete="CASCADE"), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    child_service: Mapped["ServiceCatalog"] = relationship("ServiceCatalog", foreign_keys=[child_service_id])


class DiagnosticOrder(AuditMixin, Base):
    __tablename__ = "diagnostic_orders"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    ordering_doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    clinical_notes: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[OrderStatusEnum] = mapped_column(default=OrderStatusEnum.ACTIVE, nullable=False)

    patient: Mapped["Patient"] = relationship()
    ordering_doctor: Mapped["User"] = relationship()
    tasks: Mapped[List["DiagnosticTask"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class DiagnosticTask(AuditMixin, Base):
    __tablename__ = "diagnostic_tasks"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_orders.id", ondelete="CASCADE"), nullable=False)
    service_id: Mapped[int] = mapped_column(ForeignKey("service_catalog.id"), nullable=False)
    status: Mapped[TaskStatusEnum] = mapped_column(default=TaskStatusEnum.PENDING, nullable=False)
    result_value: Mapped[Optional[str]] = mapped_column(Text)
    result_notes: Mapped[Optional[str]] = mapped_column(Text)
    is_critical_panic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    performed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    verified_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))

    order: Mapped[DiagnosticOrder] = relationship(back_populates="tasks")
    service: Mapped[ServiceCatalog] = relationship()
    performed_by: Mapped[Optional["User"]] = relationship(foreign_keys=[performed_by_id])
    verified_by: Mapped[Optional["User"]] = relationship(foreign_keys=[verified_by_id])


# ==========================================
# PHASE 5: PATIENT ADMISSIONS & ENCOUNTERS (ADT)
# ==========================================
class PatientEncounter(AuditMixin, Base):
    __tablename__ = "patient_encounters"
    __table_args__ = (Index("ix_encounters_patient_status", "patient_id", "status"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    encounter_type: Mapped[EncounterTypeEnum] = mapped_column(nullable=False)
    status: Mapped[EncounterStatusEnum] = mapped_column(default=EncounterStatusEnum.ACTIVE, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), nullable=False)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    primary_doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    chief_complaint_notes: Mapped[Optional[str]] = mapped_column(Text)

    patient: Mapped["Patient"] = relationship()
    primary_doctor: Mapped["User"] = relationship()
    department: Mapped["Department"] = relationship()
    admissions: Mapped[List["InpatientAdmission"]] = relationship(back_populates="encounter")


class InpatientAdmission(AuditMixin, Base):
    __tablename__ = "inpatient_admissions"
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
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    admission_id: Mapped[int] = mapped_column(ForeignKey("inpatient_admissions.id", ondelete="CASCADE"), nullable=False)
    from_bed_id: Mapped[Optional[int]] = mapped_column(ForeignKey("beds_master.id"))
    to_bed_id: Mapped[int] = mapped_column(ForeignKey("beds_master.id"), nullable=False)
    transfer_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), nullable=False)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    transfer_reason: Mapped[Optional[str]] = mapped_column(String(255))

    admission: Mapped[InpatientAdmission] = relationship(back_populates="transfer_logs")
    from_bed: Mapped[Optional["Bed"]] = relationship(foreign_keys=[from_bed_id])
    to_bed: Mapped["Bed"] = relationship(foreign_keys=[to_bed_id])
    requested_by: Mapped["User"] = relationship()


# ==========================================
# PHASE 6: ENTERPRISE BILLING & LEDGER
# ==========================================
class Invoice(AuditMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint("total_amount_paise >= 0", name="ck_invoice_total_non_neg"),
        CheckConstraint("discount_amount_paise >= 0", name="ck_invoice_discount_non_neg"),
        CheckConstraint("discount_amount_paise <= total_amount_paise", name="ck_invoice_discount_le_total"),
        CheckConstraint("amount_paid_paise >= 0", name="ck_invoice_paid_non_neg"),
        CheckConstraint(
            "amount_paid_paise <= (total_amount_paise - discount_amount_paise)",
            name="ck_invoice_no_overpayment",
        ),
        Index("ix_invoices_patient_id", "patient_id"),
        Index("ix_invoices_encounter_id", "encounter_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    encounter_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patient_encounters.id"))
    invoice_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    status: Mapped[InvoiceStatusEnum] = mapped_column(default=InvoiceStatusEnum.DRAFT, nullable=False)
    total_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discount_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discount_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    discount_approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    amount_paid_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    patient: Mapped["Patient"] = relationship()
    encounter: Mapped[Optional["PatientEncounter"]] = relationship()
    discount_approved_by: Mapped[Optional["User"]] = relationship(foreign_keys=[discount_approved_by_id])
    line_items: Mapped[List["InvoiceLineItem"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    payments: Mapped[List["PaymentReceipt"]] = relationship(back_populates="invoice")

    @property
    def net_amount_paise(self) -> int:
        return max(0, self.total_amount_paise - (self.discount_amount_paise or 0))


class InvoiceLineItem(AuditMixin, Base):
    __tablename__ = "invoice_line_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_line_item_qty_pos"),
        CheckConstraint("unit_price_paise >= 0", name="ck_line_item_unit_price_non_neg"),
        CheckConstraint("total_price_paise >= 0", name="ck_line_item_total_non_neg"),
        Index("ix_invoice_line_items_invoice_id", "invoice_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    service_id: Mapped[Optional[int]] = mapped_column(ForeignKey("service_catalog.id"))
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    total_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="line_items")
    service: Mapped[Optional["ServiceCatalog"]] = relationship()


class PaymentReceipt(AuditMixin, Base):
    __tablename__ = "payment_receipts"
    __table_args__ = (
        CheckConstraint("amount_paid_paise > 0", name="ck_payment_receipt_amount_pos"),
        Index("ix_payment_receipts_invoice_id", "invoice_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    receipt_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    amount_paid_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_method: Mapped[PaymentMethodEnum] = mapped_column(nullable=False)
    transaction_reference: Mapped[Optional[str]] = mapped_column(String(100))

    invoice: Mapped[Invoice] = relationship(back_populates="payments")


# ==========================================
# PHASE 7: OUTPATIENT APPOINTMENTS (OPD)
# ==========================================
class Appointment(AuditMixin, Base):
    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appointments_doctor_time", "doctor_id", "appointment_time"),
        Index("ix_appointments_patient_id", "patient_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    appointment_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AppointmentStatusEnum] = mapped_column(default=AppointmentStatusEnum.SCHEDULED, nullable=False)
    token_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason_for_visit: Mapped[Optional[str]] = mapped_column(String(255))
    encounter_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patient_encounters.id"))

    patient: Mapped["Patient"] = relationship()
    doctor: Mapped["User"] = relationship()
    department: Mapped["Department"] = relationship()
    encounter: Mapped[Optional["PatientEncounter"]] = relationship()


# ==========================================
# PHASE 8: PHARMACY & INVENTORY MANAGEMENT
# ==========================================
class MedicationMaster(AuditMixin, Base):
    __tablename__ = "medication_master"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    brand_name: Mapped[str] = mapped_column(String(100), nullable=False)
    generic_name: Mapped[str] = mapped_column(String(100), nullable=False)
    form: Mapped[MedicationFormEnum] = mapped_column(nullable=False)
    strength: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class InventoryBatch(AuditMixin, Base):
    __tablename__ = "inventory_batches"
    __table_args__ = (
        UniqueConstraint("medication_id", "batch_number", name="uq_med_batch_number"),
        CheckConstraint("stock_quantity >= 0", name="ck_batch_stock_non_neg"),
        CheckConstraint("unit_price_paise >= 0", name="ck_batch_price_non_neg"),
        Index("ix_inventory_batches_medication_id", "medication_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    medication_id: Mapped[int] = mapped_column(ForeignKey("medication_master.id"), nullable=False)
    batch_number: Mapped[str] = mapped_column(String(50), nullable=False)
    expiry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)

    medication: Mapped[MedicationMaster] = relationship()


class Prescription(AuditMixin, Base):
    __tablename__ = "prescriptions"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id"), nullable=False)
    prescribing_doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[PrescriptionStatusEnum] = mapped_column(default=PrescriptionStatusEnum.ACTIVE, nullable=False)
    clinical_notes: Mapped[Optional[str]] = mapped_column(Text)

    encounter: Mapped["PatientEncounter"] = relationship()
    prescribing_doctor: Mapped["User"] = relationship()
    items: Mapped[List["PrescriptionItem"]] = relationship(back_populates="prescription", cascade="all, delete-orphan")


class PrescriptionItem(AuditMixin, Base):
    __tablename__ = "prescription_items"
    __table_args__ = (
        CheckConstraint("requested_quantity > 0", name="ck_rx_req_qty_pos"),
        CheckConstraint("dispensed_quantity >= 0", name="ck_rx_disp_qty_non_neg"),
        CheckConstraint("dispensed_quantity <= requested_quantity", name="ck_rx_no_overdispense"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    prescription_id: Mapped[int] = mapped_column(ForeignKey("prescriptions.id", ondelete="CASCADE"), nullable=False)
    medication_id: Mapped[int] = mapped_column(ForeignKey("medication_master.id"), nullable=False)
    dosage_instruction: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    dispensed_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    prescription: Mapped[Prescription] = relationship(back_populates="items")
    medication: Mapped[MedicationMaster] = relationship()

# ==========================================
# PHASE 9: INSURANCE, TPA, PRE-AUTH & CLAIMS SETTLEMENT
# ==========================================

class InsuranceProvider(AuditMixin, Base):
    """Master catalog of TPAs, private health insurers, government schemes (PM-JAY), and corporate payers."""
    __tablename__ = "insurance_providers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    provider_type: Mapped[InsuranceProviderTypeEnum] = mapped_column(nullable=False)
    contact_email: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

class InsurancePlan(AuditMixin, Base):
    """Specific insurance plan or corporate tier under an InsuranceProvider with room-rent caps and MoU rules."""
    __tablename__ = "insurance_plans"
    __table_args__ = (
        CheckConstraint("default_copay_percentage >= 0 AND default_copay_percentage <= 100", name="ck_plan_copay_range"),
        CheckConstraint("max_room_rent_daily_paise IS NULL OR max_room_rent_daily_paise >= 0", name="ck_plan_room_cap_non_neg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("insurance_providers.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    default_copay_percentage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_room_rent_daily_paise: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # None = no daily room cap
    apply_tariff_automatically: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    provider: Mapped["InsuranceProvider"] = relationship()
    tariffs: Mapped[List["InsurancePlanTariff"]] = relationship(back_populates="plan", cascade="all, delete-orphan")


class InsurancePlanTariff(AuditMixin, Base):
    """Plan-specific negotiated rate card (Schedule of Charges) and coverage exclusion rules per ServiceCatalog item."""
    __tablename__ = "insurance_plan_tariffs"
    __table_args__ = (
        UniqueConstraint("plan_id", "service_id", name="uq_plan_service_tariff"),
        CheckConstraint("tariff_price_paise >= 0", name="ck_tariff_price_non_neg"),
        Index("ix_plan_tariffs_plan_id", "plan_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("insurance_plans.id", ondelete="CASCADE"), nullable=False)
    service_id: Mapped[int] = mapped_column(ForeignKey("service_catalog.id", ondelete="CASCADE"), nullable=False)
    tariff_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    is_covered: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    plan: Mapped["InsurancePlan"] = relationship(back_populates="tariffs")
    service: Mapped["ServiceCatalog"] = relationship()

class PatientInsurancePolicy(AuditMixin, Base):
    """Links a patient to an InsuranceProvider with sum insured, co-pay percentage, and validity dates."""
    __tablename__ = "patient_insurance_policies"
    __table_args__ = (
        UniqueConstraint("provider_id", "policy_number", name="uq_provider_policy_number"),
        CheckConstraint("sum_insured_paise > 0", name="ck_policy_sum_insured_pos"),
        CheckConstraint("copay_percentage >= 0 AND copay_percentage <= 100", name="ck_policy_copay_range"),
        Index("ix_patient_policies_patient_id", "patient_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    provider_id: Mapped[int] = mapped_column(ForeignKey("insurance_providers.id"), nullable=False)
    plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("insurance_plans.id"), nullable=True)

    policy_number: Mapped[str] = mapped_column(String(100), nullable=False)
    member_id: Mapped[str] = mapped_column(String(100), nullable=False)
    sum_insured_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    copay_percentage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0 to 100
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_till: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    patient: Mapped["Patient"] = relationship()
    provider: Mapped["InsuranceProvider"] = relationship()
    plan: Mapped[Optional["InsurancePlan"]] = relationship()

class InsurancePreAuth(AuditMixin, Base):
    """Cashless pre-authorization workflow tied to an active encounter and billable ICD-10 diagnosis."""
    __tablename__ = "insurance_pre_auths"
    __table_args__ = (
        CheckConstraint("requested_amount_paise > 0", name="ck_preauth_req_amount_pos"),
        CheckConstraint("approved_amount_paise >= 0", name="ck_preauth_app_amount_non_neg"),
        Index("ix_preauth_encounter_id", "encounter_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id"), nullable=False)
    policy_id: Mapped[int] = mapped_column(ForeignKey("patient_insurance_policies.id"), nullable=False)
    primary_diagnosis_id: Mapped[int] = mapped_column(ForeignKey("diagnoses_master.id"), nullable=False)

    preauth_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    status: Mapped[PreAuthStatusEnum] = mapped_column(default=PreAuthStatusEnum.SUBMITTED, nullable=False)
    requested_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tpa_reference_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    clinical_justification: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tpa_query_remarks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    policy: Mapped["PatientInsurancePolicy"] = relationship()
    primary_diagnosis: Mapped["DiagnosisMaster"] = relationship()


class InsuranceClaim(AuditMixin, Base):
    """Final insurance claim splitting an Invoice between Insurer Payable and Patient Co-Pay."""
    __tablename__ = "insurance_claims"
    __table_args__ = (
        CheckConstraint("claimed_amount_paise > 0", name="ck_claim_claimed_pos"),
        CheckConstraint("insurer_payable_paise >= 0", name="ck_claim_insurer_non_neg"),
        CheckConstraint("patient_copay_paise >= 0", name="ck_claim_copay_non_neg"),
        Index("ix_claims_invoice_id", "invoice_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), unique=True, nullable=False)
    policy_id: Mapped[int] = mapped_column(ForeignKey("patient_insurance_policies.id"), nullable=False)
    preauth_id: Mapped[Optional[int]] = mapped_column(ForeignKey("insurance_pre_auths.id"), nullable=True)

    claim_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    status: Mapped[ClaimStatusEnum] = mapped_column(default=ClaimStatusEnum.SUBMITTED, nullable=False)

    claimed_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    insurer_payable_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    patient_copay_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    settled_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    settlement_utr_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    remarks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    invoice: Mapped["Invoice"] = relationship()
    policy: Mapped["PatientInsurancePolicy"] = relationship()
    preauth: Mapped[Optional["InsurancePreAuth"]] = relationship()

# ==========================================
# PHASE 9D: PATIENT ADVANCE DEPOSITS & REFUND VOUCHERS
# ==========================================

class PatientDeposit(AuditMixin, Base):
    """Tracks upfront IPD/OPD advance deposits collected before invoice finalization."""
    __tablename__ = "patient_deposits"
    __table_args__ = (
        CheckConstraint("amount_paise > 0", name="ck_deposit_amount_pos"),
        CheckConstraint("remaining_balance_paise >= 0", name="ck_deposit_balance_non_neg"),
        CheckConstraint("remaining_balance_paise <= amount_paise", name="ck_deposit_balance_le_amount"),
        Index("ix_patient_deposits_patient_id", "patient_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    encounter_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patient_encounters.id"), nullable=True)

    deposit_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_balance_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_method: Mapped[PaymentMethodEnum] = mapped_column(nullable=False)
    transaction_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[DepositStatusEnum] = mapped_column(default=DepositStatusEnum.ACTIVE, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    patient: Mapped["Patient"] = relationship()
    encounter: Mapped[Optional["PatientEncounter"]] = relationship()


class RefundVoucher(AuditMixin, Base):
    """Immutable financial audit voucher for excess deposit refunds or billing credit notes."""
    __tablename__ = "refund_vouchers"
    __table_args__ = (
        CheckConstraint("refund_amount_paise > 0", name="ck_refund_amount_pos"),
        Index("ix_refund_vouchers_patient_id", "patient_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    voucher_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    deposit_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patient_deposits.id"), nullable=True)
    invoice_id: Mapped[Optional[int]] = mapped_column(ForeignKey("invoices.id"), nullable=True)

    refund_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    refund_method: Mapped[PaymentMethodEnum] = mapped_column(nullable=False)
    transaction_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    patient: Mapped["Patient"] = relationship()
    deposit: Mapped[Optional["PatientDeposit"]] = relationship()
    invoice: Mapped[Optional["Invoice"]] = relationship()
    approved_by: Mapped["User"] = relationship(foreign_keys=[approved_by_id])

# ==========================================
# PHASE 10: CLINICAL EMR (VITALS, SOAP NOTES & ICD-10 DIAGNOSES)
# ==========================================

class EncounterVital(AuditMixin, Base):
    """Time-series nursing and clinical vitals recorded during an active PatientEncounter."""
    __tablename__ = "encounter_vitals"

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
    signed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    signed_by: Mapped[Optional["User"]] = relationship(foreign_keys=[signed_by_id])


class EncounterDiagnosis(AuditMixin, Base):
    """Links an active PatientEncounter to ICD-10 DiagnosisMaster entries."""
    __tablename__ = "encounter_diagnoses"

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

class ReportTemplate(AuditMixin, Base):
    """Stores visual drag-and-drop layout schemas and compiled Jinja2 HTML templates per tenant."""
    __tablename__ = "report_templates"
    __table_args__ = (
        Index("ix_report_templates_type_default", "report_type", "is_default"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    report_type: Mapped[ReportTypeEnum] = mapped_column(nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    page_size: Mapped[PageSizeEnum] = mapped_column(default=PageSizeEnum.A4_PORTRAIT, nullable=False)

    # JSON string storing the visual drag-and-drop canvas blocks, (x,y) coordinates, and widget styles
    layout_schema: Mapped[str] = mapped_column(Text, nullable=False)

    # Compiled Jinja2 HTML and CSS executed by the sandboxed print engine
    html_template: Mapped[str] = mapped_column(Text, nullable=False)
    css_styles: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    header_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    footer_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_system_template: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

# ==========================================
# PHASE 11: INPATIENT NURSING eMAR, FLUID I/O & ER TRIAGE / MLC
# ==========================================

class MedicationAdministrationLog(AuditMixin, Base):
    """Bedside nursing eMAR log recording individual doses administered, held, or refused."""
    __tablename__ = "medication_administration_logs"
    __table_args__ = (
        Index("ix_emar_encounter_id", "encounter_id"),
        Index("ix_emar_rx_item_id", "prescription_item_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False)
    prescription_item_id: Mapped[int] = mapped_column(ForeignKey("prescription_items.id", ondelete="CASCADE"), nullable=False)
    administered_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    scheduled_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    administered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status: Mapped[eMARStatusEnum] = mapped_column(nullable=False)
    dose_given: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "500mg IV" or "1 Tablet"
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    prescription_item: Mapped["PrescriptionItem"] = relationship()
    administered_by: Mapped["User"] = relationship(foreign_keys=[administered_by_id])


class FluidBalanceLog(AuditMixin, Base):
    """Tracks inpatient fluid intake (IV, Oral, Blood) and output (Urine, Drain, Emesis) in mL."""
    __tablename__ = "fluid_balance_logs"
    __table_args__ = (
        CheckConstraint("volume_ml > 0", name="ck_fluid_volume_pos"),
        Index("ix_fluid_logs_encounter_id", "encounter_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False)
    recorded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

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
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(
        ForeignKey("patient_encounters.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    triaged_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

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

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mlc_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    encounter_id: Mapped[int] = mapped_column(
        ForeignKey("patient_encounters.id", ondelete="RESTRICT"), unique=True, nullable=False, index=True
    )
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    registered_by_doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

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

# ==========================================
# PHASE 12: OPERATION THEATRE (OT), SURGICAL SAFETY & IMPLANT BILLING
# ==========================================

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
    floor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("floors.id"), nullable=True)
    hourly_rate_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SurgicalSchedule(AuditMixin, Base):
    """Schedules a surgical case in an OperationTheatre with lead surgeon, anesthetist, and procedure link."""
    __tablename__ = "surgical_schedules"
    __table_args__ = (
        Index("ix_surgery_theatre_time", "theatre_id", "scheduled_start_time"),
        Index("ix_surgery_encounter_id", "encounter_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id"), nullable=False)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    theatre_id: Mapped[int] = mapped_column(ForeignKey("operation_theatres.id"), nullable=False)
    procedure_service_id: Mapped[int] = mapped_column(ForeignKey("service_catalog.id"), nullable=False)

    lead_surgeon_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    anesthetist_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

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
    verified_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

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

# ==========================================
# PHASE 13: BLOOD BANK TRANSFUSION SAFETY & RADIOLOGY PACS/DICOM
# ==========================================

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

# ==========================================
# PHASE 15A: PHARMACY RETURNS, OPD LEAVES, COMORBIDITIES & PHYSICAL EXAMS
# ==========================================

class PharmacyReturnLog(AuditMixin, Base):
    """Tracks ward/patient returns of dispensed medications back into InventoryBatch with auto-credit/refund."""
    __tablename__ = "pharmacy_return_logs"
    __table_args__ = (
        CheckConstraint("returned_quantity > 0", name="ck_pharm_return_qty_pos"),
        CheckConstraint("credit_amount_paise >= 0", name="ck_pharm_return_credit_non_neg"),
        Index("ix_pharm_returns_rx_id", "prescription_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    prescription_id: Mapped[int] = mapped_column(ForeignKey("prescriptions.id", ondelete="CASCADE"), nullable=False)
    prescription_item_id: Mapped[int] = mapped_column(ForeignKey("prescription_items.id", ondelete="CASCADE"), nullable=False)
    batch_id: Mapped[int] = mapped_column(ForeignKey("inventory_batches.id"), nullable=False)
    returned_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    returned_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    credit_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    return_reason: Mapped[str] = mapped_column(String(255), nullable=False)

    prescription: Mapped["Prescription"] = relationship()
    prescription_item: Mapped["PrescriptionItem"] = relationship()
    batch: Mapped["InventoryBatch"] = relationship()
    returned_by: Mapped["User"] = relationship(foreign_keys=[returned_by_id])


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


class PatientComorbidity(AuditMixin, Base):
    """Links a Patient to chronic conditions from ComorbidityMaster across episodes of care."""
    __tablename__ = "patient_comorbidities"
    __table_args__ = (
        UniqueConstraint("patient_id", "comorbidity_id", name="uq_patient_comorbidity"),
        Index("ix_patient_comorbidities_patient_id", "patient_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    comorbidity_id: Mapped[int] = mapped_column(ForeignKey("comorbidities_master.id", ondelete="RESTRICT"), nullable=False)
    recorded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
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
    exam_master_id: Mapped[int] = mapped_column(ForeignKey("physical_examinations_master.id", ondelete="RESTRICT"), nullable=False)
    recorded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    is_normal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    findings_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    exam_master: Mapped["PhysicalExaminationMaster"] = relationship()
    recorded_by: Mapped["User"] = relationship(foreign_keys=[recorded_by_id])

# ==========================================
# PHASE 15B: MATERNITY BIRTH REGISTER (AUTO-NEWBORN MRN) & MORTALITY / MCCD REGISTER
# ==========================================

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
    attending_doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

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
    certified_by_doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    immediate_cause_diagnosis_id: Mapped[int] = mapped_column(ForeignKey("diagnoses_master.id"), nullable=False)

    time_of_death: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    manner_of_death: Mapped[MannerOfDeathEnum] = mapped_column(default=MannerOfDeathEnum.NATURAL, nullable=False)
    antecedent_cause_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_autopsy_advised: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    body_handed_over_to: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    patient: Mapped["Patient"] = relationship()
    certified_by_doctor: Mapped["User"] = relationship(foreign_keys=[certified_by_doctor_id])
    immediate_cause_diagnosis: Mapped["DiagnosisMaster"] = relationship()

# ==========================================
# PHASE 17: TENANT ADMIN POLICIES & FEDERATION SOVEREIGNTY
# ==========================================

class HospitalPolicySetting(AuditMixin, Base):
    """Stores tenant-configurable clinical rules, lockout thresholds, and federation data-sharing sovereignty."""
    __tablename__ = "hospital_policy_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    max_failed_login_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    session_timeout_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    default_consultation_duration_minutes: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    allow_overbooking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enforce_financial_discharge_gate: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_unfinalized_interim_payments: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fefo_expiry_warning_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    low_stock_threshold_units: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    o_negative_critical_threshold_units: Mapped[int] = mapped_column(Integer, default=2, nullable=False)

    # Federation & Privacy Sovereignty
    federation_sharing_mode: Mapped[FederationPolicyEnum] = mapped_column(
        Enum(FederationPolicyEnum), default=FederationPolicyEnum.FULL_MESH, nullable=False
    )
    federation_share_demographics: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    federation_share_allergies: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)