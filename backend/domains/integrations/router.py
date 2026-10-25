import hmac
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.control_plane.models import ControlPlaneAuditLog, TenantRegistry
from backend.core.database import get_control_db, get_db, resolve_tenant_context
from backend.core.dependencies import RequirePermission
from backend.domains.identity.models import User
from backend.domains.integrations.models import (
    EquipmentMaster,
    EquipmentTestMapping,
    HL7IntegrationLog,
    ProtocolEnum,
)
from backend.domains.integrations.schemas import (
    EquipmentCreate,
    EquipmentResponse,
    EquipmentTestMappingCreate,
    EquipmentTestMappingResponse,
    HL7MessageIngestRequest,
    HL7MessageIngestResponse,
    HL7TaskResult,
)
from backend.domains.integrations.service import (
    build_hl7_ack_str,
    process_hl7_oru_r01_in_session,
)
from backend.security.envelope import decrypt_tenant_secret, encrypt_tenant_secret

router = APIRouter(prefix="/api/equipment", tags=["LIS & PACS Equipment Integration"])


def require_hl7_auth(
    request: Request,
    db: Session = Depends(get_db),
    control_db: Session = Depends(get_control_db),
):
    hl7_secret = request.headers.get("X-HL7-Secret")
    if hl7_secret:
        tenant_context = resolve_tenant_context(request)
        tenant = control_db.execute(
            select(TenantRegistry).where(
                TenantRegistry.subdomain_slug == tenant_context.subdomain_slug
            )
        ).scalar_one_or_none()
        if tenant and tenant.hl7_shared_secret_encrypted:
            real_secret = decrypt_tenant_secret(tenant.hl7_shared_secret_encrypted)
            if real_secret and hmac.compare_digest(
                hl7_secret.encode("utf-8"), real_secret.encode("utf-8")
            ):
                return True
        raise HTTPException(status_code=401, detail="Invalid X-HL7-Secret")

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        from backend.core.dependencies import get_current_user, user_has_permission
        try:
            user = get_current_user(request=request, token=token, db=db)
            if user and user_has_permission(db, user, "lab:write", "mdm:write"):
                return True
        except Exception:
            pass
    raise HTTPException(
        status_code=401,
        detail="Authentication required: X-HL7-Secret header or lab:write bearer token.",
    )


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


@router.post("/hl7/oru-r01", response_model=HL7MessageIngestResponse)
def ingest_hl7_oru_r01_message(
    payload: HL7MessageIngestRequest,
    db: Session = Depends(get_db),
    _: bool = Depends(require_hl7_auth),
):
    """
    Ingests an unsolicited HL7 ORU^R01 laboratory observation message from an
    analyzer or hospital interface engine. Parses analytes, checks panic flags,
    evaluates LIS delta checks, and updates patient diagnostic tasks.
    """
    ctrl_id, success, error_msg, task_results = process_hl7_oru_r01_in_session(
        db, payload.raw_message, payload.equipment_id
    )

    ack_str = build_hl7_ack_str(ctrl_id, success, error_msg)

    if not success:
        return HL7MessageIngestResponse(
            message_control_id=ctrl_id,
            status="REJECTED",
            ack_hl7=ack_str,
            matched_tasks_count=0,
            panic_alerts_count=0,
            tasks=[],
            error_details=error_msg,
        )

    panic_count = sum(1 for t in task_results if t.get("is_critical_panic"))
    return HL7MessageIngestResponse(
        message_control_id=ctrl_id,
        status="PROCESSED",
        ack_hl7=ack_str,
        matched_tasks_count=len(task_results),
        panic_alerts_count=panic_count,
        tasks=[HL7TaskResult(**t) for t in task_results],
        error_details=None,
    )


@router.post("/mappings", response_model=EquipmentTestMappingResponse, status_code=status.HTTP_201_CREATED)
def create_equipment_test_mapping(
    payload: EquipmentTestMappingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    """Maps an equipment analyte test code (e.g. Sysmex 'WBC-D') to an HMS service catalog item."""
    device = db.get(EquipmentMaster, payload.equipment_id)
    if not device:
        raise HTTPException(status_code=404, detail="Equipment analyzer not found.")
    mapping = EquipmentTestMapping(**payload.model_dump(), created_by_id=current_user.id)
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return mapping


@router.get("/mappings", response_model=List[EquipmentTestMappingResponse])
def list_equipment_test_mappings(
    equipment_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:read")),
):
    """Lists configured analyzer test code mappings."""
    stmt = select(EquipmentTestMapping).where(EquipmentTestMapping.is_active.is_(True))
    if equipment_id is not None:
        stmt = stmt.where(EquipmentTestMapping.equipment_id == equipment_id)
    return db.execute(stmt).scalars().all()


@router.post("/", response_model=EquipmentResponse, status_code=status.HTTP_201_CREATED)
def register_equipment(
    payload: EquipmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    """Registers a new laboratory analyzer or imaging modality."""
    device = EquipmentMaster(**payload.model_dump(), created_by_id=current_user.id)
    db.add(device)
    db.commit()
    db.refresh(device)
    return device


@router.get("/", response_model=List[EquipmentResponse])
def list_equipment(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:read")),
):
    """Lists all configured machines for this hospital."""
    return db.execute(select(EquipmentMaster).order_by(EquipmentMaster.name)).scalars().all()


@router.get("/logs")
def view_integration_logs(
    limit: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:read")),
):
    """Displays recent raw HL7/MLLP transmissions for IT troubleshooting."""
    return db.execute(
        select(HL7IntegrationLog).order_by(HL7IntegrationLog.created_at.desc()).limit(limit)
    ).scalars().all()
