import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select

from database import get_db
from dependencies import RequirePermission, get_current_user
from models import User, Invoice, InvoiceLineItem, PaymentReceipt, InvoiceStatusEnum
from schemas.v1.schemas import (
    PaginatedResponse, InvoiceCreate, InvoiceResponse, 
    InvoiceLineItemCreate, PaymentReceiptCreate, PaymentReceiptResponse
)

router = APIRouter(prefix="/api/billing", tags=["Billing & Financial Ledger"])

@router.post("/invoices", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
def create_invoice(payload: InvoiceCreate, db: Session = Depends(get_db), _: User = Depends(RequirePermission("process_billing"))):
    """Generates a new draft invoice ledger for a patient."""
    inv_number = f"INV-{uuid.uuid4().hex[:8].upper()}"
    invoice = Invoice(
        patient_id=payload.patient_id,
        encounter_id=payload.encounter_id,
        invoice_number=inv_number,
        notes=payload.notes,
        status=InvoiceStatusEnum.DRAFT
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return invoice

@router.post("/invoices/{invoice_id}/items", status_code=status.HTTP_201_CREATED)
def add_line_item(invoice_id: int, payload: InvoiceLineItemCreate, db: Session = Depends(get_db), _: User = Depends(RequirePermission("process_billing"))):
    """Adds a billable charge to an invoice and recalculates the total."""
    invoice = db.query(Invoice).options(joinedload(Invoice.line_items)).filter(Invoice.id == invoice_id).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status != InvoiceStatusEnum.DRAFT:
        raise HTTPException(status_code=400, detail="Cannot add items to non-draft invoices.")

    total_price = payload.quantity * payload.unit_price_paise
    item = InvoiceLineItem(
        invoice_id=invoice.id,
        service_id=payload.service_id,
        description=payload.description,
        quantity=payload.quantity,
        unit_price_paise=payload.unit_price_paise,
        total_price_paise=total_price
    )
    db.add(item)
    
    # Recalculate parent invoice total
    invoice.total_amount_paise += total_price
    db.commit()
    return {"status": "success", "message": "Line item appended to invoice."}

@router.post("/invoices/{invoice_id}/finalize", status_code=200)
def finalize_invoice(invoice_id: int, db: Session = Depends(get_db), _: User = Depends(RequirePermission("process_billing"))):
    """Locks the invoice from further line-item edits and prepares it for payment."""
    invoice = db.get(Invoice, invoice_id)
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status != InvoiceStatusEnum.DRAFT:
        raise HTTPException(status_code=400, detail="Invoice is already finalized.")

    invoice.status = InvoiceStatusEnum.UNPAID
    db.commit()
    return {"status": "success", "message": f"Invoice {invoice.invoice_number} finalized."}

@router.post("/invoices/{invoice_id}/payments", response_model=PaymentReceiptResponse, status_code=status.HTTP_201_CREATED)
def record_payment(invoice_id: int, payload: PaymentReceiptCreate, db: Session = Depends(get_db), _: User = Depends(RequirePermission("process_billing"))):
    """Records a payment against an invoice and updates the ledger state."""
    invoice = db.get(Invoice, invoice_id)
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status in [InvoiceStatusEnum.DRAFT, InvoiceStatusEnum.VOID]:
        raise HTTPException(status_code=400, detail="Cannot process payments on DRAFT or VOID invoices.")

    receipt_number = f"REC-{uuid.uuid4().hex[:8].upper()}"
    receipt = PaymentReceipt(
        invoice_id=invoice.id,
        receipt_number=receipt_number,
        amount_paid_paise=payload.amount_paid_paise,
        payment_method=payload.payment_method,
        transaction_reference=payload.transaction_reference
    )
    db.add(receipt)

    invoice.amount_paid_paise += payload.amount_paid_paise
    if invoice.amount_paid_paise >= invoice.total_amount_paise:
        invoice.status = InvoiceStatusEnum.PAID
    else:
        invoice.status = InvoiceStatusEnum.PARTIAL

    db.commit()
    db.refresh(receipt)
    return receipt
from schemas.v1.schemas import InvoiceUpdate

@router.get("/invoices", response_model=PaginatedResponse[InvoiceResponse])
def list_invoices(patient_id: Optional[int] = None, status: Optional[InvoiceStatusEnum] = None, page: int = 1, page_size: int = 50, db: Session = Depends(get_db), _: User = Depends(RequirePermission("process_billing"))):
    """Retrieves all hospital invoices, filterable by patient or payment status."""
    query = db.query(Invoice).options(joinedload(Invoice.line_items), joinedload(Invoice.payments))
    
    if patient_id:
        query = query.filter(Invoice.patient_id == patient_id)
    if status:
        query = query.filter(Invoice.status == status)
        
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@router.get("/invoices/{invoice_id}", response_model=InvoiceResponse)
def get_invoice(invoice_id: int, db: Session = Depends(get_db), _: User = Depends(RequirePermission("process_billing"))):
    """Fetches a complete master ledger including all line items and payment receipts."""
    invoice = db.query(Invoice).options(joinedload(Invoice.line_items), joinedload(Invoice.payments)).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    return invoice

@router.put("/invoices/{invoice_id}", response_model=InvoiceResponse)
def update_invoice(invoice_id: int, payload: InvoiceUpdate, db: Session = Depends(get_db), _: User = Depends(RequirePermission("process_billing"))):
    """Updates administrative notes on an invoice."""
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
        
    if payload.notes is not None:
        invoice.notes = payload.notes
        
    db.commit()
    db.refresh(invoice)
    return invoice

@router.delete("/invoices/{invoice_id}", status_code=200)
def void_invoice(invoice_id: int, db: Session = Depends(get_db), _: User = Depends(RequirePermission("process_billing"))):
    """Voids an invoice, preventing any future payments or modifications."""
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
        
    if invoice.status in [InvoiceStatusEnum.PAID, InvoiceStatusEnum.PARTIAL]:
        raise HTTPException(status_code=400, detail="Cannot void an invoice that has active payments. Refund payments first.")
        
    invoice.status = InvoiceStatusEnum.VOID
    db.commit()
    return {"status": "success", "message": f"Invoice {invoice.invoice_number} voided successfully."}

@router.delete("/invoices/{invoice_id}/items/{item_id}", status_code=200)
def remove_line_item(invoice_id: int, item_id: int, db: Session = Depends(get_db), _: User = Depends(RequirePermission("process_billing"))):
    """Removes an erroneous charge from a draft invoice and recalculates the ledger total."""
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status != InvoiceStatusEnum.DRAFT:
        raise HTTPException(status_code=400, detail="Cannot modify line items on a finalized or paid invoice.")
        
    item = db.query(InvoiceLineItem).filter(InvoiceLineItem.id == item_id, InvoiceLineItem.invoice_id == invoice_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Line item not found on this invoice.")
        
    # Reverse the financial total
    invoice.total_amount_paise -= item.total_price_paise
    
    db.delete(item)
    db.commit()
    return {"status": "success", "message": "Line item removed and invoice total recalculated."}

@router.delete("/invoices/{invoice_id}/payments/{receipt_id}", status_code=200)
def reverse_payment(invoice_id: int, receipt_id: int, db: Session = Depends(get_db), _: User = Depends(RequirePermission("process_billing"))):
    """Reverses a bounced or erroneous payment and downgrades the invoice status."""
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
        
    receipt = db.query(PaymentReceipt).filter(PaymentReceipt.id == receipt_id, PaymentReceipt.invoice_id == invoice_id).first()
    if not receipt:
        raise HTTPException(status_code=404, detail="Payment receipt not found on this invoice.")
        
    # Reverse the paid amount
    invoice.amount_paid_paise -= receipt.amount_paid_paise
    
    # Downgrade the invoice status based on the new balance
    if invoice.amount_paid_paise <= 0:
        invoice.amount_paid_paise = 0
        invoice.status = InvoiceStatusEnum.UNPAID
    elif invoice.amount_paid_paise < invoice.total_amount_paise:
        invoice.status = InvoiceStatusEnum.PARTIAL
        
    db.delete(receipt)
    db.commit()
    return {"status": "success", "message": "Payment reversed and invoice status downgraded accordingly."}