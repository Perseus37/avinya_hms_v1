import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, Enum, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ControlBase(DeclarativeBase):
    """Declarative base strictly for the central hms_control_plane database."""
    pass


class DeploymentModeEnum(str, enum.Enum):
    CLOUD = "CLOUD"
    ON_PREM = "ON_PREM"


class TenantRegistry(ControlBase):
    """Maps incoming hospital subdomains (<slug>.yourdomain.com) to isolated PostgreSQL databases."""
    __tablename__ = "tenant_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    hospital_name: Mapped[str] = mapped_column(String(150), nullable=False)
    branch_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    subdomain_slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False, index=True)
    db_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    admin_email: Mapped[str] = mapped_column(String(255), nullable=False)
    jwt_secret_key: Mapped[str] = mapped_column(String(128), nullable=False)
    backup_encryption_key: Mapped[str] = mapped_column(String(128), nullable=False)
    deployment_mode: Mapped[DeploymentModeEnum] = mapped_column(
        Enum(DeploymentModeEnum), default=DeploymentModeEnum.CLOUD, nullable=False
    )
    api_base_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


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