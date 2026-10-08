import secrets
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, joinedload
# FED-002: Purge soft-deleted patient from the Central Discovery Index
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from control_models import GlobalPatientPointer
from database import get_control_db, get_db, resolve_tenant_context
from dependencies import RequirePermission, validate_pagination
from models import AllergyMaster, AllergySeverityEnum, Patient, PatientAllergy, User
from routers.v1.federation_router import sync_patient_pointer
from schemas.v1.schemas import (
    PaginatedResponse,
    PatientAllergyCreate,
    PatientAllergyResponse,
    PatientCreate,
    PatientResponse,
    PatientUpdate,
)

router = APIRouter(prefix="/api/patients", tags=["Master Patient Index (MPI)"])


@router.post("", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
def register_patient(
    payload: PatientCreate,
    request: Request,
    db: Session = Depends(get_db),
    control_db: Session = Depends(get_control_db),
    current_user: User = Depends(RequirePermission("patients:write")),
):
    """Registers a patient locally AND automatically publishes a pointer to the Central Discovery Index."""
    tenant = resolve_tenant_context(request)
    if payload.abha_number:
        existing_abha = db.query(Patient).filter(Patient.abha_number == payload.abha_number).first()
        if existing_abha:
            raise HTTPException(status_code=409, detail="Patient with this ABHA number already exists.")

    if payload.mrn:
        existing_mrn = db.query(Patient).filter(Patient.mrn == payload.mrn).first()
        if existing_mrn:
            raise HTTPException(status_code=409, detail="Patient with this MRN already exists.")
        mrn = payload.mrn
    else:
        mrn = f"MRN-{secrets.token_hex(6).upper()}"

    dump_data = payload.model_dump(exclude={"mrn"})
    if not dump_data.get("phone_number"):
        dump_data["phone_number"] = "0000000000"

    patient = Patient(
        mrn=mrn,
        **dump_data,
        is_active=True,
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)

    try:
        sync_patient_pointer(
            control_db=control_db,
            branch_code=tenant.branch_code,
            subdomain_slug=tenant.subdomain_slug,
            patient=patient,
            tenant_db=db,
            has_critical_allergies=False,
        )
    except IntegrityError:
        control_db.rollback()
        # Concurrency collision fallback: retry sync to heal the pointer across federation mesh
        sync_patient_pointer(
            control_db=control_db,
            branch_code=tenant.branch_code,
            subdomain_slug=tenant.subdomain_slug,
            patient=patient,
            tenant_db=db,
            has_critical_allergies=False,
        )
    return patient


@router.get("", response_model=PaginatedResponse[PatientResponse])
def list_patients(
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("patients:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(Patient).filter(Patient.is_active == True)
    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            (Patient.full_name.ilike(term))
            | (Patient.mrn.ilike(term))
            | (Patient.phone_number.ilike(term))
            | (Patient.abha_number.ilike(term))
        )
    query = query.order_by(Patient.id.desc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{patient_id}", response_model=PatientResponse)
def get_patient(
    patient_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("patients:read")),
):
    patient = db.get(Patient, patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=404, detail="Active patient not found.")
    return patient


@router.put("/{patient_id}", response_model=PatientResponse)
def update_patient(
    patient_id: int,
    payload: PatientUpdate,
    request: Request,
    db: Session = Depends(get_db),
    control_db: Session = Depends(get_control_db),
    current_user: User = Depends(RequirePermission("patients:write")),
):
    tenant = resolve_tenant_context(request)
    patient = db.get(Patient, patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=404, detail="Active patient not found.")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(patient, field, value)
    patient.updated_by_id = current_user.id
    db.commit()
    db.refresh(patient)

    sync_patient_pointer(
        control_db=control_db,
        branch_code=tenant.branch_code,
        subdomain_slug=tenant.subdomain_slug,
        patient=patient,
        tenant_db=db,
    )
    return patient


@router.delete("/{patient_id}", status_code=200)
def deactivate_patient(
    patient_id: int,
    request: Request,
    db: Session = Depends(get_db),
    control_db: Session = Depends(get_control_db),
    current_user: User = Depends(RequirePermission("patients:delete")),
):
    tenant = resolve_tenant_context(request)
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
    
    patient.is_active = False
    patient.updated_by_id = current_user.id
    db.commit()
    
    pointer = control_db.execute(
        select(GlobalPatientPointer).where(
            GlobalPatientPointer.source_mrn == patient.mrn,
            GlobalPatientPointer.branch_code == tenant.branch_code
        )
    ).scalar_one_or_none()
    
    if pointer:
        control_db.delete(pointer)
        control_db.commit()

    return {"status": "success", "message": f"Patient {patient.mrn} deactivated and purged from Central Index."}


@router.post("/{patient_id}/allergies", response_model=PatientAllergyResponse, status_code=status.HTTP_201_CREATED)
def chart_patient_allergy(
    patient_id: int,
    payload: PatientAllergyCreate,
    request: Request,
    db: Session = Depends(get_db),
    control_db: Session = Depends(get_control_db),
    current_user: User = Depends(RequirePermission("patients:write")),
):
    """Charts a patient allergy AND marks this branch as holding critical allergy updates in the Central Index."""
    tenant = resolve_tenant_context(request)
    patient = db.get(Patient, patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=404, detail="Active patient not found.")

    master = db.get(AllergyMaster, payload.allergy_id)
    if not master or not master.is_active:
        raise HTTPException(status_code=404, detail="Active allergy master code not found.")

    reaction_text = payload.reaction or payload.reaction_description or "Adverse reaction"

    existing = (
        db.query(PatientAllergy)
        .filter(PatientAllergy.patient_id == patient_id, PatientAllergy.allergy_id == payload.allergy_id)
        .first()
    )
    if existing:
        existing.severity = payload.severity
        existing.reaction_description = reaction_text
        existing.recorded_by_user_id = current_user.id
        existing.is_active = True
        existing.updated_by_id = current_user.id
        record = existing
    else:
        record = PatientAllergy(
            patient_id=patient_id,
            allergy_id=payload.allergy_id,
            severity=payload.severity,
            reaction_description=reaction_text,
            recorded_by_user_id=current_user.id,
            is_active=True,
            created_by_id=current_user.id,
            updated_by_id=current_user.id,
        )
        db.add(record)

    db.commit()
    db.refresh(record)

    is_crit = payload.severity in [AllergySeverityEnum.SEVERE, AllergySeverityEnum.ANAPHYLACTIC]
    sync_patient_pointer(
        control_db=control_db,
        branch_code=tenant.branch_code,
        subdomain_slug=tenant.subdomain_slug,
        patient=patient,
        tenant_db=db,
        has_critical_allergies=is_crit,
    )
    return record


@router.get("/{patient_id}/allergies", response_model=list[PatientAllergyResponse])
def list_patient_allergies(
    patient_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("patients:read")),
):
    return (
        db.query(PatientAllergy)
        .options(joinedload(PatientAllergy.allergy))
        .filter(PatientAllergy.patient_id == patient_id, PatientAllergy.is_active == True)
        .order_by(PatientAllergy.id.asc())
        .all()
    )


@router.delete("/{patient_id}/allergies/{allergy_record_id}", status_code=200)
def remove_patient_allergy(
    patient_id: int,
    allergy_record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("patients:delete")),
):
    record = (
        db.query(PatientAllergy)
        .filter(PatientAllergy.id == allergy_record_id, PatientAllergy.patient_id == patient_id)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Charted allergy not found.")
    record.is_active = False
    record.updated_by_id = current_user.id
    db.commit()
    return {"status": "success", "message": "Patient allergy deactivated."}