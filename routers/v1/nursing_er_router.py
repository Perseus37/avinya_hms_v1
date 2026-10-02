from datetime import datetime, timezone
from typing import List
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from database import get_db, resolve_tenant_context
from dependencies import RequirePermission
from models import (
    ERTriageAssessment,
    EncounterStatusEnum,
    EncounterTypeEnum,
    FluidBalanceLog,
    FluidDirectionEnum,
    MedicationAdministrationLog,
    MedicoLegalCase,
    PatientEncounter,
    PrescriptionItem,
    ReportTypeEnum,
    User,
    eMARStatusEnum,
)
from routers.v1.reports_router import _fmt_ist, _resolve_template_for_print
from schemas.v1.schemas import (
    ERTriageCreate,
    ERTriageResponse,
    FluidBalanceSummaryResponse,
    FluidLogCreate,
    FluidLogResponse,
    MLCCreate,
    MLCPoliceIntimationRequest,
    MLCResponse,
    eMARCreate,
    eMARResponse,
)
from services.cpoe import check_allergy_hard_stop
from services.standard_templates import render_sandboxed_template

router = APIRouter(prefix="/api", tags=["Inpatient Nursing (eMAR & Fluid I/O) & Emergency (ER Triage & MLC)"])


def _get_active_encounter(db: Session, encounter_id: int) -> PatientEncounter:
    enc = db.get(PatientEncounter, encounter_id)
    if not enc:
        raise HTTPException(status_code=404, detail="Encounter not found.")
    if enc.status != EncounterStatusEnum.ACTIVE:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot record clinical nursing or ER actions on a {enc.status.value} encounter.",
        )
    return enc


# =====================================================================
# 1. INPATIENT NURSING eMAR (MEDICATION ADMINISTRATION RECORD)
# =====================================================================

@router.post(
    "/nursing/encounters/{encounter_id}/emar",
    response_model=eMARResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_emar_administration(
    encounter_id: int,
    payload: eMARCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("adt:write", "pharmacy:dispense")),
):
    """
    Records bedside medication administration by a nurse or doctor.
    Re-runs CPOE allergy hard-stop at the moment of administration in case a new drug allergy
    was discovered after the prescription was originally written.
    """
    enc = _get_active_encounter(db, encounter_id)

    stmt = (
        select(PrescriptionItem)
        .options(
            joinedload(PrescriptionItem.prescription),
            joinedload(PrescriptionItem.medication),
        )
        .where(PrescriptionItem.id == payload.prescription_item_id)
    )
    rx_item = db.execute(stmt).scalar_one_or_none()
    if not rx_item or rx_item.prescription.encounter_id != encounter_id:
        raise HTTPException(
            status_code=400,
            detail="Prescription item does not exist or does not belong to this encounter.",
        )

    if payload.status in [eMARStatusEnum.HELD, eMARStatusEnum.REFUSED, eMARStatusEnum.MISSED]:
        if not payload.notes or len(payload.notes.strip()) < 3:
            raise HTTPException(
                status_code=400,
                detail=f"Mandatory clinical notes are required when marking a dose as {payload.status.value}.",
            )

    if payload.status == eMARStatusEnum.ADMINISTERED:
        med = rx_item.medication
        check_allergy_hard_stop(db, enc.patient_id, [med.brand_name, med.generic_name, med.code])

    log = MedicationAdministrationLog(
        encounter_id=encounter_id,
        prescription_item_id=rx_item.id,
        administered_by_id=current_user.id,
        scheduled_time=payload.scheduled_time,
        status=payload.status,
        dose_given=payload.dose_given,
        notes=payload.notes,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


@router.get("/nursing/encounters/{encounter_id}/emar", response_model=List[eMARResponse])
def list_encounter_emar_logs(
    encounter_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read", "opd:read")),
):
    if not db.get(PatientEncounter, encounter_id):
        raise HTTPException(status_code=404, detail="Encounter not found.")
    stmt = (
        select(MedicationAdministrationLog)
        .where(MedicationAdministrationLog.encounter_id == encounter_id)
        .order_by(MedicationAdministrationLog.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


# =====================================================================
# 2. INPATIENT FLUID INTAKE / OUTPUT (I/O) BALANCE CHART
# =====================================================================

@router.post(
    "/nursing/encounters/{encounter_id}/fluid-logs",
    response_model=FluidLogResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_fluid_balance_log(
    encounter_id: int,
    payload: FluidLogCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("adt:write")),
):
    _get_active_encounter(db, encounter_id)
    f_log = FluidBalanceLog(
        encounter_id=encounter_id,
        recorded_by_id=current_user.id,
        direction=payload.direction,
        fluid_type=payload.fluid_type,
        volume_ml=payload.volume_ml,
        notes=payload.notes,
    )
    db.add(f_log)
    db.commit()
    db.refresh(f_log)
    return f_log


@router.get(
    "/nursing/encounters/{encounter_id}/fluid-balance",
    response_model=FluidBalanceSummaryResponse,
)
def get_encounter_fluid_balance_summary(
    encounter_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read", "opd:read")),
):
    if not db.get(PatientEncounter, encounter_id):
        raise HTTPException(status_code=404, detail="Encounter not found.")

    logs = list(
        db.execute(
            select(FluidBalanceLog)
            .where(FluidBalanceLog.encounter_id == encounter_id)
            .order_by(FluidBalanceLog.id.asc())
        )
        .scalars()
        .all()
    )
    intake_ml = sum(l.volume_ml for l in logs if l.direction == FluidDirectionEnum.INTAKE)
    output_ml = sum(l.volume_ml for l in logs if l.direction == FluidDirectionEnum.OUTPUT)

    return FluidBalanceSummaryResponse(
        encounter_id=encounter_id,
        total_intake_ml=intake_ml,
        total_output_ml=output_ml,
        net_balance_ml=intake_ml - output_ml,
        logs=logs,
    )


# =====================================================================
# 3. EMERGENCY ROOM (ER) 5-LEVEL ACUITY TRIAGE & MEDICO-LEGAL CASES (MLC)
# =====================================================================

@router.post(
    "/er/encounters/{encounter_id}/triage",
    response_model=ERTriageResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_er_triage_assessment(
    encounter_id: int,
    payload: ERTriageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("adt:write", "opd:write")),
):
    enc = _get_active_encounter(db, encounter_id)
    if enc.encounter_type != EncounterTypeEnum.ER:
        raise HTTPException(
            status_code=400,
            detail=f"ER Triage assessment can only be recorded on an ER encounter (current: {enc.encounter_type.value}).",
        )

    existing = db.execute(
        select(ERTriageAssessment).where(ERTriageAssessment.encounter_id == encounter_id)
    ).scalar_one_or_none()

    if existing:
        for field, value in payload.model_dump().items():
            setattr(existing, field, value)
        existing.triaged_by_id = current_user.id
        db.commit()
        db.refresh(existing)
        return existing

    triage = ERTriageAssessment(
        encounter_id=encounter_id,
        triaged_by_id=current_user.id,
        **payload.model_dump(),
    )
    db.add(triage)
    db.commit()
    db.refresh(triage)
    return triage


@router.get("/er/triage-queue", response_model=List[ERTriageResponse])
def get_er_triage_queue(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read", "opd:read")),
):
    """Returns active ER triage cases ordered by clinical priority."""
    stmt = (
        select(ERTriageAssessment)
        .join(ERTriageAssessment.encounter)
        .where(PatientEncounter.status == EncounterStatusEnum.ACTIVE)
        .order_by(ERTriageAssessment.id.asc())
    )
    all_active = list(db.execute(stmt).scalars().all())
    priority_rank = {
        "RED_RESUSCITATION": 1,
        "ORANGE_EMERGENT": 2,
        "YELLOW_URGENT": 3,
        "GREEN_LESS_URGENT": 4,
        "BLACK_DECEASED": 5,
    }
    all_active.sort(key=lambda t: (priority_rank.get(t.acuity_level.value, 99), t.id))
    return all_active


@router.post("/er/mlc", response_model=MLCResponse, status_code=status.HTTP_201_CREATED)
def register_medico_legal_case(
    payload: MLCCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("opd:write")),
):
    """Registers an official Medico-Legal Case (MLC). Restricted to Doctors/Admins (opd:write)."""
    enc = _get_active_encounter(db, payload.encounter_id)

    dup = db.execute(
        select(MedicoLegalCase).where(MedicoLegalCase.encounter_id == payload.encounter_id)
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail="An MLC is already registered for this encounter.")

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    mlc_num = f"MLC-{date_str}-{uuid.uuid4().hex[:8].upper()}"

    mlc = MedicoLegalCase(
        mlc_number=mlc_num,
        encounter_id=enc.id,
        patient_id=enc.patient_id,
        registered_by_doctor_id=current_user.id,
        cause_of_injury=payload.cause_of_injury,
        alleged_history=payload.alleged_history,
        identification_marks=payload.identification_marks,
        brought_by_name=payload.brought_by_name,
        brought_by_relation=payload.brought_by_relation,
        brought_by_phone=payload.brought_by_phone,
        police_station_name=payload.police_station_name,
        police_constable_buckle_no=payload.police_constable_buckle_no,
    )
    db.add(mlc)
    db.commit()
    db.refresh(mlc)
    return mlc


@router.patch("/er/mlc/{mlc_id}/police-intimation", response_model=MLCResponse)
def record_mlc_police_intimation(
    mlc_id: int,
    payload: MLCPoliceIntimationRequest,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:write", "adt:write")),
):
    """Stamps official police intimation timestamp and constable buckle number (locks against re-intimation)."""
    mlc = db.get(MedicoLegalCase, mlc_id)
    if not mlc:
        raise HTTPException(status_code=404, detail="Medico-Legal Case not found.")
    if mlc.police_intimated_at is not None:
        raise HTTPException(
            status_code=409,
            detail="Forensic Lock: Police intimation has already been recorded for this MLC.",
        )

    mlc.police_constable_buckle_no = payload.police_constable_buckle_no
    mlc.fir_number = payload.fir_number
    mlc.police_intimated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(mlc)
    return mlc


@router.get("/er/mlc/{mlc_id}/print", response_class=HTMLResponse)
def print_mlc_police_intimation_report(
    mlc_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read", "opd:read")),
):
    tenant = resolve_tenant_context(request)
    stmt = (
        select(MedicoLegalCase)
        .options(
            joinedload(MedicoLegalCase.patient),
            joinedload(MedicoLegalCase.registered_by_doctor),
        )
        .where(MedicoLegalCase.id == mlc_id)
    )
    mlc = db.execute(stmt).scalar_one_or_none()
    if not mlc:
        raise HTTPException(status_code=404, detail="Medico-Legal Case not found.")

    context = {
        "tenant": {
            "hospital_name": tenant.hospital_name,
            "branch_code": tenant.branch_code,
            "printed_at": _fmt_ist(datetime.now(timezone.utc)),
        },
        "patient": {
            "full_name": mlc.patient.full_name,
            "mrn": mlc.patient.mrn,
            "gender": mlc.patient.gender.value,
            "date_of_birth": mlc.patient.date_of_birth.strftime("%Y-%m-%d"),
        },
        "mlc": {
            "mlc_number": mlc.mlc_number,
            "cause_of_injury": mlc.cause_of_injury.value,
            "alleged_history": mlc.alleged_history,
            "identification_marks": mlc.identification_marks,
            "brought_by_name": mlc.brought_by_name,
            "brought_by_relation": mlc.brought_by_relation or "N/A",
            "police_station_name": mlc.police_station_name,
            "police_constable_buckle_no": mlc.police_constable_buckle_no or "Pending",
            "fir_number": mlc.fir_number or "Pending FIR",
            "police_intimated_at": _fmt_ist(mlc.police_intimated_at),
            "doctor_name": mlc.registered_by_doctor.full_name,
        },
    }

    tpl_parts = _resolve_template_for_print(db, ReportTypeEnum.MLC_POLICE_REPORT, None)
    html = render_sandboxed_template(
        tpl_parts["html_template"],
        tpl_parts["css_styles"],
        tpl_parts["header_html"],
        tpl_parts["footer_html"],
        context,
    )
    return HTMLResponse(content=html, status_code=200)