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

from backend.domains.diagnostics.models import ServiceCatalog
from backend.domains.encounters.models import PatientEncounter
from backend.domains.identity.models import User
from backend.domains.patients.models import Patient
# from backend.domains.procurement.models import VendorMaster, PurchaseOrder, GoodsReceiptNote

class InvoiceStatusEnum(str, PyEnum):
    DRAFT = "DRAFT"
    UNPAID = "UNPAID"
    FINALIZED = "UNPAID"  # Alias to UNPAID to match PostgreSQL invoicestatusenum
    PARTIAL = "PARTIAL"
    PAID = "PAID"
    VOID = "VOID"


class PaymentMethodEnum(str, PyEnum):
    CASH = "CASH"
    CREDIT_CARD = "CREDIT_CARD"
    INSURANCE = "INSURANCE"
    UPI = "UPI"
    BANK_TRANSFER = "BANK_TRANSFER"
    ADVANCE_ADJUSTMENT = "ADVANCE_ADJUSTMENT"


class DepositStatusEnum(str, PyEnum):
    ACTIVE = "ACTIVE"
    EXHAUSTED = "EXHAUSTED"
    REFUNDED = "REFUNDED"

# class VendorInvoiceStatusEnum(str, PyEnum):
#     PENDING_VERIFICATION = "PENDING_VERIFICATION"
#     PRICE_VARIANCE_FLAGGED = "PRICE_VARIANCE_FLAGGED"
#     MATCHED_APPROVED = "MATCHED_APPROVED"
#     PAID = "PAID"
#     REJECTED = "REJECTED"

class Invoice(AuditMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint("total_amount_paise >= 0", name="ck_invoice_total_non_neg"),
        CheckConstraint("discount_amount_paise >= 0", name="ck_invoice_discount_non_neg"),
        CheckConstraint("discount_amount_paise <= total_amount_paise", name="ck_invoice_discount_le_total"),
        CheckConstraint("amount_paid_paise >= 0", name="ck_invoice_paid_non_neg"),
        CheckConstraint(
            "amount_paid_paise <= (total_amount_paise + tax_amount_paise - discount_amount_paise)",
            name="ck_invoice_no_overpayment",
        ),
        CheckConstraint("tax_amount_paise >= 0", name="ck_invoice_tax_non_neg"),
        CheckConstraint("cgst_amount_paise >= 0", name="ck_invoice_cgst_non_neg"),
        CheckConstraint("sgst_amount_paise >= 0", name="ck_invoice_sgst_non_neg"),
        CheckConstraint("igst_amount_paise >= 0", name="ck_invoice_igst_non_neg"),
        Index("ix_invoices_patient_id", "patient_id"),
        Index("ix_invoices_encounter_id", "encounter_id"),
        # DB-005: Prevent multiple DRAFT invoices for a single encounter (PostgreSQL + SQLite compatible)
        Index(
            "uq_encounter_draft_inv",
            "encounter_id",
            unique=True,
            postgresql_where=text("status = 'DRAFT'"),
            sqlite_where=text("status = 'DRAFT'")
        ),
        Index("ix_invoices_created_at", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    encounter_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patient_encounters.id"))
    invoice_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    status: Mapped[InvoiceStatusEnum] = mapped_column(default=InvoiceStatusEnum.DRAFT, nullable=False)
    total_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discount_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discount_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    discount_approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    amount_paid_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tax_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cgst_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sgst_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    igst_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_tax_exempt: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    hsn_sac_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # DB-001: Enforce Optimistic Concurrency Control to prevent lost updates on financial ledgers
    version_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __mapper_args__ = {"version_id_col": version_id}

    patient: Mapped["Patient"] = relationship()
    encounter: Mapped[Optional["PatientEncounter"]] = relationship()
    discount_approved_by: Mapped[Optional["User"]] = relationship(foreign_keys=[discount_approved_by_id])
    line_items: Mapped[List["InvoiceLineItem"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")
    payments: Mapped[List["PaymentReceipt"]] = relationship(back_populates="invoice")

    @property
    def net_amount_paise(self) -> int:
        return max(0, self.total_amount_paise + self.tax_amount_paise - (self.discount_amount_paise or 0))


class InvoiceLineItem(AuditMixin, Base):
    __tablename__ = "invoice_line_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_line_item_qty_pos"),
        CheckConstraint("unit_price_paise >= 0", name="ck_line_item_unit_price_non_neg"),
        CheckConstraint("total_price_paise >= 0", name="ck_line_item_total_non_neg"),
        CheckConstraint("gst_rate_percent >= 0 AND gst_rate_percent <= 100", name="ck_line_item_gst_rate"),
        CheckConstraint("tax_amount_paise >= 0", name="ck_line_item_tax_non_neg"),
        Index("ix_invoice_line_items_invoice_id", "invoice_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    service_id: Mapped[Optional[int]] = mapped_column(ForeignKey("service_catalog.id"), index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    total_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    gst_rate_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tax_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hsn_sac_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    invoice: Mapped[Invoice] = relationship(back_populates="line_items")
    service: Mapped[Optional["ServiceCatalog"]] = relationship()


class PaymentReceipt(AuditMixin, Base):
    __tablename__ = "payment_receipts"
    __table_args__ = (
        CheckConstraint("amount_paid_paise > 0", name="ck_payment_receipt_amount_pos"),
        UniqueConstraint("invoice_id", "transaction_reference", name="uq_invoice_txn_ref"),
        Index("ix_payment_receipts_invoice_id", "invoice_id"),
        Index("ix_payment_receipts_created_at", "created_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    receipt_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    amount_paid_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_method: Mapped[PaymentMethodEnum] = mapped_column(nullable=False)
    transaction_reference: Mapped[Optional[str]] = mapped_column(String(100))

    invoice: Mapped[Invoice] = relationship(back_populates="payments")


class PatientDeposit(AuditMixin, Base):
    """Tracks upfront IPD/OPD advance deposits collected before invoice finalization."""
    __tablename__ = "patient_deposits"
    __table_args__ = (
        CheckConstraint("amount_paise > 0", name="ck_deposit_amount_pos"),
        CheckConstraint("remaining_balance_paise >= 0", name="ck_deposit_balance_non_neg"),
        CheckConstraint("remaining_balance_paise <= amount_paise", name="ck_deposit_balance_le_amount"),
        Index("ix_patient_deposits_patient_id", "patient_id"),
        Index("ix_patient_deposits_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    encounter_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patient_encounters.id"), nullable=True)

    deposit_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_balance_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_method: Mapped[PaymentMethodEnum] = mapped_column(nullable=False)
    transaction_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[DepositStatusEnum] = mapped_column(default=DepositStatusEnum.ACTIVE, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    patient: Mapped["Patient"] = relationship()
    encounter: Mapped[Optional["PatientEncounter"]] = relationship()


class RefundVoucher(AuditMixin, Base):
    """Immutable financial audit voucher for excess deposit refunds or billing credit notes."""
    __tablename__ = "refund_vouchers"
    __table_args__ = (
        CheckConstraint("refund_amount_paise > 0", name="ck_refund_amount_pos"),
        Index("ix_refund_vouchers_patient_id", "patient_id"),
        Index("ix_refund_vouchers_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    voucher_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    deposit_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patient_deposits.id"), nullable=True)
    invoice_id: Mapped[Optional[int]] = mapped_column(ForeignKey("invoices.id"), nullable=True)

    refund_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    refund_method: Mapped[PaymentMethodEnum] = mapped_column(nullable=False)
    transaction_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    patient: Mapped["Patient"] = relationship()
    deposit: Mapped[Optional["PatientDeposit"]] = relationship()
    invoice: Mapped[Optional["Invoice"]] = relationship()
    approved_by: Mapped["User"] = relationship(foreign_keys=[approved_by_id])

# class VendorInvoice(AuditMixin, Base):
#     """Vendor tax invoice uploaded for procurement settlement and 3-way matching."""
#     __tablename__ = "procurement_vendor_invoices"
#     __table_args__ = (
#         UniqueConstraint("vendor_id", "invoice_number", name="uq_vendor_invoice_number"),
#         CheckConstraint("total_amount_paise >= 0", name="ck_invoice_total_non_neg"),
#         Index("ix_vendor_inv_status", "status"),
#     )

#     id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
#     vendor_id: Mapped[int] = mapped_column(ForeignKey("procurement_vendors.id"), nullable=False)
#     po_id: Mapped[Optional[int]] = mapped_column(ForeignKey("procurement_purchase_orders.id"), nullable=True)
#     grn_id: Mapped[Optional[int]] = mapped_column(ForeignKey("procurement_grn.id"), nullable=True)

#     invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
#     invoice_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
#     due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

#     subtotal_paise: Mapped[int] = mapped_column(Integer, nullable=False)
#     tax_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
#     discount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
#     total_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)

#     status: Mapped[VendorInvoiceStatusEnum] = mapped_column(
#         Enum(VendorInvoiceStatusEnum), default=VendorInvoiceStatusEnum.PENDING_VERIFICATION, nullable=False
#     )
#     document_file_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
#     notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

#     verified_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
#     verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
#     linked_expense_id: Mapped[Optional[int]] = mapped_column(ForeignKey("finance_expenses.id"), nullable=True)

#     vendor: Mapped["VendorMaster"] = relationship()
#     purchase_order: Mapped[Optional["PurchaseOrder"]] = relationship()
#     grn: Mapped[Optional["GoodsReceiptNote"]] = relationship()
#     verified_by: Mapped[Optional["User"]] = relationship(foreign_keys=[verified_by_id])
#     items: Mapped[List["VendorInvoiceItem"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")


# class VendorInvoiceItem(AuditMixin, Base):
#     """Line item details extracted or mapped from the vendor invoice."""
#     __tablename__ = "procurement_vendor_invoice_items"
#     __table_args__ = (
#         CheckConstraint("quantity > 0", name="ck_inv_item_qty_pos"),
#         CheckConstraint("unit_price_paise >= 0", name="ck_inv_item_price_non_neg"),
#     )

#     id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
#     invoice_id: Mapped[int] = mapped_column(ForeignKey("procurement_vendor_invoices.id", ondelete="CASCADE"), nullable=False)
#     po_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey("procurement_po_items.id"), nullable=True)
#     medication_id: Mapped[Optional[int]] = mapped_column(ForeignKey("medication_master.id"), nullable=True)

#     item_description: Mapped[str] = mapped_column(String(200), nullable=False)
#     batch_number: Mapped[str] = mapped_column(String(50), nullable=False)
#     expiry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
#     quantity: Mapped[int] = mapped_column(Integer, nullable=False)
#     unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
#     tax_percentage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # e.g., 5, 12, 18
#     total_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)

#     invoice: Mapped["VendorInvoice"] = relationship(back_populates="items")


class ChargeCaptureEventTypeEnum(str, PyEnum):
    SURGERY_COMPLETED = "SURGERY_COMPLETED"
    MIDNIGHT_CENSUS = "MIDNIGHT_CENSUS"
    DIAGNOSTIC_VERIFIED = "DIAGNOSTIC_VERIFIED"
    CLINICAL_ORDER = "CLINICAL_ORDER"
    ROOM_BED_CHARGE = "ROOM_BED_CHARGE"
    MANUAL_ENTRY = "MANUAL_ENTRY"


class ChargeCaptureEvent(AuditMixin, Base):
    """
    Automated Revenue Cycle Management (RCM) Charge Capture ledger.
    Guarantees event idempotency and auditable linkage between clinical activities and invoice line items.
    """
    __tablename__ = "charge_capture_events"
    __table_args__ = (
        UniqueConstraint("source_reference", name="uq_charge_capture_source_ref"),
        CheckConstraint("total_charge_paise >= 0", name="ck_charge_capture_total_non_neg"),
        Index("ix_charge_capture_encounter_id", "encounter_id"),
        Index("ix_charge_capture_patient_id", "patient_id"),
        Index("ix_charge_capture_posted_at", "posted_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("patient_encounters.id", ondelete="CASCADE"), nullable=False)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"), nullable=False)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    invoice_line_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey("invoice_line_items.id", ondelete="SET NULL"), nullable=True)

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. SURGERY-12, CENSUS-4-2026-10-25
    service_id: Mapped[Optional[int]] = mapped_column(ForeignKey("service_catalog.id", ondelete="SET NULL"), nullable=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_charge_paise: Mapped[int] = mapped_column(Integer, nullable=False)

    tariff_applied: Mapped[str] = mapped_column(String(100), default="BASE_CATALOG", nullable=False)
    is_insurance_covered: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    encounter: Mapped["PatientEncounter"] = relationship()
    patient: Mapped["Patient"] = relationship()
    invoice: Mapped["Invoice"] = relationship()
    invoice_line_item: Mapped[Optional["InvoiceLineItem"]] = relationship()
    service: Mapped[Optional["ServiceCatalog"]] = relationship()
