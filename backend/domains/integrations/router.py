import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.control_plane.models import ControlPlaneAuditLog, TenantRegistry
from backend.core.database import get_control_db, get_db, resolve_tenant_context
from backend.core.dependencies import RequirePermission
from backend.domains.identity.models import User
from backend.domains.integrations.models import (
    EquipmentMaster,
    HL7IntegrationLog,
    ProtocolEnum,
)
from backend.domains.integrations.schemas import EquipmentCreate
from backend.security.envelope import encrypt_tenant_secret

router = APIRouter(prefix="/api/equipment", tags=["LIS & PACS Equipment Integration"])


@router.post("/hl7-secret/rotate")
def rotate_hl7_secret(
    request: Request,
    control_db: Session = Depends(get_control_db),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    """Rotate this hospital's HL7 shared secret; return the new value only once."""
    tenant_context = resolve_tenant_context(request)
    tenant = control_db.execute(
        select(TenantRegistry).where(
            TenantRegistry.subdomain_slug == tenant_context.subdomain_slug
        )
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Hospital tenant not found.")
    secret = secrets.token_urlsafe(32)
    tenant.hl7_shared_secret_encrypted = encrypt_tenant_secret(secret)
    control_db.add(
        ControlPlaneAuditLog(
            action="HL7_SECRET_ROTATED",
            target_slug=tenant_context.subdomain_slug,
            actor=current_user.email,
            details="Tenant HL7 shared secret rotated; plaintext returned once.",
        )
    )
    control_db.commit()
    return {
        "secret": secret,
        "message": "Configure this value in the laboratory equipment; it will not be shown again.",
    }


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