from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.audit import AuditMixin
from backend.core.database import Base
from backend.domains.identity.models import Department


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
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id"), nullable=False
    )
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
    ward_id: Mapped[int] = mapped_column(
        ForeignKey("wards.id", ondelete="CASCADE"), nullable=False
    )
    room_number: Mapped[str] = mapped_column(String(20), nullable=False)
    room_type: Mapped[RoomTypeEnum] = mapped_column(nullable=False)
    bed_capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)

    ward: Mapped[Ward] = relationship(back_populates="rooms")
    beds: Mapped[List["Bed"]] = relationship(back_populates="room")


class Bed(AuditMixin, Base):
    __tablename__ = "beds_master"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(
        ForeignKey("physical_rooms_master.id", ondelete="CASCADE"), nullable=False
    )
    bed_number: Mapped[str] = mapped_column(String(20), nullable=False)
    bed_type: Mapped[BedTypeEnum] = mapped_column(nullable=False)
    status: Mapped[BedStatusEnum] = mapped_column(
        default=BedStatusEnum.AVAILABLE, nullable=False
    )
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
