from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.audit import AuditMixin
from backend.core.database import Base


class FederatedClient(AuditMixin, Base):
    """Authorized external branches allowed to request patient records."""

    __tablename__ = "federated_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    branch_code: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    api_key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FederationTransferLog(Base):
    """Immutable audit log of cross-branch patient data lookups."""

    __tablename__ = "federation_transfer_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    client_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("federated_clients.id"), nullable=True, index=True
    )
    requesting_branch_code: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, index=True
    )
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_identifier: Mapped[str] = mapped_column(String(100), nullable=False)
    purpose: Mapped[str] = mapped_column(String(255), nullable=False)
    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
