from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from backend.domains.procurement.models import (
    VendorCategoryEnum,
    POStatusEnum,
    GRNStatusEnum,
    IndentStatusEnum,
    VendorInvoiceStatusEnum,
)

# --- Vendor Master ---
class VendorCreate(BaseModel):
    code: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=2, max_length=150)
    category: VendorCategoryEnum
    gstin: Optional[str] = None
    pan: Optional[str] = None
    contact_person: str
    phone: str
    email: EmailStr
    address: str
    bank_account_number: Optional[str] = None
    bank_ifsc: Optional[str] = None
    credit_period_days: int = Field(default=30, ge=0)

class VendorResponse(VendorCreate):
    id: int
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

# --- Purchase Orders ---
class POItemCreate(BaseModel):
    medication_id: Optional[int] = None
    item_description: str
    quantity_ordered: int = Field(gt=0)
    unit_price_paise: int = Field(ge=0)

class POCreate(BaseModel):
    vendor_id: int
    delivery_due_date: datetime
    payment_terms: str
    items: List[POItemCreate]

class POItemResponse(POItemCreate):
    id: int
    quantity_received: int
    total_price_paise: int
    model_config = ConfigDict(from_attributes=True)

class POResponse(BaseModel):
    id: int
    po_number: str
    vendor_id: int
    status: POStatusEnum
    delivery_due_date: datetime
    payment_terms: str
    total_amount_paise: int
    items: List[POItemResponse]
    model_config = ConfigDict(from_attributes=True)

# --- Goods Receipt Note (GRN) ---
class GRNItemCreate(BaseModel):
    po_item_id: int
    batch_number: str
    expiry_date: datetime
    received_quantity: int = Field(ge=0)
    accepted_quantity: int = Field(ge=0)
    rejection_reason: Optional[str] = None

class GRNCreate(BaseModel):
    po_id: int
    invoice_number: str
    invoice_date: datetime
    items: List[GRNItemCreate]

# --- Vendor Invoices (Donut AI Integration) ---
class VendorInvoiceItemResponse(BaseModel):
    id: int
    po_item_id: Optional[int]
    medication_id: Optional[int]
    item_description: str
    batch_number: str
    expiry_date: datetime
    quantity: int
    unit_price_paise: int
    tax_percentage: int
    total_price_paise: int
    model_config = ConfigDict(from_attributes=True)

class VendorInvoiceResponse(BaseModel):
    id: int
    vendor_id: int
    po_id: Optional[int]
    grn_id: Optional[int]
    invoice_number: str
    invoice_date: datetime
    due_date: datetime
    subtotal_paise: int
    tax_amount_paise: int
    discount_paise: int
    total_amount_paise: int
    status: VendorInvoiceStatusEnum
    notes: Optional[str]
    items: List[VendorInvoiceItemResponse]
    model_config = ConfigDict(from_attributes=True)

class VendorInvoiceItemReview(BaseModel):
    po_item_id: Optional[int] = None
    medication_id: Optional[int] = None
    item_description: str = Field(min_length=1, max_length=200)
    batch_number: str = Field(min_length=1, max_length=50)
    expiry_date: datetime
    quantity: int = Field(gt=0)
    unit_price_paise: int = Field(ge=0)
    tax_percentage: int = Field(ge=0, le=100)
    total_price_paise: int = Field(ge=0)

class VendorInvoiceReview(BaseModel):
    po_id: Optional[int] = None
    grn_id: Optional[int] = None
    invoice_number: str = Field(min_length=1, max_length=100)
    invoice_date: datetime
    due_date: datetime
    subtotal_paise: int = Field(ge=0)
    tax_amount_paise: int = Field(ge=0)
    discount_paise: int = Field(ge=0)
    total_amount_paise: int = Field(ge=0)
    items: List[VendorInvoiceItemReview] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_totals(self):
        if self.subtotal_paise + self.tax_amount_paise - self.discount_paise != self.total_amount_paise:
            raise ValueError("Invoice total must equal subtotal plus tax minus discount.")
        if self.discount_paise > self.subtotal_paise:
            raise ValueError("Invoice discount cannot exceed its subtotal.")
        if sum(item.total_price_paise for item in self.items) != self.subtotal_paise:
            raise ValueError("Invoice subtotal must equal the sum of its line-item totals.")
        if self.due_date < self.invoice_date:
            raise ValueError("Invoice due date cannot be earlier than its invoice date.")
        return self

class StoreIndentItemCreate(BaseModel):
    medication_id: int
    requested_quantity: int = Field(gt=0)

class StoreIndentCreate(BaseModel):
    requesting_department_id: int
    encounter_id: Optional[int] = None
    remarks: Optional[str] = None
    items: List[StoreIndentItemCreate]

class StoreIndentItemResponse(BaseModel):
    id: int
    medication_id: int
    requested_quantity: int
    issued_quantity: int
    model_config = ConfigDict(from_attributes=True)

class StoreIndentResponse(BaseModel):
    id: int
    indent_number: str
    requesting_department_id: int
    encounter_id: Optional[int]
    status: IndentStatusEnum
    remarks: Optional[str]
    items: List[StoreIndentItemResponse]
    model_config = ConfigDict(from_attributes=True)

class IndentIssueItem(BaseModel):
    indent_item_id: int
    batch_id: int
    issue_quantity: int = Field(gt=0)

class IndentIssueRequest(BaseModel):
    items: List[IndentIssueItem]

class IndentReturnItem(BaseModel):
    indent_item_id: int
    batch_id: int
    return_quantity: int = Field(gt=0)

class IndentReturnRequest(BaseModel):
    items: List[IndentReturnItem]

# --- Purchase Returns / Debit Notes ---
class PurchaseReturnItemCreate(BaseModel):
    grn_item_id: int
    return_quantity: int = Field(gt=0)
    return_reason: str = Field(min_length=5)

class PurchaseReturnCreate(BaseModel):
    grn_id: int
    vendor_id: int
    items: List[PurchaseReturnItemCreate]
    remarks: Optional[str] = None