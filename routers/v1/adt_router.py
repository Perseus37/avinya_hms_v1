from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select

from database import get_db
from dependencies import RequirePermission, get_current_user
from models import (
    User, PatientEncounter, InpatientAdmission, BedTransferLog, Bed, 
    BedStatusEnum, EncounterStatusEnum, EncounterTypeEnum, TaskStatusEnum
)
from schemas.v1.schemas import (
    PaginatedResponse, PatientEncounterCreate, PatientEncounterResponse,
    InpatientAdmissionCreate, InpatientAdmissionResponse, BedTransferRequest, 
    BedTransferResponse, ClinicalDischargeRequest
)

router = APIRouter(prefix="/api/adt", tags=["Admissions, Discharges & Transfers (ADT)"])

# -----------------------------------------------------------------------------
# Encounters (Episodes of Care)
# -----------------------------------------------------------------------------

@router.post("/encounters", response_model=PatientEncounterResponse, status_code=status.HTTP_201_CREATED)
def create_encounter(payload: PatientEncounterCreate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("write_cpoe_orders"))):
    """Initiates a new episode of care for a patient (OPD, IPD, or ER)."""
    # Verify patient doesn't already have an active encounter of the same type
    active_encounter = db.query(PatientEncounter).filter(
        PatientEncounter.patient_id == payload.patient_id,
        PatientEncounter.status == EncounterStatusEnum.ACTIVE
    ).first()
    
    if active_encounter:
        raise HTTPException(status_code=409, detail=f"Patient already has an active {active_encounter.encounter_type} encounter.")

    encounter = PatientEncounter(
        patient_id=payload.patient_id,
        encounter_type=payload.encounter_type,
        primary_doctor_id=payload.primary_doctor_id,
        department_id=payload.department_id,
        chief_complaint_notes=payload.chief_complaint_notes,
        status=EncounterStatusEnum.ACTIVE
    )
    db.add(encounter)
    db.commit()
    db.refresh(encounter)
    return encounter

@router.get("/encounters/active", response_model=PaginatedResponse[PatientEncounterResponse])
def list_active_encounters(page: int = 1, page_size: int = 50, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("write_cpoe_orders"))):
    """Retrieves all currently active patient encounters across the hospital."""
    query = db.query(PatientEncounter).filter(PatientEncounter.status == EncounterStatusEnum.ACTIVE)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}

# -----------------------------------------------------------------------------
# IPD Admissions & Bed Assignments
# -----------------------------------------------------------------------------

@router.post("/admissions", response_model=InpatientAdmissionResponse, status_code=status.HTTP_201_CREATED)
def admit_patient(payload: InpatientAdmissionCreate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("view_ward_map"))):
    """Admits a patient to a ward. Locks the requested bed and prevents race conditions."""
    encounter = db.get(PatientEncounter, payload.encounter_id)
    if not encounter or encounter.status != EncounterStatusEnum.ACTIVE:
        raise HTTPException(status_code=400, detail="Valid, active encounter required for IPD admission.")

    try:
        # 1. Concurrency Lock: Lock the bed to prevent double-booking
        stmt = select(Bed).where(Bed.id == payload.assigned_bed_id).with_for_update(nowait=True)
        bed = db.execute(stmt).scalar_one_or_none()

        if not bed:
            raise HTTPException(status_code=404, detail="Requested bed not found.")
        if bed.status != BedStatusEnum.AVAILABLE:
            raise HTTPException(status_code=409, detail=f"Bed {bed.bed_number} is currently {bed.status.value}.")

        # 2. Create the Admission Record
        admission = InpatientAdmission(
            encounter_id=payload.encounter_id,
            admission_type=payload.admission_type,
            current_bed_id=bed.id,
            admitting_doctor_id=payload.admitting_doctor_id
        )
        db.add(admission)
        db.flush() # Generate admission.id

        # 3. Update Bed State
        bed.status = BedStatusEnum.OCCUPIED
        bed.current_admission_id = admission.id

        # 4. Generate Audit Trail (Initial placement logged as a transfer from 'None')
        transfer_log = BedTransferLog(
            admission_id=admission.id,
            from_bed_id=None,
            to_bed_id=bed.id,
            requested_by_id=payload.admitting_doctor_id,
            transfer_reason="Initial IPD Admission"
        )
        db.add(transfer_log)

        db.commit()
        db.refresh(admission)
        return admission

    except Exception as e:
        db.rollback()
        # Catch standard lock timeout/nowait exceptions
        if "could not obtain lock" in str(e).lower() or isinstance(e, HTTPException):
            raise HTTPException(status_code=409, detail="Bed assignment failed. Bed is locked by a concurrent transaction or unavailable.")
        raise e

# -----------------------------------------------------------------------------
# Bed Transfers
# -----------------------------------------------------------------------------

@router.post("/transfers", response_model=BedTransferResponse, status_code=status.HTTP_201_CREATED)
def transfer_patient_bed(payload: BedTransferRequest, db: Session = Depends(get_db), current_user: User = Depends(RequirePermission("view_ward_map"))):
    """Transfers a patient between beds. Releases the old bed to Housekeeping."""
    admission = db.get(InpatientAdmission, payload.admission_id)
    if not admission or admission.discharge_time is not None:
        raise HTTPException(status_code=400, detail="Active admission required for bed transfer.")

    old_bed_id = admission.current_bed_id
    if old_bed_id == payload.new_bed_id:
        raise HTTPException(status_code=400, detail="Patient is already in the requested bed.")

    try:
        # Lock new bed
        stmt = select(Bed).where(Bed.id == payload.new_bed_id).with_for_update(nowait=True)
        new_bed = db.execute(stmt).scalar_one_or_none()

        if not new_bed or new_bed.status != BedStatusEnum.AVAILABLE:
            raise HTTPException(status_code=409, detail="Requested destination bed is unavailable.")

        # Lock old bed
        stmt_old = select(Bed).where(Bed.id == old_bed_id).with_for_update(nowait=True)
        old_bed = db.execute(stmt_old).scalar_one_or_none()

        # Update Admission
        admission.current_bed_id = new_bed.id

        # Update Old Bed
        if old_bed:
            old_bed.status = BedStatusEnum.HOUSEKEEPING
            old_bed.current_admission_id = None

        # Update New Bed
        new_bed.status = BedStatusEnum.OCCUPIED
        new_bed.current_admission_id = admission.id

        # Create Transfer Log
        transfer_log = BedTransferLog(
            admission_id=admission.id,
            from_bed_id=old_bed_id,
            to_bed_id=new_bed.id,
            requested_by_id=current_user.id,
            transfer_reason=payload.transfer_reason
        )
        db.add(transfer_log)

        db.commit()
        db.refresh(transfer_log)
        return transfer_log

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=409, detail="Transfer failed due to concurrent bed locks or data state errors.")

# -----------------------------------------------------------------------------
# Clinical Discharge
# -----------------------------------------------------------------------------

@router.post("/admissions/{admission_id}/discharge", status_code=200)
def discharge_patient(admission_id: int, payload: ClinicalDischargeRequest, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("write_cpoe_orders"))):
    """Finalizes an episode of care, releases the bed to Housekeeping, and closes the encounter."""
    admission = db.query(InpatientAdmission).options(joinedload(InpatientAdmission.encounter)).filter(InpatientAdmission.id == admission_id).first()
    
    if not admission or admission.discharge_time is not None:
        raise HTTPException(status_code=400, detail="Admission record is invalid or already discharged.")

    current_time = datetime.now(timezone.utc)

    # 1. Process Admission
    admission.discharge_time = current_time
    admission.discharge_disposition = payload.discharge_disposition
    admission.discharge_summary = payload.discharge_summary

    # 2. Release Bed
    if admission.current_bed_id:
        stmt = select(Bed).where(Bed.id == admission.current_bed_id).with_for_update(nowait=True)
        bed = db.execute(stmt).scalar_one_or_none()
        if bed:
            bed.status = BedStatusEnum.HOUSEKEEPING
            bed.current_admission_id = None
        admission.current_bed_id = None

    # 3. Close Encounter
    if admission.encounter:
        admission.encounter.status = EncounterStatusEnum.DISCHARGED
        admission.encounter.end_time = current_time

    db.commit()
    return {"status": "success", "message": f"Patient discharged. Bed released to housekeeping."}

from typing import Optional
from schemas.v1.schemas import PatientEncounterUpdate

# -----------------------------------------------------------------------------
# Encounters Missing CRUD
# -----------------------------------------------------------------------------

@router.get("/encounters/{encounter_id}", response_model=PatientEncounterResponse)
def get_encounter(encounter_id: int, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("write_cpoe_orders"))):
    """Retrieves a single patient encounter by its ID."""
    encounter = db.get(PatientEncounter, encounter_id)
    if not encounter:
        raise HTTPException(status_code=404, detail="Encounter not found.")
    return encounter

@router.put("/encounters/{encounter_id}", response_model=PatientEncounterResponse)
def update_encounter(encounter_id: int, payload: PatientEncounterUpdate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("write_cpoe_orders"))):
    """Updates non-clinical metadata for an active encounter."""
    encounter = db.get(PatientEncounter, encounter_id)
    if not encounter:
        raise HTTPException(status_code=404, detail="Encounter not found.")
        
    if payload.primary_doctor_id is not None:
        encounter.primary_doctor_id = payload.primary_doctor_id
    if payload.department_id is not None:
        encounter.department_id = payload.department_id
    if payload.chief_complaint_notes is not None:
        encounter.chief_complaint_notes = payload.chief_complaint_notes
        
    db.commit()
    db.refresh(encounter)
    return encounter

# -----------------------------------------------------------------------------
# Admissions Missing CRUD
# -----------------------------------------------------------------------------

@router.get("/admissions", response_model=PaginatedResponse[InpatientAdmissionResponse])
def list_admissions(patient_id: Optional[int] = None, page: int = 1, page_size: int = 50, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("view_ward_map"))):
    """Lists all IPD admissions, optionally filtered by a specific patient."""
    query = db.query(InpatientAdmission)
    if patient_id:
        query = query.join(InpatientAdmission.encounter).filter(PatientEncounter.patient_id == patient_id)
        
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@router.get("/admissions/{admission_id}", response_model=InpatientAdmissionResponse)
def get_admission(admission_id: int, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("view_ward_map"))):
    """Retrieves a single inpatient admission record."""
    admission = db.get(InpatientAdmission, admission_id)
    if not admission:
        raise HTTPException(status_code=404, detail="Admission not found.")
    return admission

# -----------------------------------------------------------------------------
# Transfer Logs Missing Read
# -----------------------------------------------------------------------------

@router.get("/admissions/{admission_id}/transfers", response_model=PaginatedResponse[BedTransferResponse])
def list_admission_transfers(admission_id: int, page: int = 1, page_size: int = 50, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("view_ward_map"))):
    """Retrieves the complete audit log of bed movements for a specific admission."""
    query = db.query(BedTransferLog).filter(BedTransferLog.admission_id == admission_id)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}

from schemas.v1.schemas import InpatientAdmissionUpdate

# --- Encounters Missing Delete (Cancellation) ---

@router.delete("/encounters/{encounter_id}", status_code=200)
def cancel_encounter(encounter_id: int, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("write_cpoe_orders"))):
    """Cancels a patient encounter if created in error."""
    encounter = db.get(PatientEncounter, encounter_id)
    if not encounter:
        raise HTTPException(status_code=404, detail="Encounter not found.")
        
    if encounter.status != EncounterStatusEnum.ACTIVE:
        raise HTTPException(status_code=400, detail=f"Cannot cancel an encounter that is currently {encounter.status.value}.")
        
    # Check if there are active admissions tied to this
    if any(adm.discharge_time is None for adm in encounter.admissions):
        raise HTTPException(status_code=400, detail="Cannot cancel encounter. Patient has an active admission. Cancel the admission first.")

    encounter.status = EncounterStatusEnum.CANCELLED
    encounter.end_time = datetime.now(timezone.utc)
    db.commit()
    return {"status": "success", "message": f"Encounter {encounter_id} cancelled."}


# --- Admissions Missing Update & Delete (Undo) ---

@router.put("/admissions/{admission_id}", response_model=InpatientAdmissionResponse)
def update_admission(admission_id: int, payload: InpatientAdmissionUpdate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("view_ward_map"))):
    """Updates administrative details of an active admission (e.g., correcting the admitting doctor)."""
    admission = db.get(InpatientAdmission, admission_id)
    if not admission:
        raise HTTPException(status_code=404, detail="Admission not found.")
        
    if admission.discharge_time is not None:
        raise HTTPException(status_code=400, detail="Cannot modify a discharged admission.")

    if payload.admission_type is not None: admission.admission_type = payload.admission_type
    if payload.admitting_doctor_id is not None: admission.admitting_doctor_id = payload.admitting_doctor_id
    
    db.commit()
    db.refresh(admission)
    return admission

@router.delete("/admissions/{admission_id}", status_code=200)
def cancel_admission(admission_id: int, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("view_ward_map"))):
    """Safely undoes an erroneous admission and releases the bed back to available."""
    admission = db.get(InpatientAdmission, admission_id)
    if not admission:
        raise HTTPException(status_code=404, detail="Admission not found.")
        
    if admission.discharge_time is not None:
        raise HTTPException(status_code=400, detail="Cannot cancel an already discharged admission.")

    # 1. Release the bed safely back to AVAILABLE
    if admission.current_bed_id:
        stmt = select(Bed).where(Bed.id == admission.current_bed_id).with_for_update(nowait=True)
        bed = db.execute(stmt).scalar_one_or_none()
        if bed:
            bed.status = BedStatusEnum.AVAILABLE
            bed.current_admission_id = None
        admission.current_bed_id = None

    # 2. Mark the admission as cancelled/voided (Using discharge logic to close it out)
    admission.discharge_time = datetime.now(timezone.utc)
    admission.discharge_summary = "ADMISSION CANCELLED DUE TO ADMINISTRATIVE ERROR."

    db.commit()
    return {"status": "success", "message": f"Admission {admission_id} cancelled. Bed released to AVAILABLE."}