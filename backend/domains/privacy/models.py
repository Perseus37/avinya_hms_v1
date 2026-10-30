from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    event,
    inspect,
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


class PatientConsentRecord(Base):
    __tablename__ = "patient_consent_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id"), nullable=False, index=True
    )
    consent_type: Mapped[str] = mapped_column(String(40), nullable=False)
    purpose: Mapped[str] = mapped_column(String(255), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence_reference: Mapped[str | None] = mapped_column(String(255))
    scope_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="patient", server_default="patient"
    )
    scope_key: Mapped[str | None] = mapped_column(String(255))
    consenter_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="patient", server_default="patient"
    )
    policy_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("privacy_policy_revisions.id", name="fk_consent_policy_revision"),
        index=True,
    )
    form_version: Mapped[str | None] = mapped_column(String(100))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PrivacyRetentionPolicy(Base):
    __tablename__ = "privacy_retention_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    legal_basis: Mapped[str] = mapped_column(Text, nullable=False)
    approval_reference: Mapped[str] = mapped_column(String(100), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    configured_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False,
        index=True,
    )
    configured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PrivacyPolicyRevision(Base):
    __tablename__ = "privacy_policy_revisions"
    __table_args__ = (
        CheckConstraint(
            "policy_type IN ('CONSENT', 'RETENTION')",
            name="ck_privacy_policy_revision_type",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'APPROVED')",
            name="ck_privacy_policy_revision_status",
        ),
        CheckConstraint(
            "enforcement_mode IN ('OFF', 'AUDIT_ONLY', 'ENFORCE')",
            name="ck_privacy_policy_revision_mode",
        ),
        CheckConstraint(
            "(is_active = false AND enforcement_mode = 'OFF') OR "
            "(is_active = true AND status = 'APPROVED' "
            "AND enforcement_mode IN ('AUDIT_ONLY', 'ENFORCE') "
            "AND mode_set_by_user_id IS NOT NULL "
            "AND mode_set_at IS NOT NULL "
            "AND mode_approval_reference IS NOT NULL)",
            name="ck_privacy_policy_revision_activation",
        ),
        CheckConstraint(
            "(status = 'DRAFT' AND approved_by_user_id IS NULL "
            "AND approved_at IS NULL AND approval_reference IS NULL) OR "
            "(status = 'APPROVED' AND approved_by_user_id IS NOT NULL "
            "AND approved_at IS NOT NULL AND approval_reference IS NOT NULL)",
            name="ck_privacy_policy_revision_approval",
        ),
        Index(
            "uq_privacy_policy_revision_active",
            "policy_type",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active IS TRUE"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    policy_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    policy_document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT", server_default="DRAFT"
    )
    enforcement_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="OFF", server_default="OFF"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    configured_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False,
        index=True,
    )
    configured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    approved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_reference: Mapped[str | None] = mapped_column(String(100))
    mode_set_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    mode_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mode_approval_reference: Mapped[str | None] = mapped_column(String(100))


class PrivacyDecisionAudit(Base):
    __tablename__ = "privacy_decision_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    policy_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("privacy_policy_revisions.id"),
        index=True,
    )
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    data_use_point: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    purpose_code: Mapped[str | None] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class PrivacyLegalHold(Base):
    __tablename__ = "privacy_legal_holds"
    __table_args__ = (
        CheckConstraint(
            "patient_id IS NOT NULL OR data_category IS NOT NULL",
            name="ck_privacy_legal_hold_scope",
        ),
        CheckConstraint(
            "(released_at IS NULL AND released_by_user_id IS NULL "
            "AND release_reference IS NULL) OR "
            "(released_at IS NOT NULL AND released_by_user_id IS NOT NULL "
            "AND release_reference IS NOT NULL)",
            name="ck_privacy_legal_hold_release",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int | None] = mapped_column(
        ForeignKey("patients.id"), index=True
    )
    data_category: Mapped[str | None] = mapped_column(String(50), index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    hold_reference: Mapped[str] = mapped_column(String(100), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    released_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reference: Mapped[str | None] = mapped_column(String(100))



@event.listens_for(PrivacyPolicyRevision, "before_update")
def guard_approved_policy_immutability(mapper, connection, target):
    """Blocks direct modifications to policy_document once a revision has been APPROVED."""
    state = inspect(target)
    history = state.get_history("policy_document", True)
    if history.has_changes():
        status_hist = state.get_history("status", True)
        was_approved = (target.status == "APPROVED") or (
            status_hist.deleted and status_hist.deleted[0] == "APPROVED"
        )
        if was_approved:
            raise ValueError(
                "Approved privacy policy documents are immutable and cannot be updated."
            )



class PrivacyRetentionRunLedger(Base):
    """Permanent, tamper-evident audit ledger for retention execution runs (N-06)."""
    __tablename__ = "privacy_retention_run_ledgers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    policy_revision_id: Mapped[int] = mapped_column(
        ForeignKey("privacy_policy_revisions.id"), nullable=False, index=True
    )
    categories: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    held_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    affected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    approval_reference: Mapped[str] = mapped_column(String(100), nullable=False)
    executed_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False,
        index=True,
    )
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )



class PatientDataRightsRequest(Base):
    """Lifecycle tracking for patient data rights under India's DPDP Act 2023 (M-03)."""
    __tablename__ = "patient_data_rights_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    request_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="SUBMITTED", server_default="SUBMITTED", index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    sla_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    response_notes: Mapped[str | None] = mapped_column(Text)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    handled_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class PrivacyDataBreachIncident(Base):
    """Personal data breach register and incident lifecycle tracking under DPDP / CERT-In (M-04)."""
    __tablename__ = "privacy_data_breach_incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="REPORTED", server_default="REPORTED", index=True)
    breach_type: Mapped[str] = mapped_column(String(50), nullable=False)
    affected_data_categories: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    estimated_affected_subjects: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    contained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_to_authority_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified_subjects_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    resolution_summary: Mapped[str | None] = mapped_column(Text)
    reported_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
