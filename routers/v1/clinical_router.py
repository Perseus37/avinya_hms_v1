from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from database import get_db, get_redis, resolve_tenant_context
from dependencies import RequirePermission
from models import (
    AllergyMaster,
    ChiefComplaintMaster,
    ComorbidityMaster,
    DiagnosisMaster,
    PhysicalExaminationMaster,
    User,
)
from schemas.v1.schemas import (
    AllergyCreate,
    AllergyResponse,
    AllergyUpdate,
    CPOEAllergyCheckRequest,
    ChiefComplaintCreate,
    ChiefComplaintResponse,
    ChiefComplaintUpdate,
    ComorbidityCreate,
    ComorbidityResponse,
    ComorbidityUpdate,
    DiagnosisCreate,
    DiagnosisResponse,
    DiagnosisUpdate,
    PhysicalExamCreate,
    PhysicalExamResponse,
    PhysicalExamUpdate,
)
from services.cpoe import check_allergy_hard_stop
from services.taxonomy_cache import get_department_taxonomy, invalidate_department_taxonomy_cache

router = APIRouter(prefix="/api/clinical", tags=["Clinical EMR & Taxonomies"])


@router.get("/departments/{department_id}/taxonomy/{taxonomy_type}")
def fetch_department_taxonomy(
    department_id: int,
    taxonomy_type: str,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    _: User = Depends(RequirePermission("mdm:read", "patients:read", "opd:read")),
):
    valid_types = ["allergies", "complaints", "comorbidities", "diagnoses", "physical_exams"]
    if taxonomy_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid taxonomy type. Choose from {valid_types}")

    tenant = resolve_tenant_context(request)
    data = get_department_taxonomy(
        redis_client=redis,
        session=db,
        taxonomy=taxonomy_type,
        department_id=department_id,
        tenant_slug=tenant.subdomain_slug,
    )
    return {"department_id": department_id, "tenant": tenant.subdomain_slug, "taxonomy": taxonomy_type, "items": data}


# --- Allergies ---
@router.post("/allergies", response_model=AllergyResponse, status_code=status.HTTP_201_CREATED)
def create_allergy(
    payload: AllergyCreate,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    tenant = resolve_tenant_context(request)
    allergy = AllergyMaster(**payload.model_dump(), is_active=True, created_by_id=current_user.id, updated_by_id=current_user.id)
    db.add(allergy)
    db.commit()
    db.refresh(allergy)
    invalidate_department_taxonomy_cache(redis, "allergies", allergy.department_id, tenant.subdomain_slug)
    return allergy


@router.put("/allergies/{item_id}", response_model=AllergyResponse)
def update_allergy(
    item_id: int,
    payload: AllergyUpdate,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    tenant = resolve_tenant_context(request)
    allergy = db.get(AllergyMaster, item_id)
    if not allergy:
        raise HTTPException(status_code=404, detail="Allergy not found")
    old_dept_id = allergy.department_id
    if payload.code is not None:
        allergy.code = payload.code
    if payload.name is not None:
        allergy.name = payload.name
    if payload.category is not None:
        allergy.category = payload.category
    if payload.default_severity is not None:
        allergy.default_severity = payload.default_severity
    if payload.is_active is not None:
        allergy.is_active = payload.is_active
    allergy.updated_by_id = current_user.id
    db.commit()
    db.refresh(allergy)
    invalidate_department_taxonomy_cache(redis, "allergies", old_dept_id, tenant.subdomain_slug)
    return allergy


@router.delete("/allergies/{item_id}", status_code=200)
def deactivate_allergy(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    current_user: User = Depends(RequirePermission("mdm:delete")),
):
    tenant = resolve_tenant_context(request)
    allergy = db.get(AllergyMaster, item_id)
    if not allergy:
        raise HTTPException(status_code=404, detail="Allergy not found")
    allergy.is_active = False
    allergy.updated_by_id = current_user.id
    db.commit()
    invalidate_department_taxonomy_cache(redis, "allergies", allergy.department_id, tenant.subdomain_slug)
    return {"status": "success", "message": "Allergy deactivated"}


# --- Chief Complaints ---
@router.post("/complaints", response_model=ChiefComplaintResponse, status_code=status.HTTP_201_CREATED)
def create_complaint(
    payload: ChiefComplaintCreate,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    tenant = resolve_tenant_context(request)
    complaint = ChiefComplaintMaster(**payload.model_dump(), is_active=True, created_by_id=current_user.id, updated_by_id=current_user.id)
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    invalidate_department_taxonomy_cache(redis, "complaints", complaint.department_id, tenant.subdomain_slug)
    return complaint


@router.put("/complaints/{item_id}", response_model=ChiefComplaintResponse)
def update_complaint(
    item_id: int,
    payload: ChiefComplaintUpdate,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    tenant = resolve_tenant_context(request)
    complaint = db.get(ChiefComplaintMaster, item_id)
    if not complaint:
        raise HTTPException(status_code=404, detail="Chief complaint not found")
    if payload.code is not None:
        complaint.code = payload.code
    if payload.name is not None:
        complaint.name = payload.name
    if payload.is_active is not None:
        complaint.is_active = payload.is_active
    complaint.updated_by_id = current_user.id
    db.commit()
    db.refresh(complaint)
    invalidate_department_taxonomy_cache(redis, "complaints", complaint.department_id, tenant.subdomain_slug)
    return complaint


@router.delete("/complaints/{item_id}", status_code=200)
def deactivate_complaint(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    current_user: User = Depends(RequirePermission("mdm:delete")),
):
    tenant = resolve_tenant_context(request)
    complaint = db.get(ChiefComplaintMaster, item_id)
    if not complaint:
        raise HTTPException(status_code=404, detail="Chief complaint not found")
    complaint.is_active = False
    complaint.updated_by_id = current_user.id
    db.commit()
    invalidate_department_taxonomy_cache(redis, "complaints", complaint.department_id, tenant.subdomain_slug)
    return {"status": "success", "message": "Chief complaint deactivated"}


# --- Comorbidities ---
@router.post("/comorbidities", response_model=ComorbidityResponse, status_code=status.HTTP_201_CREATED)
def create_comorbidity(
    payload: ComorbidityCreate,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    tenant = resolve_tenant_context(request)
    comorbidity = ComorbidityMaster(**payload.model_dump(), is_active=True, created_by_id=current_user.id, updated_by_id=current_user.id)
    db.add(comorbidity)
    db.commit()
    db.refresh(comorbidity)
    invalidate_department_taxonomy_cache(redis, "comorbidities", comorbidity.department_id, tenant.subdomain_slug)
    return comorbidity


@router.put("/comorbidities/{item_id}", response_model=ComorbidityResponse)
def update_comorbidity(
    item_id: int,
    payload: ComorbidityUpdate,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    tenant = resolve_tenant_context(request)
    comorbidity = db.get(ComorbidityMaster, item_id)
    if not comorbidity:
        raise HTTPException(status_code=404, detail="Comorbidity not found")
    if payload.code is not None:
        comorbidity.code = payload.code
    if payload.name is not None:
        comorbidity.name = payload.name
    if payload.icd10_code is not None:
        comorbidity.icd10_code = payload.icd10_code
    if payload.is_active is not None:
        comorbidity.is_active = payload.is_active
    comorbidity.updated_by_id = current_user.id
    db.commit()
    db.refresh(comorbidity)
    invalidate_department_taxonomy_cache(redis, "comorbidities", comorbidity.department_id, tenant.subdomain_slug)
    return comorbidity


@router.delete("/comorbidities/{item_id}", status_code=200)
def deactivate_comorbidity(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    current_user: User = Depends(RequirePermission("mdm:delete")),
):
    tenant = resolve_tenant_context(request)
    comorbidity = db.get(ComorbidityMaster, item_id)
    if not comorbidity:
        raise HTTPException(status_code=404, detail="Comorbidity not found")
    comorbidity.is_active = False
    comorbidity.updated_by_id = current_user.id
    db.commit()
    invalidate_department_taxonomy_cache(redis, "comorbidities", comorbidity.department_id, tenant.subdomain_slug)
    return {"status": "success", "message": "Comorbidity deactivated"}


# --- ICD-10 Diagnoses ---
@router.post("/diagnoses", response_model=DiagnosisResponse, status_code=status.HTTP_201_CREATED)
def create_diagnosis(
    payload: DiagnosisCreate,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    tenant = resolve_tenant_context(request)
    diagnosis = DiagnosisMaster(**payload.model_dump(), is_active=True, created_by_id=current_user.id, updated_by_id=current_user.id)
    db.add(diagnosis)
    db.commit()
    db.refresh(diagnosis)
    invalidate_department_taxonomy_cache(redis, "diagnoses", diagnosis.department_id, tenant.subdomain_slug)
    return diagnosis


@router.put("/diagnoses/{item_id}", response_model=DiagnosisResponse)
def update_diagnosis(
    item_id: int,
    payload: DiagnosisUpdate,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    tenant = resolve_tenant_context(request)
    diagnosis = db.get(DiagnosisMaster, item_id)
    if not diagnosis:
        raise HTTPException(status_code=404, detail="Diagnosis not found")
    if payload.icd10_code is not None:
        diagnosis.icd10_code = payload.icd10_code
    if payload.short_description is not None:
        diagnosis.short_description = payload.short_description
    if payload.full_description is not None:
        diagnosis.full_description = payload.full_description
    if payload.is_active is not None:
        diagnosis.is_active = payload.is_active
    diagnosis.updated_by_id = current_user.id
    db.commit()
    db.refresh(diagnosis)
    invalidate_department_taxonomy_cache(redis, "diagnoses", diagnosis.department_id, tenant.subdomain_slug)
    return diagnosis


@router.delete("/diagnoses/{item_id}", status_code=200)
def deactivate_diagnosis(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    current_user: User = Depends(RequirePermission("mdm:delete")),
):
    tenant = resolve_tenant_context(request)
    diagnosis = db.get(DiagnosisMaster, item_id)
    if not diagnosis:
        raise HTTPException(status_code=404, detail="Diagnosis not found")
    diagnosis.is_active = False
    diagnosis.updated_by_id = current_user.id
    db.commit()
    invalidate_department_taxonomy_cache(redis, "diagnoses", diagnosis.department_id, tenant.subdomain_slug)
    return {"status": "success", "message": "Diagnosis deactivated"}


# --- Physical Examinations ---
@router.post("/physical-exams", response_model=PhysicalExamResponse, status_code=status.HTTP_201_CREATED)
def create_physical_exam(
    payload: PhysicalExamCreate,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    tenant = resolve_tenant_context(request)
    exam = PhysicalExaminationMaster(**payload.model_dump(), is_active=True, created_by_id=current_user.id, updated_by_id=current_user.id)
    db.add(exam)
    db.commit()
    db.refresh(exam)
    invalidate_department_taxonomy_cache(redis, "physical_exams", exam.department_id, tenant.subdomain_slug)
    return exam


@router.put("/physical-exams/{item_id}", response_model=PhysicalExamResponse)
def update_physical_exam(
    item_id: int,
    payload: PhysicalExamUpdate,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    current_user: User = Depends(RequirePermission("mdm:write")),
):
    tenant = resolve_tenant_context(request)
    exam = db.get(PhysicalExaminationMaster, item_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Physical exam item not found")
    if payload.code is not None:
        exam.code = payload.code
    if payload.name is not None:
        exam.name = payload.name
    if payload.body_system is not None:
        exam.body_system = payload.body_system
    if payload.is_active is not None:
        exam.is_active = payload.is_active
    exam.updated_by_id = current_user.id
    db.commit()
    db.refresh(exam)
    invalidate_department_taxonomy_cache(redis, "physical_exams", exam.department_id, tenant.subdomain_slug)
    return exam


@router.delete("/physical-exams/{item_id}", status_code=200)
def deactivate_physical_exam(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    redis=Depends(get_redis),
    current_user: User = Depends(RequirePermission("mdm:delete")),
):
    tenant = resolve_tenant_context(request)
    exam = db.get(PhysicalExaminationMaster, item_id)
    if not exam:
        raise HTTPException(status_code=404, detail="Physical exam item not found")
    exam.is_active = False
    exam.updated_by_id = current_user.id
    db.commit()
    invalidate_department_taxonomy_cache(redis, "physical_exams", exam.department_id, tenant.subdomain_slug)
    return {"status": "success", "message": "Physical exam item deactivated"}


@router.post("/cpoe/allergy-check")
@router.post("/orders/check-allergy")
def run_cpoe_allergy_check(
    payload: CPOEAllergyCheckRequest,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("patients:read", "pharmacy:write")),
):
    check_allergy_hard_stop(db, payload.patient_id, payload.allergen_keywords)
    return {"status": "SAFE", "message": "No severe/anaphylactic allergy conflicts detected."}