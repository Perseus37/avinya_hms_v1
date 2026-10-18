"""Pydantic schemas owned by the Avinya HMS domain."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from backend.domains.insurance.models import ClaimStatusEnum, InsuranceProviderTypeEnum, PreAuthStatusEnum

class InsuranceProviderCreate(BaseModel):
    code: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=2, max_length=150)
    provider_type: InsuranceProviderTypeEnum
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None
    document_requirements: List[str] = Field(default_factory=list, max_length=50)

    @field_validator("document_requirements")
    @classmethod
    def validate_document_requirements(cls, values: List[str]) -> List[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 200 for value in normalized):
            raise ValueError("Each required insurance document must contain 1-200 characters.")
        if len({value.casefold() for value in normalized}) != len(normalized):
            raise ValueError("Required insurance documents must be unique.")
        return normalized


class InsuranceProviderUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None
    document_requirements: Optional[List[str]] = Field(default=None, max_length=50)

    @field_validator("document_requirements")
    @classmethod
    def validate_document_requirements(cls, values: Optional[List[str]]) -> Optional[List[str]]:
        if values is None:
            return None
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 200 for value in normalized):
            raise ValueError("Each required insurance document must contain 1-200 characters.")
        if len({value.casefold() for value in normalized}) != len(normalized):
            raise ValueError("Required insurance documents must be unique.")
        return normalized

class InsuranceProviderResponse(BaseModel):
    id: int
    code: str
    name: str
    provider_type: InsuranceProviderTypeEnum
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    document_requirements: List[str] = Field(default_factory=list)
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

class PatientInsurancePolicyCreate(BaseModel):
    patient_id: int
    provider_id: int
    plan_id: Optional[int] = None
    policy_number: str = Field(min_length=2, max_length=100)
    member_id: str = Field(min_length=2, max_length=100)
    sum_insured_paise: int = Field(gt=0)
    copay_percentage: Optional[int] = Field(default=None, ge=0, le=100)
    valid_from: datetime
    valid_till: datetime

class PatientInsurancePolicyResponse(BaseModel):
    id: int
    patient_id: int
    provider_id: int
    plan_id: Optional[int] = None
    policy_number: str
    member_id: str
    sum_insured_paise: int
    copay_percentage: int
    valid_from: datetime
    valid_till: datetime
    is_active: bool
    provider: InsuranceProviderResponse
    model_config = ConfigDict(from_attributes=True)

class InsurancePreAuthCreate(BaseModel):
    encounter_id: int
    policy_id: int
    primary_diagnosis_id: int
    requested_amount_paise: int = Field(gt=0)
    clinical_justification: Optional[str] = None

class InsurancePreAuthStatusUpdate(BaseModel):
    status: PreAuthStatusEnum
    approved_amount_paise: Optional[int] = Field(default=None, ge=0)
    tpa_reference_number: Optional[str] = None
    tpa_query_remarks: Optional[str] = None

class InsurancePreAuthResponse(BaseModel):
    id: int
    encounter_id: int
    policy_id: int
    primary_diagnosis_id: int
    preauth_number: str
    status: PreAuthStatusEnum
    requested_amount_paise: int
    approved_amount_paise: int
    tpa_reference_number: Optional[str] = None
    clinical_justification: Optional[str] = None
    tpa_query_remarks: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class InsuranceClaimCreate(BaseModel):
    invoice_id: int
    policy_id: int
    preauth_id: Optional[int] = None
    remarks: Optional[str] = None

class InsuranceClaimSettleRequest(BaseModel):
    settled_amount_paise: int = Field(gt=0)
    settlement_utr_reference: str = Field(min_length=3, max_length=100)
    remarks: Optional[str] = None

class InsuranceClaimResponse(BaseModel):
    id: int
    invoice_id: int
    policy_id: int
    preauth_id: Optional[int] = None
    claim_number: str
    status: ClaimStatusEnum
    claimed_amount_paise: int
    insurer_payable_paise: int
    patient_copay_paise: int
    settled_amount_paise: int
    settlement_utr_reference: Optional[str] = None
    remarks: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class InsurancePlanCreate(BaseModel):
    provider_id: int
    code: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=2, max_length=150)
    default_copay_percentage: int = Field(default=0, ge=0, le=100)
    max_room_rent_daily_paise: Optional[int] = Field(default=None, ge=0)
    apply_tariff_automatically: bool = True

class InsurancePlanResponse(BaseModel):
    id: int
    provider_id: int
    code: str
    name: str
    default_copay_percentage: int
    max_room_rent_daily_paise: Optional[int] = None
    apply_tariff_automatically: bool
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

class InsurancePlanTariffUpsert(BaseModel):
    service_id: int
    tariff_price_paise: int = Field(ge=0)
    is_covered: bool = True

class InsurancePlanTariffResponse(BaseModel):
    id: int
    plan_id: int
    service_id: int
    tariff_price_paise: int
    is_covered: bool
    model_config = ConfigDict(from_attributes=True)

class PlanComparisonEstimateRequest(BaseModel):
    plan_ids: List[int] = Field(min_length=1)
    service_ids: List[int] = Field(default_factory=list)
    bed_daily_rate_paise: int = Field(default=0, ge=0)
    stay_days: int = Field(default=1, ge=1)

class PlanComparisonBreakdownItem(BaseModel):
    plan_id: int
    plan_code: str
    plan_name: str
    hospital_rack_total_paise: int
    negotiated_plan_total_paise: int
    mou_tariff_savings_paise: int
    non_covered_excluded_paise: int
    room_rent_excess_paise: int
    estimated_insurer_payable_paise: int
    estimated_patient_out_of_pocket_paise: int
