from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from database import get_db, get_redis, resolve_tenant_context
from dependencies import RequirePermission
from models import (
    AllergyMaster,
    ChiefComplaintMaster,
    ClinicalNote,
    ComorbidityMaster,
    DiagnosisMaster,
    DiagnosisTypeEnum,
    EncounterDiagnosis,
    EncounterStatusEnum,
    EncounterVital,
    PatientEncounter,
    PhysicalExaminationMaster,
    User,
    EncounterPhysicalExam,
    Patient,
    PatientComorbidity,
)

from schemas.v1.schemas import (
    AllergyCreate,
    AllergyResponse,
    AllergyUpdate,
    ChiefComplaintCreate,
    ChiefComplaintResponse,
    ChiefComplaintUpdate,
    CPOEAllergyCheckRequest,
    ComorbidityResponse,
    ComorbidityCreate,
    ComorbidityUpdate,
    DiagnosisResponse,
    DiagnosisCreate,
    DiagnosisUpdate,
    PhysicalExamResponse,
    PhysicalExamCreate,
    PhysicalExamUpdate,
    EncounterVitalCreate,
    EncounterVitalResponse,
    ClinicalNoteCreate,
    ClinicalNoteResponse,
    ClinicalNoteUpdate,
    EncounterDiagnosisCreate,
    EncounterDiagnosisResponse,
    EncounterPhysicalExamCreate,
    EncounterPhysicalExamResponse,
    PatientComorbidityCreate,
    PatientComorbidityResponse,
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


def _get_active_encounter_or_raise(db: Session, encounter_id: int) -> PatientEncounter:
    enc = db.get(PatientEncounter, encounter_id)
    if not enc:
        raise HTTPException(status_code=404, detail="Encounter not found.")
    if enc.status != EncounterStatusEnum.ACTIVE:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot record clinical chart entries on a {enc.status.value} encounter.",
        )
    return enc


@router.post(
    "/encounters/{encounter_id}/vitals",
    response_model=EncounterVitalResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_encounter_vitals(
    encounter_id: int,
    payload: EncounterVitalCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("adt:write")),
):
    _get_active_encounter_or_raise(db, encounter_id)

    calculated_bmi: Optional[float] = None
    if payload.weight_kg and payload.height_cm:
        height_m = payload.height_cm / 100.0
        calculated_bmi = round(payload.weight_kg / (height_m * height_m), 2)

    vital = EncounterVital(
        encounter_id=encounter_id,
        bmi=calculated_bmi,
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
        **payload.model_dump(),
    )
    db.add(vital)
    db.commit()
    db.refresh(vital)
    return vital


@router.get("/encounters/{encounter_id}/vitals", response_model=List[EncounterVitalResponse])
def list_encounter_vitals(
    encounter_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read")),
):
    if not db.get(PatientEncounter, encounter_id):
        raise HTTPException(status_code=404, detail="Encounter not found.")
    stmt = (
        select(EncounterVital)
        .where(EncounterVital.encounter_id == encounter_id)
        .order_by(EncounterVital.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


@router.post(
    "/encounters/{encounter_id}/notes",
    response_model=ClinicalNoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_soap_clinical_note(
    encounter_id: int,
    payload: ClinicalNoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("opd:write")),
):
    _get_active_encounter_or_raise(db, encounter_id)
    note = ClinicalNote(
        encounter_id=encounter_id,
        is_signed=False,
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
        **payload.model_dump(),
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.get("/encounters/{encounter_id}/notes", response_model=List[ClinicalNoteResponse])
def list_soap_clinical_notes(
    encounter_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read")),
):
    if not db.get(PatientEncounter, encounter_id):
        raise HTTPException(status_code=404, detail="Encounter not found.")
    stmt = (
        select(ClinicalNote)
        .where(ClinicalNote.encounter_id == encounter_id)
        .order_by(ClinicalNote.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


@router.put("/notes/{note_id}", response_model=ClinicalNoteResponse)
def update_soap_clinical_note(
    note_id: int,
    payload: ClinicalNoteUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("opd:write")),
):
    note = db.get(ClinicalNote, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Clinical note not found.")
    if note.is_signed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Medico-Legal Lock: Signed clinical notes are immutable and cannot be modified.",
        )
    _get_active_encounter_or_raise(db, note.encounter_id)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(note, field, value)
    note.updated_by_id = current_user.id

    db.commit()
    db.refresh(note)
    return note


@router.patch("/notes/{note_id}/sign", response_model=ClinicalNoteResponse)
def sign_off_clinical_note(
    note_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("opd:write")),
):
    note = db.get(ClinicalNote, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Clinical note not found.")
    if note.is_signed:
        raise HTTPException(status_code=409, detail="Clinical note is already signed.")

    note.is_signed = True
    note.signed_at = datetime.now(timezone.utc)
    note.signed_by_id = current_user.id
    note.updated_by_id = current_user.id
    db.commit()
    db.refresh(note)
    return note


@router.post(
    "/encounters/{encounter_id}/diagnoses",
    response_model=EncounterDiagnosisResponse,
    status_code=status.HTTP_201_CREATED,
)
def attach_encounter_diagnosis(
    encounter_id: int,
    payload: EncounterDiagnosisCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("opd:write")),
):
    _get_active_encounter_or_raise(db, encounter_id)

    diag_master = db.get(DiagnosisMaster, payload.diagnosis_id)
    if not diag_master or not diag_master.is_active:
        raise HTTPException(status_code=404, detail="Active ICD-10 diagnosis not found.")

    if payload.diagnosis_type == DiagnosisTypeEnum.PRIMARY:
        existing_primary = db.execute(
            select(EncounterDiagnosis).where(
                EncounterDiagnosis.encounter_id == encounter_id,
                EncounterDiagnosis.diagnosis_type == DiagnosisTypeEnum.PRIMARY,
            )
        ).scalar_one_or_none()
        if existing_primary:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Encounter already has a PRIMARY diagnosis. Use SECONDARY or PROVISIONAL.",
            )

    dup_diag = db.execute(
        select(EncounterDiagnosis).where(
            EncounterDiagnosis.encounter_id == encounter_id,
            EncounterDiagnosis.diagnosis_id == payload.diagnosis_id,
        )
    ).scalar_one_or_none()
    if dup_diag:
        raise HTTPException(status_code=409, detail="This ICD-10 diagnosis is already attached to the encounter.")

    enc_diag = EncounterDiagnosis(
        encounter_id=encounter_id,
        diagnosis_id=payload.diagnosis_id,
        diagnosis_type=payload.diagnosis_type,
        clinical_remarks=payload.clinical_remarks,
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
    )
    db.add(enc_diag)
    db.commit()

    stmt = (
        select(EncounterDiagnosis)
        .options(joinedload(EncounterDiagnosis.diagnosis))
        .where(EncounterDiagnosis.id == enc_diag.id)
    )
    return db.execute(stmt).scalar_one()


@router.get("/encounters/{encounter_id}/diagnoses", response_model=List[EncounterDiagnosisResponse])
def list_encounter_diagnoses(
    encounter_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read")),
):
    if not db.get(PatientEncounter, encounter_id):
        raise HTTPException(status_code=404, detail="Encounter not found.")
    stmt = (
        select(EncounterDiagnosis)
        .options(joinedload(EncounterDiagnosis.diagnosis))
        .where(EncounterDiagnosis.encounter_id == encounter_id)
        .order_by(EncounterDiagnosis.id.asc())
    )
    return list(db.execute(stmt).scalars().all())

# =====================================================================
# PHASE 15A: PATIENT COMORBIDITIES & ENCOUNTER SYSTEMIC PHYSICAL EXAMS
# =====================================================================

@router.post(
    "/patients/{patient_id}/comorbidities",
    response_model=PatientComorbidityResponse,
    status_code=status.HTTP_201_CREATED,
)
def chart_patient_comorbidity(
    patient_id: int,
    payload: PatientComorbidityCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("patients:write", "opd:write", "adt:write")),
):
    patient = db.get(Patient, patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=404, detail="Active patient not found.")

    cm = db.get(ComorbidityMaster, payload.comorbidity_id)
    if not cm or not cm.is_active:
        raise HTTPException(status_code=404, detail="Active comorbidity master item not found.")

    existing = db.execute(
        select(PatientComorbidity).where(
            PatientComorbidity.patient_id == patient_id,
            PatientComorbidity.comorbidity_id == payload.comorbidity_id,
        )
    ).scalar_one_or_none()

    if existing:
        existing.clinical_remarks = payload.clinical_remarks
        existing.is_active = True
        existing.recorded_by_id = current_user.id
        db.commit()
        pc_id = existing.id
    else:
        pc = PatientComorbidity(
            patient_id=patient_id,
            comorbidity_id=payload.comorbidity_id,
            recorded_by_id=current_user.id,
            clinical_remarks=payload.clinical_remarks,
            is_active=True,
        )
        db.add(pc)
        db.commit()
        pc_id = pc.id

    stmt = (
        select(PatientComorbidity)
        .options(joinedload(PatientComorbidity.comorbidity))
        .where(PatientComorbidity.id == pc_id)
    )
    return db.execute(stmt).scalar_one()


@router.get("/patients/{patient_id}/comorbidities", response_model=List[PatientComorbidityResponse])
def list_patient_comorbidities(
    patient_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("patients:read", "opd:read", "adt:read")),
):
    stmt = (
        select(PatientComorbidity)
        .options(joinedload(PatientComorbidity.comorbidity))
        .where(PatientComorbidity.patient_id == patient_id, PatientComorbidity.is_active == True)
        .order_by(PatientComorbidity.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


@router.post(
    "/encounters/{encounter_id}/physical-exams",
    response_model=EncounterPhysicalExamResponse,
    status_code=status.HTTP_201_CREATED,
)
def record_encounter_physical_exam(
    encounter_id: int,
    payload: EncounterPhysicalExamCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("opd:write", "adt:write")),
):
    _get_active_encounter_or_raise(db, encounter_id)
    exam_master = db.get(PhysicalExaminationMaster, payload.exam_master_id)
    if not exam_master or not exam_master.is_active:
        raise HTTPException(status_code=404, detail="Active physical examination master item not found.")

    if not payload.is_normal and (not payload.findings_notes or len(payload.findings_notes.strip()) < 3):
        raise HTTPException(
            status_code=400,
            detail="Clinical findings_notes are mandatory when marking a physical examination as abnormal (is_normal=False).",
        )

    rec = EncounterPhysicalExam(
        encounter_id=encounter_id,
        exam_master_id=payload.exam_master_id,
        recorded_by_id=current_user.id,
        is_normal=payload.is_normal,
        findings_notes=payload.findings_notes,
    )
    db.add(rec)
    db.commit()

    stmt = (
        select(EncounterPhysicalExam)
        .options(joinedload(EncounterPhysicalExam.exam_master))
        .where(EncounterPhysicalExam.id == rec.id)
    )
    return db.execute(stmt).scalar_one()


@router.get("/encounters/{encounter_id}/physical-exams", response_model=List[EncounterPhysicalExamResponse])
def list_encounter_physical_exams(
    encounter_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:read", "adt:read")),
):
    stmt = (
        select(EncounterPhysicalExam)
        .options(joinedload(EncounterPhysicalExam.exam_master))
        .where(EncounterPhysicalExam.encounter_id == encounter_id)
        .order_by(EncounterPhysicalExam.id.asc())
    )
    return list(db.execute(stmt).scalars().all())