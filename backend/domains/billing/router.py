from datetime import date, datetime, timezone
from typing import Optional
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.core.database import get_db
from backend.core.dependencies import RequirePermission, validate_pagination
from backend.core.date_ranges import resolve_record_date_window
from backend.domains.billing.models import Invoice, InvoiceLineItem, InvoiceStatusEnum, PaymentReceipt, DepositStatusEnum, PatientDeposit, PaymentMethodEnum, RefundVoucher
from backend.domains.diagnostics.models import ServiceCatalog
from backend.domains.encounters.models import PatientEncounter
from backend.domains.identity.models import User
from backend.domains.patients.models import Patient
from backend.domains.billing.schemas import InvoiceCreate, InvoiceLineItemCreate, InvoiceResponse, PaymentReceiptCreate, PaymentReceiptResponse, InvoiceDiscountRequest, ApplyDepositToInvoiceRequest, PatientDepositCreate, DepositRefundRequest, PatientDepositResponse, RefundVoucherResponse
from backend.domains.billing.rcm_schemas import (
    ChargeCaptureEventResponse,
    ClinicalChargePostRequest,
    ClinicalChargePostResponse,
    MidnightCensusRunRequest,
    MidnightCensusRunResponse,
    SurgeryChargePostRequest,
    SurgeryChargePostResponse,
    TariffResolutionRequest,
    TariffResolutionResponse,
)
from backend.schemas.common import PaginatedResponse
from backend.domains.billing.services import resolve_patient_service_price_paise
from backend.domains.privacy.runtime import evaluate_tenant_patient_data_uses

router = APIRouter(prefix="/api/billing", tags=["Enterprise Billing & Ledger"])


def _generate_doc_number(prefix: str) -> str:
    date_prefix = datetime.now(timezone.utc).strftime("%Y%m%d")
    # CRYP-002: Use cryptographically secure hex generation instead of truncated UUIDs
    return f"{prefix}-{date_prefix}-{secrets.token_hex(6).upper()}"


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
        is_interstate=payload.is_interstate,
        total_amount_paise=0,
        amount_paid_paise=0,
        notes=payload.notes,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return invoice


@router.get(
    "/invoices",
    response_model=PaginatedResponse[InvoiceResponse],
    openapi_extra={"x-privacy-data-use-point": "billing.invoices.search"},
)
def list_invoices(
    patient_id: Optional[int] = None,
    invoice_status: Optional[InvoiceStatusEnum] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("billing:read")),
):
    page, page_size = validate_pagination(page, page_size)
    start_at, end_at = resolve_record_date_window(start_date, end_date)
    query = db.query(Invoice).options(joinedload(Invoice.line_items), joinedload(Invoice.payments))
    if patient_id:
        query = query.filter(Invoice.patient_id == patient_id)
    if invoice_status:
        query = query.filter(Invoice.status == invoice_status)
    if start_at is not None and end_at is not None:
        query = query.filter(Invoice.created_at >= start_at, Invoice.created_at < end_at)
    query = query.order_by(Invoice.id.desc())

    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="billing.invoices.search",
        patient_ids=[item.patient_id for item in items],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit one or more patient disclosures.")
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get(
    "/invoices/{invoice_id}",
    response_model=InvoiceResponse,
    openapi_extra={"x-privacy-data-use-point": "billing.invoices.read"},
)
def get_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("billing:read")),
):
    invoice = (
        db.query(Invoice)
        .options(joinedload(Invoice.line_items), joinedload(Invoice.payments))
        .filter(Invoice.id == invoice_id)
        .first()
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="billing.invoices.read",
        patient_ids=[invoice.patient_id],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit this patient disclosure.")
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

    authoritative_price = resolve_patient_service_price_paise(db, invoice.patient_id, service)
    total_price = payload.quantity * authoritative_price
    tax_paise = round((total_price * payload.gst_rate_percent) / 100) if payload.gst_rate_percent > 0 else 0

    item = InvoiceLineItem(
        invoice_id=invoice.id,
        service_id=service.id,
        description=payload.description or service.name,
        quantity=payload.quantity,
        unit_price_paise=authoritative_price,
        total_price_paise=total_price,
        gst_rate_percent=payload.gst_rate_percent,
        tax_amount_paise=tax_paise,
        hsn_sac_code=payload.hsn_sac_code or "9993",
    )
    db.add(item)
    invoice.total_amount_paise += total_price
    invoice.tax_amount_paise += tax_paise
    if getattr(invoice, "is_interstate", False):
        invoice.igst_amount_paise = invoice.tax_amount_paise
        invoice.cgst_amount_paise = 0
        invoice.sgst_amount_paise = 0
    else:
        invoice.cgst_amount_paise = invoice.tax_amount_paise // 2
        invoice.sgst_amount_paise = invoice.tax_amount_paise - invoice.cgst_amount_paise
        invoice.igst_amount_paise = 0
    invoice.is_tax_exempt = (invoice.tax_amount_paise == 0)
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

    if item.description.startswith(
        (
            "PHARMACY:",
            "OPD CONSULTATION:",
            "IPD BED CHARGE:",
            "DIAGNOSTICS [",
            "SURGERY [",
            "SURGICAL IMPLANT [",
            "BLOOD BANK [",
        )
    ):
        raise HTTPException(
            status_code=403,
            detail="Cannot delete system auto-billed clinical, surgical, blood bank, diagnostic, bed, or pharmacy charges.",
        )

    invoice.total_amount_paise = max(0, invoice.total_amount_paise - item.total_price_paise)
    invoice.tax_amount_paise = max(0, invoice.tax_amount_paise - (item.tax_amount_paise or 0))
    if getattr(invoice, "is_interstate", False):
        invoice.igst_amount_paise = invoice.tax_amount_paise
        invoice.cgst_amount_paise = 0
        invoice.sgst_amount_paise = 0
    else:
        invoice.cgst_amount_paise = invoice.tax_amount_paise // 2
        invoice.sgst_amount_paise = invoice.tax_amount_paise - invoice.cgst_amount_paise
        invoice.igst_amount_paise = 0
    invoice.is_tax_exempt = (invoice.tax_amount_paise == 0)
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

@router.patch("/invoices/{invoice_id}/discount", response_model=InvoiceResponse)
def apply_invoice_discount(
    invoice_id: int,
    payload: InvoiceDiscountRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("billing:reverse", "billing:void")),
):
    """Applies an authorized concession/discount to a DRAFT, UNPAID, or PARTIAL invoice."""
    stmt = (
        select(Invoice)
        .options(joinedload(Invoice.line_items), joinedload(Invoice.payments))
        .where(Invoice.id == invoice_id)
        .with_for_update()
    )
    invoice = db.execute(stmt).unique().scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status in [InvoiceStatusEnum.VOID, InvoiceStatusEnum.PAID]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot modify discount on an invoice in {invoice.status.value} state.",
        )
    if payload.discount_amount_paise > invoice.total_amount_paise:
        raise HTTPException(
            status_code=400,
            detail="Discount amount cannot exceed invoice gross total amount.",
        )

    new_net = max(0, (invoice.total_amount_paise + (invoice.tax_amount_paise or 0)) - payload.discount_amount_paise)
    if invoice.amount_paid_paise > new_net:
        raise HTTPException(
            status_code=400,
            detail="Discount would reduce net payable below the amount already paid.",
        )

    invoice.discount_amount_paise = payload.discount_amount_paise
    invoice.discount_reason = payload.discount_reason
    invoice.discount_approved_by_id = current_user.id

    if invoice.status != InvoiceStatusEnum.DRAFT:
        if invoice.amount_paid_paise == new_net:
            invoice.status = InvoiceStatusEnum.PAID
        elif invoice.amount_paid_paise > 0:
            invoice.status = InvoiceStatusEnum.PARTIAL
        else:
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

    if invoice.amount_paid_paise + payload.amount_paid_paise > invoice.net_amount_paise:
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
    if invoice.amount_paid_paise == invoice.net_amount_paise:
        invoice.status = InvoiceStatusEnum.PAID
    else:
        invoice.status = InvoiceStatusEnum.PARTIAL

    db.commit()
    db.refresh(receipt)
    return receipt


@router.get(
    "/invoices/{invoice_id}/payments",
    response_model=list[PaymentReceiptResponse],
    openapi_extra={"x-privacy-data-use-point": "billing.payments.read"},
)
def list_invoice_payments(
    invoice_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("billing:read")),
):
    invoice = db.get(Invoice, invoice_id)
    if invoice is not None:
        decisions = evaluate_tenant_patient_data_uses(
            db,
            data_use_point="billing.payments.read",
            patient_ids=[invoice.patient_id],
            actor_user_id=actor.id,
        )
        if any(not decision.allowed for decision in decisions):
            raise HTTPException(status_code=403, detail="Privacy policy does not permit this patient disclosure.")
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

    if receipt.payment_method == PaymentMethodEnum.INSURANCE:
        raise HTTPException(
            status_code=400,
            detail="Insurance settlement receipts cannot be reversed directly; dispute or reverse via Insurance Claims workflow.",
        )

    if receipt.payment_method == PaymentMethodEnum.ADVANCE_ADJUSTMENT and receipt.transaction_reference:
        dep = db.execute(
            select(PatientDeposit)
            .where(PatientDeposit.deposit_number == receipt.transaction_reference)
            .with_for_update()
        ).scalar_one_or_none()
        if dep:
            dep.remaining_balance_paise += receipt.amount_paid_paise
            if dep.status == DepositStatusEnum.EXHAUSTED:
                dep.status = DepositStatusEnum.ACTIVE

    invoice.amount_paid_paise = max(0, invoice.amount_paid_paise - receipt.amount_paid_paise)
    if invoice.amount_paid_paise == 0:
        invoice.status = InvoiceStatusEnum.UNPAID
    elif invoice.amount_paid_paise < invoice.net_amount_paise:
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

# -----------------------------------------------------------------------------
# Phase 9D: Patient Advance Deposits, Invoice Adjustment & Refund Vouchers
# -----------------------------------------------------------------------------
@router.post("/deposits", response_model=PatientDepositResponse, status_code=status.HTTP_201_CREATED)
def collect_patient_advance_deposit(
    payload: PatientDepositCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:write", "billing:pay")),
):
    """Collects an upfront advance deposit into the patient's hospital wallet."""
    patient = db.get(Patient, payload.patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=404, detail="Active patient not found.")

    if payload.payment_method in [PaymentMethodEnum.INSURANCE, PaymentMethodEnum.ADVANCE_ADJUSTMENT]:
        raise HTTPException(status_code=400, detail=f"Cannot collect a direct advance deposit via {payload.payment_method.value}.")

    if payload.encounter_id is not None:
        enc = db.get(PatientEncounter, payload.encounter_id)
        if not enc or enc.patient_id != payload.patient_id:
            raise HTTPException(status_code=400, detail="Encounter does not belong to this patient.")

    deposit = PatientDeposit(
        patient_id=payload.patient_id,
        encounter_id=payload.encounter_id,
        deposit_number=_generate_doc_number("DEP"),
        amount_paise=payload.amount_paise,
        remaining_balance_paise=payload.amount_paise,
        payment_method=payload.payment_method,
        transaction_reference=payload.transaction_reference,
        status=DepositStatusEnum.ACTIVE,
        notes=payload.notes,
    )
    db.add(deposit)
    db.commit()
    db.refresh(deposit)
    return deposit


@router.get(
    "/patients/{patient_id}/deposits",
    response_model=list[PatientDepositResponse],
    openapi_extra={"x-privacy-data-use-point": "billing.deposits.read"},
)
def list_patient_deposits(
    patient_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("billing:read")),
):
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="billing.deposits.read",
        patient_ids=[patient_id],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit this patient disclosure.")
    stmt = (
        select(PatientDeposit)
        .where(PatientDeposit.patient_id == patient_id)
        .order_by(PatientDeposit.id.desc())
    )
    return list(db.execute(stmt).scalars().all())


@router.post(
    "/invoices/{invoice_id}/apply-deposit",
    response_model=PaymentReceiptResponse,
    status_code=status.HTTP_201_CREATED,
)
def apply_advance_deposit_to_invoice(
    invoice_id: int,
    payload: ApplyDepositToInvoiceRequest,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:write", "billing:pay")),
):
    """
    Draws down a patient's ACTIVE advance deposit against an UNPAID/PARTIAL invoice,
    automatically generating an ADVANCE_ADJUSTMENT PaymentReceipt.
    """
    inv_stmt = select(Invoice).where(Invoice.id == invoice_id).with_for_update()
    invoice = db.execute(inv_stmt).scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status not in [InvoiceStatusEnum.UNPAID, InvoiceStatusEnum.PARTIAL]:
        raise HTTPException(
            status_code=400,
            detail=f"Can only adjust advance deposits against UNPAID or PARTIAL invoices (current: {invoice.status.value}).",
        )

    dep_stmt = select(PatientDeposit).where(PatientDeposit.id == payload.deposit_id).with_for_update()
    deposit = db.execute(dep_stmt).scalar_one_or_none()
    if not deposit:
        raise HTTPException(status_code=404, detail="Advance deposit record not found.")
    if deposit.patient_id != invoice.patient_id:
        raise HTTPException(status_code=400, detail="Advance deposit belongs to a different patient.")
    if deposit.status != DepositStatusEnum.ACTIVE or deposit.remaining_balance_paise <= 0:
        raise HTTPException(status_code=400, detail="Advance deposit has no remaining available balance.")

    invoice_due_paise = invoice.net_amount_paise - invoice.amount_paid_paise
    if payload.amount_to_apply_paise is not None:
        if payload.amount_to_apply_paise > deposit.remaining_balance_paise:
            raise HTTPException(status_code=400, detail="Requested adjustment exceeds available deposit balance.")
        if payload.amount_to_apply_paise > invoice_due_paise:
            raise HTTPException(status_code=400, detail="Requested adjustment exceeds remaining invoice balance due.")
        apply_paise = payload.amount_to_apply_paise
    else:
        apply_paise = min(deposit.remaining_balance_paise, invoice_due_paise)

    deposit.remaining_balance_paise -= apply_paise
    if deposit.remaining_balance_paise == 0:
        deposit.status = DepositStatusEnum.EXHAUSTED

    receipt = PaymentReceipt(
        invoice_id=invoice.id,
        receipt_number=_generate_doc_number("REC-ADJ"),
        amount_paid_paise=apply_paise,
        payment_method=PaymentMethodEnum.ADVANCE_ADJUSTMENT,
        transaction_reference=deposit.deposit_number,
    )
    db.add(receipt)

    invoice.amount_paid_paise += apply_paise
    if invoice.amount_paid_paise == invoice.net_amount_paise:
        invoice.status = InvoiceStatusEnum.PAID
    else:
        invoice.status = InvoiceStatusEnum.PARTIAL

    db.commit()
    db.refresh(receipt)
    return receipt


@router.post(
    "/deposits/{deposit_id}/refund",
    response_model=RefundVoucherResponse,
    status_code=status.HTTP_201_CREATED,
)
def refund_unused_patient_deposit(
    deposit_id: int,
    payload: DepositRefundRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("billing:reverse", "billing:void")),
):
    """Issues an immutable RefundVoucher for the unused balance of a patient's advance deposit."""
    dep_stmt = select(PatientDeposit).where(PatientDeposit.id == deposit_id).with_for_update()
    deposit = db.execute(dep_stmt).scalar_one_or_none()
    if not deposit:
        raise HTTPException(status_code=404, detail="Advance deposit not found.")
    if deposit.status != DepositStatusEnum.ACTIVE or deposit.remaining_balance_paise <= 0:
        raise HTTPException(status_code=400, detail="Deposit has no remaining refundable balance.")

    refund_paise = payload.refund_amount_paise or deposit.remaining_balance_paise
    if refund_paise > deposit.remaining_balance_paise:
        raise HTTPException(status_code=400, detail="Refund amount cannot exceed remaining deposit balance.")

    deposit.remaining_balance_paise -= refund_paise
    if deposit.remaining_balance_paise == 0:
        deposit.status = DepositStatusEnum.REFUNDED

    voucher = RefundVoucher(
        voucher_number=_generate_doc_number("RFND"),
        patient_id=deposit.patient_id,
        deposit_id=deposit.id,
        invoice_id=None,
        refund_amount_paise=refund_paise,
        refund_method=payload.refund_method,
        transaction_reference=payload.transaction_reference,
        reason=payload.reason,
        approved_by_id=current_user.id,
    )
    db.add(voucher)
    db.commit()
    db.refresh(voucher)
    return voucher


@router.get(
    "/patients/{patient_id}/refunds",
    response_model=list[RefundVoucherResponse],
    openapi_extra={"x-privacy-data-use-point": "billing.refunds.read"},
)
def list_patient_refund_vouchers(
    patient_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("billing:read")),
):
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="billing.refunds.read",
        patient_ids=[patient_id],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit this patient disclosure.")
    stmt = (
        select(RefundVoucher)
        .where(RefundVoucher.patient_id == patient_id)
        .order_by(RefundVoucher.id.desc())
    )
    return list(db.execute(stmt).scalars().all())


# =============================================================================
# Revenue Cycle Management (RCM) & Charge Master (CDM) Automation Endpoints
# =============================================================================

@router.post(
    "/rcm/post-clinical-charge",
    response_model=ClinicalChargePostResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_automated_clinical_charge(
    payload: ClinicalChargePostRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("billing:write", "clinical:write", "nursing:write", "surgery:write")),
):
    """
    Event-driven charge capture endpoint:
    Posts charges to patient encounter draft invoice upon clinical actions (surgery, lab verification, etc.).
    Guarantees idempotency via source_reference to prevent double billing.
    """
    from backend.domains.billing.rcm_services import post_clinical_charge
    return post_clinical_charge(
        session=db,
        encounter_id=payload.encounter_id,
        event_type=payload.event_type,
        source_reference=payload.source_reference,
        description=payload.description,
        unit_price_paise=payload.unit_price_paise,
        quantity=payload.quantity,
        service_id=payload.service_id,
        gst_rate_percent=payload.gst_rate_percent or 0,
        hsn_sac_code=payload.hsn_sac_code,
        user_id=current_user.id,
    )


@router.post(
    "/rcm/midnight-census-charges",
    response_model=MidnightCensusRunResponse,
    status_code=status.HTTP_200_OK,
)
def run_inpatient_midnight_census_charges(
    payload: MidnightCensusRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("billing:write", "admin:write")),
):
    """
    Executes automated daily midnight census room charge posting across all active IPD admissions.
    Honors insurance plan room-rent caps if the patient is under an active insurance policy.
    """
    from backend.domains.billing.rcm_services import execute_midnight_census_charge_run
    return execute_midnight_census_charge_run(
        session=db,
        census_date=payload.census_date,
        user_id=current_user.id,
    )


@router.post(
    "/rcm/resolve-tariff",
    response_model=TariffResolutionResponse,
    status_code=status.HTTP_200_OK,
)
def resolve_service_charge_tariff(
    payload: TariffResolutionRequest,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:read", "clinical:read", "insurance:read")),
):
    """
    Pre-bill multi-tariff resolution engine:
    Evaluates patient coverage and applies negotiated plan rate cards, room-rent caps, or standard base pricing.
    """
    from backend.domains.billing.rcm_services import resolve_service_tariff
    return resolve_service_tariff(
        session=db,
        encounter_id=payload.encounter_id,
        service_id=payload.service_id,
        quantity=payload.quantity,
    )


@router.post(
    "/rcm/surgeries/{surgery_id}/post-charges",
    response_model=SurgeryChargePostResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_surgical_case_completion_charges(
    surgery_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("billing:write", "surgery:write")),
):
    """
    Posts automated OT theatre hourly facility charges and procedure fee upon surgery completion.
    """
    from backend.domains.billing.rcm_services import post_surgery_completion_charges
    return post_surgery_completion_charges(
        session=db,
        surgery_id=surgery_id,
        user_id=current_user.id,
    )


@router.get(
    "/rcm/charge-events",
    response_model=list[ChargeCaptureEventResponse],
    status_code=status.HTTP_200_OK,
)
def list_charge_capture_events(
    encounter_id: Optional[int] = None,
    patient_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:read", "auditor:read")),
):
    """
    Retrieves full audit ledger of all automated clinical charge capture events.
    """
    from backend.domains.billing.models import ChargeCaptureEvent
    stmt = select(ChargeCaptureEvent).order_by(ChargeCaptureEvent.id.desc())
    if encounter_id is not None:
        stmt = stmt.where(ChargeCaptureEvent.encounter_id == encounter_id)
    if patient_id is not None:
        stmt = stmt.where(ChargeCaptureEvent.patient_id == patient_id)
    return list(db.execute(stmt).scalars().all())
