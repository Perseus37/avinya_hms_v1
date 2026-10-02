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


class PaymentMethodEnum(str, PyEnum):
    CASH = "CASH"
    CREDIT_CARD = "CREDIT_CARD"
    INSURANCE = "INSURANCE"
    UPI = "UPI"
    BANK_TRANSFER = "BANK_TRANSFER"


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
        CheckConstraint("amount_paid_paise >= 0", name="ck_invoice_paid_non_neg"),
        CheckConstraint("amount_paid_paise <= total_amount_paise", name="ck_invoice_no_overpayment"),
        Index("ix_invoices_patient_id", "patient_id"),
        Index("ix_invoices_encounter_id", "encounter_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    encounter_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patient_encounters.id"))
    invoice_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    status: Mapped[InvoiceStatusEnum] = mapped_column(default=InvoiceStatusEnum.DRAFT, nullable=False)
    total_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    amount_paid_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    patient: Mapped["Patient"] = relationship()
    encounter: Mapped[Optional["PatientEncounter"]] = relationship()
    line_items: Mapped[List["InvoiceLineItem"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    payments: Mapped[List["PaymentReceipt"]] = relationship(back_populates="invoice")


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