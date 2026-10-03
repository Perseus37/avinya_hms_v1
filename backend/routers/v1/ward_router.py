"""Ward and facility management APIs.

This router belongs to the operations and facility domain. It covers ward
inventory, room and bed configuration, and nursing shift logistics that keep
the hospital running at an operational level.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import RequirePermission, validate_pagination
from models import Bed, BedStatusEnum, Department, Floor, NursingShiftAssignment, PhysicalRoom, User, Ward
from schemas.v1.schemas import (
    BedCreate,
    BedStatusUpdate,
    FloorCreate,
    FloorResponse,
    FloorUpdate,
    PaginatedResponse,
    RoomCreate,
    RoomResponse,
    RoomUpdate,
    ShiftAssignmentCreate,
    ShiftAssignmentResponse,
    ShiftHandoffRequest,
    WardCreate,
    WardMapResponse,
    WardResponse,
    WardUpdate,
)

router = APIRouter(prefix="/api/ip", tags=["Ward & Asset Management"])


@router.get("/wards", response_model=PaginatedResponse[WardResponse])
def list_wards(
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(Ward).filter(Ward.is_active == True).order_by(Ward.id.asc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/wards", response_model=WardResponse, status_code=status.HTTP_201_CREATED)
def create_ward(
    payload: WardCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:write")),
):
    floor = db.get(Floor, payload.floor_id)
    if not floor or not floor.is_active:
        raise HTTPException(status_code=404, detail="Active floor not found.")

    dept = db.get(Department, payload.department_id)
    if not dept or not dept.is_active:
        raise HTTPException(status_code=404, detail="Active department not found.")

    existing = db.query(Ward).filter(Ward.code == payload.code).first()
    if existing:
        raise HTTPException(status_code=409, detail="Ward code already exists.")

    ward = Ward(**payload.model_dump(), is_active=True)
    db.add(ward)
    db.commit()
    db.refresh(ward)
    return ward


@router.get("/floors", response_model=PaginatedResponse[FloorResponse])
def list_floors(
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(Floor).filter(Floor.is_active == True).order_by(Floor.floor_number.asc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/wards/{ward_id}/rooms", response_model=PaginatedResponse[RoomResponse])
def list_ward_rooms(
    ward_id: int,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = (
        db.query(PhysicalRoom)
        .filter(PhysicalRoom.ward_id == ward_id, PhysicalRoom.is_active == True)
        .order_by(PhysicalRoom.id.asc())
    )
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/beds/map-view", response_model=list[WardMapResponse])
def get_map_view(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:read")),
):
    stmt = (
        select(Ward)
        .where(Ward.is_active == True)
        .options(joinedload(Ward.rooms).joinedload(PhysicalRoom.beds))
        .order_by(Ward.id.asc())
    )
    return db.execute(stmt).unique().scalars().all()


@router.post("/floors", status_code=status.HTTP_201_CREATED)
def create_floor(
    payload: FloorCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:write")),
):
    existing = db.query(Floor).filter(Floor.floor_number == payload.floor_number).first()
    if existing:
        raise HTTPException(status_code=409, detail="Floor number already exists.")
    floor = Floor(floor_number=payload.floor_number, name=payload.name, is_active=True)
    db.add(floor)
    db.commit()
    return {"status": "success", "floor_id": floor.id}


@router.post("/rooms", status_code=status.HTTP_201_CREATED)
def create_room(
    payload: RoomCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:write")),
):
    ward = db.get(Ward, payload.ward_id)
    if not ward or not ward.is_active:
        raise HTTPException(status_code=404, detail="Active ward not found.")

    room = PhysicalRoom(
        ward_id=payload.ward_id,
        room_number=payload.room_number,
        room_type=payload.room_type,
        bed_capacity=payload.bed_capacity,
        is_active=True,
    )
    db.add(room)
    db.commit()
    return {"status": "success", "room_id": room.id}


@router.post("/beds", status_code=status.HTTP_201_CREATED)
def create_bed(
    payload: BedCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:write")),
):
    room = db.get(PhysicalRoom, payload.room_id)
    if not room or not room.is_active:
        raise HTTPException(status_code=404, detail="Active room not found.")

    bed = Bed(
        room_id=payload.room_id,
        bed_number=payload.bed_number,
        bed_type=payload.bed_type,
        status=BedStatusEnum.AVAILABLE,
        base_daily_rate_paise=payload.base_daily_rate_paise,
    )
    db.add(bed)
    db.commit()
    return {"status": "success", "bed_id": bed.id}


@router.put("/wards/{ward_id}", status_code=200)
def update_ward(
    ward_id: int,
    payload: WardUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:write")),
):
    ward = db.get(Ward, ward_id)
    if not ward:
        raise HTTPException(status_code=404, detail="Ward not found")
    if payload.name is not None:
        ward.name = payload.name
    if payload.ward_type is not None:
        ward.ward_type = payload.ward_type
    if payload.is_active is not None:
        ward.is_active = payload.is_active
    db.commit()
    return {"status": "success", "message": f"Ward ID {ward_id} updated."}


@router.put("/floors/{floor_id}", status_code=200)
def update_floor(
    floor_id: int,
    payload: FloorUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:write")),
):
    floor = db.get(Floor, floor_id)
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")
    if payload.name is not None:
        floor.name = payload.name
    if payload.is_active is not None:
        floor.is_active = payload.is_active
    db.commit()
    return {"status": "success", "message": f"Floor ID {floor_id} updated."}


@router.put("/rooms/{room_id}", status_code=200)
def update_room(
    room_id: int,
    payload: RoomUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:write")),
):
    room = db.get(PhysicalRoom, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if payload.room_number is not None:
        room.room_number = payload.room_number
    if payload.room_type is not None:
        room.room_type = payload.room_type
    if payload.bed_capacity is not None:
        room.bed_capacity = payload.bed_capacity
    if payload.is_active is not None:
        room.is_active = payload.is_active
    db.commit()
    return {"status": "success", "message": f"Room ID {room_id} updated."}


@router.delete("/floors/{floor_id}", status_code=200)
def deactivate_floor(
    floor_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:delete")),
):
    floor = db.get(Floor, floor_id)
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")
    floor.is_active = False
    db.commit()
    return {"status": "success", "message": f"Floor ID {floor_id} deactivated."}


@router.delete("/wards/{ward_id}", status_code=200)
def deactivate_ward(
    ward_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:delete")),
):
    ward = db.get(Ward, ward_id)
    if not ward:
        raise HTTPException(status_code=404, detail="Ward not found")
    ward.is_active = False
    db.commit()
    return {"status": "success", "message": f"Ward ID {ward_id} deactivated."}


@router.delete("/rooms/{room_id}", status_code=200)
def deactivate_room(
    room_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:delete")),
):
    room = db.get(PhysicalRoom, room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    room.is_active = False
    db.commit()
    return {"status": "success", "message": f"Room ID {room_id} deactivated."}


@router.delete("/beds/{bed_id}", status_code=200)
def remove_bed(
    bed_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:delete")),
):
    bed = db.get(Bed, bed_id)
    if not bed:
        raise HTTPException(status_code=404, detail="Bed not found")
    if bed.status == BedStatusEnum.OCCUPIED or bed.current_admission_id is not None:
        raise HTTPException(
            status_code=409,
            detail="Cannot remove an occupied bed from circulation. Transfer or discharge the patient first.",
        )
    bed.status = BedStatusEnum.MAINTENANCE
    db.commit()
    return {"status": "success", "message": f"Bed ID {bed_id} moved to maintenance."}


@router.patch("/beds/{bed_id}/status")
def update_bed_status(
    bed_id: int,
    payload: BedStatusUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:write")),
):
    try:
        stmt = select(Bed).where(Bed.id == bed_id).with_for_update(nowait=True)
        bed = db.execute(stmt).scalar_one_or_none()
        if not bed:
            raise HTTPException(status_code=404, detail="Bed identifier not found")

        if bed.status == BedStatusEnum.OCCUPIED or bed.current_admission_id is not None:
            raise HTTPException(
                status_code=409,
                detail="Bed is currently occupied by an admitted patient. Use ADT transfer or discharge routes.",
            )
        if payload.status == BedStatusEnum.OCCUPIED:
            raise HTTPException(
                status_code=400,
                detail="Cannot manually mark a bed as OCCUPIED without an IPD admission.",
            )

        bed.status = payload.status
        db.commit()
        return {"status": "success", "new_state": bed.status.value}
    except OperationalError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Bed record is currently locked by a concurrent admission transaction.",
        )


@router.get("/wards/{ward_id}/shifts", response_model=PaginatedResponse[ShiftAssignmentResponse])
def get_ward_shifts(
    ward_id: int,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = (
        db.query(NursingShiftAssignment)
        .filter(NursingShiftAssignment.ward_id == ward_id)
        .order_by(NursingShiftAssignment.shift_date.desc(), NursingShiftAssignment.id.desc())
    )
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/shifts", response_model=ShiftAssignmentResponse, status_code=status.HTTP_201_CREATED)
def assign_nursing_shift(
    payload: ShiftAssignmentCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:write")),
):
    ward = db.get(Ward, payload.ward_id)
    if not ward or not ward.is_active:
        raise HTTPException(status_code=404, detail="Active ward not found.")

    assignment = NursingShiftAssignment(
        ward_id=payload.ward_id,
        nurse_user_id=payload.nurse_user_id,
        shift_type=payload.shift_type,
        shift_date=payload.shift_date,
        handoff_notes=payload.handoff_notes,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment


@router.patch("/shifts/{shift_id}/handoff", response_model=ShiftAssignmentResponse, status_code=200)
def complete_shift_handoff(
    shift_id: int,
    payload: ShiftHandoffRequest,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:write")),
):
    shift = db.get(NursingShiftAssignment, shift_id)
    if not shift:
        raise HTTPException(status_code=404, detail="Nursing shift assignment not found.")
    if shift.handed_off_at is not None:
        raise HTTPException(status_code=409, detail="Shift handoff has already been finalized.")

    shift.handoff_notes = payload.handoff_notes
    shift.handed_off_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(shift)
    return shift