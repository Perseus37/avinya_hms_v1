import hashlib
import secrets
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from dependencies import RequirePermission, get_federated_client, validate_pagination
from models import (
    AllergyMaster,
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

router = APIRouter(prefix="/api/federation", tags=["Inter-Branch & Tenant Federation"])


# -----------------------------------------------------------------------------
# 1. Admin Management of Branch / Partner API Keys
# -----------------------------------------------------------------------------
@router.post("/clients", response_model=FederatedClientCreatedResponse, status_code=status.HTTP_201_CREATED)
def register_federated_branch(
    payload: FederatedClientCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("manage_users")),
):
    """Issues a new machine-to-machine API key for a sister branch or external tenant."""
    existing = db.query(FederatedClient).filter(FederatedClient.branch_code == payload.branch_code).first()
    if existing:
        raise HTTPException(status_code=409, detail="Branch code is already registered.")

    raw_key = f"fed_live_{secrets.token_urlsafe(32)}"
    key_prefix = raw_key[:14]
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    client = FederatedClient(
        branch_code=payload.branch_code,
        name=payload.name,
        api_key_prefix=key_prefix,
        api_key_hash=key_hash,
        is_active=True,
    )
    db.add(client)
    db.commit()
    db.refresh(client)

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
    _: User = Depends(RequirePermission("manage_users")),
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
    _: User = Depends(RequirePermission("manage_users")),
):
    """Immediately revokes an external branch's API key."""
    client = db.get(FederatedClient, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Federated client not found.")
    client.is_active = False
    db.commit()
    return {"status": "success", "message": f"API key for branch '{client.branch_code}' revoked."}


# -----------------------------------------------------------------------------
# 2. Machine-to-Machine Patient Exchange (Authenticated via X-Federation-API-Key)
# -----------------------------------------------------------------------------
@router.get("/patients/lookup", response_model=FederatedPatientSummaryResponse)
def export_patient_for_partner_branch(
    mrn: Optional[str] = Query(default=None, description="Patient MRN at this hospital"),
    abha_number: Optional[str] = Query(default=None, description="National ABHA ID"),
    phone_number: Optional[str] = Query(default=None, description="Patient registered phone number"),
    purpose: str = Query(default="CONTINUITY_OF_CARE", min_length=3, max_length=255),
    client: FederatedClient = Depends(get_federated_client),
    db: Session = Depends(get_db),
):
    """
    Called by a partner branch/tenant using `X-Federation-API-Key`.
    Returns patient demographics and active allergies, and logs the cross-tenant read.
    """
    if not any([mrn, abha_number, phone_number]):
        raise HTTPException(status_code=400, detail="Provide at least one lookup identifier: mrn, abha_number, or phone_number.")

    query = db.query(Patient).filter(Patient.is_active == True)
    if mrn:
        patient = query.filter(Patient.mrn == mrn).first()
        identifier_used = f"mrn:{mrn}"
    elif abha_number:
        patient = query.filter(Patient.abha_number == abha_number).first()
        identifier_used = f"abha:{abha_number}"
    else:
        patient = query.filter(Patient.phone_number == phone_number).order_by(Patient.id.desc()).first()
        identifier_used = f"phone:{phone_number}"

    if not patient:
        raise HTTPException(status_code=404, detail="Active patient record not found.")

    # Fetch active allergies with master names so the receiving branch gets full clinical context
    stmt = (
        select(PatientAllergy, AllergyMaster)
        .join(AllergyMaster, PatientAllergy.allergy_id == AllergyMaster.id)
        .where(PatientAllergy.patient_id == patient.id, PatientAllergy.is_active == True)
    )
    allergy_rows = db.execute(stmt).all()
    allergies = [
        FederatedAllergySummary(
            allergy_code=master.code,
            allergy_name=master.name,
            severity=pa.severity,
            reaction=pa.reaction,
        )
        for pa, master in allergy_rows
    ]

    # Record immutable cross-branch read audit trail
    transfer_log = FederationTransferLog(
        client_id=client.id,
        patient_id=patient.id,
        requested_identifier=identifier_used,
        purpose=purpose,
    )
    db.add(transfer_log)
    db.commit()

    return FederatedPatientSummaryResponse(
        source_mrn=patient.mrn,
        abha_number=patient.abha_number,
        full_name=patient.full_name,
        date_of_birth=patient.date_of_birth,
        gender=patient.gender,
        blood_group=patient.blood_group,
        phone_number=patient.phone_number,
        email=patient.email,
        address=patient.address,
        emergency_contact_name=patient.emergency_contact_name,
        emergency_contact_phone=patient.emergency_contact_phone,
        active_allergies=allergies,
    )


# -----------------------------------------------------------------------------
# 3. Local Reception Import Endpoint
# -----------------------------------------------------------------------------
@router.post("/patients/import", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
def import_federated_patient_locally(
    payload: FederatedPatientSummaryResponse,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("manage_clinical_mdm") or  RequirePermission("write_cpoe_orders")),
):
    """Imports a patient payload fetched from a partner branch into the local database."""
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
        phone_number=payload.phone_number,
        email=payload.email,
        address=payload.address,
        emergency_contact_name=payload.emergency_contact_name,
        emergency_contact_phone=payload.emergency_contact_phone,
        is_active=True,
    )
    db.add(new_patient)
    db.flush()

    # Automatically map any matching local AllergyMaster codes
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
            db.add(
                PatientAllergy(
                    patient_id=new_patient.id,
                    allergy_id=local_master.id,
                    severity=remote_alg.severity,
                    reaction=remote_alg.reaction,
                    is_active=True,
                )
            )

    db.commit()
    db.refresh(new_patient)
    return new_patient