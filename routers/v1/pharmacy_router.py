from datetime import datetime, timezone
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import RequirePermission, validate_pagination
from models import (
    EncounterStatusEnum,
    InventoryBatch,
    Invoice,
    InvoiceLineItem,
    InvoiceStatusEnum,
    MedicationMaster,
    PatientEncounter,
    Prescription,
    PrescriptionItem,
    PrescriptionStatusEnum,
    User,
    PaymentMethodEnum,
    PharmacyReturnLog,
    RefundVoucher,
)
from schemas.v1.schemas import (
    DispenseRequest,
    InventoryBatchCreate,
    InventoryBatchResponse,
    InventoryBatchUpdate,
    InventoryStockAdjustment,
    MedicationCreate,
    MedicationResponse,
    MedicationUpdate,
    PaginatedResponse,
    PrescriptionCreate,
    PrescriptionResponse,
    PrescriptionUpdate,
    PharmacyReturnLogResponse,
    PharmacyReturnRequest,

)
from services.cpoe import check_allergy_hard_stop

router = APIRouter(prefix="/api/pharmacy", tags=["Pharmacy & Inventory Management"])


def _generate_invoice_number() -> str:
    date_prefix = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"INV-{date_prefix}-{uuid.uuid4().hex[:12].upper()}"

def _ensure_utc(dt: datetime) -> datetime:
    """Ensures a datetime is timezone-aware in UTC before comparison."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

# -----------------------------------------------------------------------------
# Formulary & Inventory Stock Control
# -----------------------------------------------------------------------------
@router.post("/medications", response_model=MedicationResponse, status_code=status.HTTP_201_CREATED)
def add_medication_to_formulary(
    payload: MedicationCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:write", "mdm:write")),
):
    existing = db.query(MedicationMaster).filter(MedicationMaster.code == payload.code).first()
    if existing:
        raise HTTPException(status_code=409, detail="Medication code already exists in formulary.")

    med = MedicationMaster(**payload.model_dump(), is_active=True)
    db.add(med)
    db.commit()
    db.refresh(med)
    return med


@router.get("/medications", response_model=PaginatedResponse[MedicationResponse])
def list_medications(
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:read", "mdm:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(MedicationMaster).filter(MedicationMaster.is_active == True).order_by(MedicationMaster.id.asc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.put("/medications/{med_id}", response_model=MedicationResponse)
def update_medication(
    med_id: int,
    payload: MedicationUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:write", "mdm:write")),
):
    med = db.get(MedicationMaster, med_id)
    if not med:
        raise HTTPException(status_code=404, detail="Medication not found.")

    if payload.generic_name is not None:
        med.generic_name = payload.generic_name
    if payload.brand_name is not None:
        med.brand_name = payload.brand_name
    if payload.form is not None:
        med.form = payload.form
    elif payload.dosage_form is not None:
        med.form = payload.dosage_form
    if payload.strength is not None:
        med.strength = payload.strength
    if payload.is_active is not None:
        med.is_active = payload.is_active

    db.commit()
    db.refresh(med)
    return med


@router.delete("/medications/{med_id}", status_code=200)
def deactivate_medication(
    med_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:void", "mdm:delete")),
):
    med = db.get(MedicationMaster, med_id)
    if not med:
        raise HTTPException(status_code=404, detail="Medication not found.")
    med.is_active = False
    db.commit()
    return {"status": "success", "message": f"Medication {med.code} deactivated."}


@router.post("/inventory/batches", response_model=InventoryBatchResponse, status_code=status.HTTP_201_CREATED)
def receive_inventory_batch(
    payload: InventoryBatchCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:write")),
):
    if payload.stock_quantity <= 0:
        raise HTTPException(status_code=400, detail="Initial batch stock quantity must be greater than zero.")
    if payload.unit_price_paise < 0:
        raise HTTPException(status_code=400, detail="Unit price cannot be negative.")
    if _ensure_utc(payload.expiry_date) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Cannot receive an already expired inventory batch.")

    med = db.get(MedicationMaster, payload.medication_id)
    if not med or not med.is_active:
        raise HTTPException(status_code=404, detail="Active medication not found.")

    batch = InventoryBatch(**payload.model_dump())
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


@router.get("/inventory/batches", response_model=PaginatedResponse[InventoryBatchResponse])
def list_inventory_batches(
    medication_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(InventoryBatch).options(joinedload(InventoryBatch.medication)).filter(InventoryBatch.stock_quantity > 0)
    if medication_id:
        query = query.filter(InventoryBatch.medication_id == medication_id)

    query = query.order_by(InventoryBatch.expiry_date.asc(), InventoryBatch.id.asc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.put("/inventory/batches/{batch_id}", response_model=InventoryBatchResponse)
def update_inventory_batch(
    batch_id: int,
    payload: InventoryBatchUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:write")),
):
    batch = db.get(InventoryBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found.")

    if payload.batch_number is not None:
        batch.batch_number = payload.batch_number

    db.commit()
    db.refresh(batch)
    return batch


@router.patch("/inventory/batches/{batch_id}/adjust", response_model=InventoryBatchResponse)
def adjust_inventory_stock(
    batch_id: int,
    payload: InventoryStockAdjustment,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:write")),
):
    if payload.quantity_adjustment == 0:
        raise HTTPException(status_code=400, detail="Quantity adjustment cannot be zero.")

    try:
        stmt = select(InventoryBatch).where(InventoryBatch.id == batch_id).with_for_update(nowait=True)
        batch = db.execute(stmt).scalar_one_or_none()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found.")

        if batch.stock_quantity + payload.quantity_adjustment < 0:
            raise HTTPException(status_code=400, detail="Stock adjustment cannot result in negative inventory.")

        batch.stock_quantity += payload.quantity_adjustment
        db.commit()
        db.refresh(batch)
        return batch
    except OperationalError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Inventory batch is locked by another transaction.")


@router.delete("/inventory/batches/{batch_id}", status_code=200)
def void_inventory_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:void")),
):
    try:
        stmt = select(InventoryBatch).where(InventoryBatch.id == batch_id).with_for_update(nowait=True)
        batch = db.execute(stmt).scalar_one_or_none()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found.")

        batch.stock_quantity = 0
        db.commit()
        return {"status": "success", "message": f"Batch {batch_id} voided (stock zeroed for recall traceability)."}
    except OperationalError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Inventory batch is locked by another transaction.")


# -----------------------------------------------------------------------------
# Electronic Prescribing (eRx) & Atomic Dispensing
# -----------------------------------------------------------------------------
@router.post("/prescriptions", response_model=PrescriptionResponse, status_code=status.HTTP_201_CREATED)
def write_prescription(
    payload: PrescriptionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("pharmacy:write")),
):
    if not payload.items:
        raise HTTPException(status_code=400, detail="Prescription must contain at least one medication item.")

    encounter = db.get(PatientEncounter, payload.encounter_id)
    if not encounter or encounter.status != EncounterStatusEnum.ACTIVE:
        raise HTTPException(status_code=400, detail="Active patient encounter not found.")

    med_ids = [item.medication_id for item in payload.items]
    meds = db.query(MedicationMaster).filter(MedicationMaster.id.in_(med_ids)).all()
    med_map = {m.id: m for m in meds}

    if len(med_map) != len(set(med_ids)):
        raise HTTPException(status_code=400, detail="One or more requested medication IDs do not exist.")

    allergen_keywords: list[str] = []
    for item in payload.items:
        if item.requested_quantity <= 0:
            raise HTTPException(status_code=400, detail="Requested medication quantity must be greater than zero.")
        med = med_map[item.medication_id]
        if not med.is_active:
            raise HTTPException(status_code=400, detail=f"Medication '{med.brand_name}' is inactive and cannot be prescribed.")
        allergen_keywords.extend([med.brand_name, med.generic_name])

    check_allergy_hard_stop(db, encounter.patient_id, allergen_keywords)

    prescription = Prescription(
        encounter_id=payload.encounter_id,
        prescribing_doctor_id=current_user.id,
        clinical_notes=payload.clinical_notes,
        status=PrescriptionStatusEnum.ACTIVE,
    )
    db.add(prescription)
    db.flush()

    items = [
        PrescriptionItem(
            prescription_id=prescription.id,
            medication_id=item.medication_id,
            dosage_instruction=item.dosage_instruction,
            requested_quantity=item.requested_quantity,
        )
        for item in payload.items
    ]
    db.add_all(items)
    db.commit()
    db.refresh(prescription)
    return prescription


@router.get("/prescriptions", response_model=PaginatedResponse[PrescriptionResponse])
def list_prescriptions(
    encounter_id: Optional[int] = None,
    status_filter: Optional[PrescriptionStatusEnum] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(Prescription).options(joinedload(Prescription.items).joinedload(PrescriptionItem.medication))
    if encounter_id:
        query = query.filter(Prescription.encounter_id == encounter_id)
    if status_filter:
        query = query.filter(Prescription.status == status_filter)
    query = query.order_by(Prescription.id.desc())

    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/prescriptions/{prescription_id}", response_model=PrescriptionResponse)
def get_prescription(
    prescription_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:read")),
):
    rx = (
        db.query(Prescription)
        .options(joinedload(Prescription.items).joinedload(PrescriptionItem.medication))
        .filter(Prescription.id == prescription_id)
        .first()
    )
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found.")
    return rx


@router.put("/prescriptions/{prescription_id}", response_model=PrescriptionResponse)
def update_prescription(
    prescription_id: int,
    payload: PrescriptionUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:write")),
):
    rx = db.get(Prescription, prescription_id)
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found.")
    if rx.status in [PrescriptionStatusEnum.DISPENSED, PrescriptionStatusEnum.CANCELLED]:
        raise HTTPException(status_code=400, detail="Cannot modify a dispensed or cancelled prescription.")

    if payload.clinical_notes is not None:
        rx.clinical_notes = payload.clinical_notes
    if payload.status is not None:
        rx.status = payload.status

    db.commit()
    db.refresh(rx)
    return rx


@router.delete("/prescriptions/{prescription_id}", status_code=200)
def cancel_prescription(
    prescription_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:void")),
):
    rx = db.query(Prescription).options(joinedload(Prescription.items)).filter(Prescription.id == prescription_id).first()
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found.")
    if rx.status != PrescriptionStatusEnum.ACTIVE or any(i.dispensed_quantity > 0 for i in rx.items):
        raise HTTPException(status_code=400, detail="Cannot cancel a prescription once dispensing has started.")

    rx.status = PrescriptionStatusEnum.CANCELLED
    db.commit()
    return {"status": "success", "message": f"Prescription {prescription_id} cancelled."}


@router.post("/prescriptions/{prescription_id}/dispense", status_code=200)
def dispense_medication(
    prescription_id: int,
    payload: DispenseRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("pharmacy:write")),
):
    if not payload.items:
        raise HTTPException(status_code=400, detail="Dispense request must include at least one item.")

    try:
        stmt_rx = (
            select(Prescription)
            .options(joinedload(Prescription.encounter))
            .where(Prescription.id == prescription_id)
            .with_for_update(nowait=True)
        )
        prescription = db.execute(stmt_rx).scalar_one_or_none()
        if not prescription or prescription.status in [PrescriptionStatusEnum.DISPENSED, PrescriptionStatusEnum.CANCELLED]:
            raise HTTPException(status_code=400, detail="Prescription is invalid, cancelled, or already fully dispensed.")

        stmt_inv = (
            select(Invoice)
            .where(
                Invoice.encounter_id == prescription.encounter_id,
                Invoice.status == InvoiceStatusEnum.DRAFT,
            )
            .with_for_update()
        )
        invoice = db.execute(stmt_inv).scalar_one_or_none()

        if not invoice:
            invoice = Invoice(
                invoice_number=_generate_invoice_number(),
                patient_id=prescription.encounter.patient_id,
                encounter_id=prescription.encounter_id,
                status=InvoiceStatusEnum.DRAFT,
                total_amount_paise=0,
                amount_paid_paise=0,
            )
            db.add(invoice)
            db.flush()

        total_dispensed_cost_paise = 0

        for disp_item in payload.items:
            if disp_item.quantity_to_dispense <= 0:
                raise HTTPException(status_code=400, detail="Quantity to dispense must be strictly positive.")

            stmt_item = (
                select(PrescriptionItem)
                .where(
                    PrescriptionItem.id == disp_item.prescription_item_id,
                    PrescriptionItem.prescription_id == prescription.id,
                )
                .with_for_update(nowait=True)
            )
            rx_item = db.execute(stmt_item).scalar_one_or_none()
            if not rx_item:
                raise HTTPException(status_code=404, detail=f"Prescription item {disp_item.prescription_item_id} not found.")

            remaining_qty = rx_item.requested_quantity - rx_item.dispensed_quantity
            if disp_item.quantity_to_dispense > remaining_qty:
                raise HTTPException(status_code=400, detail="Cannot dispense more than the remaining prescribed quantity.")

            stmt_batch = select(InventoryBatch).where(InventoryBatch.id == disp_item.batch_id).with_for_update(nowait=True)
            batch = db.execute(stmt_batch).scalar_one_or_none()
            if not batch or batch.medication_id != rx_item.medication_id:
                raise HTTPException(status_code=400, detail="Invalid batch ID for the requested medication.")

            if _ensure_utc(batch.expiry_date) <= datetime.now(timezone.utc):
                raise HTTPException(status_code=409, detail=f"Batch {batch.batch_number} is EXPIRED. Dispensing blocked.")

            if batch.stock_quantity < disp_item.quantity_to_dispense:
                raise HTTPException(status_code=409, detail=f"Insufficient stock in batch {batch.batch_number}.")

            batch.stock_quantity -= disp_item.quantity_to_dispense
            rx_item.dispensed_quantity += disp_item.quantity_to_dispense

            line_cost = disp_item.quantity_to_dispense * batch.unit_price_paise
            total_dispensed_cost_paise += line_cost

            med = db.get(MedicationMaster, rx_item.medication_id)
            if med is None:
                raise HTTPException(status_code=400, detail="Medication not found for the prescription item.")

            line_item = InvoiceLineItem(
                invoice_id=invoice.id,
                service_id=None,
                description=(
                    f"PHARMACY: {med.brand_name} ({med.strength}) - "
                    f"Batch: {batch.batch_number} [DispensedBy:{current_user.id}]"
                ),
                quantity=disp_item.quantity_to_dispense,
                unit_price_paise=batch.unit_price_paise,
                total_price_paise=line_cost,
            )
            db.add(line_item)

        invoice.total_amount_paise += total_dispensed_cost_paise

        db.flush()
        all_rx_items = db.query(PrescriptionItem).filter(PrescriptionItem.prescription_id == prescription.id).all()
        if all_rx_items and all(i.dispensed_quantity == i.requested_quantity for i in all_rx_items):
            prescription.status = PrescriptionStatusEnum.DISPENSED
        else:
            prescription.status = PrescriptionStatusEnum.PARTIAL

        db.commit()
        return {
            "status": "success",
            "prescription_status": prescription.status.value,
            "billed_to_invoice": invoice.invoice_number,
        }
    except OperationalError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Dispensing failed because the prescription or inventory batch is locked by a concurrent transaction.",
        )

@router.post(
    "/prescriptions/{prescription_id}/return",
    response_model=list[PharmacyReturnLogResponse],
    status_code=status.HTTP_201_CREATED,
)
def return_dispensed_medication(
    prescription_id: int,
    payload: PharmacyReturnRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("pharmacy:write", "pharmacy:dispense")),
):
    """
    Processes ward/patient medication stock returns:
    1. Restores returned quantity to InventoryBatch.stock_quantity.
    2. Decrements PrescriptionItem.dispensed_quantity and updates Prescription.status.
    3. Automatically credits/reduces the 'PHARMACY:' line item on a DRAFT invoice,
       or issues an immutable RefundVoucher if the encounter's invoice is already PAID.
    """
    stmt_rx = (
        select(Prescription)
        .options(joinedload(Prescription.encounter))
        .where(Prescription.id == prescription_id)
        .with_for_update()
    )
    rx = db.execute(stmt_rx).scalar_one_or_none()
    if not rx:
        raise HTTPException(status_code=404, detail="Prescription not found.")

    return_logs: list[PharmacyReturnLog] = []
    total_credit_paise = 0

    inv_stmt = (
        select(Invoice)
        .options(joinedload(Invoice.line_items))
        .where(Invoice.encounter_id == rx.encounter_id, Invoice.status != InvoiceStatusEnum.VOID)
        .order_by(Invoice.id.desc())
        .with_for_update()
    )
    invoice = db.execute(inv_stmt).unique().scalars().first()

    for ret in payload.items:
        rx_item = db.execute(
            select(PrescriptionItem)
            .where(
                PrescriptionItem.id == ret.prescription_item_id,
                PrescriptionItem.prescription_id == rx.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if not rx_item:
            raise HTTPException(status_code=404, detail=f"Prescription item {ret.prescription_item_id} not found.")
        if ret.quantity_to_return > rx_item.dispensed_quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot return {ret.quantity_to_return} units; only {rx_item.dispensed_quantity} units are currently dispensed.",
            )

        batch = db.execute(
            select(InventoryBatch).where(InventoryBatch.id == ret.batch_id).with_for_update()
        ).scalar_one_or_none()
        if not batch or batch.medication_id != rx_item.medication_id:
            raise HTTPException(status_code=400, detail="Batch does not match prescription item medication.")

        med = db.get(MedicationMaster, rx_item.medication_id)
        credit_paise = ret.quantity_to_return * batch.unit_price_paise
        total_credit_paise += credit_paise

        # 1. Restore physical stock & decrement dispensed count
        batch.stock_quantity += ret.quantity_to_return
        rx_item.dispensed_quantity -= ret.quantity_to_return

        # 2. Adjust DRAFT invoice line item if invoice is still open
        if invoice and invoice.status == InvoiceStatusEnum.DRAFT and med:
            batch_tag = f"Batch: {batch.batch_number}"
            matching_line = next(
                (
                    li
                    for li in invoice.line_items
                    if li.description.startswith("PHARMACY:") and batch_tag in li.description
                ),
                None,
            )
            if matching_line:
                if matching_line.quantity <= ret.quantity_to_return:
                    invoice.total_amount_paise = max(0, invoice.total_amount_paise - matching_line.total_price_paise)
                    db.delete(matching_line)
                else:
                    matching_line.quantity -= ret.quantity_to_return
                    matching_line.total_price_paise = matching_line.quantity * matching_line.unit_price_paise
                    invoice.total_amount_paise = max(0, invoice.total_amount_paise - credit_paise)

        log = PharmacyReturnLog(
            prescription_id=rx.id,
            prescription_item_id=rx_item.id,
            batch_id=batch.id,
            returned_by_id=current_user.id,
            returned_quantity=ret.quantity_to_return,
            credit_amount_paise=credit_paise,
            return_reason=ret.return_reason,
        )
        db.add(log)
        return_logs.append(log)

    # If the invoice was already PAID, automatically issue an auditable RefundVoucher
    if invoice and invoice.status == InvoiceStatusEnum.PAID and total_credit_paise > 0:
        date_prefix = datetime.now(timezone.utc).strftime("%Y%m%d")
        voucher = RefundVoucher(
            voucher_number=f"RFND-PHM-{date_prefix}-{uuid.uuid4().hex[:8].upper()}",
            patient_id=invoice.patient_id,
            deposit_id=None,
            invoice_id=invoice.id,
            refund_amount_paise=total_credit_paise,
            refund_method=PaymentMethodEnum.CASH,
            transaction_reference=f"RX-RET-{rx.id}",
            reason="Pharmacy dispensed stock return credit on paid invoice",
            approved_by_id=current_user.id,
        )
        db.add(voucher)

    db.flush()
    all_items = db.query(PrescriptionItem).filter(PrescriptionItem.prescription_id == rx.id).all()
    if all(i.dispensed_quantity == 0 for i in all_items):
        rx.status = PrescriptionStatusEnum.ACTIVE
    elif all(i.dispensed_quantity == i.requested_quantity for i in all_items):
        rx.status = PrescriptionStatusEnum.DISPENSED
    else:
        rx.status = PrescriptionStatusEnum.PARTIAL

    db.commit()
    for l in return_logs:
        db.refresh(l)
    return return_logs