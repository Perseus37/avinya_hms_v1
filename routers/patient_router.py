from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
from dependencies import get_current_user, RequirePermission
from schemas import PatientCreate, PatientResponse, PatientAllergyCreate
from models import Patient, PatientAllergy, User

router = APIRouter(prefix="/api/patients", tags=["Patient Management & EMR"])

@router.post("", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
def register_patient(payload: PatientCreate, db: Session = Depends(get_db), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
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
    return db.query(Patient).filter(Patient.is_active == True).all()

@router.post("/allergies", status_code=status.HTTP_201_CREATED)
def chart_patient_allergy(payload: PatientAllergyCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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