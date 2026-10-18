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



class FederationPolicyEnum(str, PyEnum):
    FULL_MESH = "FULL_MESH"
    CORPORATE_GROUP_ONLY = "CORPORATE_GROUP_ONLY"
    EMERGENCY_ALLERGIES_ONLY = "EMERGENCY_ALLERGIES_ONLY"
    ISOLATED_SILO = "ISOLATED_SILO"


class HospitalPolicySetting(AuditMixin, Base):
    """Stores tenant-configurable clinical rules, lockout thresholds, and federation data-sharing sovereignty."""
    __tablename__ = "hospital_policy_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    max_failed_login_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    session_timeout_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    default_consultation_duration_minutes: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    allow_overbooking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enforce_financial_discharge_gate: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_unfinalized_interim_payments: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fefo_expiry_warning_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    low_stock_threshold_units: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    inventory_alert_detail_limit: Mapped[int] = mapped_column(
        Integer,
        default=100,
        server_default=text("100"),
        nullable=False,
    )
    o_negative_critical_threshold_units: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    invoice_document_profile_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    report_print_modes_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
        server_default=text("'{}'"),
        nullable=False,
    )

    # Federation & Privacy Sovereignty
    federation_sharing_mode: Mapped[FederationPolicyEnum] = mapped_column(
        Enum(FederationPolicyEnum), default=FederationPolicyEnum.FULL_MESH, nullable=False
    )
    federation_share_demographics: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    federation_share_allergies: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
