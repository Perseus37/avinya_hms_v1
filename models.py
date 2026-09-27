from datetime import datetime
from typing import List, Optional
from sqlalchemy import String, Integer, Numeric, Boolean, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from enum import Enum as PyEnum

class Base(DeclarativeBase):
    pass

# --- ENUMS ---
class BedStatus(str, PyEnum):
    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    HOUSEKEEPING = "HOUSEKEEPING"
    MAINTENANCE = "MAINTENANCE"

# --- PHASE 1: IAM FOUNDATION ---
class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    can_access_dashboard: Mapped[bool] = mapped_column(default=False)
    can_access_inventory: Mapped[bool] = mapped_column(default=False)
    can_access_patients: Mapped[bool] = mapped_column(default=False)
    
    users: Mapped[List["User"]] = relationship(back_populates="role")

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="RESTRICT"))
    email: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    role: Mapped["Role"] = relationship(back_populates="users")
    doctor_profile: Mapped[Optional["DoctorProfile"]] = relationship(back_populates="user", cascade="all, delete-orphan")

class DoctorProfile(Base):
    __tablename__ = "doctor_profiles"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    department_id: Mapped[int] = mapped_column(Integer, nullable=False)
    doctor_code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    op_consultation_fee: Mapped[float] = mapped_column(Numeric(10, 2), default=0.00)
    
    user: Mapped["User"] = relationship(back_populates="doctor_profile")

# --- PHASE 2: ASSET MANAGEMENT ---
class Ward(Base):
    __tablename__ = "wards"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rooms: Mapped[List["PhysicalRoom"]] = relationship(back_populates="ward")

class PhysicalRoom(Base):
    __tablename__ = "physical_rooms_master"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ward_id: Mapped[int] = mapped_column(ForeignKey("wards.id"))
    room_number: Mapped[str] = mapped_column(String(20), nullable=False)
    
    ward: Mapped["Ward"] = relationship(back_populates="rooms")
    beds: Mapped[List["Bed"]] = relationship(back_populates="room")

class Bed(Base):
    __tablename__ = "beds_master"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("physical_rooms_master.id"))
    bed_number: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[BedStatus] = mapped_column(default=BedStatus.AVAILABLE)
    
    room: Mapped["PhysicalRoom"] = relationship(back_populates="beds")
# --- PHASE 3: CLINICAL TAXONOMY ---
class Department(Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))

class AllergyMaster(Base):
    __tablename__ = "allergies_master"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(default=True)

class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mrn: Mapped[str] = mapped_column(String(20), unique=True)

class PatientAllergy(Base):
    __tablename__ = "patient_allergies"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    allergy_id: Mapped[int] = mapped_column(ForeignKey("allergies_master.id"))
    
    allergy_master: Mapped["AllergyMaster"] = relationship()