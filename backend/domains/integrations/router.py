from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.dependencies import RequirePermission
from backend.domains.identity.models import User
from backend.domains.integrations.models import (
    EquipmentMaster,
    HL7IntegrationLog,
    ProtocolEnum,
)
from backend.domains.integrations.schemas import EquipmentCreate
router = APIRouter(prefix="/api/equipment", tags=["LIS & PACS Equipment Integration"])

@router.post("/", status_code=status.HTTP_201_CREATED)
def register_equipment(
    payload: EquipmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("mdm:write"))
):
    """Registers a new laboratory analyzer or imaging modality."""
    device = EquipmentMaster(**payload.model_dump(), created_by_id=current_user.id)
    db.add(device)
    db.commit()
    db.refresh(device)
    return device

@router.get("/")
def list_equipment(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:read"))
):
    """Lists all configured machines for this hospital."""
    return db.execute(select(EquipmentMaster).order_by(EquipmentMaster.name)).scalars().all()

@router.get("/logs")
def view_integration_logs(
    limit: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:read"))
):
    """Displays recent raw HL7/MLLP transmissions for IT troubleshooting."""
    return db.execute(
        select(HL7IntegrationLog).order_by(HL7IntegrationLog.created_at.desc()).limit(limit)
    ).scalars().all()