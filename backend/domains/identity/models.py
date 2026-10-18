from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.audit import AuditMixin
from backend.core.database import Base


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

    permissions: Mapped[List["Permission"]] = relationship(
        secondary="role_permissions", back_populates="roles"
    )
    users: Mapped[List["User"]] = relationship(
        secondary="user_roles", back_populates="roles"
    )


class Permission(AuditMixin, Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    module: Mapped[str] = mapped_column(String(50), nullable=False)

    roles: Mapped[List["Role"]] = relationship(
        secondary="role_permissions", back_populates="permissions"
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[int] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )


class User(AuditMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    login_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone_number: Mapped[Optional[str]] = mapped_column(String(20))
    gender: Mapped[GenderEnum] = mapped_column(default=GenderEnum.OTHER)
    status: Mapped[UserStatusEnum] = mapped_column(default=UserStatusEnum.ACTIVE)
    is_superuser: Mapped[bool] = mapped_column(default=False)
    failed_login_attempts: Mapped[int] = mapped_column(default=0)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    first_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    roles: Mapped[List["Role"]] = relationship(
        secondary="user_roles", back_populates="users"
    )
    doctor_profile: Mapped[Optional["DoctorProfile"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserRole(AuditMixin, Base):
    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    department_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL")
    )


class DoctorProfile(AuditMixin, Base):
    __tablename__ = "doctor_profiles"
    __table_args__ = (
        CheckConstraint(
            "consultation_duration_minutes >= 5", name="ck_doctor_min_duration"
        ),
        CheckConstraint("consultation_fee_paise >= 0", name="ck_doctor_non_neg_fee"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    clinical_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id"), nullable=False
    )
    qualification: Mapped[str] = mapped_column(String(100), nullable=False)
    specialization: Mapped[str] = mapped_column(String(100), nullable=False)
    medical_council_name: Mapped[str] = mapped_column(String(100), nullable=False)
    medical_registration_number: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False
    )
    registration_valid_till: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consultation_fee_paise: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    consultation_duration_minutes: Mapped[int] = mapped_column(
        Integer, default=15, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="doctor_profile")
    department: Mapped["Department"] = relationship()
