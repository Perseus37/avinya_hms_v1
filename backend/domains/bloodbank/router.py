from datetime import datetime, timezone
from typing import Dict, List, Set

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.core.database import get_db
from backend.core.dependencies import RequirePermission
from backend.domains.bloodbank.models import BloodBagInventory, BloodBagStatusEnum, BloodComponentTypeEnum, BloodCrossMatchOrder, RadiologyStudyMetadata, TransfusionReactionTypeEnum
from backend.domains.encounters.models import EncounterStatusEnum, PatientEncounter
from backend.domains.identity.models import BloodGroupEnum, User
from backend.domains.diagnostics.models import DiagnosticTask, ServiceTypeEnum
from backend.domains.bloodbank.schemas import BloodBagCreate, BloodBagResponse, BloodCrossMatchCreate, BloodCrossMatchResponse, RadiologyStudyResponse, RadiologyStudyUpsert, TransfusionReactionRequest
from backend.domains.billing.services import auto_bill_blood_transfusion_issue
from backend.domains.privacy.runtime import evaluate_tenant_patient_data_uses

router = APIRouter(prefix="/api", tags=["Blood Bank Transfusion Safety & Radiology PACS/DICOM"])


def _to_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# Biological Red Cell (PRBC / Whole Blood) Donor-to-Recipient Compatibility Matrix
# Key = Recipient Blood Group -> Value = Set of Compatible Donor Blood Groups
PRBC_COMPATIBILITY_MATRIX: Dict[BloodGroupEnum, Set[BloodGroupEnum]] = {
    BloodGroupEnum.O_NEG: {BloodGroupEnum.O_NEG},
    BloodGroupEnum.O_POS: {BloodGroupEnum.O_NEG, BloodGroupEnum.O_POS},
    BloodGroupEnum.A_NEG: {BloodGroupEnum.O_NEG, BloodGroupEnum.A_NEG},
    BloodGroupEnum.A_POS: {BloodGroupEnum.O_NEG, BloodGroupEnum.O_POS, BloodGroupEnum.A_NEG, BloodGroupEnum.A_POS},
    BloodGroupEnum.B_NEG: {BloodGroupEnum.O_NEG, BloodGroupEnum.B_NEG},
    BloodGroupEnum.B_POS: {BloodGroupEnum.O_NEG, BloodGroupEnum.O_POS, BloodGroupEnum.B_NEG, BloodGroupEnum.B_POS},
    BloodGroupEnum.AB_NEG: {BloodGroupEnum.O_NEG, BloodGroupEnum.A_NEG, BloodGroupEnum.B_NEG, BloodGroupEnum.AB_NEG},
    BloodGroupEnum.AB_POS: {
        BloodGroupEnum.O_NEG,
        BloodGroupEnum.O_POS,
        BloodGroupEnum.A_NEG,
        BloodGroupEnum.A_POS,
        BloodGroupEnum.B_NEG,
        BloodGroupEnum.B_POS,
        BloodGroupEnum.AB_NEG,
        BloodGroupEnum.AB_POS,
    },
}


# =====================================================================
# 1. BLOOD BANK INVENTORY, ABO/RH CROSS-MATCHING & TRANSFUSION SAFETY
# =====================================================================

@router.post("/blood-bank/bags", response_model=BloodBagResponse, status_code=status.HTTP_201_CREATED)
def receive_blood_bag(
    payload: BloodBagCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("diagnostics:write", "pharmacy:write")),
):
    dup = db.execute(select(BloodBagInventory).where(BloodBagInventory.bag_number == payload.bag_number)).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail=f"Blood bag '{payload.bag_number}' already exists in inventory.")

    now_utc = datetime.now(timezone.utc)
    if _to_utc(payload.expiry_date) <= now_utc:
        raise HTTPException(status_code=400, detail="Cannot receive an already expired blood bag into inventory.")
    if _to_utc(payload.expiry_date) <= _to_utc(payload.collection_date):
        raise HTTPException(status_code=400, detail="expiry_date must be after collection_date.")

    bag = BloodBagInventory(**payload.model_dump(), status=BloodBagStatusEnum.AVAILABLE)
    db.add(bag)
    db.commit()
    db.refresh(bag)
    return bag


@router.get("/blood-bank/bags", response_model=List[BloodBagResponse])
def list_blood_bags(
    blood_group: BloodGroupEnum | None = None,
    bag_status: BloodBagStatusEnum | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("diagnostics:read", "adt:read", "opd:read")),
):
    stmt = select(BloodBagInventory)
    if blood_group:
        stmt = stmt.where(BloodBagInventory.blood_group == blood_group)
    if bag_status:
        stmt = stmt.where(BloodBagInventory.status == bag_status)
    return list(db.execute(stmt.order_by(BloodBagInventory.id.asc())).scalars().all())


@router.post(
    "/blood-bank/cross-match",
    response_model=BloodCrossMatchResponse,
    status_code=status.HTTP_201_CREATED,
)
def cross_match_and_reserve_blood_bag(
    payload: BloodCrossMatchCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("diagnostics:verify", "diagnostics:write")),
):
    """
    Performs ABO/Rh compatibility verification between Patient and Donor Blood Bag.
    Blocks incompatible blood types with a 409 Biological Hard-Stop.
    """
    enc = (
        db.execute(
            select(PatientEncounter)
            .options(joinedload(PatientEncounter.patient))
            .where(PatientEncounter.id == payload.encounter_id)
        )
        .scalar_one_or_none()
    )
    if not enc or enc.status != EncounterStatusEnum.ACTIVE:
        raise HTTPException(status_code=400, detail="Active patient encounter required for blood cross-matching.")

    patient = enc.patient
    if not patient.blood_group:
        raise HTTPException(
            status_code=400,
            detail="Patient blood_group is not recorded on MPI chart. Cannot perform cross-match.",
        )

    bag = db.execute(
        select(BloodBagInventory).where(BloodBagInventory.id == payload.blood_bag_id).with_for_update()
    ).scalar_one_or_none()
    if not bag:
        raise HTTPException(status_code=404, detail="Blood bag not found.")
    if bag.status != BloodBagStatusEnum.AVAILABLE:
        raise HTTPException(status_code=409, detail=f"Blood bag '{bag.bag_number}' is currently {bag.status.value}.")
    if _to_utc(bag.expiry_date) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail=f"Blood bag '{bag.bag_number}' has expired and cannot be cross-matched.")

    # Enforce Biological ABO/Rh Compatibility Matrix
    if bag.component_type in [BloodComponentTypeEnum.PRBC, BloodComponentTypeEnum.WHOLE_BLOOD]:
        compatible_donors = PRBC_COMPATIBILITY_MATRIX.get(patient.blood_group, set())
        if bag.blood_group not in compatible_donors:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"BIOLOGICAL HARD-STOP: Incompatible ABO/Rh blood group! "
                    f"Cannot transfuse {bag.blood_group.value} {bag.component_type.value} to a {patient.blood_group.value} recipient."
                ),
            )
    elif bag.component_type == BloodComponentTypeEnum.FFP:
        # For Plasma (FFP), AB is the universal donor; otherwise group must match recipient's compatible plasma
        if bag.blood_group not in {patient.blood_group, BloodGroupEnum.AB_POS, BloodGroupEnum.AB_NEG}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"BIOLOGICAL HARD-STOP: Incompatible FFP plasma group! "
                    f"Cannot transfuse {bag.blood_group.value} FFP to a {patient.blood_group.value} recipient."
                ),
            )
    # CLIN-001: Enforce specific biological compatibility matrices for Platelets and Cryoprecipitate
    elif bag.component_type in [BloodComponentTypeEnum.PLATELETS, BloodComponentTypeEnum.CRYOPRECIPITATE]:
        if bag.blood_group != patient.blood_group:
            # Strictest rule: require an exact ABO/Rh match for Platelets/Cryo
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"BIOLOGICAL HARD-STOP: Incompatible Platelet/Cryo group! "
                    f"Cannot transfuse {bag.blood_group.value} {bag.component_type.value} to a {patient.blood_group.value} recipient. Exact match required."
                ),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail=f"No biological compatibility rule defined for {bag.component_type.value}."
        )

    bag.status = BloodBagStatusEnum.CROSS_MATCHED
    cm_order = BloodCrossMatchOrder(
        encounter_id=enc.id,
        patient_id=patient.id,
        blood_bag_id=bag.id,
        ordered_by_doctor_id=payload.ordered_by_doctor_id,
        cross_matched_by_id=current_user.id,
        reaction_type=TransfusionReactionTypeEnum.NONE,
    )
    db.add(cm_order)
    db.commit()

    stmt = (
        select(BloodCrossMatchOrder)
        .options(joinedload(BloodCrossMatchOrder.blood_bag))
        .where(BloodCrossMatchOrder.id == cm_order.id)
    )
    return db.execute(stmt).scalar_one()


@router.patch("/blood-bank/cross-match/{cross_match_id}/issue", response_model=BloodCrossMatchResponse)
def issue_blood_bag_for_transfusion(
    cross_match_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("diagnostics:write", "adt:write")),
):
    """Issues a CROSS_MATCHED blood bag to the ward/OT and auto-bills the processing fee."""
    stmt = (
        select(BloodCrossMatchOrder)
        .options(joinedload(BloodCrossMatchOrder.blood_bag))
        .where(BloodCrossMatchOrder.id == cross_match_id)
        .with_for_update()
    )
    cm = db.execute(stmt).scalar_one_or_none()
    if not cm:
        raise HTTPException(status_code=404, detail="Cross-match order not found.")
    if cm.blood_bag.status != BloodBagStatusEnum.CROSS_MATCHED:
        raise HTTPException(
            status_code=400,
            detail=f"Blood bag must be in CROSS_MATCHED state to issue (current: {cm.blood_bag.status.value}).",
        )

    cm.blood_bag.status = BloodBagStatusEnum.ISSUED
    cm.issued_at = datetime.now(timezone.utc)
    cm.issued_by_id = current_user.id

    auto_bill_blood_transfusion_issue(db, cm)

    db.commit()
    db.refresh(cm)
    return cm


@router.patch("/blood-bank/cross-match/{cross_match_id}/reaction", response_model=BloodCrossMatchResponse)
def report_transfusion_reaction(
    cross_match_id: int,
    payload: TransfusionReactionRequest,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:write", "opd:write")),
):
    """Halts transfusion and quarantines the blood bag as REACTION_HALTED upon an adverse reaction."""
    stmt = (
        select(BloodCrossMatchOrder)
        .options(joinedload(BloodCrossMatchOrder.blood_bag))
        .where(BloodCrossMatchOrder.id == cross_match_id)
        .with_for_update()
    )
    cm = db.execute(stmt).scalar_one_or_none()
    if not cm:
        raise HTTPException(status_code=404, detail="Cross-match order not found.")
    if cm.issued_at is None:
        raise HTTPException(status_code=400, detail="Cannot report a transfusion reaction on an unissued blood bag.")

    cm.reaction_type = payload.reaction_type
    cm.reaction_notes = payload.reaction_notes
    if payload.reaction_type != TransfusionReactionTypeEnum.NONE:
        cm.blood_bag.status = BloodBagStatusEnum.REACTION_HALTED

    db.commit()
    db.refresh(cm)
    return cm


# =====================================================================
# 2. RADIOLOGY PACS / DICOM STUDY METADATA INTEGRATION
# =====================================================================

@router.put(
    "/diagnostics/tasks/{task_id}/dicom-study",
    response_model=RadiologyStudyResponse,
)
def attach_pacs_dicom_study_metadata(
    task_id: int,
    payload: RadiologyStudyUpsert,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("diagnostics:write", "diagnostics:verify")),
):
    """Attaches PACS DICOM Study UID, viewer URL, and radiation dose to a RADIOLOGY diagnostic task."""
    stmt = (
        select(DiagnosticTask)
        .options(joinedload(DiagnosticTask.service))
        .where(DiagnosticTask.id == task_id)
    )
    task = db.execute(stmt).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Diagnostic task not found.")

    if task.service.service_type != ServiceTypeEnum.RADIOLOGY:
        raise HTTPException(
            status_code=400,
            detail=f"PACS DICOM study metadata can only be attached to RADIOLOGY tasks (current service type: {task.service.service_type.value}).",
        )

    existing = db.execute(
        select(RadiologyStudyMetadata).where(RadiologyStudyMetadata.diagnostic_task_id == task_id)
    ).scalar_one_or_none()

    if existing:
        for field, value in payload.model_dump().items():
            setattr(existing, field, value)
        db.commit()
        db.refresh(existing)
        return existing

    dup_uid = db.execute(
        select(RadiologyStudyMetadata).where(RadiologyStudyMetadata.study_instance_uid == payload.study_instance_uid)
    ).scalar_one_or_none()
    if dup_uid:
        raise HTTPException(status_code=409, detail="DICOM study_instance_uid is already linked to another task.")

    study = RadiologyStudyMetadata(diagnostic_task_id=task_id, **payload.model_dump())
    db.add(study)
    db.commit()
    db.refresh(study)
    return study


@router.get(
    "/diagnostics/tasks/{task_id}/dicom-study",
    response_model=RadiologyStudyResponse,
    openapi_extra={"x-privacy-data-use-point": "diagnostics.study.read"},
)
def get_pacs_dicom_study_metadata(
    task_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("diagnostics:read", "opd:read", "adt:read")),
):
    study = db.execute(
        select(RadiologyStudyMetadata).where(RadiologyStudyMetadata.diagnostic_task_id == task_id)
    ).scalar_one_or_none()
    if not study:
        raise HTTPException(status_code=404, detail="DICOM study metadata not found for this task.")
    task = db.get(DiagnosticTask, task_id)
    if task is not None and task.order is not None:
        decisions = evaluate_tenant_patient_data_uses(
            db,
            data_use_point="diagnostics.study.read",
            patient_ids=[task.order.patient_id],
            actor_user_id=actor.id,
        )
        if any(not decision.allowed for decision in decisions):
            raise HTTPException(status_code=403, detail="Privacy policy does not permit this patient disclosure.")
    return study