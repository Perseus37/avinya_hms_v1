from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.audit import AuditMixin
from backend.core.database import Base
from backend.domains.identity.models import Department, User
from backend.domains.encounters.models import PatientEncounter
from backend.domains.pharmacy.models import MedicationMaster, InventoryBatch
# from backend.domains.procurement.models import VendorMaster, PurchaseOrder, GoodsReceiptNote


class VendorCategoryEnum(str, PyEnum):
    PHARMACEUTICALS = "PHARMACEUTICALS"
    SURGICAL_IMPLANTS = "SURGICAL_IMPLANTS"
    LAB_REAGENTS = "LAB_REAGENTS"
    EQUIPMENT_MAINTENANCE = "EQUIPMENT_MAINTENANCE"
    GENERAL_SUPPLIES = "GENERAL_SUPPLIES"


class POStatusEnum(str, PyEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class GRNStatusEnum(str, PyEnum):
    PENDING_INSPECTION = "PENDING_INSPECTION"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class IndentStatusEnum(str, PyEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    FULFILLED = "FULFILLED"
    CANCELLED = "CANCELLED"

class VendorInvoiceStatusEnum(str, PyEnum):
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    PRICE_VARIANCE_FLAGGED = "PRICE_VARIANCE_FLAGGED"
    MATCHED_APPROVED = "MATCHED_APPROVED"
    PAID = "PAID"
    REJECTED = "REJECTED"

class VendorMaster(AuditMixin, Base):
    """Registered hospital suppliers, distributors, and manufacturers."""
    __tablename__ = "procurement_vendors"
    __table_args__ = (
        CheckConstraint("credit_period_days >= 0", name="ck_vendor_credit_days_non_neg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    category: Mapped[VendorCategoryEnum] = mapped_column(nullable=False)
    gstin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    pan: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    contact_person: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    bank_account_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    bank_ifsc: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    credit_period_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PurchaseOrder(AuditMixin, Base):
    """Official procurement order placed with a vendor."""
    __tablename__ = "procurement_purchase_orders"
    __table_args__ = (
        Index("ix_po_vendor_status", "vendor_id", "status"),
        CheckConstraint("total_amount_paise >= 0", name="ck_po_total_non_neg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    po_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("procurement_vendors.id"), nullable=False)
    status: Mapped[POStatusEnum] = mapped_column(default=POStatusEnum.DRAFT, nullable=False)
    delivery_due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payment_terms: Mapped[str] = mapped_column(String(255), nullable=False)
    total_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    vendor: Mapped["VendorMaster"] = relationship()
    approved_by: Mapped[Optional["User"]] = relationship(foreign_keys=[approved_by_id])
    items: Mapped[List["PurchaseOrderItem"]] = relationship(back_populates="purchase_order", cascade="all, delete-orphan")


class PurchaseOrderItem(AuditMixin, Base):
    """Line items on a Purchase Order (Medication or General Consumables)."""
    __tablename__ = "procurement_po_items"
    __table_args__ = (
        CheckConstraint("quantity_ordered > 0", name="ck_po_item_qty_pos"),
        CheckConstraint("unit_price_paise >= 0", name="ck_po_item_price_non_neg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("procurement_purchase_orders.id", ondelete="CASCADE"), nullable=False)
    medication_id: Mapped[Optional[int]] = mapped_column(ForeignKey("medication_master.id"), nullable=True)
    item_description: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity_ordered: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_received: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    total_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)

    purchase_order: Mapped["PurchaseOrder"] = relationship(back_populates="items")
    medication: Mapped[Optional["MedicationMaster"]] = relationship()


class GoodsReceiptNote(AuditMixin, Base):
    """Document verifying delivery inspection and physical stock arrival."""
    __tablename__ = "procurement_grn"
    __table_args__ = (
        Index("ix_grn_po_id", "po_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    grn_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("procurement_purchase_orders.id"), nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    invoice_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[GRNStatusEnum] = mapped_column(default=GRNStatusEnum.PENDING_INSPECTION, nullable=False)
    inspected_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    inspection_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    purchase_order: Mapped["PurchaseOrder"] = relationship()
    inspected_by: Mapped["User"] = relationship(foreign_keys=[inspected_by_id])
    items: Mapped[List["GoodsReceiptItem"]] = relationship(back_populates="grn", cascade="all, delete-orphan")


class GoodsReceiptItem(AuditMixin, Base):
    """Specific stock items checked into inventory batches upon GRN acceptance."""
    __tablename__ = "procurement_grn_items"
    __table_args__ = (
        CheckConstraint("received_quantity >= 0", name="ck_grn_rcv_qty_non_neg"),
        CheckConstraint("accepted_quantity >= 0", name="ck_grn_acc_qty_non_neg"),
        CheckConstraint("accepted_quantity <= received_quantity", name="ck_grn_acc_lte_rcv"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    grn_id: Mapped[int] = mapped_column(ForeignKey("procurement_grn.id", ondelete="CASCADE"), nullable=False)
    po_item_id: Mapped[int] = mapped_column(ForeignKey("procurement_po_items.id"), nullable=False)
    batch_number: Mapped[str] = mapped_column(String(50), nullable=False)
    expiry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    created_batch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("inventory_batches.id"), nullable=True)

    grn: Mapped["GoodsReceiptNote"] = relationship(back_populates="items")
    po_item: Mapped["PurchaseOrderItem"] = relationship()
    created_batch: Mapped[Optional["InventoryBatch"]] = relationship()


class StoreIndent(AuditMixin, Base):
    """Ward or Department request for consumables/medicines from the Central Pharmacy/Store either for ward stock or a specific patient."""
    __tablename__ = "procurement_indents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    indent_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    requesting_department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False)

    encounter_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patient_encounters.id"), nullable=True)

    status: Mapped[IndentStatusEnum] = mapped_column(default=IndentStatusEnum.PENDING, nullable=False)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    remarks: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    encounter: Mapped[Optional["PatientEncounter"]] = relationship()

    requesting_department: Mapped["Department"] = relationship()
    requested_by: Mapped["User"] = relationship(foreign_keys=[requested_by_id])
    approved_by: Mapped[Optional["User"]] = relationship(foreign_keys=[approved_by_id])
    items: Mapped[List["StoreIndentItem"]] = relationship(back_populates="indent", cascade="all, delete-orphan")


class StoreIndentItem(AuditMixin, Base):
    """Individual line items requested in an Indent."""
    __tablename__ = "procurement_indent_items"
    __table_args__ = (
        CheckConstraint("requested_quantity > 0", name="ck_indent_req_qty_pos"),
        CheckConstraint("issued_quantity >= 0", name="ck_indent_iss_qty_non_neg"),
        CheckConstraint("issued_quantity <= requested_quantity", name="ck_indent_no_overissue"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    indent_id: Mapped[int] = mapped_column(ForeignKey("procurement_indents.id", ondelete="CASCADE"), nullable=False)
    medication_id: Mapped[int] = mapped_column(ForeignKey("medication_master.id"), nullable=False)
    requested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    issued_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    indent: Mapped["StoreIndent"] = relationship(back_populates="items")
    medication: Mapped["MedicationMaster"] = relationship()

class VendorInvoice(AuditMixin, Base):
    """Vendor tax invoice uploaded for procurement settlement and 3-way matching."""
    __tablename__ = "procurement_vendor_invoices"
    __table_args__ = (
        UniqueConstraint("vendor_id", "invoice_number", name="uq_vendor_invoice_number"),
        CheckConstraint("total_amount_paise >= 0", name="ck_invoice_total_non_neg"),
        Index("ix_vendor_inv_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("procurement_vendors.id"), nullable=False)
    po_id: Mapped[Optional[int]] = mapped_column(ForeignKey("procurement_purchase_orders.id"), nullable=True)
    grn_id: Mapped[Optional[int]] = mapped_column(ForeignKey("procurement_grn.id"), nullable=True)

    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    invoice_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    subtotal_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_amount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discount_paise: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[VendorInvoiceStatusEnum] = mapped_column(
        Enum(VendorInvoiceStatusEnum), default=VendorInvoiceStatusEnum.PENDING_VERIFICATION, nullable=False
    )
    document_file_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    verified_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    linked_expense_id: Mapped[Optional[int]] = mapped_column(ForeignKey("finance_expenses.id"), nullable=True)

    vendor: Mapped["VendorMaster"] = relationship()
    purchase_order: Mapped[Optional["PurchaseOrder"]] = relationship()
    grn: Mapped[Optional["GoodsReceiptNote"]] = relationship()
    verified_by: Mapped[Optional["User"]] = relationship(foreign_keys=[verified_by_id])
    items: Mapped[List["VendorInvoiceItem"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")


class ProcurementDocumentDownloadAudit(Base):
    """Records authorized requests to download a procurement invoice document."""
    __tablename__ = "procurement_document_download_audits"
    __table_args__ = (
        Index("ix_proc_doc_download_invoice_time", "invoice_id", "requested_at"),
        Index("ix_proc_doc_download_actor_time", "actor_user_id", "requested_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("procurement_vendor_invoices.id", ondelete="SET NULL"),
        nullable=True,
    )
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_email: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class VendorInvoiceItem(AuditMixin, Base):
    """Line item details extracted or mapped from the vendor invoice."""
    __tablename__ = "procurement_vendor_invoice_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_inv_item_qty_pos"),
        CheckConstraint("unit_price_paise >= 0", name="ck_inv_item_price_non_neg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("procurement_vendor_invoices.id", ondelete="CASCADE"), nullable=False)
    po_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey("procurement_po_items.id"), nullable=True)
    medication_id: Mapped[Optional[int]] = mapped_column(ForeignKey("medication_master.id"), nullable=True)

    item_description: Mapped[str] = mapped_column(String(200), nullable=False)
    batch_number: Mapped[str] = mapped_column(String(50), nullable=False)
    expiry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_percentage: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # e.g., 5, 12, 18
    total_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)

    invoice: Mapped["VendorInvoice"] = relationship(back_populates="items")