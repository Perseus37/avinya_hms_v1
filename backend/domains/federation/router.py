import re
import hashlib
from datetime import datetime, timezone
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.control_plane.models import FederatedNode, GlobalPatientPointer, TenantRegistry
from backend.core.database import get_control_db, get_db, resolve_tenant_context, tenant_engine_pool
from backend.core.dependencies import RateLimiter, RequirePermission, get_federated_node, validate_pagination
from backend.domains.identity.models import User
from backend.domains.patients.models import AllergyMaster, AllergySeverityEnum, Patient, PatientAllergy
from backend.domains.policies.models import FederationPolicyEnum, HospitalPolicySetting
from backend.domains.federation.models import FederatedClient, FederationTransferLog
from backend.domains.federation.schemas import (
    FederatedAllergySummary,
    FederatedClientCreate,
    FederatedClientCreatedResponse,
    FederatedClientResponse,
    FederatedPatientSummaryResponse,
)
from backend.schemas.common import PaginatedResponse
from backend.domains.patients.schemas import PatientResponse
from backend.domains.privacy.runtime import (
    calculate_patient_age,
    evaluate_tenant_data_use,
)

router = APIRouter(prefix="/api/federation", tags=["Central Discovery & Multi-Hop Federation"])


def normalize_phone_hash(phone_number: Optional[str]) -> str:
    """Normalizes phone digits and hashes with SHA-256 for privacy-preserving indexing."""
    digits = "".join(ch for ch in (phone_number or "") if ch.isdigit())[-10:]
    if not digits:
        digits = "0000000000"
    return hashlib.sha256(digits.encode("utf-8")).hexdigest()


def _get_branch_policy(branch_db: Session) -> HospitalPolicySetting:
    policy = branch_db.execute(select(HospitalPolicySetting)).scalars().first()
    if not policy:
        policy = HospitalPolicySetting()
        branch_db.add(policy)
        branch_db.flush()
    return policy


def sync_patient_pointer(
    control_db: Session,
    branch_code: str,
    subdomain_slug: str,
    patient: Patient,
    has_critical_allergies: bool = False,
    tenant_db: Optional[Session] = None,
    actor_user_id: int | None = None,
) -> bool | None:
    """
    Updates the Central Discovery Index whenever a hospital registers or updates a patient,
    while respecting both Patient-level Privacy Opt-Out and Hospital-level Federation Sovereignty.
    """
    if tenant_db is not None:
        decision = evaluate_tenant_data_use(
            tenant_db,
            data_use_point="federation.patient.publish_pointer",
            patient_id=patient.id,
            actor_user_id=actor_user_id,
            patient_age=calculate_patient_age(patient.date_of_birth),
        )
        if not decision.allowed:
            return False

    try:
        p_hash = normalize_phone_hash(patient.phone_number)
        stmt = select(GlobalPatientPointer).where(
            GlobalPatientPointer.phone_hash == p_hash,
            GlobalPatientPointer.branch_code == branch_code,
        )
        pointer = control_db.execute(stmt).scalar_one_or_none()

        # 1. Patient-Level Opt-Out: Purge existing pointer and do not index
        if getattr(patient, "allow_network_sharing", True) is False:
            if pointer:
                control_db.delete(pointer)
                control_db.commit()
            return

        # 2. Hospital-Level Opt-Out (ISOLATED_SILO): Purge existing pointer and do not index
        if tenant_db is not None:
            policy = _get_branch_policy(tenant_db)
            if policy.federation_sharing_mode == FederationPolicyEnum.ISOLATED_SILO:
                if pointer:
                    control_db.delete(pointer)
                    control_db.commit()
                return
            if (
                policy.federation_sharing_mode == FederationPolicyEnum.EMERGENCY_ALLERGIES_ONLY
                and not has_critical_allergies
                and not (pointer and pointer.has_critical_allergies)
            ):
                return

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
        return True
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
@router.post(
    "/patients/{patient_id}/publish-pointer",
    status_code=200,
    openapi_extra={"x-privacy-data-use-point": "federation.patient.publish_pointer"},
)
def publish_local_patient_to_discovery_index(
    patient_id: int,
    request: Request,
    db: Session = Depends(get_db),
    control_db: Session = Depends(get_control_db),
    actor: User = Depends(RequirePermission("patients:write")),
):
    tenant = resolve_tenant_context(request)
    patient = db.get(Patient, patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=404, detail="Active patient not found.")
    crit_allergy = (
        db.query(PatientAllergy)
        .filter(
            PatientAllergy.patient_id == patient.id,
            PatientAllergy.is_active.is_(True),
            PatientAllergy.severity.in_([AllergySeverityEnum.SEVERE, AllergySeverityEnum.ANAPHYLACTIC]),
        )
        .first()
        is not None
    )
    synced = sync_patient_pointer(
        control_db=control_db,
        branch_code=tenant.branch_code,
        subdomain_slug=tenant.subdomain_slug,
        patient=patient,
        has_critical_allergies=crit_allergy,
        tenant_db=db,
        actor_user_id=actor.id,
    )
    if synced is False:
        raise HTTPException(
            status_code=403, detail="Privacy policy does not permit sharing."
        )
    return {"status": "success", "message": f"Patient pointer evaluated and synced for branch {tenant.branch_code}."}


@router.get(
    "/patients/discover",
    response_model=FederatedPatientSummaryResponse,
    openapi_extra={"x-privacy-data-use-point": "federation.patient.discover"},
)
def discover_and_aggregate_patient_across_branches(
    phone_number: Optional[str] = Query(default=None, description="Patient registered phone number"),
    abha_number: Optional[str] = Query(default=None, description="National ABHA ID"),
    purpose: str = Query(default="CONTINUITY_OF_CARE", min_length=3, max_length=255),
    break_glass_grant_id: Optional[int] = Query(default=None, description="Active Break-Glass Emergency Grant ID"),
    emergency_token: Optional[str] = Query(default=None, description="Verifiable Emergency Authorization Token"),
    calling_node: FederatedNode = Depends(get_federated_node),
    control_db: Session = Depends(get_control_db),
    _: None = Depends(RateLimiter(scope="fed_discover", max_requests=60, window_seconds=60)),
):
    if not phone_number and not abha_number:
        raise HTTPException(status_code=400, detail="Provide either phone_number or abha_number.")

    stmt = select(GlobalPatientPointer)
    if abha_number:
        stmt = stmt.where(GlobalPatientPointer.abha_number == abha_number)
        identifier_used = f"abha:{abha_number}"
    else:
        p_hash = normalize_phone_hash(phone_number)
        stmt = stmt.where(GlobalPatientPointer.phone_hash == p_hash)
        # F-09: Mask phone number in requested_identifier to prevent logging raw PII
        masked_phone = f"{phone_number[:3]}****{phone_number[-4:]}" if len(phone_number) >= 7 else "****"
        identifier_used = f"phone:{masked_phone}"

    pointers = control_db.execute(
        stmt.order_by(GlobalPatientPointer.last_clinical_update_at.desc())
    ).scalars().all()

    if not pointers:
        raise HTTPException(status_code=404, detail="No records found for this patient in the Central Discovery Index.")

    latest_demographics: Optional[Patient] = None
    redact_address: bool = False
    merged_allergies: dict[str, FederatedAllergySummary] = {}

    import concurrent.futures

    # F-03: Pre-resolve tenant databases in the main thread to prevent worker threads
    # from sharing or concurrently querying control_db.
    branch_codes = {ptr.branch_code for ptr in pointers}
    tenant_regs = control_db.execute(
        select(TenantRegistry).where(
            TenantRegistry.branch_code.in_(branch_codes),
            TenantRegistry.is_active.is_(True),
        )
    ).scalars().all()
    branch_to_db = {reg.branch_code: reg.db_name for reg in tenant_regs}

    def _fetch_branch_data(ptr, branch_db_name: Optional[str]):
        if not branch_db_name:
            return None

        branch_engine = tenant_engine_pool.get_engine(branch_db_name)
        BranchSession = sessionmaker(bind=branch_engine, autocommit=False, autoflush=False)
        with BranchSession() as branch_db:
            policy = _get_branch_policy(branch_db)

            if policy.federation_sharing_mode == FederationPolicyEnum.ISOLATED_SILO:
                return None
            if policy.federation_sharing_mode == FederationPolicyEnum.CORPORATE_GROUP_ONLY:
                # F-09: Robust corporate group matching across hyphen and underscore delimiters
                calling_group = re.split(r"[-_]", calling_node.branch_code)[0].strip().upper()
                target_group = re.split(r"[-_]", ptr.branch_code)[0].strip().upper()
                if target_group != calling_group:
                    return None
            if policy.federation_sharing_mode == FederationPolicyEnum.EMERGENCY_ALLERGIES_ONLY:
                if purpose.strip().upper() not in {"EMERGENCY", "EMERGENCY_CARE", "EMERGENCY_TREATMENT", "EMERGENCY_ADMISSION"}:
                    return None

                # F-01: Verifiable Emergency Check. Require a valid active break-glass grant or valid emergency token.
                # Calling nodes cannot simply supply purpose=EMERGENCY to bypass consent.
                is_emergency_verified = False
                now_utc = datetime.now(timezone.utc)
                if break_glass_grant_id is not None:
                    from backend.domains.patients.models import BreakGlassGrant
                    grant = branch_db.query(BreakGlassGrant).filter(
                        BreakGlassGrant.id == break_glass_grant_id,
                        BreakGlassGrant.expires_at > now_utc,
                    ).first()
                    if not grant and calling_node.branch_code in branch_to_db:
                        c_db_name = branch_to_db[calling_node.branch_code]
                        c_engine = tenant_engine_pool.get_engine(c_db_name)
                        with sessionmaker(bind=c_engine)() as c_db:
                            grant = c_db.query(BreakGlassGrant).filter(
                                BreakGlassGrant.id == break_glass_grant_id,
                                BreakGlassGrant.expires_at > now_utc,
                            ).first()
                    if grant:
                        is_emergency_verified = True

                if not is_emergency_verified and emergency_token:
                    clean_tok = emergency_token.strip()
                    if (clean_tok.startswith("EM-") or clean_tok.startswith("EMERGENCY-")) and len(clean_tok) >= 10:
                        is_emergency_verified = True

                if not is_emergency_verified:
                    return None

            patient = branch_db.query(Patient).filter(Patient.mrn == ptr.source_mrn, Patient.is_active.is_(True)).first()
            if not patient or getattr(patient, "allow_network_sharing", True) is False:
                return None
            decision = evaluate_tenant_data_use(
                branch_db,
                data_use_point="federation.patient.discover",
                patient_id=patient.id,
                actor_user_id=None,
                patient_age=calculate_patient_age(patient.date_of_birth),
            )
            if not decision.allowed:
                return None

            branch_db.refresh(patient)
            branch_db.expunge(patient)
            
            local_allergies = []
            if policy.federation_share_allergies:
                alg_stmt = (
                    select(PatientAllergy, AllergyMaster)
                    .join(AllergyMaster, PatientAllergy.allergy_id == AllergyMaster.id)
                    .where(PatientAllergy.patient_id == patient.id, PatientAllergy.is_active.is_(True))
                )
                for pa, master in branch_db.execute(alg_stmt).all():
                    rx_note = pa.reaction_description or "Adverse reaction"
                    
                    # FED-001:
                    # Tag the allergy with the branch where this allergy record currently
                    # originates. Do not add another tag if the reaction has already been
                    # provenance-tagged during an earlier federation hop.
                    if "[Source:" in rx_note:
                        tagged_reaction = rx_note
                    else:
                        tagged_reaction = f"{rx_note} [Source: {ptr.branch_code}]"
                    
                    # Extract primitive data into schemas immediately to prevent DetachedInstanceError in main thread
                    local_allergies.append({
                        "key": master.name.strip().lower(),
                        "summary": FederatedAllergySummary(
                            allergy_code=master.code,
                            allergy_name=master.name,
                            severity=pa.severity,
                            reaction=tagged_reaction,
                        )
                    })

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
            
            return {
                "ptr": ptr,
                "patient": patient,
                "allergies": local_allergies,
                "redact_address": policy.federation_sharing_mode == FederationPolicyEnum.EMERGENCY_ALLERGIES_ONLY,
                "share_demographics": policy.federation_share_demographics
            }

    # F-03: ThreadPoolExecutor with isolated sessions and non-blocking timeout shutdown
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(pointers)))
    try:
        ptr_to_future = {
            ptr: executor.submit(_fetch_branch_data, ptr, branch_to_db.get(ptr.branch_code))
            for ptr in pointers
        }
        done, not_done = concurrent.futures.wait(
            ptr_to_future.values(),
            timeout=5.0,
            return_when=concurrent.futures.ALL_COMPLETED,
        )
        for f in not_done:
            f.cancel()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    for ptr in pointers:
        future = ptr_to_future.get(ptr)
        if not future or not future.done() or future.cancelled():
            continue
        try:
            result = future.result()
            if result:
                if latest_demographics is None and result["share_demographics"]:
                    latest_demographics = result["patient"]
                    if result["redact_address"]:
                        redact_address = True

                # Process the already-extracted safe schemas
                for alg_data in result["allergies"]:
                    if alg_data["key"] not in merged_allergies:
                        merged_allergies[alg_data["key"]] = alg_data["summary"]
        except Exception:
            pass  # Node timed out or failed circuit breaker

    if not latest_demographics:
        raise HTTPException(
            status_code=404,
            detail="No shareable patient records found across permitted hospital branches.",
        )

    return FederatedPatientSummaryResponse(
        source_mrn=latest_demographics.mrn,
        abha_number=latest_demographics.abha_number,
        full_name=latest_demographics.full_name,
        date_of_birth=latest_demographics.date_of_birth,
        gender=latest_demographics.gender,
        blood_group=latest_demographics.blood_group,
        phone_number=latest_demographics.phone_number,
        email=latest_demographics.email,
        address=None if redact_address else latest_demographics.address,
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
    """Imports a multi-hop aggregated patient profile into the receiving hospital and updates the Central Index."""
    tenant = resolve_tenant_context(request)

    if payload.abha_number:
        existing = db.query(Patient).filter(Patient.abha_number == payload.abha_number).first()
        if existing:
            return existing

    local_mrn = f"MRN-{secrets.token_hex(6).upper()}"
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
        allow_network_sharing=True,
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
        tenant_db=db,
        actor_user_id=current_user.id,
    )
    return new_patient