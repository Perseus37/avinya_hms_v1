"""
Pydantic schemas for ABDM (M1, M2, M3) & NHCX Gateway Connectors.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


# -----------------------------------------------------------------------------
# ABDM Milestone M1: ABHA Profile & Scan-Share
# -----------------------------------------------------------------------------

class ABHAProfileCreate(BaseModel):
    patient_id: Optional[int] = Field(default=None, description="Existing Patient ID to link, or None to register new")
    aadhaar_or_mobile: str = Field(..., min_length=10, max_length=12, description="12-digit Aadhaar or 10-digit mobile number")
    preferred_abha_address: Optional[str] = Field(default=None, max_length=100, description="Preferred PHR handle (e.g. john@abdm)")
    full_name: Optional[str] = Field(default=None, max_length=100)
    gender: Optional[str] = Field(default="MALE")
    date_of_birth: Optional[str] = Field(default="1990-01-01T00:00:00Z")


class ABHAProfileResponse(BaseModel):
    patient_id: int
    mrn: str
    abha_number: str
    abha_address: str
    full_name: str
    gender: str
    date_of_birth: datetime
    status: str


class ABDMScanShareRequest(BaseModel):
    counter_id: str = Field(..., max_length=50, description="Hospital OPD Registration desk / counter identifier")
    qr_payload: str = Field(..., description="Encrypted or plain ABDM demographic payload from patient's ABHA app")


class ABDMScanShareResponse(BaseModel):
    token_number: str
    counter_id: str
    patient_id: int
    mrn: str
    abha_number: str
    abha_address: str
    full_name: str
    registered_at: datetime


# -----------------------------------------------------------------------------
# ABDM Milestone M2: Consent Management (HIP / HIU)
# -----------------------------------------------------------------------------

class ConsentArtefactNotifyRequest(BaseModel):
    consent_id: str = Field(..., description="Unique consent ID issued by ABDM Consent Manager")
    patient_id: int = Field(..., description="Hospital patient record ID")
    hiu_id: str = Field(..., description="Requesting Health Information User ID")
    hip_id: str = Field(default="IN2910000001", description="Health Information Provider ID (Hospital)")
    status: str = Field(default="GRANTED", description="GRANTED, REVOKED, EXPIRED")
    hi_types: List[str] = Field(..., description="Permitted record types (Prescription, DiagnosticReport, DischargeSummary, OPConsultation)")
    date_range_from: datetime
    date_range_to: datetime
    expiry_time: datetime
    consent_manager_id: Optional[str] = "sbx"
    signature: Optional[str] = None


class ConsentArtefactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    consent_id: str
    patient_id: int
    hiu_id: str
    hip_id: str
    status: str
    hi_types: str
    date_range_from: datetime
    date_range_to: datetime
    expiry_time: datetime
    consent_manager_id: str


# -----------------------------------------------------------------------------
# ABDM Milestone M3: Health Information Exchange (FHIR Bundles)
# -----------------------------------------------------------------------------

class HealthDataTransferRequest(BaseModel):
    consent_id: str = Field(..., description="Active consent artefact ID")
    hi_types: List[str] = Field(..., description="Health record types requested for transfer")


class HealthDataTransferResponse(BaseModel):
    transaction_id: str
    consent_id: str
    patient_id: int
    patient_abha: Optional[str]
    fhir_bundle: Dict[str, Any]
    status: str


# -----------------------------------------------------------------------------
# NHCX: National Health Claims Exchange
# -----------------------------------------------------------------------------

class NHCXClaimSubmissionRequest(BaseModel):
    claim_id: int = Field(..., description="Internal InsuranceClaim ID to submit to NHCX")


class NHCXClaimSubmissionResponse(BaseModel):
    transaction_id: str
    claim_id: int
    patient_id: int
    encounter_id: int
    exchange_type: str
    nhcx_status: str
    fhir_claim_bundle: Dict[str, Any]
    submitted_at: datetime


class NHCXAdjudicationCallbackRequest(BaseModel):
    transaction_id: str = Field(..., description="NHCX Transaction ID")
    adjudication_status: str = Field(..., description="APPROVED, REJECTED, PARTIAL")
    settled_amount_paise: int = Field(default=0, ge=0)
    settlement_utr: Optional[str] = Field(default=None, max_length=100)
    rejection_reason: Optional[str] = Field(default=None, max_length=255)
    adjudication_bundle: Optional[Dict[str, Any]] = None


class NHCXAdjudicationCallbackResponse(BaseModel):
    transaction_id: str
    claim_id: int
    claim_number: str
    claim_status: str
    settled_amount_paise: int
    settlement_utr_reference: Optional[str]
