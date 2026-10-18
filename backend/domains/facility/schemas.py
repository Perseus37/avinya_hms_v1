"""Pydantic schemas owned by the Avinya HMS domain."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from backend.domains.facility.models import BedStatusEnum, BedTypeEnum, RoomTypeEnum, ShiftTypeEnum, WardTypeEnum
from backend.domains.surgery.schemas import OperationTheatreCreate

class FloorCreate(BaseModel):
    floor_number: int
    name: str

class FloorUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None

class FloorResponse(BaseModel):
    id: int
    floor_number: int
    name: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

class WardCreate(BaseModel):
    floor_id: int
    department_id: int
    code: str
    name: str
    ward_type: WardTypeEnum

class WardUpdate(BaseModel):
    floor_id: Optional[int] = None
    department_id: Optional[int] = None
    name: Optional[str] = None
    ward_type: Optional[WardTypeEnum] = None
    is_active: Optional[bool] = None

class WardResponse(BaseModel):
    id: int
    code: str
    name: str
    ward_type: WardTypeEnum
    model_config = ConfigDict(from_attributes=True)

class BedSchema(BaseModel):
    id: int
    bed_number: str
    status: BedStatusEnum
    model_config = ConfigDict(from_attributes=True)

class RoomSchema(BaseModel):
    id: int
    room_number: str
    beds: List[BedSchema]
    model_config = ConfigDict(from_attributes=True)

class WardMapResponse(BaseModel):
    id: int
    code: str
    name: str
    ward_type: WardTypeEnum
    rooms: List[RoomSchema]
    model_config = ConfigDict(from_attributes=True)

class RoomCreate(BaseModel):
    ward_id: int
    room_number: str
    room_type: RoomTypeEnum
    bed_capacity: int

class RoomUpdate(BaseModel):
    ward_id: Optional[int] = None
    room_number: Optional[str] = None
    room_type: Optional[RoomTypeEnum] = None
    bed_capacity: Optional[int] = None
    is_active: Optional[bool] = None

class RoomResponse(BaseModel):
    id: int
    ward_id: int
    room_number: str
    room_type: RoomTypeEnum
    bed_capacity: int
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

class BedCreate(BaseModel):
    room_id: int
    bed_number: str
    bed_type: BedTypeEnum
    base_daily_rate_paise: int

class BedStatusUpdate(BaseModel):
    status: BedStatusEnum

class BedReconfigure(BaseModel):
    room_id: Optional[int] = None
    bed_number: Optional[str] = None
    bed_type: Optional[BedTypeEnum] = None
    base_daily_rate_paise: Optional[int] = Field(default=None, ge=0)
    status: Optional[BedStatusEnum] = None

class ShiftAssignmentCreate(BaseModel):
    ward_id: int
    nurse_user_id: int
    shift_type: ShiftTypeEnum
    shift_date: datetime
    handoff_notes: Optional[str] = None

class ShiftAssignmentResponse(BaseModel):
    id: int
    ward_id: int
    nurse_user_id: int
    shift_type: ShiftTypeEnum
    shift_date: datetime
    handoff_notes: Optional[str] = None
    handed_off_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

class ShiftHandoffRequest(BaseModel):
    handoff_notes: str = Field(min_length=1, description="Clinical handoff summary notes")

class BedSpec(BaseModel):
    prefix: str = "BED"
    start_number: int = 1
    count: int
    bed_type: BedTypeEnum = BedTypeEnum.STANDARD
    base_daily_rate_paise: int = 150000

class RoomSpec(BaseModel):
    room_number_pattern: str = "10{n}"  # e.g., 101, 102...
    room_type: RoomTypeEnum = RoomTypeEnum.DOUBLE
    rooms_count: int
    beds_per_room: BedSpec

class WardSpec(BaseModel):
    code: str
    name: str
    ward_type: WardTypeEnum
    department_code: str
    rooms: list[RoomSpec]

class FloorSpec(BaseModel):
    floor_number: int
    name: str
    wards: list[WardSpec]

class SpatialMatrixRequest(BaseModel):
    floors: list[FloorSpec]
    operation_theatres: list[OperationTheatreCreate] = []
