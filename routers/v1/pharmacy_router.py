import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select

from database import get_db
from dependencies import RequirePermission, get_current_user
from models import (
    User, MedicationMaster, InventoryBatch, Prescription, PrescriptionItem, 
    Invoice, InvoiceLineItem, InvoiceStatusEnum, PrescriptionStatusEnum
)
from schemas.v1.schemas import (
    PaginatedResponse, MedicationMasterCreate, MedicationMasterResponse, MedicationMasterUpdate,
    InventoryBatchCreate, InventoryBatchResponse, InventoryStockAdjustment,
    PrescriptionCreate, PrescriptionResponse, PrescriptionDispenseRequest
)

router = APIRouter(prefix="/api/pharmacy", tags=["Pharmacy & Inventory"])

# -----------------------------------------------------------------------------
# Medication Master Catalog
# -----------------------------------------------------------------------------
@router.post("/medications", response_model=MedicationMasterResponse, status_code=status.HTTP_201_CREATED)
def create_medication(payload: MedicationMasterCreate, db: Session = Depends(get_db), _: User = Depends(RequirePermission("manage_clinical_mdm"))):
    """Adds a new approved medication to the hospital formulary."""
    existing = db.query(MedicationMaster).filter(MedicationMaster.code == payload.code).first()
    if existing:
        raise HTTPException(status_code=409, detail="Medication code already exists.")
        
    med = MedicationMaster(**payload.model_dump(), is_active=True)
    db.add(med)
    db.commit()
    db.refresh(med)
    return med

@router.get("/medications", response_model=PaginatedResponse[MedicationMasterResponse])
def list_medications(page: int = 1, page_size: int = 50, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    query = db.query(MedicationMaster).filter(MedicationMaster.is_active == True)
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@router.put("/medications/{med_id}", response_model=MedicationMasterResponse)
def update_medication(med_id: int, payload: MedicationMasterUpdate, db: Session = Depends(get_db), _: User = Depends(RequirePermission("manage_clinical_mdm"))):
    med = db.get(MedicationMaster, med_id)
    if not med: raise HTTPException(status_code=404, detail="Medication not found.")
    
    if payload.brand_name is not None: med.brand_name = payload.brand_name
    if payload.generic_name is not None: med.generic_name = payload.generic_name
    if payload.is_active is not None: med.is_active = payload.is_active
    
    db.commit()
    db.refresh(med)
    return med

@router.delete("/medications/{med_id}", status_code=200)
def deactivate_medication(med_id: int, db: Session = Depends(get_db), _: User = Depends(RequirePermission("manage_clinical_mdm"))):
    med = db.get(MedicationMaster, med_id)
    if not med: raise HTTPException(status_code=404, detail="Medication not found.")
    med.is_active = False
    db.commit()
    return {"status": "success", "message": f"Medication {med_id} deactivated."}

# -----------------------------------------------------------------------------
# Inventory Batches & Stock
# -----------------------------------------------------------------------------
@router.post("/inventory/batches", response_model=InventoryBatchResponse, status_code=status.HTTP_201_CREATED)
def receive_stock_batch(payload: InventoryBatchCreate, db: Session = Depends(get_db), _: User = Depends(RequirePermission("manage_pharmacy"))):
    """Receives physical inventory with expiration tracking."""
    batch = InventoryBatch(**payload.model_dump())
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch

@router.get("/inventory/batches", response_model=PaginatedResponse[InventoryBatchResponse])
def list_inventory(medication_id: Optional[int] = None, page: int = 1, page_size: int = 50, db: Session = Depends(get_db), _: User = Depends(RequirePermission("manage_pharmacy"))):
    query = db.query(InventoryBatch).options(joinedload(InventoryBatch.medication)).filter(InventoryBatch.stock_quantity > 0)
    if medication_id:
        query = query.filter(InventoryBatch.medication_id == medication_id)
        
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@router.patch("/inventory/batches/{batch_id}/adjust", status_code=200)
def adjust_inventory_stock(batch_id: int, payload: InventoryStockAdjustment, db: Session = Depends(get_db), _: User = Depends(RequirePermission("manage_pharmacy"))):
    """Manually adjust stock for audits, damage, or write-offs."""
    stmt = select(InventoryBatch).where(InventoryBatch.id == batch_id).with_for_update(nowait=True)
    batch = db.execute(stmt).scalar_one_or_none()
    if not batch: raise HTTPException(status_code=404, detail="Batch not found.")
    
    new_quantity = batch.stock_quantity + payload.quantity_adjustment
    if new_quantity < 0:
        raise HTTPException(status_code=400, detail="Adjustment results in negative stock.")
        
    batch.stock_quantity = new_quantity
    db.commit()
    return {"status": "success", "new_stock_quantity": batch.stock_quantity}

# -----------------------------------------------------------------------------
# Prescriptions & Dispensing Workflow
# -----------------------------------------------------------------------------
@router.post("/prescriptions", response_model=PrescriptionResponse, status_code=status.HTTP_201_CREATED)
def write_prescription(payload: PrescriptionCreate, db: Session = Depends(get_db), current_user: User = Depends(RequirePermission("write_cpoe_orders"))):
    """CPOE route for doctors to issue prescriptions to the pharmacy queue."""
    prescription = Prescription(
        encounter_id=payload.encounter_id,
        prescribing_doctor_id=current_user.id,
        clinical_notes=payload.clinical_notes,
        status=PrescriptionStatusEnum.ACTIVE
    )
    db.add(prescription)
    db.flush()
    
    items = [
        PrescriptionItem(
            prescription_id=prescription.id,
            medication_id=item.medication_id,
            dosage_instruction=item.dosage_instruction,
            requested_quantity=item.requested_quantity
        ) for item in payload.items
    ]
    db.add_all(items)
    db.commit()
    db.refresh(prescription)
    return prescription

@router.get("/prescriptions/{prescription_id}", response_model=PrescriptionResponse)
def get_prescription(prescription_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    prescription = db.query(Prescription).options(joinedload(Prescription.items).joinedload(PrescriptionItem.medication)).filter(Prescription.id == prescription_id).first()
    if not prescription: raise HTTPException(status_code=404, detail="Prescription not found.")
    return prescription

@router.post("/prescriptions/{prescription_id}/dispense", status_code=200)
def dispense_prescription(prescription_id: int, payload: PrescriptionDispenseRequest, db: Session = Depends(get_db), _: User = Depends(RequirePermission("manage_pharmacy"))):
    """Deducts stock, fulfills the prescription, and seamlessly bills the patient's encounter ledger."""
    prescription = db.query(Prescription).options(joinedload(Prescription.items).joinedload(PrescriptionItem.medication), joinedload(Prescription.encounter)).filter(Prescription.id == prescription_id).first()
    if not prescription or prescription.status in [PrescriptionStatusEnum.DISPENSED, PrescriptionStatusEnum.CANCELLED]:
        raise HTTPException(status_code=400, detail="Invalid or already finalized prescription.")

    # 1. Fetch or generate a DRAFT invoice for this encounter
    invoice = db.query(Invoice).filter(Invoice.encounter_id == prescription.encounter_id, Invoice.status == InvoiceStatusEnum.DRAFT).first()
    if not invoice:
        inv_number = f"INV-{uuid.uuid4().hex[:8].upper()}"
        invoice = Invoice(patient_id=prescription.encounter.patient_id, encounter_id=prescription.encounter_id, invoice_number=inv_number, status=InvoiceStatusEnum.DRAFT)
        db.add(invoice)
        db.flush()

    # 2. Process Dispense Line Items
    for d_item in payload.items:
        p_item = next((i for i in prescription.items if i.id == d_item.prescription_item_id), None)
        if not p_item: raise HTTPException(status_code=400, detail=f"Item {d_item.prescription_item_id} not found in this prescription.")
        if p_item.dispensed_quantity + d_item.quantity_to_dispense > p_item.requested_quantity:
            raise HTTPException(status_code=400, detail="Cannot dispense more than requested.")

        # Concurrency Lock on the specific Inventory Batch
        stmt = select(InventoryBatch).where(InventoryBatch.id == d_item.batch_id).with_for_update(nowait=True)
        batch = db.execute(stmt).scalar_one_or_none()
        
        if not batch or batch.medication_id != p_item.medication_id:
            raise HTTPException(status_code=400, detail="Invalid batch for the requested medication.")
        if batch.expiry_date < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail=f"Batch {batch.batch_number} is expired.")
        if batch.stock_quantity < d_item.quantity_to_dispense:
            raise HTTPException(status_code=400, detail=f"Insufficient stock in batch {batch.batch_number}.")

        # Deduct Physical Stock
        batch.stock_quantity -= d_item.quantity_to_dispense
        p_item.dispensed_quantity += d_item.quantity_to_dispense

        # Auto-Bill Patient Ledger
        total_price = d_item.quantity_to_dispense * batch.unit_price_paise
        line_item = InvoiceLineItem(
            invoice_id=invoice.id,
            description=f"Rx: {p_item.medication.brand_name} (Batch: {batch.batch_number})",
            quantity=d_item.quantity_to_dispense,
            unit_price_paise=batch.unit_price_paise,
            total_price_paise=total_price
        )
        db.add(line_item)
        invoice.total_amount_paise += total_price

    # 3. Update Status
    all_dispensed = all(i.dispensed_quantity >= i.requested_quantity for i in prescription.items)
    prescription.status = PrescriptionStatusEnum.DISPENSED if all_dispensed else PrescriptionStatusEnum.PARTIAL

    db.commit()
    return {"status": "success", "message": "Medications dispensed and billed to patient ledger successfully."}

@router.delete("/prescriptions/{prescription_id}", status_code=200)
def cancel_prescription(prescription_id: int, db: Session = Depends(get_db), _: User = Depends(RequirePermission("write_cpoe_orders"))):
    prescription = db.get(Prescription, prescription_id)
    if not prescription or prescription.status in [PrescriptionStatusEnum.DISPENSED, PrescriptionStatusEnum.CANCELLED]:
        raise HTTPException(status_code=400, detail="Cannot cancel this prescription.")
        
    prescription.status = PrescriptionStatusEnum.CANCELLED
    db.commit()
    return {"status": "success", "message": "Prescription cancelled."}

from schemas.v1.schemas import InventoryBatchUpdate

@router.put("/inventory/batches/{batch_id}", response_model=InventoryBatchResponse)
def update_inventory_batch(batch_id: int, payload: InventoryBatchUpdate, db: Session = Depends(get_db), _: User = Depends(RequirePermission("manage_pharmacy"))):
    """Corrects data-entry errors on a batch (e.g., fixing pricing before it bills patients)."""
    batch = db.get(InventoryBatch, batch_id)
    if not batch: raise HTTPException(status_code=404, detail="Batch not found.")
    
    if payload.batch_number is not None: batch.batch_number = payload.batch_number
    if payload.expiry_date is not None: batch.expiry_date = payload.expiry_date
    if payload.unit_price_paise is not None: batch.unit_price_paise = payload.unit_price_paise
    
    db.commit()
    db.refresh(batch)
    return batch

@router.delete("/inventory/batches/{batch_id}", status_code=200)
def void_inventory_batch(batch_id: int, db: Session = Depends(get_db), _: User = Depends(RequirePermission("manage_pharmacy"))):
    """Permanently voids an inventory batch created in error."""
    batch = db.get(InventoryBatch, batch_id)
    if not batch: raise HTTPException(status_code=404, detail="Batch not found.")
    
    db.delete(batch)
    db.commit()
    return {"status": "success", "message": f"Batch {batch_id} voided from inventory successfully."}