import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, event, func
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
    hl7_shared_secret_encrypted: Mapped[Optional[str]] = mapped_column(Text)
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
    provisioning_config_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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

    @property
    def pricing_model_string(self) -> str:
        return self.pricing_model.value

    @pricing_model_string.setter
    def pricing_model_string(self, value: str | PricingModelEnum) -> None:
        self.pricing_model = (
            value if isinstance(value, PricingModelEnum) else PricingModelEnum(value)
        )

    @property
    def billing_cycle_string(self) -> str:
        return self.billing_cycle.value

    @billing_cycle_string.setter
    def billing_cycle_string(self, value: str | BillingCycleEnum) -> None:
        self.billing_cycle = (
            value if isinstance(value, BillingCycleEnum) else BillingCycleEnum(value)
        )


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
        # DB-003: Include source_mrn so mother and newborn sharing a phone hash don't collide
        UniqueConstraint("phone_hash", "branch_code", "source_mrn", name="uq_pointer_phone_branch_mrn"),
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


@event.listens_for(ControlPlaneAuditLog, "before_update")
def _prevent_audit_log_update(mapper, connection, target):
    raise PermissionError("ControlPlaneAuditLog records are strictly immutable and cannot be updated.")


@event.listens_for(ControlPlaneAuditLog, "before_delete")
def _prevent_audit_log_delete(mapper, connection, target):
    raise PermissionError("ControlPlaneAuditLog records are strictly immutable and cannot be deleted.")



class TenantSnapshotRegistry(ControlBase):
    """Tracks encrypted backup artifacts for UI-driven disaster recovery."""
    __tablename__ = "tenant_snapshots_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    subdomain_slug: Mapped[str] = mapped_column(String(63), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(50), default="SCHEDULED", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ModuleCatalog(ControlBase):
    __tablename__ = "module_catalog"

    code: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    base_annual_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class BundleCatalog(ControlBase):
    __tablename__ = "bundle_catalog"

    code: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    modules_json: Mapped[str] = mapped_column(Text, nullable=False)
    bundle_annual_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class BlueprintCatalog(ControlBase):
    __tablename__ = "blueprint_catalog"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    beds: Mapped[int] = mapped_column(Integer, nullable=False)
    floors_count: Mapped[int] = mapped_column(Integer, nullable=False)
    theatres_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MigrationSchema(ControlBase):
    __tablename__ = "migration_schemas"
    __table_args__ = (UniqueConstraint("entity_type", name="uq_migration_schemas_entity_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_json: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ControlPlaneOperator(ControlBase):
    __tablename__ = "control_plane_operators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="viewer", server_default="viewer", nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    mfa_secret: Mapped[Optional[str]] = mapped_column(String(255))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_last_used_counter: Mapped[Optional[int]] = mapped_column(Integer)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ControlPlaneOperatorSession(ControlBase):
    __tablename__ = "control_plane_operator_sessions"

    token_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    operator_id: Mapped[int] = mapped_column(
        ForeignKey("control_plane_operators.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )