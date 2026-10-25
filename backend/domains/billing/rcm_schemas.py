"""
Pydantic schemas for Revenue Cycle Management (RCM), CDM Charge Capture & Multi-Tariff Engine.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class TariffResolutionRequest(BaseModel):
    encounter_id: int = Field(..., description="Active clinical encounter ID")
    service_id: int = Field(..., description="Service catalog item ID")
    quantity: int = Field(default=1, ge=1, description="Number of service units")


class TariffResolutionResponse(BaseModel):
    service_id: int
    service_code: str
    service_name: str
    base_price_paise: int
    effective_unit_price_paise: int
    quantity: int
    total_price_paise: int
    gst_rate_percent: int
    tax_amount_paise: int
    tariff_source: str  # "INSURANCE_PLAN_TARIFF", "PLAN_ROOM_RENT_CAP", "BASE_CATALOG"
    is_covered: bool
    plan_code: Optional[str] = None
    plan_name: Optional[str] = None


class ClinicalChargePostRequest(BaseModel):
    encounter_id: int = Field(..., description="Target patient encounter ID")
    event_type: str = Field(..., description="Event type (SURGERY_COMPLETED, MIDNIGHT_CENSUS, DIAGNOSTIC_VERIFIED, etc.)")
    source_reference: str = Field(..., min_length=3, max_length=100, description="Unique source transaction reference for idempotency")
    description: str = Field(..., min_length=2, max_length=255, description="Charge description on invoice")
    service_id: Optional[int] = Field(default=None, description="Optional service catalog ID to automatically resolve negotiated tariff")
    unit_price_paise: Optional[int] = Field(default=None, ge=0, description="Override unit price in paise (if not set, resolved from CDM/Tariff)")
    quantity: int = Field(default=1, ge=1, description="Quantity of charge units")
    gst_rate_percent: Optional[int] = Field(default=0, ge=0, le=100, description="GST rate percentage")
    hsn_sac_code: Optional[str] = Field(default=None, max_length=50, description="HSN/SAC code for GST compliance")


class ClinicalChargePostResponse(BaseModel):
    charge_event_id: int
    invoice_id: int
    invoice_number: str
    invoice_line_item_id: Optional[int]
    source_reference: str
    description: str
    quantity: int
    unit_price_paise: int
    tax_amount_paise: int
    total_charge_paise: int
    tariff_applied: str
    is_insurance_covered: bool
    invoice_total_amount_paise: int
    invoice_net_amount_paise: int
    posted_at: datetime


class MidnightCensusRunRequest(BaseModel):
    census_date: Optional[str] = Field(default=None, description="Target census date in YYYY-MM-DD format (defaults to current UTC date)")


class MidnightCensusRunResponse(BaseModel):
    census_date: str
    active_ipd_encounters_count: int
    charges_posted_count: int
    already_posted_count: int
    total_revenue_posted_paise: int
    details: List[Dict[str, Any]]


class SurgeryChargePostRequest(BaseModel):
    surgery_id: int = Field(..., description="Completed SurgicalSchedule ID")


class SurgeryChargePostResponse(BaseModel):
    surgery_id: int
    case_number: str
    charges_posted_count: int
    total_charges_posted_paise: int
    details: List[Dict[str, Any]]


class ChargeCaptureEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    encounter_id: int
    patient_id: int
    invoice_id: int
    invoice_line_item_id: Optional[int]
    event_type: str
    source_reference: str
    service_id: Optional[int]
    description: str
    quantity: int
    unit_price_paise: int
    tax_amount_paise: int
    total_charge_paise: int
    tariff_applied: str
    is_insurance_covered: bool
    posted_at: datetime
