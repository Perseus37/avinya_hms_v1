"""Pydantic schemas owned by the Avinya HMS domain."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from backend.domains.billing.models import DepositStatusEnum, InvoiceStatusEnum, PaymentMethodEnum

class InvoiceCreate(BaseModel):
    patient_id: int
    encounter_id: Optional[int] = None
    notes: Optional[str] = None

class InvoiceLineItemCreate(BaseModel):
    service_id: Optional[int] = None
    description: str
    quantity: int = Field(gt=0, description="Quantity must be strictly positive")
    unit_price_paise: int = Field(ge=0, description="Price cannot be negative")
    gst_rate_percent: int = Field(default=0, ge=0, le=100)
    hsn_sac_code: Optional[str] = None

class InvoiceUpdate(BaseModel):
    notes: Optional[str] = None

class PaymentReceiptCreate(BaseModel):
    amount_paid_paise: int = Field(gt=0, description="Payment must be strictly positive")
    payment_method: PaymentMethodEnum
    transaction_reference: Optional[str] = None

class InvoiceLineItemResponse(BaseModel):
    id: int
    service_id: Optional[int]
    description: str
    quantity: int
    unit_price_paise: int
    total_price_paise: int
    gst_rate_percent: int = 0
    tax_amount_paise: int = 0
    hsn_sac_code: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class PaymentReceiptResponse(BaseModel):
    id: int
    receipt_number: str
    amount_paid_paise: int
    payment_method: PaymentMethodEnum
    transaction_reference: Optional[str]
    model_config = ConfigDict(from_attributes=True)

class InvoiceResponse(BaseModel):
    id: int
    patient_id: int
    encounter_id: Optional[int]
    invoice_number: str
    status: InvoiceStatusEnum
    total_amount_paise: int
    tax_amount_paise: int = 0
    cgst_amount_paise: int = 0
    sgst_amount_paise: int = 0
    igst_amount_paise: int = 0
    is_tax_exempt: bool = True
    hsn_sac_code: Optional[str] = None
    discount_amount_paise: int = 0
    net_amount_paise: int = 0
    discount_reason: Optional[str] = None
    discount_approved_by_id: Optional[int] = None
    amount_paid_paise: int
    line_items: List[InvoiceLineItemResponse]
    payments: List[PaymentReceiptResponse]
    model_config = ConfigDict(from_attributes=True)

class InvoiceDiscountRequest(BaseModel):
    discount_amount_paise: int = Field(ge=0, description="Discount in paise")
    discount_reason: str = Field(min_length=3, max_length=255, description="Mandatory audit reason for concession/waiver")

class PatientDepositCreate(BaseModel):
    patient_id: int
    encounter_id: Optional[int] = None
    amount_paise: int = Field(gt=0)
    payment_method: PaymentMethodEnum
    transaction_reference: Optional[str] = None
    notes: Optional[str] = None

class PatientDepositResponse(BaseModel):
    id: int
    patient_id: int
    encounter_id: Optional[int] = None
    deposit_number: str
    amount_paise: int
    remaining_balance_paise: int
    payment_method: PaymentMethodEnum
    transaction_reference: Optional[str] = None
    status: DepositStatusEnum
    notes: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class ApplyDepositToInvoiceRequest(BaseModel):
    deposit_id: int
    amount_to_apply_paise: Optional[int] = Field(default=None, gt=0)

class DepositRefundRequest(BaseModel):
    refund_amount_paise: Optional[int] = Field(default=None, gt=0)
    refund_method: PaymentMethodEnum = PaymentMethodEnum.BANK_TRANSFER
    transaction_reference: Optional[str] = None
    reason: str = Field(min_length=3, max_length=255)

class RefundVoucherResponse(BaseModel):
    id: int
    voucher_number: str
    patient_id: int
    deposit_id: Optional[int] = None
    invoice_id: Optional[int] = None
    refund_amount_paise: int
    refund_method: PaymentMethodEnum
    transaction_reference: Optional[str] = None
    reason: str
    approved_by_id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
