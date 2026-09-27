from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
from dependencies import get_current_user, RequirePermission
from schemas import PatientCreate, PatientResponse, PatientUpdate, PatientAllergyCreate
from models import Patient, PatientAllergy, User

router = APIRouter(prefix="/api/patients", tags=["Patient Management & EMR"])

@router.post("", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
def register_patient(payload: PatientCreate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Registers a new patient into the hospital system."""
    existing = db.query(Patient).filter(Patient.mrn == payload.mrn).first()
    if existing:
        raise HTTPException(status_code=409, detail="Patient with this MRN already exists.")
    
    patient = Patient(
        mrn=payload.mrn,
        full_name=payload.full_name,
        date_of_birth=payload.date_of_birth,
        gender=payload.gender,
        is_active=True
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient

@router.get("", response_model=list[PatientResponse])
def list_patients(db: Session = Depends(get_db), _: bool = Depends(get_current_user)):
    """Retrieves all active hospital patients."""
    return db.query(Patient).filter(Patient.is_active == True).all()

@router.get("/{patient_id}", response_model=PatientResponse)
def get_patient(patient_id: int, db: Session = Depends(get_db), _: bool = Depends(get_current_user)):
    """Retrieves a single patient by ID."""
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient

@router.put("/{patient_id}", response_model=PatientResponse)
def update_patient(patient_id: int, payload: PatientUpdate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Updates patient demographic records."""
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
        
    if payload.full_name is not None: patient.full_name = payload.full_name
    if payload.date_of_birth is not None: patient.date_of_birth = payload.date_of_birth
    if payload.gender is not None: patient.gender = payload.gender
    if payload.is_active is not None: patient.is_active = payload.is_active
    
    db.commit()
    db.refresh(patient)
    return patient

@router.delete("/{patient_id}", status_code=200)
def deactivate_patient(patient_id: int, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Soft-deactivates a patient record."""
    patient = db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
        
    patient.is_active = False
    db.commit()
    return {"status": "success", "message": f"Patient ID {patient_id} deactivated successfully."}

@router.post("/allergies", status_code=status.HTTP_201_CREATED)
def chart_patient_allergy(payload: PatientAllergyCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Charts an active allergy relation for a patient (triggers CPOE safety checks)."""
    patient_allergy = PatientAllergy(
        patient_id=payload.patient_id,
        allergy_id=payload.allergy_id,
        severity=payload.severity,
        reaction_description=payload.reaction_description,
        recorded_by_user_id=current_user.id,
        is_active=True
    )
    db.add(patient_allergy)
    db.commit()
    return {"status": "success", "message": "Patient allergy charted successfully."}