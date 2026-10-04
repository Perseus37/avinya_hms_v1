import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, Enum, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ControlBase(DeclarativeBase):
    """Declarative base strictly for the central hms_control_plane database."""
    pass


class DeploymentModeEnum(str, enum.Enum):
    CLOUD = "CLOUD"
    ON_PREM = "ON_PREM"


class PricingModelEnum(str, enum.Enum):
    CUSTOM_A_LA_CARTE = "CUSTOM_A_LA_CARTE"
    BUNDLE_OPD_DAYCARE = "BUNDLE_OPD_DAYCARE"
    BUNDLE_DIAGNOSTICS_HUB = "BUNDLE_DIAGNOSTICS_HUB"
    BUNDLE_SURGICAL_SUITE = "BUNDLE_SURGICAL_SUITE"
    BUNDLE_TOTAL_HOSPITAL = "BUNDLE_TOTAL_HOSPITAL"
    # Legacy alias values for backward compatibility
    STARTER_OPD = "STARTER_OPD"
    PRO_MULTISPECIALITY = "PRO_MULTISPECIALITY"
    ENTERPRISE_QUATERNARY = "ENTERPRISE_QUATERNARY"


# Alias for backward compatibility
SubscriptionTierEnum = PricingModelEnum


class BillingCycleEnum(str, enum.Enum):
    ANNUAL_LICENSE = "ANNUAL_LICENSE"
    MONTHLY_SUBSCRIPTION = "MONTHLY_SUBSCRIPTION"
    PER_ACTIVE_BED_MONTHLY = "PER_ACTIVE_BED_MONTHLY"
    ONE_TIME_PERPETUAL = "ONE_TIME_PERPETUAL"


class TenantRegistry(ControlBase):
    """Maps incoming hospital subdomains (<slug>.yourdomain.com) to isolated PostgreSQL databases and modular contracts."""
    __tablename__ = "tenant_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    hospital_name: Mapped[str] = mapped_column(String(150), nullable=False)
    branch_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    subdomain_slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False, index=True)
    db_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    admin_email: Mapped[str] = mapped_column(String(255), nullable=False)
    jwt_secret_key: Mapped[str] = mapped_column(String(255), nullable=False)
    backup_encryption_key: Mapped[str] = mapped_column(String(255), nullable=False)
    deployment_mode: Mapped[DeploymentModeEnum] = mapped_column(
        Enum(DeploymentModeEnum), default=DeploymentModeEnum.CLOUD, nullable=False
    )

    # Modular & Bundle Commercial Licensing
    pricing_model: Mapped[PricingModelEnum] = mapped_column(
        Enum(PricingModelEnum), default=PricingModelEnum.BUNDLE_TOTAL_HOSPITAL, nullable=False
    )
    billing_cycle: Mapped[BillingCycleEnum] = mapped_column(
        Enum(BillingCycleEnum), default=BillingCycleEnum.ANNUAL_LICENSE, nullable=False
    )
    base_license_fee_paise: Mapped[int] = mapped_column(Integer, default=29500000, nullable=False)
    enabled_modules_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default='{"all": true}')
    commercial_contract_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quota_limits_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ui_branding_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    maintenance_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    maintenance_notice: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    api_base_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    @property
    def subscription_tier(self) -> PricingModelEnum:
        return self.pricing_model

    @subscription_tier.setter
    def subscription_tier(self, value: PricingModelEnum) -> None:
        self.pricing_model = value


class FederatedNode(ControlBase):
    """Stores SHA-256 hashed Federation API keys for every Cloud and On-Premise hospital branch."""
    __tablename__ = "federated_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    branch_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    hospital_name: Mapped[str] = mapped_column(String(150), nullable=False)
    subdomain_slug: Mapped[str] = mapped_column(String(63), nullable=False)
    api_key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class GlobalPatientPointer(ControlBase):
    """
    Central Record Locator Service (Solves Hospital A -> B -> C routing).
    Stores pointers to which branch holds a patient's record and when it was last updated.
    """
    __tablename__ = "global_patient_pointers"
    __table_args__ = (
        UniqueConstraint("phone_hash", "branch_code", name="uq_pointer_phone_branch"),
        Index("ix_pointer_lookup_phone", "phone_hash", "last_clinical_update_at"),
        Index("ix_pointer_lookup_abha", "abha_number", "last_clinical_update_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    abha_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    branch_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    subdomain_slug: Mapped[str] = mapped_column(String(63), nullable=False)
    source_mrn: Mapped[str] = mapped_column(String(50), nullable=False)
    has_critical_allergies: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_clinical_update_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ControlPlaneAuditLog(ControlBase):
    """Immutable audit trail for vendor-level administrative operations."""
    __tablename__ = "control_plane_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_slug: Mapped[str] = mapped_column(String(63), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(100), default="VENDOR_CONSOLE", nullable=False)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TenantSnapshotRegistry(ControlBase):
    """Tracks encrypted backup artifacts for UI-driven disaster recovery."""
    __tablename__ = "tenant_snapshots_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    subdomain_slug: Mapped[str] = mapped_column(String(63), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(50), default="SCHEDULED", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)