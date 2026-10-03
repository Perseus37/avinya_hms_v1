from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import RequirePermission, validate_pagination
from models import (
    Bed,
    BedStatusEnum,
    BedTransferLog,
    EncounterStatusEnum,
    EncounterTypeEnum,
    InpatientAdmission,
    PatientEncounter,
    User,
    DischargeDispositionEnum,
    InsurancePreAuth,
    Invoice,
    InvoiceStatusEnum,
    PreAuthStatusEnum,
)
from schemas.v1.schemas import (
    BedTransferRequest,
    BedTransferResponse,
    ClinicalDischargeRequest,
    InpatientAdmissionCreate,
    InpatientAdmissionResponse,
    InpatientAdmissionUpdate,
    PaginatedResponse,
    PatientEncounterCreate,
    PatientEncounterResponse,
    PatientEncounterUpdate,
)
from services.auto_billing import auto_bill_ipd_bed_stay

router = APIRouter(prefix="/api/adt", tags=["Admissions, Discharges & Transfers (ADT)"])


# -----------------------------------------------------------------------------
# Encounters (Episodes of Care)
# -----------------------------------------------------------------------------
@router.post("/encounters", response_model=PatientEncounterResponse, status_code=status.HTTP_201_CREATED)
def create_encounter(
    payload: PatientEncounterCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:write")),
):
    active_encounter = (
        db.query(PatientEncounter)
        .filter(
            PatientEncounter.patient_id == payload.patient_id,
            PatientEncounter.encounter_type == payload.encounter_type,
            PatientEncounter.status == EncounterStatusEnum.ACTIVE,
        )
        .first()
    )
    if active_encounter:
        raise HTTPException(status_code=409, detail=f"Patient already has an active {payload.encounter_type.value} encounter.")

    encounter = PatientEncounter(**payload.model_dump(), status=EncounterStatusEnum.ACTIVE)
    db.add(encounter)
    db.commit()
    db.refresh(encounter)
    return encounter


@router.get("/encounters/active", response_model=PaginatedResponse[PatientEncounterResponse])
def list_active_encounters(
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(PatientEncounter).filter(PatientEncounter.status == EncounterStatusEnum.ACTIVE).order_by(PatientEncounter.id.desc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/encounters/{encounter_id}", response_model=PatientEncounterResponse)
def get_encounter(
    encounter_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read")),
):
    encounter = db.get(PatientEncounter, encounter_id)
    if not encounter:
        raise HTTPException(status_code=404, detail="Encounter not found.")
    return encounter


@router.put("/encounters/{encounter_id}", response_model=PatientEncounterResponse)
def update_encounter(
    encounter_id: int,
    payload: PatientEncounterUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:write")),
):
    encounter = db.get(PatientEncounter, encounter_id)
    if not encounter:
        raise HTTPException(status_code=404, detail="Encounter not found.")
    if encounter.status != EncounterStatusEnum.ACTIVE:
        raise HTTPException(status_code=400, detail="Cannot modify a closed or cancelled encounter.")

    if payload.primary_doctor_id is not None:
        encounter.primary_doctor_id = payload.primary_doctor_id
    if payload.department_id is not None:
        encounter.department_id = payload.department_id
    if payload.chief_complaint_notes is not None:
        encounter.chief_complaint_notes = payload.chief_complaint_notes

    db.commit()
    db.refresh(encounter)
    return encounter


@router.delete("/encounters/{encounter_id}", status_code=200)
def cancel_encounter(
    encounter_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:discharge")),
):
    encounter = db.get(PatientEncounter, encounter_id)
    if not encounter:
        raise HTTPException(status_code=404, detail="Encounter not found.")
    if encounter.status != EncounterStatusEnum.ACTIVE:
        raise HTTPException(status_code=400, detail=f"Cannot cancel an encounter that is currently {encounter.status.value}.")
    if any(adm.discharge_time is None for adm in encounter.admissions):
        raise HTTPException(status_code=400, detail="Cannot cancel encounter. Patient has an active admission.")

    encounter.status = EncounterStatusEnum.CANCELLED
    encounter.end_time = datetime.now(timezone.utc)
    db.commit()
    return {"status": "success", "message": f"Encounter {encounter_id} cancelled."}


# -----------------------------------------------------------------------------
# IPD Admissions & Bed Assignments
# -----------------------------------------------------------------------------
@router.post("/admissions", response_model=InpatientAdmissionResponse, status_code=status.HTTP_201_CREATED)
def admit_patient(
    payload: InpatientAdmissionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("adt:write")),
):
    encounter = db.get(PatientEncounter, payload.encounter_id)
    if not encounter or encounter.status != EncounterStatusEnum.ACTIVE:
        raise HTTPException(status_code=400, detail="Valid, active encounter required for IPD admission.")
    if encounter.encounter_type != EncounterTypeEnum.IPD:
        raise HTTPException(status_code=400, detail="Only IPD encounters can be admitted to a bed.")

    try:
        stmt = select(Bed).where(Bed.id == payload.assigned_bed_id).with_for_update(nowait=True)
        bed = db.execute(stmt).scalar_one_or_none()
        if not bed:
            raise HTTPException(status_code=404, detail="Requested bed not found.")
        if bed.status != BedStatusEnum.AVAILABLE:
            raise HTTPException(status_code=409, detail=f"Bed {bed.bed_number} is currently {bed.status.value}.")

        admission = InpatientAdmission(
            encounter_id=payload.encounter_id,
            admission_type=payload.admission_type,
            current_bed_id=bed.id,
            admitting_doctor_id=payload.admitting_doctor_id,
        )
        db.add(admission)
        db.flush()

        bed.status = BedStatusEnum.OCCUPIED
        bed.current_admission_id = admission.id

        transfer_log = BedTransferLog(
            admission_id=admission.id,
            from_bed_id=None,
            to_bed_id=bed.id,
            requested_by_id=current_user.id,
            transfer_reason="Initial IPD Admission",
        )
        db.add(transfer_log)

        db.commit()
        db.refresh(admission)
        return admission
    except OperationalError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Bed assignment failed. Bed is locked by a concurrent transaction.")


@router.get("/admissions", response_model=PaginatedResponse[InpatientAdmissionResponse])
def list_admissions(
    patient_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(InpatientAdmission)
    if patient_id:
        query = query.join(InpatientAdmission.encounter).filter(PatientEncounter.patient_id == patient_id)
    query = query.order_by(InpatientAdmission.id.desc())

    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/admissions/{admission_id}", response_model=InpatientAdmissionResponse)
def get_admission(
    admission_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read")),
):
    admission = db.get(InpatientAdmission, admission_id)
    if not admission:
        raise HTTPException(status_code=404, detail="Admission not found.")
    return admission


@router.put("/admissions/{admission_id}", response_model=InpatientAdmissionResponse)
def update_admission(
    admission_id: int,
    payload: InpatientAdmissionUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:write")),
):
    admission = db.get(InpatientAdmission, admission_id)
    if not admission:
        raise HTTPException(status_code=404, detail="Admission not found.")
    if admission.discharge_time is not None:
        raise HTTPException(status_code=400, detail="Cannot modify a discharged admission.")

    if payload.admission_type is not None:
        admission.admission_type = payload.admission_type
    if payload.admitting_doctor_id is not None:
        admission.admitting_doctor_id = payload.admitting_doctor_id

    db.commit()
    db.refresh(admission)
    return admission


@router.delete("/admissions/{admission_id}", status_code=200)
def cancel_admission(
    admission_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:discharge")),
):
    admission = db.get(InpatientAdmission, admission_id)
    if not admission:
        raise HTTPException(status_code=404, detail="Admission not found.")
    if admission.discharge_time is not None:
        raise HTTPException(status_code=400, detail="Cannot cancel an already discharged admission.")

    try:
        if admission.current_bed_id:
            stmt = select(Bed).where(Bed.id == admission.current_bed_id).with_for_update(nowait=True)
            bed = db.execute(stmt).scalar_one_or_none()
            if bed:
                bed.status = BedStatusEnum.AVAILABLE
                bed.current_admission_id = None
            admission.current_bed_id = None

        db.delete(admission)
        db.commit()
        return {"status": "success", "message": f"Admission {admission_id} cancelled. Bed released to AVAILABLE."}
    except OperationalError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Failed to release bed due to concurrent lock.")


# -----------------------------------------------------------------------------
# Bed Transfers
# -----------------------------------------------------------------------------
@router.post("/transfers", response_model=BedTransferResponse, status_code=status.HTTP_201_CREATED)
def transfer_patient_bed(
    payload: BedTransferRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("adt:write")),
):
    admission = db.get(InpatientAdmission, payload.admission_id)
    if not admission or admission.discharge_time is not None:
        raise HTTPException(status_code=400, detail="Active admission required for bed transfer.")

    old_bed_id = admission.current_bed_id
    if old_bed_id == payload.new_bed_id:
        raise HTTPException(status_code=400, detail="Patient is already in the requested bed.")

    try:
        stmt = select(Bed).where(Bed.id == payload.new_bed_id).with_for_update(nowait=True)
        new_bed = db.execute(stmt).scalar_one_or_none()
        if not new_bed or new_bed.status != BedStatusEnum.AVAILABLE:
            raise HTTPException(status_code=409, detail="Requested destination bed is unavailable.")

        stmt_old = select(Bed).where(Bed.id == old_bed_id).with_for_update(nowait=True)
        old_bed = db.execute(stmt_old).scalar_one_or_none()

        admission.current_bed_id = new_bed.id
        if old_bed:
            old_bed.status = BedStatusEnum.HOUSEKEEPING
            old_bed.current_admission_id = None

        new_bed.status = BedStatusEnum.OCCUPIED
        new_bed.current_admission_id = admission.id

        transfer_log = BedTransferLog(
            admission_id=admission.id,
            from_bed_id=old_bed_id,
            to_bed_id=new_bed.id,
            requested_by_id=current_user.id,
            transfer_reason=payload.transfer_reason,
        )
        db.add(transfer_log)
        db.commit()
        db.refresh(transfer_log)
        return transfer_log
    except OperationalError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Transfer failed due to concurrent bed locks.")


@router.get("/admissions/{admission_id}/transfers", response_model=PaginatedResponse[BedTransferResponse])
def list_admission_transfers(
    admission_id: int,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(BedTransferLog).filter(BedTransferLog.admission_id == admission_id).order_by(BedTransferLog.id.asc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# -----------------------------------------------------------------------------
# Clinical Discharge
# -----------------------------------------------------------------------------
@router.post("/admissions/{admission_id}/discharge", status_code=200)
def discharge_patient(
    admission_id: int,
    payload: ClinicalDischargeRequest,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:discharge")),
):
    try:
        stmt = (
            select(InpatientAdmission)
            .options(joinedload(InpatientAdmission.encounter))
            .where(InpatientAdmission.id == admission_id)
            .with_for_update(nowait=True)
        )
        admission = db.execute(stmt).scalar_one_or_none()
    except OperationalError:
        raise HTTPException(status_code=409, detail="Admission record is currently locked.")

    if not admission or admission.discharge_time is not None:
        raise HTTPException(status_code=400, detail="Admission record is invalid or already discharged.")

    # Phase 15B: Financial Clearance Gate ("No-Dues Gate") before routine discharge
    if not payload.force_financial_override and payload.discharge_disposition not in [
        DischargeDispositionEnum.DECEASED,
        DischargeDispositionEnum.AGAINST_MEDICAL_ADVICE,
    ]:
        unpaid_invoices = list(
            db.execute(
                select(Invoice).where(
                    Invoice.encounter_id == admission.encounter_id,
                    Invoice.status.in_([InvoiceStatusEnum.UNPAID, InvoiceStatusEnum.PARTIAL]),
                )
            )
            .scalars()
            .all()
        )
        unsettled_balance_paise = sum(
            max(0, inv.net_amount_paise - inv.amount_paid_paise) for inv in unpaid_invoices
        )
        if unsettled_balance_paise > 0:
            approved_preauth = db.execute(
                select(InsurancePreAuth).where(
                    InsurancePreAuth.encounter_id == admission.encounter_id,
                    InsurancePreAuth.status == PreAuthStatusEnum.APPROVED,
                    InsurancePreAuth.approved_amount_paise >= unsettled_balance_paise,
                )
            ).scalar_one_or_none()
            if not approved_preauth:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Financial Clearance Gate: Cannot complete routine discharge while finalized invoice has an "
                        f"unsettled balance of {unsettled_balance_paise} paise. Settle bill, obtain cashless pre-auth approval, "
                        f"or pass force_financial_override=True."
                    ),
                )
            
    current_time = datetime.now(timezone.utc)
    admission.discharge_time = current_time
    admission.discharge_disposition = payload.discharge_disposition
    admission.discharge_summary = payload.discharge_summary

    if admission.current_bed_id:
        try:
            stmt_bed = select(Bed).where(Bed.id == admission.current_bed_id).with_for_update(nowait=True)
            bed = db.execute(stmt_bed).scalar_one_or_none()
            if bed:
                bed.status = BedStatusEnum.HOUSEKEEPING
                bed.current_admission_id = None
            admission.current_bed_id = None
        except OperationalError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Failed to release bed to housekeeping.")

    if admission.encounter:
        admission.encounter.status = EncounterStatusEnum.DISCHARGED
        admission.encounter.end_time = current_time

    auto_bill_ipd_bed_stay(db, admission, current_time)

    db.commit()
    return {"status": "success", "message": "Patient discharged. Bed released to housekeeping."}