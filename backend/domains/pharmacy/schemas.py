"""Pydantic schemas owned by the Avinya HMS domain."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from backend.domains.pharmacy.models import MedicationFormEnum, PrescriptionStatusEnum

class MedicationMasterCreate(BaseModel):
    code: str
    brand_name: str
    generic_name: str
    form: MedicationFormEnum
    strength: str
    low_stock_threshold_units: Optional[int] = Field(default=None, ge=0)
    fefo_expiry_warning_days: Optional[int] = Field(default=None, ge=1, le=365)

class MedicationMasterUpdate(BaseModel):
    brand_name: Optional[str] = None
    generic_name: Optional[str] = None
    form: Optional[MedicationFormEnum] = None
    dosage_form: Optional[MedicationFormEnum] = None
    strength: Optional[str] = None
    is_active: Optional[bool] = None
    low_stock_threshold_units: Optional[int] = Field(default=None, ge=0)
    fefo_expiry_warning_days: Optional[int] = Field(default=None, ge=1, le=365)

class MedicationMasterResponse(BaseModel):
    id: int
    code: str
    brand_name: str
    generic_name: str
    form: MedicationFormEnum
    strength: str
    is_active: bool
    low_stock_threshold_units: Optional[int]
    fefo_expiry_warning_days: Optional[int]
    model_config = ConfigDict(from_attributes=True)

class InventoryBatchCreate(BaseModel):
    medication_id: int
    batch_number: str
    expiry_date: datetime
    stock_quantity: int
    unit_price_paise: int

class InventoryBatchUpdate(BaseModel):
    batch_number: Optional[str] = None
    expiry_date: Optional[datetime] = None
    unit_price_paise: Optional[int] = None

class InventoryBatchResponse(BaseModel):
    id: int
    medication_id: int
    batch_number: str
    expiry_date: datetime
    stock_quantity: int
    unit_price_paise: int
    medication: MedicationMasterResponse
    model_config = ConfigDict(from_attributes=True)

class InventoryStockAdjustment(BaseModel):
    quantity_adjustment: int = Field(default=..., description="Must not be zero")

class PrescriptionItemCreate(BaseModel):
    medication_id: int
    dosage_instruction: str
    requested_quantity: int

class PrescriptionCreate(BaseModel):
    encounter_id: int
    clinical_notes: Optional[str] = None
    items: List[PrescriptionItemCreate]
    override_ddi_warning: Optional[bool] = False
    ddi_override_justification: Optional[str] = None

class ClinicalDdiPrecheckRequest(BaseModel):
    patient_id: int
    medication_ids: List[int] = Field(min_length=1)

class ClinicalDdiPrecheckResponse(BaseModel):
    has_critical_hard_stop: bool
    has_critical_warning: bool
    findings: List[dict]

class PrescriptionUpdate(BaseModel):
    clinical_notes: Optional[str] = None
    status: Optional[PrescriptionStatusEnum] = None

class PrescriptionItemResponse(BaseModel):
    id: int
    medication_id: int
    dosage_instruction: str
    requested_quantity: int
    dispensed_quantity: int
    medication: MedicationMasterResponse
    model_config = ConfigDict(from_attributes=True)

class PrescriptionResponse(BaseModel):
    id: int
    encounter_id: int
    prescribing_doctor_id: int
    status: PrescriptionStatusEnum
    clinical_notes: Optional[str]
    items: List[PrescriptionItemResponse]
    model_config = ConfigDict(from_attributes=True)

class DispenseItem(BaseModel):
    prescription_item_id: int
    batch_id: int
    quantity_to_dispense: int = Field(gt=0, description="Cannot dispense negative or zero stock")

class PrescriptionDispenseRequest(BaseModel):
    items: List[DispenseItem]

class PharmacyReturnItem(BaseModel):
    prescription_item_id: int
    batch_id: int
    quantity_to_return: int = Field(gt=0)
    return_reason: str = Field(min_length=3, max_length=255)

class PharmacyReturnRequest(BaseModel):
    items: List[PharmacyReturnItem] = Field(min_length=1)

class PharmacyReturnLogResponse(BaseModel):
    id: int
    prescription_id: int
    prescription_item_id: int
    batch_id: int
    returned_by_id: int
    returned_quantity: int
    credit_amount_paise: int
    return_reason: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

MedicationCreate = MedicationMasterCreate
MedicationResponse = MedicationMasterResponse
MedicationUpdate = MedicationMasterUpdate
DispenseRequest = PrescriptionDispenseRequest
