import logging

from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import select
from fastapi import HTTPException

from backend.core.database import tenant_engine_pool
from backend.domains.procurement.models import (
    StoreIndent,
    StoreIndentItem,
    IndentStatusEnum,
    GoodsReceiptNote,
    GoodsReceiptItem,
    PurchaseOrder,
    POStatusEnum,
    GRNStatusEnum,
    VendorInvoice,
    VendorInvoiceItem,
    VendorInvoiceStatusEnum,
)
from backend.domains.pharmacy.models import InventoryBatch, MedicationMaster
from backend.domains.finance.models import ExpenseRecord, ExpenseCategoryEnum, ExpenseStatusEnum
from backend.domains.procurement.ai_parser import DonutInvoiceParser
from backend.domains.billing.models import Invoice, InvoiceLineItem, InvoiceStatusEnum
from backend.domains.billing.rcm_services import recalculate_invoice_totals
from backend.domains.reports.pdf import render_html_pdf
from backend.domains.reports.templates import render_sandboxed_template

logger = logging.getLogger(__name__)

def process_grn_acceptance(db: Session, grn: GoodsReceiptNote, current_user_id: int):
    """Atomic acceptance of goods: increments PO tracking and injects FEFO batches into Pharmacy."""
    po = db.get(PurchaseOrder, grn.po_id)

    all_po_items_fulfilled = True

    for grn_item in grn.items:
        if grn_item.accepted_quantity > 0:
            # 1. Update PO received tracking
            po_item = grn_item.po_item
            po_item.quantity_received += grn_item.accepted_quantity

            if po_item.quantity_received < po_item.quantity_ordered:
                all_po_items_fulfilled = False

            # 2. Automatically provision live Pharmacy stock batches if medication is linked
            if po_item.medication_id:
                new_batch = InventoryBatch(
                    medication_id=po_item.medication_id,
                    batch_number=grn_item.batch_number,
                    expiry_date=grn_item.expiry_date,
                    stock_quantity=grn_item.accepted_quantity,
                    unit_price_paise=grn_item.unit_price_paise,
                    created_by_id=current_user_id
                )
                db.add(new_batch)
                db.flush()
                grn_item.created_batch_id = new_batch.id

    po.status = POStatusEnum.COMPLETED if all_po_items_fulfilled else POStatusEnum.PARTIALLY_RECEIVED

def trigger_donut_invoice_extraction(db_name: str, invoice_id: int, document_path: str):
    """Extract invoice data in a fresh tenant session while retaining the source file."""
    session_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=tenant_engine_pool.get_engine(db_name),
    )
    db = session_factory()
    try:
        invoice = db.get(VendorInvoice, invoice_id)
        if not invoice:
            logger.error("Invoice extraction target %s no longer exists.", invoice_id)
            return

        parser = DonutInvoiceParser()
        extracted_data = parser.extract_invoice_data(document_path)

        # Map Donut's nested output to strict procurement schema
        invoice.invoice_number = extracted_data.get("invoice_number", "UNKNOWN")

        raw_date = extracted_data.get("date")
        if raw_date:
            try:
                invoice.invoice_date = datetime.strptime(raw_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        total_price_str = str(extracted_data.get("total_price", "0")).replace(",", "").replace(".", "")
        invoice.total_amount_paise = int(total_price_str) if total_price_str.isdigit() else 0

        # Parse line items
        line_items = extracted_data.get("line_items", [])
        if isinstance(line_items, dict):
            line_items = [line_items]

        for item in line_items:
            qty_str = str(item.get("quantity", "1")).replace(",", "")
            price_str = str(item.get("price", "0")).replace(",", "").replace(".", "")

            db.add(VendorInvoiceItem(
                invoice_id=invoice.id,
                item_description=item.get("name", "Unknown Item"),
                batch_number=item.get("batch_number", "UNKNOWN_BATCH"),
                expiry_date=datetime.now(timezone.utc), # Requires heuristic refinement based on Donut output
                quantity=int(qty_str) if qty_str.isdigit() else 1,
                unit_price_paise=int(price_str) if price_str.isdigit() else 0,
                total_price_paise=(int(qty_str) * int(price_str)) if (qty_str.isdigit() and price_str.isdigit()) else 0
            ))

        invoice.status = VendorInvoiceStatusEnum.PENDING_VERIFICATION
        db.commit()

    except Exception:
        db.rollback()
        invoice = db.get(VendorInvoice, invoice_id)
        if invoice:
            logger.exception("AI extraction failed for vendor invoice %s.", invoice_id)
            invoice.notes = "AI extraction failed; the source document is retained for manual review."
            invoice.status = VendorInvoiceStatusEnum.PENDING_VERIFICATION
            db.commit()
    finally:
        db.close()

def execute_3_way_match(db: Session, invoice: VendorInvoice) -> VendorInvoiceStatusEnum:
    """Validates Vendor Invoice against PO and GRN for financial integrity."""
    if not invoice.po_id:
        return VendorInvoiceStatusEnum.PENDING_VERIFICATION

    po = db.get(PurchaseOrder, invoice.po_id)
    if (
        not po
        or po.vendor_id != invoice.vendor_id
        or po.status
        not in {
            POStatusEnum.APPROVED,
            POStatusEnum.PARTIALLY_RECEIVED,
            POStatusEnum.COMPLETED,
        }
    ):
        return VendorInvoiceStatusEnum.PENDING_VERIFICATION
    receipt = None
    if invoice.grn_id:
        receipt = db.get(GoodsReceiptNote, invoice.grn_id)
        if (
            not receipt
            or receipt.po_id != po.id
            or receipt.status != GRNStatusEnum.ACCEPTED
        ):
            return VendorInvoiceStatusEnum.PENDING_VERIFICATION
    calculated_gross = invoice.subtotal_paise + invoice.tax_amount_paise - invoice.discount_paise

    # Rule 1: Mathematical Integrity Tolerance (+/- 100 paise)
    if abs(calculated_gross - invoice.total_amount_paise) > 100:
        return VendorInvoiceStatusEnum.PENDING_VERIFICATION

    # Rule 2: PO Line Item Validation
    for item in invoice.items:
        if not item.po_item_id:
            return VendorInvoiceStatusEnum.PENDING_VERIFICATION
        po_line = next((line for line in po.items if line.id == item.po_item_id), None)
        if not po_line:
            return VendorInvoiceStatusEnum.PENDING_VERIFICATION
        if item.quantity > po_line.quantity_ordered:
            return VendorInvoiceStatusEnum.PENDING_VERIFICATION
        if receipt:
            received_quantity = sum(
                receipt_item.accepted_quantity
                for receipt_item in receipt.items
                if receipt_item.po_item_id == po_line.id
            )
            if item.quantity > received_quantity:
                return VendorInvoiceStatusEnum.PENDING_VERIFICATION

        # Flag variance if invoiced unit price strictly exceeds PO unit price
        if item.unit_price_paise > po_line.unit_price_paise:
            return VendorInvoiceStatusEnum.PRICE_VARIANCE_FLAGGED

    return VendorInvoiceStatusEnum.MATCHED_APPROVED

def post_invoice_to_accounts_payable(db: Session, invoice: VendorInvoice, current_user_id: int):
    """Triggers Accounts Payable ledger entry upon successful invoice match."""
    expense = ExpenseRecord(
        category=ExpenseCategoryEnum.PHARMACY_STOCK,
        title=f"Vendor Invoice Payout: {invoice.invoice_number}",
        amount_paise=invoice.total_amount_paise,
        status=ExpenseStatusEnum.APPROVED,
        approved_by_id=current_user_id,
        approved_at=datetime.now(timezone.utc),
        due_date=invoice.due_date,
        vendor_name=invoice.vendor.name,
        created_by_id=current_user_id
    )
    db.add(expense)
    db.flush()
    invoice.linked_expense_id = expense.id

def issue_patient_indent(db: Session, indent: StoreIndent, issue_request: list, current_user_id: int):
    """Fulfills indent, decrements FEFO stock, and auto-bills the patient's active encounter."""
    invoice = None
    if indent.encounter_id:
        stmt_inv = select(Invoice).where(
            Invoice.encounter_id == indent.encounter_id,
            Invoice.status == InvoiceStatusEnum.DRAFT
        ).with_for_update()
        invoice = db.execute(stmt_inv).scalar_one_or_none()

        if not invoice:
            import secrets
            date_prefix = datetime.now(timezone.utc).strftime("%Y%m%d")
            invoice = Invoice(
                invoice_number=f"INV-{date_prefix}-{secrets.token_hex(6).upper()}",
                patient_id=indent.encounter.patient_id,
                encounter_id=indent.encounter_id,
                status=InvoiceStatusEnum.DRAFT,
                total_amount_paise=0,
                amount_paid_paise=0
            )
            db.add(invoice)
            db.flush()

    total_billed_paise = 0
    all_fulfilled = True

    for issue_item in issue_request:
        indent_item = next((i for i in indent.items if i.id == issue_item.indent_item_id), None)
        if not indent_item:
            raise HTTPException(status_code=404, detail=f"Indent item {issue_item.indent_item_id} not found.")

        if indent_item.issued_quantity + issue_item.issue_quantity > indent_item.requested_quantity:
            raise HTTPException(status_code=400, detail="Cannot issue more than requested quantity.")

        batch = db.execute(select(InventoryBatch).where(InventoryBatch.id == issue_item.batch_id).with_for_update()).scalar_one_or_none()
        if not batch or batch.medication_id != indent_item.medication_id:
            raise HTTPException(status_code=400, detail="Invalid batch for requested medication.")

        if batch.stock_quantity < issue_item.issue_quantity:
            raise HTTPException(status_code=409, detail=f"Insufficient stock in batch {batch.batch_number}.")

        # Decrement physical stock
        batch.stock_quantity -= issue_item.issue_quantity
        indent_item.issued_quantity += issue_item.issue_quantity

        if indent_item.issued_quantity < indent_item.requested_quantity:
            all_fulfilled = False

        # Auto-bill to patient invoice
        if invoice:
            med = db.get(MedicationMaster, indent_item.medication_id)
            line_cost = issue_item.issue_quantity * batch.unit_price_paise
            total_billed_paise += line_cost

            line_item = InvoiceLineItem(
                invoice_id=invoice.id,
                description=f"WARD ISSUE: {med.brand_name} - Batch: {batch.batch_number} [IndentItem:{indent_item.id}]",
                quantity=issue_item.issue_quantity,
                unit_price_paise=batch.unit_price_paise,
                total_price_paise=line_cost
            )
            db.add(line_item)

    if invoice:
        invoice.total_amount_paise += total_billed_paise

    indent.status = IndentStatusEnum.FULFILLED if all_fulfilled else IndentStatusEnum.PARTIALLY_FULFILLED
    indent.approved_by_id = current_user_id

def process_purchase_return(db: Session, vendor_id: int, grn_id: int, return_items: list, current_user_id: int):
    """Processes vendor debit notes, removes physical stock, and logs a negative Accounts Payable entry."""
    total_credit_paise = 0

    for item in return_items:
        grn_item = db.execute(select(GoodsReceiptItem).where(GoodsReceiptItem.id == item.grn_item_id).with_for_update()).scalar_one_or_none()
        if not grn_item or grn_item.grn.po.vendor_id != vendor_id:
            raise HTTPException(status_code=400, detail="Invalid GRN item for specified vendor.")

        if not grn_item.created_batch_id:
            raise HTTPException(status_code=400, detail="GRN item has no associated inventory batch.")

        batch = db.execute(select(InventoryBatch).where(InventoryBatch.id == grn_item.created_batch_id).with_for_update()).scalar_one_or_none()
        if not batch or batch.stock_quantity < item.return_quantity:
            raise HTTPException(status_code=409, detail="Insufficient current stock to process return to vendor.")

        batch.stock_quantity -= item.return_quantity
        total_credit_paise += (item.return_quantity * batch.unit_price_paise)

    # Generate Accounts Payable Debit Note (Negative Expense)
    debit_note = ExpenseRecord(
        category=ExpenseCategoryEnum.PHARMACY_STOCK,
        title=f"Purchase Return / Debit Note for GRN: {grn_id}",
        amount_paise=-total_credit_paise,
        status=ExpenseStatusEnum.APPROVED,
        approved_by_id=current_user_id,
        approved_at=datetime.now(timezone.utc),
        vendor_name=grn_item.grn.purchase_order.vendor.name,
        created_by_id=current_user_id
    )
    db.add(debit_note)

def return_patient_indent_stock(db: Session, indent: StoreIndent, return_request: list, current_user_id: int):
    """Processes ward returns, restores FEFO batch stock, and credits the patient's DRAFT invoice."""
    invoice = None
    if indent.encounter_id:
        stmt_inv = select(Invoice).where(
            Invoice.encounter_id == indent.encounter_id,
            Invoice.status == InvoiceStatusEnum.DRAFT
        ).with_for_update()
        invoice = db.execute(stmt_inv).scalar_one_or_none()

    total_credit_paise = 0

    for return_item in return_request:
        indent_item = next((i for i in indent.items if i.id == return_item.indent_item_id), None)
        if not indent_item:
            raise HTTPException(status_code=404, detail=f"Indent item {return_item.indent_item_id} not found.")

        if return_item.return_quantity > indent_item.issued_quantity:
            raise HTTPException(status_code=400, detail="Cannot return more stock than was issued.")

        batch = db.execute(select(InventoryBatch).where(InventoryBatch.id == return_item.batch_id).with_for_update()).scalar_one_or_none()
        if not batch or batch.medication_id != indent_item.medication_id:
            raise HTTPException(status_code=400, detail="Invalid batch for the returned medication.")

        # 1. Restore physical stock and decrement issued tracking
        batch.stock_quantity += return_item.return_quantity
        indent_item.issued_quantity -= return_item.return_quantity

        # 2. Credit the patient's DRAFT invoice by reversing the specific line item
        if invoice:
            exact_tag = f"[IndentItem:{indent_item.id}]"
            matching_line = next((li for li in invoice.line_items if exact_tag in li.description), None)

            if matching_line:
                credit_value = return_item.return_quantity * matching_line.unit_price_paise
                total_credit_paise += credit_value

                if matching_line.quantity <= return_item.return_quantity:
                    if matching_line in invoice.line_items:
                        invoice.line_items.remove(matching_line)
                    db.delete(matching_line)
                else:
                    matching_line.quantity -= return_item.return_quantity
                    matching_line.total_price_paise -= credit_value
                    if matching_line.gst_rate_percent and matching_line.gst_rate_percent > 0:
                        matching_line.tax_amount_paise = round(
                            (matching_line.total_price_paise * matching_line.gst_rate_percent) / 100
                        )

    if invoice:
        db.flush()
        recalculate_invoice_totals(invoice)
        if invoice.discount_amount_paise and invoice.discount_amount_paise > invoice.total_amount_paise:
            invoice.discount_amount_paise = invoice.total_amount_paise

    # Re-evaluate Indent Status
    if all(i.issued_quantity == 0 for i in indent.items):
        indent.status = IndentStatusEnum.PENDING
    else:
        indent.status = IndentStatusEnum.PARTIALLY_FULFILLED