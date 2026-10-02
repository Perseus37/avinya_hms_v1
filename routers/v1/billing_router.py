from datetime import datetime, timezone
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import RequirePermission, validate_pagination
from models import (
    Invoice,
    InvoiceLineItem,
    InvoiceStatusEnum,
    Patient,
    PatientEncounter,
    PaymentReceipt,
    ServiceCatalog,
    User,
)
from schemas.v1.schemas import (
    InvoiceCreate,
    InvoiceLineItemCreate,
    InvoiceResponse,
    PaginatedResponse,
    PaymentReceiptCreate,
    PaymentReceiptResponse,
)

router = APIRouter(prefix="/api/billing", tags=["Enterprise Billing & Ledger"])


def _generate_doc_number(prefix: str) -> str:
    date_prefix = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{prefix}-{date_prefix}-{uuid.uuid4().hex[:12].upper()}"


# -----------------------------------------------------------------------------
# Invoice Lifecycle Management
# -----------------------------------------------------------------------------
@router.post("/invoices", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
def create_invoice(
    payload: InvoiceCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:write")),
):
    patient = db.get(Patient, payload.patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=404, detail="Active patient not found.")

    if payload.encounter_id is not None:
        encounter = db.get(PatientEncounter, payload.encounter_id)
        if not encounter or encounter.patient_id != payload.patient_id:
            raise HTTPException(status_code=400, detail="Encounter does not exist or does not belong to this patient.")

    invoice = Invoice(
        invoice_number=_generate_doc_number("INV"),
        patient_id=payload.patient_id,
        encounter_id=payload.encounter_id,
        status=InvoiceStatusEnum.DRAFT,
        total_amount_paise=0,
        amount_paid_paise=0,
        notes=payload.notes,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return invoice


@router.get("/invoices", response_model=PaginatedResponse[InvoiceResponse])
def list_invoices(
    patient_id: Optional[int] = None,
    invoice_status: Optional[InvoiceStatusEnum] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(Invoice).options(joinedload(Invoice.line_items), joinedload(Invoice.payments))
    if patient_id:
        query = query.filter(Invoice.patient_id == patient_id)
    if invoice_status:
        query = query.filter(Invoice.status == invoice_status)
    query = query.order_by(Invoice.id.desc())

    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/invoices/{invoice_id}", response_model=InvoiceResponse)
def get_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:read")),
):
    invoice = (
        db.query(Invoice)
        .options(joinedload(Invoice.line_items), joinedload(Invoice.payments))
        .filter(Invoice.id == invoice_id)
        .first()
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    return invoice


@router.post("/invoices/{invoice_id}/items", status_code=status.HTTP_201_CREATED)
def add_line_item(
    invoice_id: int,
    payload: InvoiceLineItemCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:write")),
):
    if payload.quantity <= 0:
        raise HTTPException(status_code=400, detail="Line item quantity must be strictly positive.")
    if payload.service_id is None:
        raise HTTPException(status_code=400, detail="A valid service_id from the Service Catalog is required.")

    stmt = select(Invoice).where(Invoice.id == invoice_id).with_for_update()
    invoice = db.execute(stmt).scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status != InvoiceStatusEnum.DRAFT:
        raise HTTPException(status_code=400, detail=f"Cannot add items to an invoice in {invoice.status.value} state.")

    service = db.get(ServiceCatalog, payload.service_id)
    if not service or not service.is_active:
        raise HTTPException(status_code=404, detail="Active service catalog item not found.")

    authoritative_price = service.base_price_paise
    total_price = payload.quantity * authoritative_price

    item = InvoiceLineItem(
        invoice_id=invoice.id,
        service_id=service.id,
        description=payload.description or service.name,
        quantity=payload.quantity,
        unit_price_paise=authoritative_price,
        total_price_paise=total_price,
    )
    db.add(item)
    invoice.total_amount_paise += total_price
    db.commit()
    return {"status": "success", "message": "Line item appended to invoice."}


@router.delete("/invoices/{invoice_id}/items/{item_id}", status_code=200)
def remove_line_item(
    invoice_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:reverse")),
):
    stmt = select(Invoice).where(Invoice.id == invoice_id).with_for_update()
    invoice = db.execute(stmt).scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status != InvoiceStatusEnum.DRAFT:
        raise HTTPException(status_code=400, detail="Cannot remove items once an invoice has been finalized.")

    item = (
        db.query(InvoiceLineItem)
        .filter(InvoiceLineItem.id == item_id, InvoiceLineItem.invoice_id == invoice_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Line item not found on this invoice.")

    if item.service_id is None and item.description.startswith("PHARMACY:"):
        raise HTTPException(
            status_code=403,
            detail="Cannot delete auto-billed pharmacy dispensing charges. Process a stock return instead.",
        )

    invoice.total_amount_paise = max(0, invoice.total_amount_paise - item.total_price_paise)
    db.delete(item)
    db.commit()
    return {"status": "success", "message": "Line item removed and invoice total updated."}


@router.patch("/invoices/{invoice_id}/finalize", response_model=InvoiceResponse)
def finalize_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:write")),
):
    stmt = (
        select(Invoice)
        .options(joinedload(Invoice.line_items))
        .where(Invoice.id == invoice_id)
        .with_for_update()
    )
    invoice = db.execute(stmt).unique().scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status != InvoiceStatusEnum.DRAFT:
        raise HTTPException(status_code=400, detail="Only DRAFT invoices can be finalized.")
    if not invoice.line_items or invoice.total_amount_paise <= 0:
        raise HTTPException(status_code=400, detail="Cannot finalize an invoice with zero line items or zero total.")

    invoice.status = InvoiceStatusEnum.UNPAID
    db.commit()
    db.refresh(invoice)
    return invoice


@router.delete("/invoices/{invoice_id}/void", status_code=200)
def void_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:reverse")),
):
    stmt = select(Invoice).where(Invoice.id == invoice_id).with_for_update()
    invoice = db.execute(stmt).scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.amount_paid_paise > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot void an invoice that has recorded payments. Reverse all payments first.",
        )

    invoice.status = InvoiceStatusEnum.VOID
    db.commit()
    return {"status": "success", "message": f"Invoice {invoice.invoice_number} has been voided."}


# -----------------------------------------------------------------------------
# Payment Processing & Reversals
# -----------------------------------------------------------------------------
@router.post("/invoices/{invoice_id}/payments", response_model=PaymentReceiptResponse, status_code=status.HTTP_201_CREATED)
def record_payment(
    invoice_id: int,
    payload: PaymentReceiptCreate,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:write")),
):
    if payload.amount_paid_paise <= 0:
        raise HTTPException(status_code=400, detail="Payment amount must be strictly positive.")

    stmt = select(Invoice).where(Invoice.id == invoice_id).with_for_update()
    invoice = db.execute(stmt).scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status in [InvoiceStatusEnum.DRAFT, InvoiceStatusEnum.VOID, InvoiceStatusEnum.PAID]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot record payment on an invoice in {invoice.status.value} status.",
        )

    effective_ref = payload.transaction_reference or idempotency_key
    if effective_ref:
        duplicate = (
            db.query(PaymentReceipt)
            .filter(
                PaymentReceipt.invoice_id == invoice_id,
                PaymentReceipt.transaction_reference == effective_ref,
            )
            .first()
        )
        if duplicate:
            return duplicate

    if invoice.amount_paid_paise + payload.amount_paid_paise > invoice.total_amount_paise:
        raise HTTPException(status_code=400, detail="Payment amount exceeds the remaining invoice balance.")

    receipt = PaymentReceipt(
        invoice_id=invoice.id,
        receipt_number=_generate_doc_number("REC"),
        amount_paid_paise=payload.amount_paid_paise,
        payment_method=payload.payment_method,
        transaction_reference=effective_ref,
    )
    db.add(receipt)

    invoice.amount_paid_paise += payload.amount_paid_paise
    if invoice.amount_paid_paise == invoice.total_amount_paise:
        invoice.status = InvoiceStatusEnum.PAID
    else:
        invoice.status = InvoiceStatusEnum.PARTIAL

    db.commit()
    db.refresh(receipt)
    return receipt


@router.get("/invoices/{invoice_id}/payments", response_model=list[PaymentReceiptResponse])
def list_invoice_payments(
    invoice_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:read")),
):
    return (
        db.query(PaymentReceipt)
        .filter(PaymentReceipt.invoice_id == invoice_id)
        .order_by(PaymentReceipt.id.asc())
        .all()
    )


@router.delete("/invoices/{invoice_id}/payments/{receipt_id}", status_code=200)
def reverse_payment(
    invoice_id: int,
    receipt_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:reverse")),
):
    stmt = select(Invoice).where(Invoice.id == invoice_id).with_for_update()
    invoice = db.execute(stmt).scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    receipt = (
        db.query(PaymentReceipt)
        .filter(PaymentReceipt.id == receipt_id, PaymentReceipt.invoice_id == invoice_id)
        .first()
    )
    if not receipt:
        raise HTTPException(status_code=404, detail="Payment receipt not found on this invoice.")

    invoice.amount_paid_paise = max(0, invoice.amount_paid_paise - receipt.amount_paid_paise)
    if invoice.amount_paid_paise == 0:
        invoice.status = InvoiceStatusEnum.UNPAID
    elif invoice.amount_paid_paise < invoice.total_amount_paise:
        invoice.status = InvoiceStatusEnum.PARTIAL
    else:
        invoice.status = InvoiceStatusEnum.PAID

    db.delete(receipt)
    db.commit()
    return {
        "status": "success",
        "message": f"Payment receipt {receipt.receipt_number} reversed.",
        "new_invoice_status": invoice.status.value,
    }