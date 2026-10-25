"""
FastAPI router for ABDM (M1, M2, M3) & NHCX National Health Claims Exchange.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.dependencies import RequirePermission
from backend.domains.abdm.models import ABDMConsentArtefact, NHCXClaimExchangeLog
from backend.domains.abdm.schemas import (
    ABHAProfileCreate,
    ABHAProfileResponse,
    ABDMScanShareRequest,
    ABDMScanShareResponse,
    ConsentArtefactNotifyRequest,
    ConsentArtefactResponse,
    HealthDataTransferRequest,
    HealthDataTransferResponse,
    NHCXAdjudicationCallbackRequest,
    NHCXAdjudicationCallbackResponse,
    NHCXClaimSubmissionRequest,
    NHCXClaimSubmissionResponse,
)
from backend.domains.abdm.services import (
    generate_abdm_fhir_health_data,
    generate_nhcx_claim_bundle,
    generate_or_link_abha,
    process_abdm_scan_share,
    process_nhcx_adjudication_callback,
    record_consent_artefact,
)
from backend.domains.identity.models import User

router = APIRouter(tags=["ABDM & NHCX National Health Gateways"])


# =============================================================================
# 1. ABDM Milestone M1: ABHA Profile & Scan-Share
# =============================================================================

@router.post(
    "/api/abdm/m1/generate-abha",
    response_model=ABHAProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_or_link_patient_abha(
    payload: ABHAProfileCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("patients:write", "opd:write", "adt:write")),
):
    """Generates a 14-digit ABHA number and links an ABHA address to a patient record."""
    return generate_or_link_abha(db, payload)


@router.post(
    "/api/abdm/m1/scan-share",
    response_model=ABDMScanShareResponse,
    status_code=status.HTTP_201_CREATED,
)
def handle_abdm_scan_and_share(
    payload: ABDMScanShareRequest,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("patients:write", "opd:write", "adt:write")),
):
    """Processes digital scan-and-share at hospital counter, emitting OPD registration token."""
    return process_abdm_scan_share(db, payload.counter_id, payload.qr_payload)


# =============================================================================
# 2. ABDM Milestone M2: Consent Management
# =============================================================================

@router.post(
    "/api/abdm/m2/consent/on-notify",
    response_model=ConsentArtefactResponse,
    status_code=status.HTTP_201_CREATED,
)
def notify_consent_grant(
    payload: ConsentArtefactNotifyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("privacy:write", "integrations:write", "admin:write")),
):
    """Records an incoming consent grant notification from the ABDM Consent Manager."""
    return record_consent_artefact(db, payload, user_id=current_user.id)


@router.get(
    "/api/abdm/m2/consent/artefacts/{consent_id}",
    response_model=ConsentArtefactResponse,
    status_code=status.HTTP_200_OK,
)
def get_consent_artefact(
    consent_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("privacy:read", "clinical:read")),
):
    """Retrieves an active ABDM consent artefact record by ID."""
    artefact = db.execute(
        select(ABDMConsentArtefact).where(ABDMConsentArtefact.consent_id == consent_id)
    ).scalars().first()
    if not artefact:
        raise HTTPException(status_code=404, detail="Consent artefact not found.")
    return artefact


# =============================================================================
# 3. ABDM Milestone M3: Health Information Exchange
# =============================================================================

@router.post(
    "/api/abdm/m3/health-information/request",
    response_model=HealthDataTransferResponse,
    status_code=status.HTTP_200_OK,
)
def fetch_health_records_under_consent(
    payload: HealthDataTransferRequest,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("privacy:read", "clinical:read", "integrations:read")),
):
    """Compiles and packages patient clinical records into an ABDM FHIR R4 document bundle."""
    return generate_abdm_fhir_health_data(db, payload.consent_id, payload.hi_types)


# =============================================================================
# 4. NHCX: National Health Claims Exchange
# =============================================================================

@router.post(
    "/api/nhcx/claims/submit-bundle",
    response_model=NHCXClaimSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_nhcx_claim_fhir_bundle(
    payload: NHCXClaimSubmissionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("billing:write", "insurance:write")),
):
    """Compiles internal claim details and invoice items into an NHCX FHIR Claim Bundle and submits."""
    return generate_nhcx_claim_bundle(db, payload.claim_id, user_id=current_user.id)


@router.post(
    "/api/nhcx/claims/adjudication-callback",
    response_model=NHCXAdjudicationCallbackResponse,
    status_code=status.HTTP_200_OK,
)
def receive_nhcx_claim_adjudication_callback(
    payload: NHCXAdjudicationCallbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("billing:write", "insurance:write", "integrations:write")),
):
    """Processes adjudication callback from NHCX Gateway and updates insurance claim status."""
    return process_nhcx_adjudication_callback(
        session=db,
        transaction_id=payload.transaction_id,
        adjudication_status=payload.adjudication_status,
        settled_amount_paise=payload.settled_amount_paise,
        settlement_utr=payload.settlement_utr,
        rejection_reason=payload.rejection_reason,
        adjudication_bundle=payload.adjudication_bundle,
        user_id=current_user.id,
    )


@router.get(
    "/api/nhcx/transactions/{transaction_id}",
    status_code=status.HTTP_200_OK,
)
def get_nhcx_transaction_log(
    transaction_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:read", "insurance:read", "auditor:read")),
):
    """Fetches full NHCX transaction exchange log by transaction ID."""
    log = db.execute(
        select(NHCXClaimExchangeLog).where(NHCXClaimExchangeLog.transaction_id == transaction_id)
    ).scalars().first()
    if not log:
        raise HTTPException(status_code=404, detail="NHCX Transaction log not found.")
    import json
    return {
        "transaction_id": log.transaction_id,
        "encounter_id": log.encounter_id,
        "patient_id": log.patient_id,
        "claim_id": log.claim_id,
        "exchange_type": log.exchange_type,
        "nhcx_status": log.nhcx_status,
        "adjudicated_amount_paise": log.adjudicated_amount_paise,
        "rejection_reason": log.rejection_reason,
        "bundle": json.loads(log.bundle_json) if log.bundle_json else None,
        "submitted_at": log.created_at,
    }
