"""
ABDM (Ayushman Bharat Digital Mission) & NHCX (National Health Claims Exchange) Models:
1. ABDMConsentArtefact: Health Information Provider (HIP) consent tracking & cryptographic artifacts
2. NHCXClaimExchangeLog: FHIR R4 insurance pre-auth and claim exchange transaction audit trail
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.audit import AuditMixin
from backend.core.database import Base
from backend.domains.encounters.models import PatientEncounter
from backend.domains.insurance.models import InsuranceClaim, InsurancePreAuth
from backend.domains.patients.models import Patient


class ABDMConsentArtefact(AuditMixin, Base):
    """
    HIP Consent Artefact under ABDM M2 architecture.
    Records patient consent grants, validity timeboxes, and permitted health information types.
    """
    __tablename__ = "abdm_consent_artefacts"
    __table_args__ = (
        UniqueConstraint("consent_id", name="uq_abdm_consent_id"),
        Index("ix_abdm_consent_patient_id", "patient_id"),
        Index("ix_abdm_consent_status", "status"),
        Index("ix_abdm_consent_expiry", "expiry_time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    consent_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)

    hiu_id: Mapped[str] = mapped_column(String(100), nullable=False)  # Health Information User (Requester)
    hip_id: Mapped[str] = mapped_column(String(100), nullable=False)  # Avinya Hospital Provider ID
    status: Mapped[str] = mapped_column(String(30), default="GRANTED", nullable=False)  # GRANTED, REVOKED, EXPIRED

    hi_types: Mapped[str] = mapped_column(String(255), nullable=False)  # JSON e.g. ["Prescription", "DiagnosticReport"]
    date_range_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    date_range_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expiry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consent_manager_id: Mapped[str] = mapped_column(String(100), default="sbx", nullable=False)
    signature: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    patient: Mapped["Patient"] = relationship()


class NHCXClaimExchangeLog(AuditMixin, Base):
    """
    Auditable transaction exchange log for National Health Claims Exchange (NHCX).
    Persists raw FHIR R4 Claim bundles, pre-auth messages, and insurer adjudication payloads.
    """
    __tablename__ = "nhcx_claim_exchange_logs"
    __table_args__ = (
        UniqueConstraint("transaction_id", name="uq_nhcx_txn_id"),
        Index("ix_nhcx_encounter_id", "encounter_id"),
        Index("ix_nhcx_claim_id", "claim_id"),
        Index("ix_nhcx_preauth_id", "preauth_id"),
        Index("ix_nhcx_posted_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)

    claim_id: Mapped[Optional[int]] = mapped_column(ForeignKey("insurance_claims.id", ondelete="SET NULL"), nullable=True)
    preauth_id: Mapped[Optional[int]] = mapped_column(ForeignKey("insurance_pre_auths.id", ondelete="SET NULL"), nullable=True)

    exchange_type: Mapped[str] = mapped_column(String(50), nullable=False)  # PRE_AUTH_REQUEST, CLAIM_SUBMISSION, ADJUDICATION_RESPONSE
    nhcx_status: Mapped[str] = mapped_column(String(50), default="SUBMITTED", nullable=False)  # SUBMITTED, ACKNOWLEDGED, ADJUDICATED, REJECTED, SETTLED

    bundle_json: Mapped[str] = mapped_column(Text, nullable=False)
    response_bundle_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    adjudicated_amount_paise: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    patient: Mapped["Patient"] = relationship()
    claim: Mapped[Optional["InsuranceClaim"]] = relationship()
    preauth: Mapped[Optional["InsurancePreAuth"]] = relationship()
