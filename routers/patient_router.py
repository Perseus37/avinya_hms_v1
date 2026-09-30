"""Patient registration and EMR-facing patient operations.

This router belongs to the patient care domain. It covers demographic records,
visits, and allergy charting that are attached to the patient journey in the
hospital.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies import RequirePermission, get_current_user
from models import Patient, PatientAllergy, User
from schemas import PaginatedResponse, PatientAllergyCreate, PatientCreate, PatientResponse, PatientUpdate

router = APIRouter(prefix="/api/patients", tags=["Patient Management & EMR"])

# -----------------------------------------------------------------------------
# Patient master data domain
# -----------------------------------------------------------------------------
@router.post("", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
def register_patient(payload: PatientCreate, db: Session = Depends(get_db),current_user: User = Depends(RequirePermission("manage_clinical_mdm"))):
    """Registers a new patient into the hospital system."""
    existing = db.query(Patient).filter(Patient.mrn == payload.mrn).first()
    if existing:
        raise HTTPException(status_code=409, detail="Patient with this MRN already exists.")

    patient = Patient(
        mrn=payload.mrn,
        full_name=payload.full_name,
        date_of_birth=payload.date_of_birth,
        gender=payload.gender,
        is_active=True,
    )

    patient.updated_by_id = current_user.id

    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


@router.get("", response_model=PaginatedResponse[PatientResponse])
def list_patients(
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    _: bool = Depends(RequirePermission("write_cpoe_orders")),
):
    """Retrieves active hospital patients with pagination support."""
    if page < 1:
        raise HTTPException(status_code=400, detail="Page must be greater than 0")
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="Page size must be between 1 and 200")

    query = db.query(Patient).filter(Patient.is_active == True)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{patient_id}", response_model=PatientResponse)
def get_patient(patient_id: int, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("write_cpoe_orders"))):
    """Retrieves a single patient by ID."""
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


@router.put("/{patient_id}", response_model=PatientResponse)
def update_patient(patient_id: int, payload: PatientUpdate, db: Session = Depends(get_db),current_user: User = Depends(RequirePermission("manage_clinical_mdm"))):
    """Updates patient demographic records."""
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    if payload.full_name is not None:
        patient.full_name = payload.full_name
    if payload.date_of_birth is not None:
        patient.date_of_birth = payload.date_of_birth
    if payload.gender is not None:
        patient.gender = payload.gender
    if payload.is_active is not None:
        patient.is_active = payload.is_active
    patient.updated_by_id = current_user.id
    db.commit()
    db.refresh(patient)
    return patient


@router.delete("/{patient_id}", status_code=200)
def deactivate_patient(patient_id: int, db: Session = Depends(get_db),current_user: User = Depends(RequirePermission("manage_clinical_mdm"))):
    """Soft-deactivates a patient record."""
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    patient.is_active = False
    patient.updated_by_id = current_user.id
    db.commit()
    return {"status": "success", "message": f"Patient ID {patient_id} deactivated successfully."}


# -----------------------------------------------------------------------------
# Allergy and patient safety domain
# -----------------------------------------------------------------------------
@router.post("/allergies", status_code=status.HTTP_201_CREATED)
def chart_patient_allergy(payload: PatientAllergyCreate, db: Session = Depends(get_db), current_user: User = Depends(RequirePermission("write_cpoe_orders"))):
    """Charts an active allergy relation for a patient (triggers CPOE safety checks)."""
    patient_allergy = PatientAllergy(
        patient_id=payload.patient_id,
        allergy_id=payload.allergy_id,
        severity=payload.severity,
        reaction_description=payload.reaction_description,
        recorded_by_user_id=current_user.id,
        is_active=True,
    )
    db.add(patient_allergy)
    db.commit()
    return {"status": "success", "message": "Patient allergy charted successfully."}

from sqlalchemy.orm import joinedload
from schemas import PatientAllergyResponse

@router.get("/{patient_id}/allergies", response_model=list[PatientAllergyResponse])
def get_patient_allergies(patient_id: int, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("write_cpoe_orders"))):
    """Retrieves all actively charted allergies for a specific patient."""
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
        
    stmt = select(PatientAllergy).options(joinedload(PatientAllergy.allergy)).where(
        PatientAllergy.patient_id == patient_id,
        PatientAllergy.is_active == True
    )
    return db.execute(stmt).scalars().all()

@router.delete("/allergies/{chart_id}", status_code=200)
def unchart_patient_allergy(chart_id: int, db: Session = Depends(get_db),current_user: User = Depends(RequirePermission("write_cpoe_orders"))):
    """Soft-deletes a charted allergy in case of data entry error."""
    charted_allergy = db.get(PatientAllergy, chart_id)
    if not charted_allergy:
        raise HTTPException(status_code=404, detail="Charted patient allergy not found.")
    charted_allergy.is_active = False
    db.commit()
    return {"status": "success", "message": f"Patient allergy chart ID {chart_id} deactivated."}