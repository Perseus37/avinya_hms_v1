from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select
from database import get_db
from dependencies import RequirePermission
from schemas import WardMapResponse, BedStatusUpdate, FloorCreate, WardUpdate, RoomCreate, BedCreate, ShiftAssignmentCreate
from models import Ward, PhysicalRoom, Bed, BedStatusEnum, NursingShiftAssignment, Floor

router = APIRouter(prefix="/api/ip", tags=["Ward & Asset Management"])

@router.get("/beds/map-view", response_model=list[WardMapResponse])
def get_map_view(db: Session = Depends(get_db), _: bool = Depends(RequirePermission("view_ward_map"))):
    stmt = select(Ward).options(joinedload(Ward.rooms).joinedload(PhysicalRoom.beds))
    return db.execute(stmt).unique().scalars().all()

@router.patch("/beds/{bed_id}/status")
def update_bed_status(bed_id: int, payload: BedStatusUpdate, db: Session = Depends(get_db)):
    try:
        stmt = select(Bed).where(Bed.id == bed_id).with_for_update(nowait=True)
        bed = db.execute(stmt).scalar_one_or_none()
        
        if not bed:
            raise HTTPException(status_code=404, detail="Bed identifier not found")
            
        if bed.status == BedStatusEnum.OCCUPIED and payload.status == BedStatusEnum.OCCUPIED:
            raise HTTPException(status_code=409, detail="Race condition prevented: Bed is already occupied.")
            
        bed.status = payload.status
        db.commit()
        return {"status": "success", "new_state": bed.status}
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail="Bed record is currently locked by a concurrent admission transaction.")

@router.post("/floors", status_code=201)
def create_floor(payload: FloorCreate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    floor = Floor(floor_number=payload.floor_number, name=payload.name, is_active=True)
    db.add(floor)
    db.commit()
    return {"status": "success", "floor_id": floor.id}

@router.put("/wards/{ward_id}", status_code=200)
def update_ward(ward_id: int, payload: WardUpdate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    ward = db.get(Ward, ward_id)
    if not ward:
        raise HTTPException(status_code=404, detail="Ward not found")
        
    if payload.name is not None: ward.name = payload.name
    if payload.ward_type is not None: ward.ward_type = payload.ward_type
    if payload.is_active is not None: ward.is_active = payload.is_active
    db.commit()
    return {"status": "success", "message": f"Ward ID {ward_id} updated."}

@router.post("/rooms", status_code=201)
def create_room(payload: RoomCreate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    room = PhysicalRoom(
        ward_id=payload.ward_id,
        room_number=payload.room_number,
        room_type=payload.room_type,
        bed_capacity=payload.bed_capacity,
        is_active=True
    )
    db.add(room)
    db.commit()
    return {"status": "success", "room_id": room.id}

@router.post("/beds", status_code=201)
def create_bed(payload: BedCreate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    bed = Bed(
        room_id=payload.room_id,
        bed_number=payload.bed_number,
        bed_type=payload.bed_type,
        status=BedStatusEnum.AVAILABLE,
        base_daily_rate_paise=payload.base_daily_rate_paise
    )
    db.add(bed)
    db.commit()
    return {"status": "success", "bed_id": bed.id}

@router.post("/shifts", status_code=201)
def assign_nursing_shift(payload: ShiftAssignmentCreate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("view_ward_map"))):
    assignment = NursingShiftAssignment(
        ward_id=payload.ward_id,
        nurse_user_id=payload.nurse_user_id,
        shift_type=payload.shift_type,
        shift_date=payload.shift_date,
        handoff_notes=payload.handoff_notes
    )
    db.add(assignment)
    db.commit()
    return {"status": "success", "message": "Nursing shift assigned and handoff log initialized."}

@router.get("/wards/{ward_id}/shifts", status_code=200)
def get_ward_shifts(ward_id: int, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("view_ward_map"))):
    return db.query(NursingShiftAssignment).filter(NursingShiftAssignment.ward_id == ward_id).all()

@router.patch("/shifts/{shift_id}/handoff", status_code=200)
def complete_shift_handoff(shift_id: int, handoff_notes: str, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("view_ward_map"))):
    shift = db.get(NursingShiftAssignment, shift_id)
    if not shift:
        raise HTTPException(status_code=404, detail="Nursing shift assignment not found.")
        
    shift.handoff_notes = handoff_notes
    shift.handed_off_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "success", "message": f"Shift ID {shift_id} handoff completed."}

@router.get("/wards", response_model=list[WardResponse])
def list_wards(db: Session = Depends(get_db), _: bool = Depends(get_current_user)):
    """Lists all active hospital wards."""
    return db.query(Ward).filter(Ward.is_active == True).all()

@router.get("/wards/{ward_id}/rooms", status_code=200)
def list_ward_rooms(ward_id: int, db: Session = Depends(get_db), _: bool = Depends(get_current_user)):
    """Lists all physical rooms associated with a specific ward."""
    return db.query(PhysicalRoom).filter(PhysicalRoom.ward_id == ward_id, PhysicalRoom.is_active == True).all()