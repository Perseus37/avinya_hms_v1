from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, func
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
    evidence_reference: Mapped[str | None] = mapped_column(String(100))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False
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
        ForeignKey("users.id"), nullable=False
    )
    configured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
