import hashlib
from datetime import datetime, timezone
import secrets
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from control_models import FederatedNode, GlobalPatientPointer, TenantRegistry
from database import get_control_db, get_db, resolve_tenant_context, tenant_engine_pool
from dependencies import RequirePermission, get_federated_node, validate_pagination
from models import (
    AllergyMaster,
    AllergySeverityEnum,
    FederatedClient,
    FederationTransferLog,
    Patient,
    PatientAllergy,
    User,
)
from schemas.v1.schemas import (
    FederatedAllergySummary,
    FederatedClientCreate,
    FederatedClientCreatedResponse,
    FederatedClientResponse,
    FederatedPatientSummaryResponse,
    PaginatedResponse,
    PatientResponse,
)

router = APIRouter(prefix="/api/federation", tags=["Central Discovery & Multi-Hop Federation"])


def normalize_phone_hash(phone_number: Optional[str]) -> str:
    """Normalizes phone digits and hashes with SHA-256 for privacy-preserving indexing."""
    digits = "".join(ch for ch in (phone_number or "") if ch.isdigit())[-10:]
    if not digits:
        digits = "0000000000"
    return hashlib.sha256(digits.encode("utf-8")).hexdigest()


def sync_patient_pointer(
    control_db: Session,
    branch_code: str,
    subdomain_slug: str,
    patient: Patient,
    has_critical_allergies: bool = False,
) -> None:
    """Updates the Central Discovery Index whenever a hospital registers or updates a patient."""
    try:
        p_hash = normalize_phone_hash(patient.phone_number)
        stmt = select(GlobalPatientPointer).where(
            GlobalPatientPointer.phone_hash == p_hash,
            GlobalPatientPointer.branch_code == branch_code,
        )
        pointer = control_db.execute(stmt).scalar_one_or_none()
        if pointer:
            pointer.abha_number = patient.abha_number or pointer.abha_number
            pointer.source_mrn = patient.mrn
            pointer.has_critical_allergies = has_critical_allergies or pointer.has_critical_allergies
            pointer.last_clinical_update_at = datetime.now(timezone.utc)
        else:
            pointer = GlobalPatientPointer(
                phone_hash=p_hash,
                abha_number=patient.abha_number,
                branch_code=branch_code,
                subdomain_slug=subdomain_slug,
                source_mrn=patient.mrn,
                has_critical_allergies=has_critical_allergies,
            )
            control_db.add(pointer)
        control_db.commit()
    except Exception:
        control_db.rollback()


# -----------------------------------------------------------------------------
# 1. Branch / Partner API Key Management (iam:write, iam:read, iam:delete)
# -----------------------------------------------------------------------------
@router.post("/clients", response_model=FederatedClientCreatedResponse, status_code=status.HTTP_201_CREATED)
def register_federated_branch(
    payload: FederatedClientCreate,
    request: Request,
    db: Session = Depends(get_db),
    control_db: Session = Depends(get_control_db),
    _: User = Depends(RequirePermission("iam:write")),
):
    tenant = resolve_tenant_context(request)
    branch_code = payload.branch_code.strip().upper()
    existing = db.query(FederatedClient).filter(FederatedClient.branch_code == branch_code).first()
    if existing:
        raise HTTPException(status_code=409, detail="Branch code is already registered.")

    raw_key = f"fed_live_{secrets.token_urlsafe(32)}"
    key_prefix = raw_key[:14]
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    client = FederatedClient(
        branch_code=branch_code,
        name=payload.name,
        api_key_prefix=key_prefix,
        api_key_hash=key_hash,
        is_active=True,
    )
    db.add(client)
    db.commit()
    db.refresh(client)

    node_exists = control_db.query(FederatedNode).filter(FederatedNode.branch_code == branch_code).first()
    if not node_exists:
        control_db.add(
            FederatedNode(
                branch_code=branch_code,
                hospital_name=payload.name,
                subdomain_slug=tenant.subdomain_slug,
                api_key_prefix=key_prefix,
                api_key_hash=key_hash,
                is_active=True,
            )
        )
        control_db.commit()

    return FederatedClientCreatedResponse(
        id=client.id,
        branch_code=client.branch_code,
        name=client.name,
        api_key_prefix=client.api_key_prefix,
        raw_api_key=raw_key,
        is_active=client.is_active,
    )


@router.get("/clients", response_model=PaginatedResponse[FederatedClientResponse])
def list_federated_branches(
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("iam:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(FederatedClient).order_by(FederatedClient.id.asc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.delete("/clients/{client_id}", status_code=200)
def revoke_federated_branch_key(
    client_id: int,
    db: Session = Depends(get_db),
    control_db: Session = Depends(get_control_db),
    _: User = Depends(RequirePermission("iam:delete")),
):
    client = db.get(FederatedClient, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Federated client not found.")
    client.is_active = False
    db.commit()

    node = control_db.query(FederatedNode).filter(FederatedNode.branch_code == client.branch_code).first()
    if node:
        node.is_active = False
        control_db.commit()

    return {"status": "success", "message": f"API key for branch '{client.branch_code}' revoked."}


# -----------------------------------------------------------------------------
# 2. Central Discovery Index & Multi-Hop Patient Aggregation (A -> B -> C)
# -----------------------------------------------------------------------------
@router.post("/patients/{patient_id}/publish-pointer", status_code=200)
def publish_local_patient_to_discovery_index(
    patient_id: int,
    request: Request,
    db: Session = Depends(get_db),
    control_db: Session = Depends(get_control_db),
    _: User = Depends(RequirePermission("patients:write")),
):
    tenant = resolve_tenant_context(request)
    patient = db.get(Patient, patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=404, detail="Active patient not found.")

    crit_allergy = (
        db.query(PatientAllergy)
        .filter(
            PatientAllergy.patient_id == patient.id,
            PatientAllergy.is_active == True,
            PatientAllergy.severity.in_([AllergySeverityEnum.SEVERE, AllergySeverityEnum.ANAPHYLACTIC]),
        )
        .first()
        is not None
    )
    sync_patient_pointer(
        control_db=control_db,
        branch_code=tenant.branch_code,
        subdomain_slug=tenant.subdomain_slug,
        patient=patient,
        has_critical_allergies=crit_allergy,
    )
    return {"status": "success", "message": f"Patient pointer synced for branch {tenant.branch_code}."}


@router.get("/patients/discover", response_model=FederatedPatientSummaryResponse)
def discover_and_aggregate_patient_across_branches(
    phone_number: Optional[str] = Query(default=None, description="Patient registered phone number"),
    abha_number: Optional[str] = Query(default=None, description="National ABHA ID"),
    purpose: str = Query(default="CONTINUITY_OF_CARE", min_length=3, max_length=255),
    calling_node: FederatedNode = Depends(get_federated_node),
    control_db: Session = Depends(get_control_db),
):
    """
    SOLVES HOSPITAL A -> B -> C ROUTING:
    1. Queries the Central Discovery Index for all hospitals visited by this patient, ordered newest first.
    2. Pulls the latest demographics from the most recent hospital (Hospital B).
    3. Aggregates the union of all active allergies across ALL visited hospitals (Hospital B + Hospital A).
    """
    if not phone_number and not abha_number:
        raise HTTPException(status_code=400, detail="Provide either phone_number or abha_number.")

    stmt = select(GlobalPatientPointer)
    if abha_number:
        stmt = stmt.where(GlobalPatientPointer.abha_number == abha_number)
        identifier_used = f"abha:{abha_number}"
    else:
        p_hash = normalize_phone_hash(phone_number)
        stmt = stmt.where(GlobalPatientPointer.phone_hash == p_hash)
        identifier_used = f"phone:{phone_number}"

    pointers = control_db.execute(
        stmt.order_by(GlobalPatientPointer.last_clinical_update_at.desc())
    ).scalars().all()

    if not pointers:
        raise HTTPException(status_code=404, detail="No records found for this patient in the Central Discovery Index.")

    latest_demographics: Optional[Patient] = None
    merged_allergies: dict[str, FederatedAllergySummary] = {}

    for ptr in pointers:
        tenant_reg = (
            control_db.query(TenantRegistry)
            .filter(TenantRegistry.branch_code == ptr.branch_code, TenantRegistry.is_active == True)
            .first()
        )
        if not tenant_reg:
            continue

        branch_engine = tenant_engine_pool.get_engine(tenant_reg.db_name)
        BranchSession = sessionmaker(bind=branch_engine, autocommit=False, autoflush=False)
        with BranchSession() as branch_db:
            patient = (
                branch_db.query(Patient)
                .filter(Patient.mrn == ptr.source_mrn, Patient.is_active == True)
                .first()
            )
            if not patient:
                continue

            if latest_demographics is None:
                branch_db.expunge(patient)
                latest_demographics = patient

            alg_stmt = (
                select(PatientAllergy, AllergyMaster)
                .join(AllergyMaster, PatientAllergy.allergy_id == AllergyMaster.id)
                .where(PatientAllergy.patient_id == patient.id, PatientAllergy.is_active == True)
            )
            for pa, master in branch_db.execute(alg_stmt).all():
                key = master.name.strip().lower()
                if key not in merged_allergies:
                    rx_note = pa.reaction_description or "Adverse reaction"
                    tagged_reaction = (
                        rx_note if "[Source:" in rx_note else f"{rx_note} [Source: {ptr.branch_code}]"
                    )
                    merged_allergies[key] = FederatedAllergySummary(
                        allergy_code=master.code,
                        allergy_name=master.name,
                        severity=pa.severity,
                        reaction=tagged_reaction,
                    )

            branch_db.add(
                FederationTransferLog(
                    client_id=None,
                    requesting_branch_code=calling_node.branch_code,
                    patient_id=patient.id,
                    requested_identifier=f"{calling_node.branch_code}->{identifier_used}",
                    purpose=purpose,
                )
            )
            branch_db.commit()

    if not latest_demographics:
        raise HTTPException(status_code=404, detail="Patient records were inactive at all indexed branches.")

    return FederatedPatientSummaryResponse(
        source_mrn=latest_demographics.mrn,
        abha_number=latest_demographics.abha_number,
        full_name=latest_demographics.full_name,
        date_of_birth=latest_demographics.date_of_birth,
        gender=latest_demographics.gender,
        blood_group=latest_demographics.blood_group,
        phone_number=latest_demographics.phone_number,
        email=latest_demographics.email,
        address=latest_demographics.address,
        emergency_contact_name=latest_demographics.emergency_contact_name,
        emergency_contact_phone=latest_demographics.emergency_contact_phone,
        active_allergies=list(merged_allergies.values()),
    )


@router.post("/patients/import", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
def import_federated_patient_locally(
    payload: FederatedPatientSummaryResponse,
    request: Request,
    db: Session = Depends(get_db),
    control_db: Session = Depends(get_control_db),
    current_user: User = Depends(RequirePermission("patients:write")),
):
    """Imports a multi-hop aggregated patient profile into the receiving hospital (Hospital C) and updates the Central Index."""
    tenant = resolve_tenant_context(request)

    if payload.abha_number:
        existing = db.query(Patient).filter(Patient.abha_number == payload.abha_number).first()
        if existing:
            return existing

    local_mrn = f"MRN-{uuid.uuid4().hex[:8].upper()}"
    new_patient = Patient(
        mrn=local_mrn,
        abha_number=payload.abha_number,
        full_name=payload.full_name,
        date_of_birth=payload.date_of_birth,
        gender=payload.gender,
        blood_group=payload.blood_group,
        phone_number=payload.phone_number or "0000000000",
        email=payload.email,
        address=payload.address,
        emergency_contact_name=payload.emergency_contact_name,
        emergency_contact_phone=payload.emergency_contact_phone,
        is_active=True,
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
    )
    db.add(new_patient)
    db.flush()

    has_crit = False
    for remote_alg in payload.active_allergies:
        local_master = (
            db.query(AllergyMaster)
            .filter(
                (AllergyMaster.code == remote_alg.allergy_code)
                | (AllergyMaster.name.ilike(remote_alg.allergy_name))
            )
            .first()
        )
        if local_master:
            if remote_alg.severity in [AllergySeverityEnum.SEVERE, AllergySeverityEnum.ANAPHYLACTIC]:
                has_crit = True
            db.add(
                PatientAllergy(
                    patient_id=new_patient.id,
                    allergy_id=local_master.id,
                    severity=remote_alg.severity,
                    reaction_description=remote_alg.reaction,
                    recorded_by_user_id=current_user.id,
                    is_active=True,
                    created_by_id=current_user.id,
                    updated_by_id=current_user.id,
                )
            )

    db.commit()
    db.refresh(new_patient)

    sync_patient_pointer(
        control_db=control_db,
        branch_code=tenant.branch_code,
        subdomain_slug=tenant.subdomain_slug,
        patient=new_patient,
        has_critical_allergies=has_crit,
    )
    return new_patient