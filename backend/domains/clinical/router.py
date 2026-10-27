from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.core.database import get_db, get_redis, resolve_tenant_context
from backend.core.dependencies import RequirePermission
from backend.domains.clinical.models import (
    ClinicalEarlyWarningScore,
    ClinicalNote,
    EarlyWarningRiskEnum,
    EncounterDiagnosis,
    EncounterPhysicalExam,
    EncounterVital,
    OrderSetItem,
    OrderSetItemTypeEnum,
    OrderSetMaster,
    OrderSetPriorityEnum,
    PatientComorbidity,
)
from backend.domains.encounters.models import DiagnosisTypeEnum, EncounterStatusEnum, PatientEncounter
from backend.domains.identity.models import User
from backend.domains.patients.models import AllergyMaster, ChiefComplaintMaster, ComorbidityMaster, DiagnosisMaster, PhysicalExaminationMaster, Patient

from backend.domains.clinical.schemas import EncounterVitalCreate, EncounterVitalResponse, ClinicalNoteCreate, ClinicalNoteResponse, ClinicalNoteUpdate, EncounterDiagnosisCreate, EncounterDiagnosisResponse, EncounterPhysicalExamCreate, EncounterPhysicalExamResponse, PatientComorbidityCreate, PatientComorbidityResponse
from backend.domains.patients.schemas import AllergyCreate, AllergyResponse, AllergyUpdate, ChiefComplaintCreate, ChiefComplaintResponse, ChiefComplaintUpdate, CPOEAllergyCheckRequest, ComorbidityResponse, ComorbidityCreate, ComorbidityUpdate, DiagnosisResponse, DiagnosisCreate, DiagnosisUpdate, PhysicalExamResponse, PhysicalExamCreate, PhysicalExamUpdate
from backend.domains.clinical.services import (
    calculate_news2_score,
    check_allergy_hard_stop,
    check_drug_disease_contraindications,
    check_drug_drug_interactions,
)
from backend.domains.patients.services import (
    get_department_taxonomy,
    invalidate_department_taxonomy_cache,
)
from backend.domains.privacy.runtime import (
    calculate_patient_age,
    evaluate_tenant_data_use,
)

router = APIRouter(prefix="/api/clinical", tags=["Clinical EMR & Taxonomies"])


def _evaluate_patient_read(
    db: Session, patient_id: int, actor: User, data_use_point: str
) -> None:
    patient = db.get(Patient, patient_id)
    if patient is None:
        return
    decision = evaluate_tenant_data_use(
        db,
        data_use_point=data_use_point,
        patient_id=patient.id,
        actor_user_id=actor.id,
        patient_age=calculate_patient_age(patient.date_of_birth),
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Privacy policy does not permit this patient disclosure.",
        )


def _evaluate_encounter_read(
    db: Session, encounter_id: int, actor: User, data_use_point: str
) -> PatientEncounter | None:
    encounter = db.get(PatientEncounter, encounter_id)
    if encounter is not None:
        _evaluate_patient_read(db, encounter.patient_id, actor, data_use_point)
    return encounter


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


@router.get(
    "/encounters/{encounter_id}/vitals",
    response_model=List[EncounterVitalResponse],
    openapi_extra={
        "x-privacy-data-use-point": "clinical.encounter.vitals.read"
    },
)
def list_encounter_vitals(
    encounter_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("adt:read")),
):
    if not _evaluate_encounter_read(
        db, encounter_id, actor, "clinical.encounter.vitals.read"
    ):
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


@router.get(
    "/encounters/{encounter_id}/notes",
    response_model=List[ClinicalNoteResponse],
    openapi_extra={"x-privacy-data-use-point": "clinical.encounter.notes.read"},
)
def list_soap_clinical_notes(
    encounter_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("adt:read")),
):
    if not _evaluate_encounter_read(
        db, encounter_id, actor, "clinical.encounter.notes.read"
    ):
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


@router.get(
    "/encounters/{encounter_id}/diagnoses",
    response_model=List[EncounterDiagnosisResponse],
    openapi_extra={
        "x-privacy-data-use-point": "clinical.encounter.diagnoses.read"
    },
)
def list_encounter_diagnoses(
    encounter_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("adt:read")),
):
    if not _evaluate_encounter_read(
        db, encounter_id, actor, "clinical.encounter.diagnoses.read"
    ):
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


@router.get(
    "/patients/{patient_id}/comorbidities",
    response_model=List[PatientComorbidityResponse],
    openapi_extra={
        "x-privacy-data-use-point": "clinical.patient.comorbidities.read"
    },
)
def list_patient_comorbidities(
    patient_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("patients:read", "opd:read", "adt:read")),
):
    _evaluate_patient_read(
        db, patient_id, actor, "clinical.patient.comorbidities.read"
    )
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


@router.get(
    "/encounters/{encounter_id}/physical-exams",
    response_model=List[EncounterPhysicalExamResponse],
    openapi_extra={
        "x-privacy-data-use-point": "clinical.encounter.physical_exams.read"
    },
)
def list_encounter_physical_exams(
    encounter_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("opd:read", "adt:read")),
):
    _evaluate_encounter_read(
        db, encounter_id, actor, "clinical.encounter.physical_exams.read"
    )
    stmt = (
        select(EncounterPhysicalExam)
        .options(joinedload(EncounterPhysicalExam.exam_master))
        .where(EncounterPhysicalExam.encounter_id == encounter_id)
        .order_by(EncounterPhysicalExam.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


# -----------------------------------------------------------------------------
# Enterprise CPOE Order Sets & Protocol Bundles
# -----------------------------------------------------------------------------

class OrderSetItemInput(BaseModel):
    item_type: OrderSetItemTypeEnum
    medication_id: Optional[int] = None
    service_id: Optional[int] = None
    dosage_instruction: Optional[str] = None
    quantity: int = 1
    priority: OrderSetPriorityEnum = OrderSetPriorityEnum.ROUTINE
    clinical_instructions: Optional[str] = None


class OrderSetCreateRequest(BaseModel):
    code: str
    title: str
    specialty: Optional[str] = "General"
    clinical_indication: Optional[str] = None
    items: List[OrderSetItemInput] = []


class OrderSetItemResponse(BaseModel):
    id: int
    item_type: str
    medication_id: Optional[int] = None
    service_id: Optional[int] = None
    dosage_instruction: Optional[str] = None
    quantity: int
    priority: str
    clinical_instructions: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class OrderSetMasterResponse(BaseModel):
    id: int
    code: str
    title: str
    specialty: Optional[str] = "General"
    clinical_indication: Optional[str] = None
    is_active: bool
    items: List[OrderSetItemResponse] = []
    model_config = ConfigDict(from_attributes=True)


class ApplyOrderSetPayload(BaseModel):
    order_set_id: int
    priority: Optional[str] = "ROUTINE"
    clinical_justification: Optional[str] = None
    override_ddi_warning: bool = False
    ddi_override_justification: Optional[str] = None
    override_contraindication: bool = False
    contraindication_override_justification: Optional[str] = None


class NEWS2EvaluationRequest(BaseModel):
    vital_id: Optional[int] = None
    respiratory_rate: Optional[int] = None
    spo2_percent: Optional[int] = None
    bp_systolic: Optional[int] = None
    pulse_bpm: Optional[int] = None
    consciousness: Optional[str] = "ALERT"
    temperature_f: Optional[float] = None
    on_supplemental_oxygen: bool = False
    spo2_scale: int = 1


@router.post("/order-sets", response_model=OrderSetMasterResponse, status_code=status.HTTP_201_CREATED)
def create_cpoe_order_set(
    payload: OrderSetCreateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("clinical:write", "mdm:write")),
):
    """Creates a standardized CPOE order set template (e.g. Sepsis protocol, Chest Pain pathway)."""
    existing = db.execute(
        select(OrderSetMaster).where(OrderSetMaster.code == payload.code)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Order set with code '{payload.code}' already exists.")

    order_set = OrderSetMaster(
        code=payload.code,
        title=payload.title,
        specialty=payload.specialty,
        clinical_indication=payload.clinical_indication,
        is_active=True,
    )
    db.add(order_set)
    db.flush()

    for item_in in payload.items:
        db.add(
            OrderSetItem(
                order_set_id=order_set.id,
                item_type=item_in.item_type,
                medication_id=item_in.medication_id,
                service_id=item_in.service_id,
                dosage_instruction=item_in.dosage_instruction,
                quantity=item_in.quantity,
                priority=item_in.priority,
                clinical_instructions=item_in.clinical_instructions,
            )
        )

    db.commit()
    db.refresh(order_set)
    return order_set


@router.get("/order-sets", response_model=List[OrderSetMasterResponse])
def list_cpoe_order_sets(
    specialty: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("clinical:read", "opd:read", "adt:read")),
):
    """Lists all active CPOE order set protocols, optionally filtered by medical specialty."""
    stmt = select(OrderSetMaster).options(joinedload(OrderSetMaster.items)).where(OrderSetMaster.is_active == True)
    if specialty:
        stmt = stmt.where(OrderSetMaster.specialty.ilike(f"%{specialty}%"))
    return list(db.execute(stmt.order_by(OrderSetMaster.title.asc())).unique().scalars().all())


@router.get("/order-sets/{order_set_id}", response_model=OrderSetMasterResponse)
def get_cpoe_order_set(
    order_set_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("clinical:read", "opd:read", "adt:read")),
):
    """Retrieves full details of an OrderSetMaster template including all line items."""
    stmt = (
        select(OrderSetMaster)
        .options(joinedload(OrderSetMaster.items))
        .where(OrderSetMaster.id == order_set_id, OrderSetMaster.is_active == True)
    )
    order_set = db.execute(stmt).unique().scalar_one_or_none()
    if not order_set:
        raise HTTPException(status_code=404, detail="Order set template not found.")
    return order_set


@router.post("/encounters/{encounter_id}/apply-order-set", status_code=status.HTTP_201_CREATED)
def apply_cpoe_order_set_to_encounter(
    encounter_id: int,
    payload: ApplyOrderSetPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("clinical:write", "opd:write", "adt:write")),
):
    """
    Atomically instantiates a standardized OrderSetMaster for an active clinical encounter.
    Executes clinical safety checks (Allergy, DDI, and Drug-Disease Contraindications),
    and creates Prescriptions, PrescriptionItems, DiagnosticOrders, and DiagnosticTasks in one transaction.
    """
    encounter = _get_active_encounter_or_raise(db, encounter_id)

    stmt = (
        select(OrderSetMaster)
        .options(joinedload(OrderSetMaster.items))
        .where(OrderSetMaster.id == payload.order_set_id, OrderSetMaster.is_active == True)
    )
    order_set = db.execute(stmt).unique().scalar_one_or_none()
    if not order_set:
        raise HTTPException(status_code=404, detail="Active order set template not found.")

    from backend.domains.pharmacy.models import (
        MedicationMaster,
        Prescription,
        PrescriptionItem,
        PrescriptionStatusEnum,
    )
    from backend.domains.diagnostics.models import (
        DiagnosticOrder,
        DiagnosticTask,
        OrderStatusEnum,
        TaskStatusEnum,
    )

    med_items = [it for it in order_set.items if it.item_type == OrderSetItemTypeEnum.MEDICATION and it.medication_id]
    lab_items = [it for it in order_set.items if it.item_type in (OrderSetItemTypeEnum.DIAGNOSTIC_LAB, OrderSetItemTypeEnum.RADIOLOGY) and it.service_id]

    prescription = None
    if med_items:
        med_ids = [it.medication_id for it in med_items]
        meds = list(db.execute(select(MedicationMaster).where(MedicationMaster.id.in_(med_ids))).scalars().all())

        # 1. Check Allergy hard-stop
        allergy_keywords = [m.brand_name for m in meds] + [m.generic_name for m in meds]
        check_allergy_hard_stop(db, encounter.patient_id, allergy_keywords)

        # 2. Check Drug-Drug Interactions
        check_drug_drug_interactions(
            session=db,
            patient_id=encounter.patient_id,
            candidate_medications=meds,
            encounter_id=encounter_id,
            override_warning=payload.override_ddi_warning,
            override_justification=payload.ddi_override_justification,
        )

        # 3. Check Drug-Disease Contraindications
        med_generics = [m.generic_name for m in meds]
        check_drug_disease_contraindications(
            session=db,
            patient_id=encounter.patient_id,
            medication_generics=med_generics,
            override_contraindication=payload.override_contraindication,
            override_justification=payload.contraindication_override_justification,
        )

        # Instantiate Prescription
        prescription = Prescription(
            encounter_id=encounter_id,
            prescribing_doctor_id=current_user.id,
            status=PrescriptionStatusEnum.ACTIVE,
            clinical_notes=f"CPOE Order Set applied: {order_set.title} ({order_set.code}). Justification: {payload.clinical_justification or 'Protocol order'}",
        )
        db.add(prescription)
        db.flush()

        for item in med_items:
            db.add(
                PrescriptionItem(
                    prescription_id=prescription.id,
                    medication_id=item.medication_id,
                    dosage_instruction=item.dosage_instruction or "As directed by clinical protocol",
                    requested_quantity=item.quantity,
                    dispensed_quantity=0,
                )
            )

    diag_order = None
    if lab_items:
        diag_order = DiagnosticOrder(
            patient_id=encounter.patient_id,
            ordering_doctor_id=current_user.id,
            clinical_notes=f"CPOE Order Set: {order_set.title} ({order_set.code})",
            status=OrderStatusEnum.ACTIVE,
        )
        db.add(diag_order)
        db.flush()

        for item in lab_items:
            db.add(
                DiagnosticTask(
                    order_id=diag_order.id,
                    service_id=item.service_id,
                    status=TaskStatusEnum.PENDING,
                )
            )

    db.commit()

    return {
        "status": "success",
        "message": f"Order set '{order_set.title}' successfully instantiated for encounter {encounter_id}.",
        "order_set_id": order_set.id,
        "order_set_code": order_set.code,
        "prescription_id": prescription.id if prescription else None,
        "diagnostic_order_id": diag_order.id if diag_order else None,
        "medication_count": len(med_items),
        "investigation_count": len(lab_items),
    }


# -----------------------------------------------------------------------------
# Enterprise Clinical Early Warning Scoring (NEWS2 / qSOFA)
# -----------------------------------------------------------------------------

class EarlyWarningScoreResponse(BaseModel):
    id: int
    encounter_id: int
    vital_id: Optional[int] = None
    evaluated_by_id: int
    news2_score: int
    risk_level: str
    qsofa_score: int
    is_rrt_alert_triggered: bool
    clinical_recommendation: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


@router.post("/encounters/{encounter_id}/vitals/news2", response_model=dict, status_code=status.HTTP_201_CREATED)
def evaluate_encounter_news2(
    encounter_id: int,
    payload: NEWS2EvaluationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("clinical:write", "opd:write", "adt:write")),
):
    """
    Computes National Early Warning Score 2 (NEWS2) & qSOFA for a patient encounter.
    Stores the score and automatically flags Rapid Response Team (RRT) triggers for critical deterioration.
    """
    _ = _get_active_encounter_or_raise(db, encounter_id)

    rr = payload.respiratory_rate
    spo2 = payload.spo2_percent
    sbp = payload.bp_systolic
    pulse = payload.pulse_bpm
    temp = payload.temperature_f
    consciousness = payload.consciousness or "ALERT"

    # Hydrate from EncounterVital if vital_id provided and fields were omitted
    if payload.vital_id:
        vital = db.get(EncounterVital, payload.vital_id)
        if vital and vital.encounter_id == encounter_id:
            if rr is None: rr = vital.respiratory_rate
            if spo2 is None: spo2 = vital.spo2_percent
            if sbp is None: sbp = vital.bp_systolic
            if pulse is None: pulse = vital.pulse_bpm
            if temp is None: temp = vital.temperature_f

    try:
        news_result = calculate_news2_score(
            respiratory_rate=rr,
            spo2_percent=spo2,
            bp_systolic=sbp,
            pulse_bpm=pulse,
            consciousness=consciousness,
            temperature_f=temp,
            on_supplemental_oxygen=payload.on_supplemental_oxygen,
            spo2_scale=payload.spo2_scale,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    score_rec = ClinicalEarlyWarningScore(
        encounter_id=encounter_id,
        vital_id=payload.vital_id,
        evaluated_by_id=current_user.id,
        news2_score=news_result["news2_score"],
        risk_level=EarlyWarningRiskEnum(news_result["risk_level"]),
        qsofa_score=news_result["qsofa_score"],
        is_rrt_alert_triggered=news_result["is_rrt_alert_triggered"],
        clinical_recommendation=news_result["clinical_recommendation"],
    )
    db.add(score_rec)
    db.commit()
    db.refresh(score_rec)

    return {
        "score_id": score_rec.id,
        "encounter_id": encounter_id,
        "news2_score": news_result["news2_score"],
        "risk_level": news_result["risk_level"],
        "qsofa_score": news_result["qsofa_score"],
        "is_rrt_alert_triggered": news_result["is_rrt_alert_triggered"],
        "is_complete_set": news_result["is_complete_set"],
        "missing_parameters": news_result["missing_parameters"],
        "sub_scores": news_result["sub_scores"],
        "clinical_recommendation": news_result["clinical_recommendation"],
    }


@router.get(
    "/encounters/{encounter_id}/early-warning-scores",
    response_model=List[EarlyWarningScoreResponse],
    openapi_extra={"x-privacy-data-use-point": "clinical.encounter.vitals.read"},
)
def list_early_warning_scores(
    encounter_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("clinical:read", "opd:read", "adt:read")),
):
    """Retrieves all historical NEWS2 and deterioration scores recorded for an encounter."""
    _evaluate_encounter_read(db, encounter_id, actor, "clinical.encounter.vitals.read")
    stmt = (
        select(ClinicalEarlyWarningScore)
        .where(ClinicalEarlyWarningScore.encounter_id == encounter_id)
        .order_by(ClinicalEarlyWarningScore.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())
