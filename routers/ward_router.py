from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select
from database import get_db
from dependencies import RequirePermission
from schemas import WardMapResponse, BedStatusUpdate
from models import Ward, PhysicalRoom, Bed, BedStatusEnum

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