from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db, get_redis
from dependencies import get_current_user, RequirePermission
from schemas import (
    AllergyCheckRequest, TaxonomyResponse, AllergyMasterCreate, 
    ChiefComplaintCreate, ComorbidityCreate, DiagnosisCreate, PhysicalExamCreate
)
from services.taxonomy_cache import get_department_taxonomy, invalidate_department_taxonomy_cache
from services.cpoe import check_allergy_hard_stop
from models import AllergyMaster, ChiefComplaintMaster, ComorbidityMaster, DiagnosisMaster, PhysicalExaminationMaster

router = APIRouter(prefix="/api/clinical", tags=["Clinical Core & EMR"])

@router.post("/allergies", status_code=status.HTTP_201_CREATED)
def create_allergy_master(payload: AllergyMasterCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    allergy = AllergyMaster(
        department_id=payload.department_id,
        code=payload.code,
        name=payload.name,
        category=payload.category,
        default_severity=payload.default_severity,
        is_active=True
    )
    db.add(allergy)
    db.commit()
    invalidate_department_taxonomy_cache(redis, "allergies", payload.department_id)
    return {"status": "success", "message": "Allergy master created and cache busted."}

@router.post("/complaints", status_code=201)
def create_chief_complaint(payload: ChiefComplaintCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    item = ChiefComplaintMaster(department_id=payload.department_id, code=payload.code, name=payload.name, is_active=True)
    db.add(item)
    db.commit()
    invalidate_department_taxonomy_cache(redis, "complaints", payload.department_id)
    return {"status": "success", "message": "Chief complaint created."}

@router.post("/diagnoses", status_code=201)
def create_diagnosis(payload: DiagnosisCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    item = DiagnosisMaster(
        department_id=payload.department_id,
        icd10_code=payload.icd10_code,
        short_description=payload.short_description,
        full_description=payload.full_description,
        is_billable=payload.is_billable,
        parent_diagnosis_id=payload.parent_diagnosis_id,
        is_active=True
    )
    db.add(item)
    db.commit()
    invalidate_department_taxonomy_cache(redis, "diagnoses", payload.department_id)
    return {"status": "success", "message": f"ICD-10 Diagnosis {payload.icd10_code} created."}

@router.get("/taxonomy/{taxonomy}/{department_id}", response_model=TaxonomyResponse)
def fetch_taxonomy(taxonomy: str, department_id: int, db: Session = Depends(get_db), redis=Depends(get_redis)):
    items = get_department_taxonomy(redis, db, taxonomy, department_id)
    return {"items": items}

@router.post("/orders/check-allergy")
def verify_prescription_safety(payload: AllergyCheckRequest, db: Session = Depends(get_db), _=Depends(get_current_user)):
    check_allergy_hard_stop(db, payload.patient_id, payload.allergen_keywords)
    return {"status": "safe", "message": "CPOE verification passed: No clinical allergy conflicts detected."}

@router.put("/allergies/{allergy_id}", status_code=200)
def update_allergy_master(allergy_id: int, payload: AllergyMasterCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    allergy = db.get(AllergyMaster, allergy_id)
    if not allergy:
        raise HTTPException(status_code=404, detail="Allergy master record not found")
        
    allergy.code = payload.code
    allergy.name = payload.name
    allergy.category = payload.category
    allergy.default_severity = payload.default_severity
    db.commit()
    invalidate_department_taxonomy_cache(redis, "allergies", payload.department_id)
    return {"status": "success", "message": f"Allergy ID {allergy_id} updated successfully."}

@router.delete("/allergies/{allergy_id}", status_code=200)
def deactivate_allergy_master(allergy_id: int, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    allergy = db.get(AllergyMaster, allergy_id)
    if not allergy:
        raise HTTPException(status_code=404, detail="Allergy master record not found")
        
    allergy.is_active = False
    db.commit()
    invalidate_department_taxonomy_cache(redis, "allergies", allergy.department_id)
    return {"status": "success", "message": f"Allergy ID {allergy_id} deactivated successfully."}

@router.post("/comorbidities", status_code=201)
def create_comorbidity(payload: ComorbidityCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Creates a master comorbidity definition linked to a department."""
    item = ComorbidityMaster(
        department_id=payload.department_id,
        code=payload.code,
        name=payload.name,
        icd10_code=payload.icd10_code,
        is_active=True
    )
    db.add(item)
    db.commit()
    invalidate_department_taxonomy_cache(redis, "comorbidities", payload.department_id)
    return {"status": "success", "message": "Comorbidity master created."}

@router.post("/physical-exams", status_code=201)
def create_physical_exam(payload: PhysicalExamCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Creates a physical examination taxonomy master definition."""
    item = PhysicalExaminationMaster(
        department_id=payload.department_id,
        body_system=payload.body_system,
        code=payload.code,
        name=payload.name,
        is_active=True
    )
    db.add(item)
    db.commit()
    invalidate_department_taxonomy_cache(redis, "physical_exams", payload.department_id)
    return {"status": "success", "message": "Physical examination master created."}

@router.put("/comorbidities/{comorbidity_id}", status_code=200)
def update_comorbidity(comorbidity_id: int, payload: ComorbidityCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    item = db.get(ComorbidityMaster, comorbidity_id)
    if not item:
        raise HTTPException(status_code=404, detail="Comorbidity master not found")
    item.code = payload.code
    item.name = payload.name
    item.icd10_code = payload.icd10_code
    db.commit()
    invalidate_department_taxonomy_cache(redis, "comorbidities", payload.department_id)
    return {"status": "success", "message": f"Comorbidity ID {comorbidity_id} updated."}

@router.delete("/comorbidities/{comorbidity_id}", status_code=200)
def deactivate_comorbidity(comorbidity_id: int, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    item = db.get(ComorbidityMaster, comorbidity_id)
    if not item:
        raise HTTPException(status_code=404, detail="Comorbidity master not found")
    item.is_active = False
    db.commit()
    invalidate_department_taxonomy_cache(redis, "comorbidities", item.department_id)
    return {"status": "success", "message": f"Comorbidity ID {comorbidity_id} deactivated."}

@router.put("/diagnoses/{diagnosis_id}", status_code=200)
def update_diagnosis(diagnosis_id: int, payload: DiagnosisCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    item = db.get(DiagnosisMaster, diagnosis_id)
    if not item:
        raise HTTPException(status_code=404, detail="Diagnosis master not found")
    item.icd10_code = payload.icd10_code
    item.short_description = payload.short_description
    item.full_description = payload.full_description
    item.is_billable = payload.is_billable
    db.commit()
    invalidate_department_taxonomy_cache(redis, "diagnoses", payload.department_id)
    return {"status": "success", "message": f"Diagnosis ID {diagnosis_id} updated."}

@router.delete("/diagnoses/{diagnosis_id}", status_code=200)
def deactivate_diagnosis(diagnosis_id: int, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    item = db.get(DiagnosisMaster, diagnosis_id)
    if not item:
        raise HTTPException(status_code=404, detail="Diagnosis master not found")
    item.is_active = False
    db.commit()
    invalidate_department_taxonomy_cache(redis, "diagnoses", item.department_id)
    return {"status": "success", "message": f"Diagnosis ID {diagnosis_id} deactivated."}

@router.get("/diagnoses/{department_id}", response_model=TaxonomyResponse)
def get_department_diagnoses(department_id: int, db: Session = Depends(get_db), redis=Depends(get_redis)):
    """Retrieves all active ICD-10 diagnoses for a department (cached via Redis)."""
    items = get_department_taxonomy(redis, db, "diagnoses", department_id)
    return {"items": items}

@router.get("/comorbidities/{department_id}", response_model=TaxonomyResponse)
def get_department_comorbidities(department_id: int, db: Session = Depends(get_db), redis=Depends(get_redis)):
    """Retrieves all active comorbidities for a department (cached via Redis)."""
    items = get_department_taxonomy(redis, db, "comorbidities", department_id)
    return {"items": items}

@router.get("/physical-exams/{department_id}", response_model=TaxonomyResponse)
def get_department_physical_exams(department_id: int, db: Session = Depends(get_db), redis=Depends(get_redis)):
    """Retrieves all active physical examination taxonomies for a department (cached via Redis)."""
    items = get_department_taxonomy(redis, db, "physical_exams", department_id)
    return {"items": items}