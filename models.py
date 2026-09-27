"""Core SQLAlchemy models for the Avinya HMS domain.

The model layer is grouped by functional responsibility so the healthcare data
model reads like an application domain model instead of a flat list of tables.
"""

from datetime import datetime, timezone
from typing import List, Optional
from enum import Enum as PyEnum

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

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

# -----------------------------------------------------------------------------
# Auditing mixin for timestamp fields
# -----------------------------------------------------------------------------
class TimestampMixin:
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

# -----------------------------------------------------------------------------
# Identity and IAM domain
# -----------------------------------------------------------------------------
class Department(TimestampMixin, Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    ndhm_speciality_code: Mapped[Optional[str]] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(default=True)


class Role(TimestampMixin, Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    is_system_role: Mapped[bool] = mapped_column(default=False)

    permissions: Mapped[List["Permission"]] = relationship(secondary="role_permissions", back_populates="roles")
    users: Mapped[List["User"]] = relationship(secondary="user_roles", back_populates="roles")


class Permission(TimestampMixin, Base):
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


class User(TimestampMixin, Base):
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


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))


class DoctorProfile(Base):
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
class Floor(TimestampMixin, Base):
    __tablename__ = "floors"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    floor_number: Mapped[int] = mapped_column(unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)

    wards: Mapped[List["Ward"]] = relationship(back_populates="floor")


class Ward(TimestampMixin, Base):
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


class PhysicalRoom(TimestampMixin, Base):
    __tablename__ = "physical_rooms_master"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ward_id: Mapped[int] = mapped_column(ForeignKey("wards.id", ondelete="CASCADE"), nullable=False)
    room_number: Mapped[str] = mapped_column(String(20), nullable=False)
    room_type: Mapped[RoomTypeEnum] = mapped_column(nullable=False)
    bed_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)

    ward: Mapped[Ward] = relationship(back_populates="rooms")
    beds: Mapped[List["Bed"]] = relationship(back_populates="room")


class Bed(TimestampMixin, Base):
    __tablename__ = "beds_master"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("physical_rooms_master.id", ondelete="CASCADE"), nullable=False)
    bed_number: Mapped[str] = mapped_column(String(20), nullable=False)
    bed_type: Mapped[BedTypeEnum] = mapped_column(nullable=False)
    status: Mapped[BedStatusEnum] = mapped_column(default=BedStatusEnum.AVAILABLE, nullable=False)
    base_daily_rate_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    current_admission_id: Mapped[Optional[int]] = mapped_column(Integer)  # Future admissions table hook

    room: Mapped[PhysicalRoom] = relationship(back_populates="beds")


class NursingShiftAssignment(TimestampMixin, Base):
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
class ChiefComplaintMaster(TimestampMixin, Base):
    __tablename__ = "chief_complaints_master"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class ComorbidityMaster(TimestampMixin, Base):
    __tablename__ = "comorbidities_master"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    icd10_code: Mapped[Optional[str]] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(default=True)


class AllergyMaster(TimestampMixin, Base):
    __tablename__ = "allergies_master"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[AllergyCategoryEnum] = mapped_column(nullable=False)
    default_severity: Mapped[AllergySeverityEnum] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class DiagnosisMaster(TimestampMixin, Base):
    __tablename__ = "diagnoses_master"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    icd10_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    short_description: Mapped[str] = mapped_column(String(100), nullable=False)
    full_description: Mapped[str] = mapped_column(Text, nullable=False)
    is_billable: Mapped[bool] = mapped_column(default=True)
    parent_diagnosis_id: Mapped[Optional[int]] = mapped_column(ForeignKey("diagnoses_master.id"))
    is_active: Mapped[bool] = mapped_column(default=True)


class PhysicalExaminationMaster(TimestampMixin, Base):
    __tablename__ = "physical_examinations_master"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)
    body_system: Mapped[BodySystemEnum] = mapped_column(nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class Patient(TimestampMixin, Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mrn: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_of_birth: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    gender: Mapped[GenderEnum] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class PatientAllergy(TimestampMixin, Base):
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