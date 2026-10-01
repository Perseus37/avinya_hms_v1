"""Clinical and EMR catalog APIs.

This router belongs to the clinical and care-delivery domain. It manages
master data, department taxonomies, and safety rules used to standardize care
and protect patient wellbeing.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db, get_redis
from dependencies import RequirePermission
from models import (
    AllergyMaster,
    ChiefComplaintMaster,
    ComorbidityMaster,
    DiagnosisMaster,
    PhysicalExaminationMaster,
)
from schemas.v1.schemas import (
    AllergyCheckRequest,
    AllergyMasterCreate,
    ChiefComplaintCreate,
    ComorbidityCreate,
    DiagnosisCreate,
    PaginatedResponse,
    PhysicalExamCreate,
    TaxonomyResponse,
)
from services.cpoe import check_allergy_hard_stop
from services.taxonomy_cache import get_department_taxonomy, invalidate_department_taxonomy_cache

router = APIRouter(prefix="/api/clinical", tags=["Clinical Core & EMR"])

# -----------------------------------------------------------------------------
# Clinical taxonomy and lookup domain
# -----------------------------------------------------------------------------
@router.get("/taxonomy/{taxonomy}/{department_id}", response_model=PaginatedResponse[dict])
def fetch_taxonomy(
    taxonomy: str,
    department_id: int,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    _: bool = Depends(RequirePermission("write_cpoe_orders")),
):
    """Return a paginated department taxonomy collection, using Redis cache when available."""
    if page < 1:
        raise HTTPException(status_code=400, detail="Page must be greater than 0")
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="Page size must be between 1 and 200")

    items = get_department_taxonomy(redis, db, taxonomy, department_id)
    total = len(items)
    paginated = items[(page - 1) * page_size : page * page_size]
    return {"items": paginated, "total": total, "page": page, "page_size": page_size}


@router.get("/diagnoses/{department_id}", response_model=PaginatedResponse[dict])
def get_department_diagnoses(
    department_id: int,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    _: bool = Depends(RequirePermission("write_cpoe_orders")),
):
    """Retrieves active ICD-10 diagnoses for a department with pagination support."""
    if page < 1:
        raise HTTPException(status_code=400, detail="Page must be greater than 0")
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="Page size must be between 1 and 200")

    items = get_department_taxonomy(redis, db, "diagnoses", department_id)
    total = len(items)
    paginated = items[(page - 1) * page_size : page * page_size]
    return {"items": paginated, "total": total, "page": page, "page_size": page_size}


@router.get("/comorbidities/{department_id}", response_model=PaginatedResponse[dict])
def get_department_comorbidities(
    department_id: int,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    _: bool = Depends(RequirePermission("write_cpoe_orders")),
):
    """Retrieves active comorbidity taxonomies for a department with pagination support."""
    if page < 1:
        raise HTTPException(status_code=400, detail="Page must be greater than 0")
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="Page size must be between 1 and 200")

    items = get_department_taxonomy(redis, db, "comorbidities", department_id)
    total = len(items)
    paginated = items[(page - 1) * page_size : page * page_size]
    return {"items": paginated, "total": total, "page": page, "page_size": page_size}


@router.get("/physical-exams/{department_id}", response_model=PaginatedResponse[dict])
def get_department_physical_exams(
    department_id: int,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    _: bool = Depends(RequirePermission("write_cpoe_orders")),
):
    """Retrieves active physical examination taxonomies for a department with pagination support."""
    if page < 1:
        raise HTTPException(status_code=400, detail="Page must be greater than 0")
    if page_size < 1 or page_size > 200:
        raise HTTPException(status_code=400, detail="Page size must be between 1 and 200")

    items = get_department_taxonomy(redis, db, "physical_exams", department_id)
    total = len(items)
    paginated = items[(page - 1) * page_size : page * page_size]
    return {"items": paginated, "total": total, "page": page, "page_size": page_size}


# -----------------------------------------------------------------------------
# Clinical master data domain
# -----------------------------------------------------------------------------
@router.post("/allergies", status_code=status.HTTP_201_CREATED)
def create_allergy_master(payload: AllergyMasterCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Create a department allergy definition and invalidate cached taxonomy data."""
    allergy = AllergyMaster(
        department_id=payload.department_id,
        code=payload.code,
        name=payload.name,
        category=payload.category,
        default_severity=payload.default_severity,
        is_active=True,
    )
    db.add(allergy)
    db.commit()
    invalidate_department_taxonomy_cache(redis, "allergies", allergy.department_id)
    return {"status": "success", "message": "Allergy master created and cache busted."}


@router.post("/complaints", status_code=201)
def create_chief_complaint(payload: ChiefComplaintCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Create a chief complaint taxonomy item for a department."""
    item = ChiefComplaintMaster(department_id=payload.department_id, code=payload.code, name=payload.name, is_active=True)
    db.add(item)
    db.commit()
    invalidate_department_taxonomy_cache(redis, "complaints", item.department_id)
    return {"status": "success", "message": "Chief complaint created."}


@router.post("/diagnoses", status_code=201)
def create_diagnosis(payload: DiagnosisCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Create an ICD-10 diagnosis record and refresh the department taxonomy cache."""
    item = DiagnosisMaster(
        department_id=payload.department_id,
        icd10_code=payload.icd10_code,
        short_description=payload.short_description,
        full_description=payload.full_description,
        is_billable=payload.is_billable,
        parent_diagnosis_id=payload.parent_diagnosis_id,
        is_active=True,
    )
    db.add(item)
    db.commit()
    invalidate_department_taxonomy_cache(redis, "diagnoses", item.department_id)
    return {"status": "success", "message": f"ICD-10 Diagnosis {payload.icd10_code} created."}


@router.post("/comorbidities", status_code=201)
def create_comorbidity(payload: ComorbidityCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Creates a master comorbidity definition linked to a department."""
    item = ComorbidityMaster(
        department_id=payload.department_id,
        code=payload.code,
        name=payload.name,
        icd10_code=payload.icd10_code,
        is_active=True,
    )
    db.add(item)
    db.commit()
    invalidate_department_taxonomy_cache(redis, "comorbidities", item.department_id)
    return {"status": "success", "message": "Comorbidity master created."}


@router.post("/physical-exams", status_code=201)
def create_physical_exam(payload: PhysicalExamCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Creates a physical examination taxonomy master definition."""
    item = PhysicalExaminationMaster(
        department_id=payload.department_id,
        body_system=payload.body_system,
        code=payload.code,
        name=payload.name,
        is_active=True,
    )
    db.add(item)
    db.commit()
    invalidate_department_taxonomy_cache(redis, "physical_exams", item.department_id)
    return {"status": "success", "message": "Physical examination master created."}


@router.put("/complaints/{complaint_id}", status_code=200)
def update_chief_complaint(complaint_id: int, payload: ChiefComplaintCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Update a chief complaint master entry."""
    item = db.get(ChiefComplaintMaster, complaint_id)
    if not item:
        raise HTTPException(status_code=404, detail="Chief complaint not found")
    item.code = payload.code
    item.name = payload.name
    if payload.department_id is not None:
        item.department_id = payload.department_id
    db.commit()
    invalidate_department_taxonomy_cache(redis, "complaints", item.department_id)
    return {"status": "success", "message": f"Complaint ID {complaint_id} updated."}


@router.delete("/complaints/{complaint_id}", status_code=200)
def deactivate_chief_complaint(complaint_id: int, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Soft-deactivate a chief complaint taxonomy item."""
    item = db.get(ChiefComplaintMaster, complaint_id)
    if not item:
        raise HTTPException(status_code=404, detail="Chief complaint not found")
    item.is_active = False
    db.commit()
    invalidate_department_taxonomy_cache(redis, "complaints", item.department_id)
    return {"status": "success", "message": f"Complaint ID {complaint_id} deactivated."}


@router.put("/allergies/{allergy_id}", status_code=200)
def update_allergy_master(allergy_id: int, payload: AllergyMasterCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Update allergy metadata for an existing master record."""
    allergy = db.get(AllergyMaster, allergy_id)
    if not allergy:
        raise HTTPException(status_code=404, detail="Allergy master record not found")

    allergy.code = payload.code
    allergy.name = payload.name
    allergy.category = payload.category
    allergy.default_severity = payload.default_severity
    db.commit()
    invalidate_department_taxonomy_cache(redis, "allergies", allergy.department_id)
    return {"status": "success", "message": f"Allergy ID {allergy_id} updated successfully."}


@router.put("/comorbidities/{comorbidity_id}", status_code=200)
def update_comorbidity(comorbidity_id: int, payload: ComorbidityCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Update an existing comorbidity master record."""
    item = db.get(ComorbidityMaster, comorbidity_id)
    if not item:
        raise HTTPException(status_code=404, detail="Comorbidity master not found")
    item.code = payload.code
    item.name = payload.name
    item.icd10_code = payload.icd10_code
    db.commit()
    invalidate_department_taxonomy_cache(redis, "comorbidities", item.department_id)
    return {"status": "success", "message": f"Comorbidity ID {comorbidity_id} updated."}


@router.put("/diagnoses/{diagnosis_id}", status_code=200)
def update_diagnosis(diagnosis_id: int, payload: DiagnosisCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Update an existing diagnosis definition."""
    item = db.get(DiagnosisMaster, diagnosis_id)
    if not item:
        raise HTTPException(status_code=404, detail="Diagnosis master not found")
    item.icd10_code = payload.icd10_code
    item.short_description = payload.short_description
    item.full_description = payload.full_description
    item.is_billable = payload.is_billable
    db.commit()
    invalidate_department_taxonomy_cache(redis, "diagnoses", item.department_id)
    return {"status": "success", "message": f"Diagnosis ID {diagnosis_id} updated."}


@router.delete("/allergies/{allergy_id}", status_code=200)
def deactivate_allergy_master(allergy_id: int, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Soft-delete an allergy definition by marking it inactive."""
    allergy = db.get(AllergyMaster, allergy_id)
    if not allergy:
        raise HTTPException(status_code=404, detail="Allergy master record not found")

    allergy.is_active = False
    db.commit()
    invalidate_department_taxonomy_cache(redis, "allergies", allergy.department_id)
    return {"status": "success", "message": f"Allergy ID {allergy_id} deactivated successfully."}


@router.delete("/comorbidities/{comorbidity_id}", status_code=200)
def deactivate_comorbidity(comorbidity_id: int, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Deactivate a comorbidity entry while preserving historical data."""
    item = db.get(ComorbidityMaster, comorbidity_id)
    if not item:
        raise HTTPException(status_code=404, detail="Comorbidity master not found")
    item.is_active = False
    db.commit()
    invalidate_department_taxonomy_cache(redis, "comorbidities", item.department_id)
    return {"status": "success", "message": f"Comorbidity ID {comorbidity_id} deactivated."}


@router.delete("/diagnoses/{diagnosis_id}", status_code=200)
def deactivate_diagnosis(diagnosis_id: int, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Soft-delete a diagnosis record from the active taxonomy set."""
    item = db.get(DiagnosisMaster, diagnosis_id)
    if not item:
        raise HTTPException(status_code=404, detail="Diagnosis master not found")
    item.is_active = False
    db.commit()
    invalidate_department_taxonomy_cache(redis, "diagnoses", item.department_id)
    return {"status": "success", "message": f"Diagnosis ID {diagnosis_id} deactivated."}


@router.put("/physical-exams/{exam_id}", status_code=200)
def update_physical_exam(exam_id: int, payload: PhysicalExamCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Update a physical examination master entry."""
    item = db.get(PhysicalExaminationMaster, exam_id)
    if not item:
        raise HTTPException(status_code=404, detail="Physical exam not found")
    item.code = payload.code
    item.name = payload.name
    item.body_system = payload.body_system
    if payload.department_id is not None:
        item.department_id = payload.department_id
    db.commit()
    invalidate_department_taxonomy_cache(redis, "physical_exams", item.department_id)
    return {"status": "success", "message": f"Physical exam ID {exam_id} updated."}


@router.delete("/physical-exams/{exam_id}", status_code=200)
def deactivate_physical_exam(exam_id: int, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    """Soft-deactivate a physical examination master item."""
    item = db.get(PhysicalExaminationMaster, exam_id)
    if not item:
        raise HTTPException(status_code=404, detail="Physical exam not found")
    item.is_active = False
    db.commit()
    invalidate_department_taxonomy_cache(redis, "physical_exams", item.department_id)
    return {"status": "success", "message": f"Physical exam ID {exam_id} deactivated."}


# -----------------------------------------------------------------------------
# Clinical safety domain
# -----------------------------------------------------------------------------
@router.post("/orders/check-allergy")
def verify_prescription_safety(
    payload: AllergyCheckRequest,
    db: Session = Depends(get_db),
    _: bool = Depends(RequirePermission("write_cpoe_orders")),
):
    """Check whether a patient has an active allergy conflict before prescribing."""
    check_allergy_hard_stop(db, payload.patient_id, payload.allergen_keywords)
    return {"status": "safe", "message": "CPOE verification passed: No clinical allergy conflicts detected."}
