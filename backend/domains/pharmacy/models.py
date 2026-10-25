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

from backend.domains.encounters.models import PatientEncounter
from backend.domains.identity.models import User


class MedicationFormEnum(str, PyEnum):
    TABLET = "TABLET"
    CAPSULE = "CAPSULE"
    SYRUP = "SYRUP"
    INJECTION = "INJECTION"
    OINTMENT = "OINTMENT"
    DROPS = "DROPS"


class PrescriptionStatusEnum(str, PyEnum):
    ACTIVE = "ACTIVE"
    PARTIAL = "PARTIAL"
    DISPENSED = "DISPENSED"
    CANCELLED = "CANCELLED"


class eMARStatusEnum(str, PyEnum):
    ADMINISTERED = "ADMINISTERED"
    HELD = "HELD"
    REFUSED = "REFUSED"
    MISSED = "MISSED"


class MedicationMaster(AuditMixin, Base):
    __tablename__ = "medication_master"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    brand_name: Mapped[str] = mapped_column(String(100), nullable=False)
    generic_name: Mapped[str] = mapped_column(String(100), nullable=False)
    form: Mapped[MedicationFormEnum] = mapped_column(nullable=False)
    strength: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    is_controlled_substance: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ndc_or_barcode: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    low_stock_threshold_units: Mapped[Optional[int]] = mapped_column(Integer)
    fefo_expiry_warning_days: Mapped[Optional[int]] = mapped_column(Integer)


class InventoryBatch(AuditMixin, Base):
    __tablename__ = "inventory_batches"
    __table_args__ = (
        UniqueConstraint("medication_id", "batch_number", name="uq_med_batch_number"),
        CheckConstraint("stock_quantity >= 0", name="ck_batch_stock_non_neg"),
        CheckConstraint("unit_price_paise >= 0", name="ck_batch_price_non_neg"),
        Index("ix_inventory_batches_medication_id", "medication_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    medication_id: Mapped[int] = mapped_column(ForeignKey("medication_master.id"), nullable=False)
    batch_number: Mapped[str] = mapped_column(String(50), nullable=False)
    expiry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)

    medication: Mapped[MedicationMaster] = relationship()


class Prescription(AuditMixin, Base):
    __tablename__ = "prescriptions"
    __table_args__ = (Index("ix_prescriptions_created_at", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id"), nullable=False)
    prescribing_doctor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[PrescriptionStatusEnum] = mapped_column(default=PrescriptionStatusEnum.ACTIVE, nullable=False)
    clinical_notes: Mapped[Optional[str]] = mapped_column(Text)

    encounter: Mapped["PatientEncounter"] = relationship()
    prescribing_doctor: Mapped["User"] = relationship()
    items: Mapped[List["PrescriptionItem"]] = relationship(back_populates="prescription", cascade="all, delete-orphan")


class PrescriptionItem(AuditMixin, Base):
    __tablename__ = "prescription_items"
    __table_args__ = (
        CheckConstraint("requested_quantity > 0", name="ck_rx_req_qty_pos"),
        CheckConstraint("dispensed_quantity >= 0", name="ck_rx_disp_qty_non_neg"),
        CheckConstraint("dispensed_quantity <= requested_quantity", name="ck_rx_no_overdispense"),
        Index("ix_prescription_items_created_at", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    prescription_id: Mapped[int] = mapped_column(ForeignKey("prescriptions.id", ondelete="CASCADE"), nullable=False)
    medication_id: Mapped[int] = mapped_column(ForeignKey("medication_master.id"), nullable=False)
    dosage_instruction: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    dispensed_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    prescription: Mapped[Prescription] = relationship(back_populates="items")
    medication: Mapped[MedicationMaster] = relationship()


class MedicationAdministrationLog(AuditMixin, Base):
    """Bedside nursing eMAR log recording individual doses administered, held, or refused."""
    __tablename__ = "medication_administration_logs"
    __table_args__ = (
        Index("ix_emar_encounter_id", "encounter_id"),
        Index("ix_emar_rx_item_id", "prescription_item_id"),
        Index("ix_medication_admin_logs_administered_at", "administered_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False)
    prescription_item_id: Mapped[int] = mapped_column(ForeignKey("prescription_items.id", ondelete="CASCADE"), nullable=False)
    administered_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    scheduled_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    administered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status: Mapped[eMARStatusEnum] = mapped_column(nullable=False)
    dose_given: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "500mg IV" or "1 Tablet"
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    prescription_item: Mapped["PrescriptionItem"] = relationship()
    administered_by: Mapped["User"] = relationship(foreign_keys=[administered_by_id])


class PharmacyReturnLog(AuditMixin, Base):
    """Tracks ward/patient returns of dispensed medications back into InventoryBatch with auto-credit/refund."""
    __tablename__ = "pharmacy_return_logs"
    __table_args__ = (
        CheckConstraint("returned_quantity > 0", name="ck_pharm_return_qty_pos"),
        CheckConstraint("credit_amount_paise >= 0", name="ck_pharm_return_credit_non_neg"),
        Index("ix_pharm_returns_rx_id", "prescription_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    prescription_id: Mapped[int] = mapped_column(ForeignKey("prescriptions.id", ondelete="CASCADE"), nullable=False)
    prescription_item_id: Mapped[int] = mapped_column(ForeignKey("prescription_items.id", ondelete="CASCADE"), nullable=False)
    batch_id: Mapped[int] = mapped_column(ForeignKey("inventory_batches.id"), nullable=False)
    returned_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    returned_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    credit_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    return_reason: Mapped[str] = mapped_column(String(255), nullable=False)

    prescription: Mapped["Prescription"] = relationship()
    prescription_item: Mapped["PrescriptionItem"] = relationship()
    batch: Mapped["InventoryBatch"] = relationship()
    returned_by: Mapped["User"] = relationship(foreign_keys=[returned_by_id])


class ControlledSubstanceVault(AuditMixin, Base):
    """Perpetual inventory vault register for Schedule II / X controlled narcotics (e.g. Fentanyl, Morphine)."""
    __tablename__ = "controlled_substance_vaults"
    __table_args__ = (
        CheckConstraint("current_balance_units >= 0", name="ck_vault_bal_non_neg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    medication_id: Mapped[int] = mapped_column(ForeignKey("medication_master.id"), unique=True, nullable=False)
    vault_location: Mapped[str] = mapped_column(String(100), default="MAIN_NARCOTICS_SAFE", nullable=False)
    current_balance_units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unit_of_measure: Mapped[str] = mapped_column(String(30), default="AMPOULE", nullable=False)
    tamper_seal_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    medication: Mapped[MedicationMaster] = relationship()


class ControlledSubstanceMovementLog(AuditMixin, Base):
    """Auditable perpetual movement log for narcotics receipts, dispenses, and dual-witness wastage."""
    __tablename__ = "controlled_substance_movement_logs"
    __table_args__ = (
        CheckConstraint("quantity_units > 0", name="ck_narcotic_move_qty_pos"),
        CheckConstraint("balance_after >= 0", name="ck_narcotic_bal_after_non_neg"),
        Index("ix_narcotic_move_vault_id", "vault_id"),
        Index("ix_narcotic_move_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    vault_id: Mapped[int] = mapped_column(ForeignKey("controlled_substance_vaults.id", ondelete="CASCADE"), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(30), nullable=False)  # RECEIPT, DISPENSE, ADMINISTRATION, RETURN_TO_VAULT, WASTAGE_DESTROYED
    quantity_units: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_before: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    encounter_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patient_encounters.id"), nullable=True)
    prescription_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey("prescription_items.id"), nullable=True)
    primary_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    witness_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    witness_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    wastage_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    disposal_method: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    batch_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    vault: Mapped[ControlledSubstanceVault] = relationship()
    primary_user: Mapped["User"] = relationship(foreign_keys=[primary_user_id])
    witness_user: Mapped[Optional["User"]] = relationship(foreign_keys=[witness_user_id])


class BCMAScanAuditLog(AuditMixin, Base):
    """Bedside Barcode Medication Administration (BCMA) 5-Rights verification log and scan history."""
    __tablename__ = "bcma_scan_audit_logs"
    __table_args__ = (
        Index("ix_bcma_encounter_time", "encounter_id", "scanned_at"),
        Index("ix_bcma_rx_item", "prescription_item_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False)
    prescription_item_id: Mapped[int] = mapped_column(ForeignKey("prescription_items.id", ondelete="CASCADE"), nullable=False)
    administered_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    scanned_patient_barcode: Mapped[str] = mapped_column(String(100), nullable=False)
    scanned_medication_barcode: Mapped[str] = mapped_column(String(100), nullable=False)
    patient_matched: Mapped[bool] = mapped_column(Boolean, nullable=False)
    medication_matched: Mapped[bool] = mapped_column(Boolean, nullable=False)
    dose_matched: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    time_status: Mapped[str] = mapped_column(String(30), default="ON_TIME", nullable=False)  # ON_TIME, EARLY, LATE, PRN
    verification_status: Mapped[str] = mapped_column(String(50), nullable=False)  # VERIFIED, WARNING_OVERRIDDEN, BLOCKED_MISMATCH
    override_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    emar_log_id: Mapped[Optional[int]] = mapped_column(ForeignKey("medication_administration_logs.id", ondelete="SET NULL"), nullable=True)

    encounter: Mapped["PatientEncounter"] = relationship()
    prescription_item: Mapped[PrescriptionItem] = relationship()
    administered_by: Mapped["User"] = relationship()
    emar_log: Mapped[Optional[MedicationAdministrationLog]] = relationship()
