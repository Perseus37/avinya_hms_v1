"""Core SQLAlchemy models for the Avinya HMS domain.

The model layer is grouped by functional responsibility so the healthcare data
model reads like an application domain model instead of a flat list of tables.
"""

from datetime import datetime, timezone
from typing import List, Optional
from enum import Enum as PyEnum

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, event, func
from sqlalchemy.orm import Mapped, Session as SQLAlchemySession, mapped_column, relationship

from database import Base

# -----------------------------------------------------------------------------
# Shared enums
# -----------------------------------------------------------------------------
class GenderEnum(str, PyEnum):
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"


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
    OPD = "OPD"  # Outpatient
    IPD = "IPD"  # Inpatient
    ER = "ER"    # Emergency Room

class EncounterStatusEnum(str, PyEnum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    DISCHARGED = "DISCHARGED"
    CANCELLED = "CANCELLED"

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
    PARTIAL = "PARTIAL"
    PAID = "PAID"
    VOID = "VOID"

class PaymentMethodEnum(str, PyEnum):
    CASH = "CASH"
    CREDIT_CARD = "CREDIT_CARD"
    INSURANCE = "INSURANCE"
    UPI = "UPI"
    BANK_TRANSFER = "BANK_TRANSFER"

# -----------------------------------------------------------------------------
# Auditing mixin for timestamp fields
# -----------------------------------------------------------------------------

class AuditMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        onupdate=lambda: datetime.now(timezone.utc), 
        nullable=False
    )
    # Using Integer instead of strict ForeignKey to prevent circular dependencies in the base mixin
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
    current_admission_id: Mapped[Optional[int]] = mapped_column(Integer)  # Future admissions table hook

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

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class ComorbidityMaster(AuditMixin, Base):
    __tablename__ = "comorbidities_master"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    icd10_code: Mapped[Optional[str]] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(default=True)


class AllergyMaster(AuditMixin, Base):
    __tablename__ = "allergies_master"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[AllergyCategoryEnum] = mapped_column(nullable=False)
    default_severity: Mapped[AllergySeverityEnum] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class DiagnosisMaster(AuditMixin, Base):
    __tablename__ = "diagnoses_master"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    icd10_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    short_description: Mapped[str] = mapped_column(String(100), nullable=False)
    full_description: Mapped[str] = mapped_column(Text, nullable=False)
    is_billable: Mapped[bool] = mapped_column(default=True)
    parent_diagnosis_id: Mapped[Optional[int]] = mapped_column(ForeignKey("diagnoses_master.id"))
    is_active: Mapped[bool] = mapped_column(default=True)


class PhysicalExaminationMaster(AuditMixin, Base):
    __tablename__ = "physical_examinations_master"

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
    is_active: Mapped[bool] = mapped_column(default=True)


class PatientAllergy(AuditMixin, Base):
    __tablename__ = "patient_allergies"

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


# ==========================================
# PHASE 4: DIAGNOSTIC SERVICES & LIS CORE
# ==========================================

class ServiceCategory(AuditMixin, Base):
    """Categorizes billable and executable services (e.g., Pathology, MRI, Health Checks)."""
    __tablename__ = "service_categories"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    
    department: Mapped["Department"] = relationship()
    services: Mapped[List["ServiceCatalog"]] = relationship(back_populates="category")


class ServiceCatalog(AuditMixin, Base):
    """The central master catalog for all hospital services, tests, and order sets."""
    __tablename__ = "service_catalog"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("service_categories.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    service_type: Mapped[ServiceTypeEnum] = mapped_column(nullable=False)
    base_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    
    # Standard Clinical Data Mapping
    loinc_code: Mapped[Optional[str]] = mapped_column(String(50))
    cpt_code: Mapped[Optional[str]] = mapped_column(String(50))
    
    # Order Explosion Triggers
    is_order_set: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    
    category: Mapped[ServiceCategory] = relationship(back_populates="services")


class ServiceBundleMapping(AuditMixin, Base):
    """Maps a parent package (e.g., 'Master Health Check') to executable child services."""
    __tablename__ = "service_bundle_mappings"
    parent_service_id: Mapped[int] = mapped_column(ForeignKey("service_catalog.id", ondelete="CASCADE"), primary_key=True)
    child_service_id: Mapped[int] = mapped_column(ForeignKey("service_catalog.id", ondelete="CASCADE"), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class DiagnosticOrder(AuditMixin, Base):
    """The overarching CPOE clinical request grouping multiple tests for a patient."""
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
    """The exploded, department-level execution tickets resulting from a DiagnosticOrder."""
    __tablename__ = "diagnostic_tasks"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_orders.id", ondelete="CASCADE"), nullable=False)
    service_id: Mapped[int] = mapped_column(ForeignKey("service_catalog.id"), nullable=False)
    status: Mapped[TaskStatusEnum] = mapped_column(default=TaskStatusEnum.PENDING, nullable=False)
    
    # Execution Tracking
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
    """The overarching episode of care, covering OPD visits, IPD stays, or ER visits."""
    __tablename__ = "patient_encounters"
    
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
    """Specific IPD record tracking bed assignment and admission lifecycle."""
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
    """Audit trail for patient movement across wards and beds during an active admission."""
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
    """The master billing ledger generated for a patient or an encounter."""
    __tablename__ = "invoices"
    
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
    """Granular billable charges (e.g., bed charges, pharmacy, diagnostic tests)."""
    __tablename__ = "invoice_line_items"
    
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
    """Immutable ledger of funds received against an invoice."""
    __tablename__ = "payment_receipts"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    
    receipt_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    amount_paid_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_method: Mapped[PaymentMethodEnum] = mapped_column(nullable=False)
    transaction_reference: Mapped[Optional[str]] = mapped_column(String(100))
    
    invoice: Mapped[Invoice] = relationship(back_populates="payments")