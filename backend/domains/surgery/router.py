from datetime import datetime, timezone
from typing import List, Optional
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.core.database import get_db, resolve_tenant_context
from backend.core.dependencies import RequirePermission
from backend.domains.diagnostics.models import ServiceCatalog
from backend.domains.encounters.models import EncounterStatusEnum, PatientEncounter
from backend.domains.identity.models import User
from backend.domains.reports.models import PrintModeEnum, ReportTypeEnum
from backend.domains.surgery.models import OperationTheatre, OperativeAnesthesiaRecord, SurgeryStatusEnum, SurgicalImplantLog, SurgicalSchedule, WHOSurgicalSafetyChecklist
from backend.domains.reports.router import (
    _fmt_local_time,
    _render_template_parts,
    _resolve_template_for_print,
)
from backend.domains.surgery.schemas import OperationTheatreCreate, OperationTheatreResponse, OperativeRecordResponse, OperativeRecordUpsert, SurgeryStatusUpdateRequest, SurgicalImplantCreate, SurgicalImplantResponse, SurgicalScheduleCreate, SurgicalScheduleResponse, WHOChecklistResponse, WHOChecklistUpsert
from backend.domains.billing.services import auto_bill_completed_surgery
from backend.domains.privacy.runtime import evaluate_tenant_patient_data_uses

router = APIRouter(prefix="/api/ot", tags=["Operation Theatre (OT), Surgical Safety & Implants"])


def _to_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# -----------------------------------------------------------------------------
# 1. Operation Theatres (OT Rooms) Master
# -----------------------------------------------------------------------------
@router.post("/theatres", response_model=OperationTheatreResponse, status_code=status.HTTP_201_CREATED)
def create_operation_theatre(
    payload: OperationTheatreCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:write", "mdm:write")),
):
    dup = db.execute(select(OperationTheatre).where(OperationTheatre.code == payload.code)).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail=f"Operation theatre '{payload.code}' already exists.")

    ot = OperationTheatre(**payload.model_dump(), is_active=True)
    db.add(ot)
    db.commit()
    db.refresh(ot)
    return ot


@router.get("/theatres", response_model=List[OperationTheatreResponse])
def list_operation_theatres(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("wards:read", "adt:read", "opd:read")),
):
    return list(db.execute(select(OperationTheatre).order_by(OperationTheatre.id.asc())).scalars().all())


# -----------------------------------------------------------------------------
# 2. Surgical Scheduling & Conflict Detection
# -----------------------------------------------------------------------------
@router.post("/surgeries", response_model=SurgicalScheduleResponse, status_code=status.HTTP_201_CREATED)
def schedule_surgery(
    payload: SurgicalScheduleCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:write", "opd:write")),
):
    enc = db.get(PatientEncounter, payload.encounter_id)
    if not enc or enc.status != EncounterStatusEnum.ACTIVE:
        raise HTTPException(status_code=400, detail="Surgery scheduling requires an ACTIVE patient encounter.")

    theatre = db.get(OperationTheatre, payload.theatre_id)
    if not theatre or not theatre.is_active:
        raise HTTPException(status_code=404, detail="Active operation theatre not found.")

    proc = db.get(ServiceCatalog, payload.procedure_service_id)
    if not proc or not proc.is_active:
        raise HTTPException(status_code=404, detail="Active surgical procedure service not found in catalog.")

    start_utc = _to_utc(payload.scheduled_start_time)
    end_utc = _to_utc(payload.scheduled_end_time)
    if end_utc <= start_utc:
        raise HTTPException(status_code=400, detail="scheduled_end_time must be after scheduled_start_time.")

    # ARCH-004: Push overlap evaluation down to PostgreSQL to prevent OOM and race conditions
    from sqlalchemy import or_, and_
    overlap_conflict = db.execute(
        select(SurgicalSchedule).where(
            SurgicalSchedule.status != SurgeryStatusEnum.CANCELLED,
            or_(
                SurgicalSchedule.theatre_id == payload.theatre_id,
                SurgicalSchedule.lead_surgeon_id == payload.lead_surgeon_id,
            ),
            and_(
                SurgicalSchedule.scheduled_start_time < end_utc,
                SurgicalSchedule.scheduled_end_time > start_utc,
            )
        ).with_for_update()
    ).scalars().first()

    if overlap_conflict:
        if overlap_conflict.theatre_id == payload.theatre_id:
            raise HTTPException(
                status_code=409,
                detail=f"OT Scheduling Conflict: Theatre '{theatre.code}' is already booked during this window.",
            )
        if overlap_conflict.lead_surgeon_id == payload.lead_surgeon_id:
            raise HTTPException(
                status_code=409,
                detail="Surgeon Scheduling Conflict: Lead surgeon is already booked for another surgery during this window.",
            )

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    case_no = f"OT-{date_str}-{secrets.token_hex(6).upper()}"

    surgery = SurgicalSchedule(
        case_number=case_no,
        encounter_id=enc.id,
        patient_id=enc.patient_id,
        theatre_id=theatre.id,
        procedure_service_id=proc.id,
        lead_surgeon_id=payload.lead_surgeon_id,
        anesthetist_id=payload.anesthetist_id,
        scheduled_start_time=payload.scheduled_start_time,
        scheduled_end_time=payload.scheduled_end_time,
        status=SurgeryStatusEnum.SCHEDULED,
        pre_op_diagnosis_notes=payload.pre_op_diagnosis_notes,
    )
    db.add(surgery)
    db.commit()
    db.refresh(surgery)
    return surgery


# -----------------------------------------------------------------------------
# 3. WHO Surgical Safety Checklist, Operative Record & Implants
# -----------------------------------------------------------------------------
@router.put("/surgeries/{surgery_id}/checklist", response_model=WHOChecklistResponse)
def upsert_who_safety_checklist(
    surgery_id: int,
    payload: WHOChecklistUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("adt:write", "opd:write")),
):
    surgery = db.get(SurgicalSchedule, surgery_id)
    if not surgery:
        raise HTTPException(status_code=404, detail="Surgical schedule not found.")

    checklist = db.execute(
        select(WHOSurgicalSafetyChecklist).where(WHOSurgicalSafetyChecklist.surgery_id == surgery_id)
    ).scalar_one_or_none()

    if checklist:
        for field, value in payload.model_dump().items():
            setattr(checklist, field, value)
        checklist.verified_by_id = current_user.id
    else:
        checklist = WHOSurgicalSafetyChecklist(
            surgery_id=surgery_id,
            verified_by_id=current_user.id,
            **payload.model_dump(),
        )
        db.add(checklist)

    # Auto-advance SCHEDULED -> PRE_OP_CLEARED if all pre-incision checks are True
    if surgery.status == SurgeryStatusEnum.SCHEDULED and all(
        [
            payload.patient_identity_confirmed,
            payload.site_marked,
            payload.consent_signed,
            payload.pulse_oximeter_functioning,
            payload.allergy_checked,
        ]
    ):
        surgery.status = SurgeryStatusEnum.PRE_OP_CLEARED

    db.commit()
    db.refresh(checklist)
    return checklist


@router.put("/surgeries/{surgery_id}/operative-record", response_model=OperativeRecordResponse)
def upsert_operative_anesthesia_record(
    surgery_id: int,
    payload: OperativeRecordUpsert,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:write")),
):
    surgery = db.get(SurgicalSchedule, surgery_id)
    if not surgery:
        raise HTTPException(status_code=404, detail="Surgical schedule not found.")

    rec = db.execute(
        select(OperativeAnesthesiaRecord).where(OperativeAnesthesiaRecord.surgery_id == surgery_id)
    ).scalar_one_or_none()

    if rec:
        for field, value in payload.model_dump().items():
            setattr(rec, field, value)
    else:
        rec = OperativeAnesthesiaRecord(surgery_id=surgery_id, **payload.model_dump())
        db.add(rec)

    db.commit()
    db.refresh(rec)
    return rec


@router.post(
    "/surgeries/{surgery_id}/implants",
    response_model=SurgicalImplantResponse,
    status_code=status.HTTP_201_CREATED,
)
def log_surgical_implant(
    surgery_id: int,
    payload: SurgicalImplantCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:write", "opd:write")),
):
    surgery = db.get(SurgicalSchedule, surgery_id)
    if not surgery:
        raise HTTPException(status_code=404, detail="Surgical schedule not found.")
    if surgery.status in [SurgeryStatusEnum.COMPLETED, SurgeryStatusEnum.CANCELLED]:
        raise HTTPException(status_code=400, detail=f"Cannot add implants to a {surgery.status.value} surgery.")

    imp = SurgicalImplantLog(surgery_id=surgery_id, **payload.model_dump())
    db.add(imp)
    db.commit()
    db.refresh(imp)
    return imp


# -----------------------------------------------------------------------------
# 4. Surgical Status Lifecycle & Automatic Billing Trigger
# -----------------------------------------------------------------------------
@router.patch("/surgeries/{surgery_id}/status", response_model=SurgicalScheduleResponse)
def update_surgery_status(
    surgery_id: int,
    payload: SurgeryStatusUpdateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:write", "adt:write")),
):
    stmt = (
        select(SurgicalSchedule)
        .options(
            joinedload(SurgicalSchedule.safety_checklist),
            joinedload(SurgicalSchedule.operative_record),
            joinedload(SurgicalSchedule.procedure_service),
            joinedload(SurgicalSchedule.theatre),
            joinedload(SurgicalSchedule.implants),
        )
        .where(SurgicalSchedule.id == surgery_id)
    )
    surgery = db.execute(stmt).unique().scalar_one_or_none()
    if not surgery:
        raise HTTPException(status_code=404, detail="Surgical schedule not found.")

    if surgery.status in [SurgeryStatusEnum.COMPLETED, SurgeryStatusEnum.CANCELLED]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot transition surgery from terminal state '{surgery.status.value}'.",
        )

    chk = surgery.safety_checklist
    now_utc = datetime.now(timezone.utc)

    if payload.status == SurgeryStatusEnum.IN_PROGRESS:
        if not chk or not all(
            [
                chk.patient_identity_confirmed,
                chk.site_marked,
                chk.consent_signed,
                chk.pulse_oximeter_functioning,
                chk.allergy_checked,
            ]
        ):
            raise HTTPException(
                status_code=400,
                detail="WHO Safety Hard-Stop: Cannot start surgery (IN_PROGRESS) until Sign-In and Time-Out checklist items (identity, site mark, consent, pulse oximeter, allergy check) are confirmed.",
            )
        surgery.actual_start_time = now_utc

    elif payload.status == SurgeryStatusEnum.COMPLETED:
        if surgery.status != SurgeryStatusEnum.IN_PROGRESS:
            raise HTTPException(status_code=400, detail="Surgery must be IN_PROGRESS before marking COMPLETED.")
        if not chk or not chk.sponge_instrument_count_verified:
            raise HTTPException(
                status_code=400,
                detail="WHO Safety Hard-Stop: Cannot complete surgery until sponge and instrument counts are verified (Sign-Out).",
            )
        if not surgery.operative_record:
            raise HTTPException(
                status_code=400,
                detail="Cannot complete surgery without recording the Operative & Anesthesia findings.",
            )
        surgery.actual_end_time = now_utc
        auto_bill_completed_surgery(db, surgery)

    surgery.status = payload.status
    db.commit()
    db.refresh(surgery)
    return surgery


@router.get(
    "/surgeries/{surgery_id}/print",
    response_class=HTMLResponse,
    openapi_extra={"x-privacy-data-use-point": "ot.surgery.print"},
)
def print_surgical_operative_note(
    surgery_id: int,
    request: Request,
    print_mode: Optional[PrintModeEnum] = Query(default=None),
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("adt:read", "opd:read")),
):
    tenant = resolve_tenant_context(request)
    stmt = (
        select(SurgicalSchedule)
        .options(
            joinedload(SurgicalSchedule.patient),
            joinedload(SurgicalSchedule.theatre),
            joinedload(SurgicalSchedule.procedure_service),
            joinedload(SurgicalSchedule.lead_surgeon),
            joinedload(SurgicalSchedule.operative_record),
            joinedload(SurgicalSchedule.implants),
        )
        .where(SurgicalSchedule.id == surgery_id)
    )
    surgery = db.execute(stmt).unique().scalar_one_or_none()
    if not surgery:
        raise HTTPException(status_code=404, detail="Surgical schedule not found.")
    if surgery.status != SurgeryStatusEnum.COMPLETED or not surgery.operative_record:
        raise HTTPException(status_code=400, detail="Operative note can only be printed for COMPLETED surgeries.")
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="ot.surgery.print",
        patient_ids=[surgery.patient_id],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit this patient disclosure.")

    rec = surgery.operative_record
    context = {
        "tenant": {
            "hospital_name": tenant.hospital_name,
            "branch_code": tenant.branch_code,
            "printed_at": _fmt_local_time(datetime.now(timezone.utc)),
        },
        "patient": {
            "full_name": surgery.patient.full_name,
            "mrn": surgery.patient.mrn,
            "gender": surgery.patient.gender.value,
            "date_of_birth": surgery.patient.date_of_birth.strftime("%Y-%m-%d"),
        },
        "surgery": {
            "case_number": surgery.case_number,
            "theatre_name": f"{surgery.theatre.name} ({surgery.theatre.code})",
            "procedure_name": surgery.procedure_service.name,
            "surgeon_name": surgery.lead_surgeon.full_name,
        },
        "record": {
            "anesthesia_type": rec.anesthesia_type.value,
            "asa_physical_status": rec.asa_physical_status,
            "estimated_blood_loss_ml": rec.estimated_blood_loss_ml,
            "operative_findings": rec.operative_findings,
            "post_op_instructions": rec.post_op_instructions or "Standard post-op recovery protocol",
        },
        "implants": [
            {
                "implant_name": i.implant_name,
                "manufacturer": i.manufacturer or "Standard",
                "serial_or_batch_number": i.serial_or_batch_number,
                "quantity": i.quantity,
            }
            for i in surgery.implants
        ],
    }

    tpl_parts = _resolve_template_for_print(
        db, ReportTypeEnum.SURGICAL_OPERATIVE_NOTE, None, print_mode
    )
    html = _render_template_parts(tpl_parts, context)
    return HTMLResponse(content=html, status_code=200)