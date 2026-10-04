from datetime import datetime, timezone
from typing import List
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from database import get_db, resolve_tenant_context, get_control_db
from dependencies import RequirePermission
from models import (
    Bed,
    BedStatusEnum,
    BirthRecord,
    DeathRecord,
    DiagnosisMaster,
    DischargeDispositionEnum,
    EncounterStatusEnum,
    InpatientAdmission,
    Patient,
    PatientEncounter,
    ReportTypeEnum,
    User,
)
from routers.v1.federation_router import sync_patient_pointer
from routers.v1.reports_router import _fmt_ist, _resolve_template_for_print
from schemas.v1.schemas import (
    BirthRecordCreate,
    BirthRecordResponse,
    DeathRecordCreate,
    DeathRecordResponse,
)
from services.standard_templates import render_sandboxed_template

router = APIRouter(
    prefix="/api/registries",
    tags=["Phase 15B: Statutory Maternity Birth & Mortality (MCCD) Registries"],
)


def _gen_cert_no(prefix: str) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{prefix}-{date_str}-{uuid.uuid4().hex[:8].upper()}"


# =====================================================================
# 1. MATERNITY BIRTH REGISTER & AUTOMATIC NEWBORN MPI REGISTRATION
# =====================================================================

@router.post("/births", response_model=BirthRecordResponse, status_code=status.HTTP_201_CREATED)
def register_maternity_birth(
    payload: BirthRecordCreate,
    request: Request,
    db: Session = Depends(get_db),
    control_db: Session = Depends(get_control_db),
    current_user: User = Depends(RequirePermission("adt:write", "opd:write")),
):
    """
    Registers a childbirth event under the mother's encounter and automatically
    creates a linked Newborn Patient record (with MRN-NB-...) in the MPI for live births.
    """
    enc = (
        db.execute(
            select(PatientEncounter)
            .options(joinedload(PatientEncounter.patient))
            .where(PatientEncounter.id == payload.mother_encounter_id)
        )
        .scalar_one_or_none()
    )
    if not enc or enc.status != EncounterStatusEnum.ACTIVE:
        raise HTTPException(status_code=400, detail="Active mother encounter is required to register a birth.")

    mother = enc.patient
    baby_name = payload.baby_full_name or f"Baby of {mother.full_name}"
    newborn_id = None
    newborn_mrn = None
    newborn = None

    if not payload.is_stillborn:
        newborn_mrn = f"MRN-NB-{uuid.uuid4().hex[:8].upper()}"
        newborn = Patient(
            mrn=newborn_mrn,
            full_name=baby_name,
            date_of_birth=payload.time_of_birth,
            gender=payload.baby_gender,
            blood_group=payload.baby_blood_group,
            phone_number=mother.phone_number,
            address=mother.address,
            emergency_contact_name=mother.full_name,
            emergency_contact_phone=mother.phone_number,
            is_active=True,
        )
        db.add(newborn)
        db.flush()
        newborn_id = newborn.id

    rec = BirthRecord(
        certificate_number=_gen_cert_no("BIRTH"),
        mother_encounter_id=enc.id,
        mother_patient_id=mother.id,
        newborn_patient_id=newborn_id,
        attending_doctor_id=current_user.id,
        baby_full_name=baby_name,
        baby_gender=payload.baby_gender,
        baby_blood_group=payload.baby_blood_group,
        time_of_birth=payload.time_of_birth,
        delivery_mode=payload.delivery_mode,
        birth_weight_kg=payload.birth_weight_kg,
        apgar_1min=payload.apgar_1min,
        apgar_5min=payload.apgar_5min,
        is_stillborn=payload.is_stillborn,
        clinical_notes=payload.clinical_notes,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    if newborn is not None:
        tenant = resolve_tenant_context(request)
        sync_patient_pointer(
            control_db=control_db,
            branch_code=tenant.branch_code,
            subdomain_slug=tenant.subdomain_slug,
            patient=newborn,
            has_critical_allergies=False,
            tenant_db=db,
        )

    resp = BirthRecordResponse.model_validate(rec)
    resp.newborn_mrn = newborn_mrn
    return resp


@router.get("/births", response_model=List[BirthRecordResponse])
def list_birth_records(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read", "opd:read")),
):
    stmt = (
        select(BirthRecord)
        .options(joinedload(BirthRecord.newborn_patient))
        .order_by(BirthRecord.id.desc())
    )
    records = list(db.execute(stmt).scalars().all())
    results = []
    for r in records:
        item = BirthRecordResponse.model_validate(r)
        item.newborn_mrn = r.newborn_patient.mrn if r.newborn_patient else None
        results.append(item)
    return results


@router.get("/births/{birth_id}/print", response_class=HTMLResponse)
def print_official_birth_certificate(
    birth_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read", "opd:read")),
):
    tenant = resolve_tenant_context(request)
    stmt = (
        select(BirthRecord)
        .options(
            joinedload(BirthRecord.mother_patient),
            joinedload(BirthRecord.newborn_patient),
            joinedload(BirthRecord.attending_doctor),
        )
        .where(BirthRecord.id == birth_id)
    )
    rec = db.execute(stmt).scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="Birth record not found.")

    context = {
        "tenant": {
            "hospital_name": tenant.hospital_name,
            "branch_code": tenant.branch_code,
            "printed_at": _fmt_ist(datetime.now(timezone.utc)),
        },
        "patient": {
            "full_name": rec.baby_full_name,
            "mrn": rec.newborn_patient.mrn if rec.newborn_patient else "STILLBORN",
            "gender": rec.baby_gender.value,
            "date_of_birth": rec.time_of_birth.strftime("%Y-%m-%d"),
        },
        "mother": {
            "full_name": rec.mother_patient.full_name,
            "mrn": rec.mother_patient.mrn,
        },
        "birth": {
            "certificate_number": rec.certificate_number,
            "baby_full_name": rec.baby_full_name,
            "newborn_mrn": rec.newborn_patient.mrn if rec.newborn_patient else "N/A",
            "baby_gender": rec.baby_gender.value,
            "time_of_birth": _fmt_ist(rec.time_of_birth),
            "delivery_mode": rec.delivery_mode.value,
            "birth_weight_kg": rec.birth_weight_kg,
            "apgar_1min": rec.apgar_1min,
            "apgar_5min": rec.apgar_5min,
            "doctor_name": rec.attending_doctor.full_name,
        },
    }
    tpl_parts = _resolve_template_for_print(db, ReportTypeEnum.BIRTH_CERTIFICATE, None)
    html = render_sandboxed_template(
        tpl_parts["html_template"],
        tpl_parts["css_styles"],
        tpl_parts["header_html"],
        tpl_parts["footer_html"],
        context,
    )
    return HTMLResponse(content=html, status_code=200)


# =====================================================================
# 2. STATUTORY MORTALITY & FORM 4 MCCD DEATH CERTIFICATE REGISTER
# =====================================================================

@router.post("/deaths", response_model=DeathRecordResponse, status_code=status.HTTP_201_CREATED)
def certify_patient_death_mccd(
    payload: DeathRecordCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("opd:write")),
):
    """
    Certifies patient mortality (restricted to Doctors/Admins with opd:write),
    records Form 4 MCCD ICD-10 cause of death, releases any occupied IPD bed to HOUSEKEEPING,
    and closes the encounter.
    """
    enc = (
        db.execute(
            select(PatientEncounter)
            .options(joinedload(PatientEncounter.admissions))
            .where(PatientEncounter.id == payload.encounter_id)
            .with_for_update()
        )
        .unique()
        .scalar_one_or_none()
    )
    if not enc:
        raise HTTPException(status_code=404, detail="Encounter not found.")

    dup = db.execute(select(DeathRecord).where(DeathRecord.patient_id == enc.patient_id)).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail="A death certificate is already registered for this patient.")

    diag = db.get(DiagnosisMaster, payload.immediate_cause_diagnosis_id)
    if not diag or not diag.is_active:
        raise HTTPException(status_code=404, detail="Active ICD-10 cause-of-death diagnosis not found.")

    # Automatically close any active IPD admission and release bed to HOUSEKEEPING
    for adm in enc.admissions:
        if adm.discharge_time is None:
            adm.discharge_time = payload.time_of_death
            adm.discharge_disposition = DischargeDispositionEnum.DECEASED
            adm.discharge_summary = f"Patient declared deceased at {payload.time_of_death.isoformat()}. Cause: {diag.icd10_code}"
            if adm.current_bed_id:
                bed = db.get(Bed, adm.current_bed_id)
                if bed:
                    bed.status = BedStatusEnum.HOUSEKEEPING
                    bed.current_admission_id = None
                adm.current_bed_id = None

    enc.status = EncounterStatusEnum.DISCHARGED
    enc.end_time = payload.time_of_death

    rec = DeathRecord(
        certificate_number=_gen_cert_no("MCCD"),
        encounter_id=enc.id,
        patient_id=enc.patient_id,
        certified_by_doctor_id=current_user.id,
        immediate_cause_diagnosis_id=diag.id,
        time_of_death=payload.time_of_death,
        manner_of_death=payload.manner_of_death,
        antecedent_cause_notes=payload.antecedent_cause_notes,
        is_autopsy_advised=payload.is_autopsy_advised,
        body_handed_over_to=payload.body_handed_over_to,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


@router.get("/deaths/{death_id}/print", response_class=HTMLResponse)
def print_mccd_death_certificate(
    death_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read", "opd:read")),
):
    tenant = resolve_tenant_context(request)
    stmt = (
        select(DeathRecord)
        .options(
            joinedload(DeathRecord.patient),
            joinedload(DeathRecord.certified_by_doctor),
            joinedload(DeathRecord.immediate_cause_diagnosis),
        )
        .where(DeathRecord.id == death_id)
    )
    rec = db.execute(stmt).scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="Death record not found.")

    context = {
        "tenant": {
            "hospital_name": tenant.hospital_name,
            "branch_code": tenant.branch_code,
            "printed_at": _fmt_ist(datetime.now(timezone.utc)),
        },
        "patient": {
            "full_name": rec.patient.full_name,
            "mrn": rec.patient.mrn,
            "gender": rec.patient.gender.value,
            "date_of_birth": rec.patient.date_of_birth.strftime("%Y-%m-%d"),
        },
        "death": {
            "certificate_number": rec.certificate_number,
            "time_of_death": _fmt_ist(rec.time_of_death),
            "manner_of_death": rec.manner_of_death.value,
            "icd10_code": rec.immediate_cause_diagnosis.icd10_code,
            "diagnosis_description": rec.immediate_cause_diagnosis.short_description,
            "antecedent_cause_notes": rec.antecedent_cause_notes or "None recorded",
            "is_autopsy_advised": "YES" if rec.is_autopsy_advised else "NO",
            "body_handed_over_to": rec.body_handed_over_to or "Pending",
            "doctor_name": rec.certified_by_doctor.full_name,
        },
    }
    tpl_parts = _resolve_template_for_print(db, ReportTypeEnum.DEATH_CERTIFICATE_MCCD, None)
    html = render_sandboxed_template(
        tpl_parts["html_template"],
        tpl_parts["css_styles"],
        tpl_parts["header_html"],
        tpl_parts["footer_html"],
        context,
    )
    return HTMLResponse(content=html, status_code=200)