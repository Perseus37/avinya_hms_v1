from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select
from database import get_db
from dependencies import RequirePermission
from schemas import WardMapResponse, BedStatusUpdate
from models import Ward, Bed, BedStatus

router = APIRouter(prefix="/api/ip/beds", tags=["Wards"])

@router.get("/map-view", response_model=list[WardMapResponse])
def get_map_view(db: Session = Depends(get_db), _: bool = Depends(RequirePermission("can_access_dashboard"))):
    stmt = select(Ward).options(joinedload(Ward.rooms).joinedload(PhysicalRoom.beds))
    return db.execute(stmt).unique().scalars().all()

@router.patch("/{bed_id}/status")
def update_bed(bed_id: int, payload: BedStatusUpdate, db: Session = Depends(get_db)):
    try:
        stmt = select(Bed).where(Bed.id == bed_id).with_for_update(nowait=True)
        bed = db.execute(stmt).scalar_one_or_none()
        
        if not bed:
            raise HTTPException(status_code=404, detail="Bed not found")
        if bed.status == BedStatus.OCCUPIED and payload.status == BedStatus.OCCUPIED:
            raise HTTPException(status_code=409, detail="Bed locked by another transaction")
            
        bed.status = payload.status
        db.commit()
        return {"status": "success"}
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail="Bed record locked.")