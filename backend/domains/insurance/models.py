from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.audit import AuditMixin
from backend.core.database import Base

from backend.domains.billing.models import Invoice
from backend.domains.diagnostics.models import ServiceCatalog
from backend.domains.encounters.models import PatientEncounter
from backend.domains.patients.models import DiagnosisMaster, Patient


class InsuranceProviderTypeEnum(str, PyEnum):
    TPA = "TPA"
    INSURER = "INSURER"
    GOVERNMENT_SCHEME = "GOVERNMENT_SCHEME"
    CORPORATE = "CORPORATE"


class PreAuthStatusEnum(str, PyEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    QUERY_RAISED = "QUERY_RAISED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ClaimStatusEnum(str, PyEnum):
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    SETTLED = "SETTLED"
    REJECTED = "REJECTED"


class InsuranceProvider(AuditMixin, Base):
    """Master catalog of TPAs, private health insurers, government schemes (PM-JAY), and corporate payers."""
    __tablename__ = "insurance_providers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    provider_type: Mapped[InsuranceProviderTypeEnum] = mapped_column(nullable=False)
    contact_email: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    document_requirements_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    @property
    def document_requirements(self) -> List[str]:
        import json

        try:
            values = json.loads(self.document_requirements_json or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
        return values if isinstance(values, list) else []

    @document_requirements.setter
    def document_requirements(self, values: List[str]) -> None:
        import json

        self.document_requirements_json = json.dumps(values)


class InsurancePlan(AuditMixin, Base):
    """Specific insurance plan or corporate tier under an InsuranceProvider with room-rent caps and MoU rules."""
    __tablename__ = "insurance_plans"
    __table_args__ = (
        CheckConstraint("default_copay_percentage >= 0 AND default_copay_percentage <= 100", name="ck_plan_copay_range"),
        CheckConstraint("max_room_rent_daily_paise IS NULL OR max_room_rent_daily_paise >= 0", name="ck_plan_room_cap_non_neg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("insurance_providers.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    default_copay_percentage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_room_rent_daily_paise: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # None = no daily room cap
    apply_tariff_automatically: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    provider: Mapped["InsuranceProvider"] = relationship()
    tariffs: Mapped[List["InsurancePlanTariff"]] = relationship(back_populates="plan", cascade="all, delete-orphan")


class InsurancePlanTariff(AuditMixin, Base):
    """Plan-specific negotiated rate card (Schedule of Charges) and coverage exclusion rules per ServiceCatalog item."""
    __tablename__ = "insurance_plan_tariffs"
    __table_args__ = (
        UniqueConstraint("plan_id", "service_id", name="uq_plan_service_tariff"),
        CheckConstraint("tariff_price_paise >= 0", name="ck_tariff_price_non_neg"),
        Index("ix_plan_tariffs_plan_id", "plan_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("insurance_plans.id", ondelete="CASCADE"), nullable=False)
    service_id: Mapped[int] = mapped_column(ForeignKey("service_catalog.id", ondelete="CASCADE"), nullable=False)
    tariff_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    is_covered: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    plan: Mapped["InsurancePlan"] = relationship(back_populates="tariffs")
    service: Mapped["ServiceCatalog"] = relationship()


class PatientInsurancePolicy(AuditMixin, Base):
    """Links a patient to an InsuranceProvider with sum insured, co-pay percentage, and validity dates."""
    __tablename__ = "patient_insurance_policies"
    __table_args__ = (
        UniqueConstraint("provider_id", "policy_number", name="uq_provider_policy_number"),
        CheckConstraint("sum_insured_paise > 0", name="ck_policy_sum_insured_pos"),
        CheckConstraint("copay_percentage >= 0 AND copay_percentage <= 100", name="ck_policy_copay_range"),
        Index("ix_patient_policies_patient_id", "patient_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    provider_id: Mapped[int] = mapped_column(ForeignKey("insurance_providers.id"), nullable=False)
    plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("insurance_plans.id"), nullable=True)

    policy_number: Mapped[str] = mapped_column(String(100), nullable=False)
    member_id: Mapped[str] = mapped_column(String(100), nullable=False)
    sum_insured_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    copay_percentage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0 to 100
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_till: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    patient: Mapped["Patient"] = relationship()
    provider: Mapped["InsuranceProvider"] = relationship()
    plan: Mapped[Optional["InsurancePlan"]] = relationship()


class InsurancePreAuth(AuditMixin, Base):
    """Cashless pre-authorization workflow tied to an active encounter and billable ICD-10 diagnosis."""
    __tablename__ = "insurance_pre_auths"
    __table_args__ = (
        CheckConstraint("requested_amount_paise > 0", name="ck_preauth_req_amount_pos"),
        CheckConstraint("approved_amount_paise >= 0", name="ck_preauth_app_amount_non_neg"),
        Index("ix_preauth_encounter_id", "encounter_id"),
        Index("ix_insurance_pre_auths_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id"), nullable=False)
    policy_id: Mapped[int] = mapped_column(ForeignKey("patient_insurance_policies.id"), nullable=False)
    primary_diagnosis_id: Mapped[int] = mapped_column(ForeignKey("diagnoses_master.id"), nullable=False)

    preauth_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    status: Mapped[PreAuthStatusEnum] = mapped_column(default=PreAuthStatusEnum.SUBMITTED, nullable=False)
    requested_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tpa_reference_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    clinical_justification: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tpa_query_remarks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    policy: Mapped["PatientInsurancePolicy"] = relationship()
    primary_diagnosis: Mapped["DiagnosisMaster"] = relationship()


class InsuranceClaim(AuditMixin, Base):
    """Final insurance claim splitting an Invoice between Insurer Payable and Patient Co-Pay."""
    __tablename__ = "insurance_claims"
    __table_args__ = (
        CheckConstraint("claimed_amount_paise > 0", name="ck_claim_claimed_pos"),
        CheckConstraint("insurer_payable_paise >= 0", name="ck_claim_insurer_non_neg"),
        CheckConstraint("patient_copay_paise >= 0", name="ck_claim_copay_non_neg"),
        Index("ix_claims_invoice_id", "invoice_id"),
        Index("ix_insurance_claims_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), unique=True, nullable=False)
    policy_id: Mapped[int] = mapped_column(ForeignKey("patient_insurance_policies.id"), nullable=False)
    preauth_id: Mapped[Optional[int]] = mapped_column(ForeignKey("insurance_pre_auths.id"), nullable=True)

    claim_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    status: Mapped[ClaimStatusEnum] = mapped_column(default=ClaimStatusEnum.SUBMITTED, nullable=False)

    claimed_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    insurer_payable_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    patient_copay_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    settled_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    settlement_utr_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    remarks: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    invoice: Mapped["Invoice"] = relationship()
    policy: Mapped["PatientInsurancePolicy"] = relationship()
    preauth: Mapped[Optional["InsurancePreAuth"]] = relationship()
