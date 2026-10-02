"""Pydantic schemas for the Avinya HMS API."""

from datetime import datetime
from typing import Generic, List, Optional, TypeVar
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from models import (
    AdmissionTypeEnum,
    AllergyCategoryEnum,
    AllergySeverityEnum,
    AppointmentStatusEnum,
    BedStatusEnum,
    BedTypeEnum,
    BloodGroupEnum,
    BodySystemEnum,
    DischargeDispositionEnum,
    EncounterStatusEnum,
    EncounterTypeEnum,
    GenderEnum,
    InvoiceStatusEnum,
    MedicationFormEnum,
    OrderStatusEnum,
    PaymentMethodEnum,
    PrescriptionStatusEnum,
    RoomTypeEnum,
    ServiceTypeEnum,
    SeverityEnum,
    ShiftTypeEnum,
    TaskStatusEnum,
    UserStatusEnum,
    WardTypeEnum,
    DiagnosisTypeEnum,
    InsuranceProviderTypeEnum,
    PreAuthStatusEnum,
    ClaimStatusEnum,
    DepositStatusEnum,
    FluidDirectionEnum,
    MLCCauseEnum,
    TriageAcuityEnum,
    eMARStatusEnum,
    AnesthesiaTypeEnum,
    OTTheatreTypeEnum,
    SurgeryStatusEnum,
    BloodBagStatusEnum,
    BloodComponentTypeEnum,
    DicomModalityEnum,
    TransfusionReactionTypeEnum,

)

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    success: bool = True
    total: int
    page: int
    page_size: int
    items: List[T]


# -----------------------------------------------------------------------------
# Inter-Branch / Multi-Tenant Federation Schemas
# -----------------------------------------------------------------------------
class FederatedClientCreate(BaseModel):
    branch_code: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=3, max_length=150)


class FederatedClientCreatedResponse(BaseModel):
    id: int
    branch_code: str
    name: str
    api_key_prefix: str
    raw_api_key: str
    is_active: bool


class FederatedClientResponse(BaseModel):
    id: int
    branch_code: str
    name: str
    api_key_prefix: str
    is_active: bool
    last_used_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class FederatedAllergySummary(BaseModel):
    allergy_code: str
    allergy_name: str
    severity: SeverityEnum
    reaction: str


class FederatedPatientSummaryResponse(BaseModel):
    source_mrn: str
    abha_number: Optional[str] = None
    full_name: str
    date_of_birth: datetime
    gender: GenderEnum
    blood_group: Optional[BloodGroupEnum] = None
    phone_number: Optional[str] = None
    email: Optional[EmailStr] = None
    address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    active_allergies: list[FederatedAllergySummary] = []


# -----------------------------------------------------------------------------
# Authentication and IAM schemas
# -----------------------------------------------------------------------------
class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class RoleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    can_access_dashboard: bool = False
    can_access_patients: bool = False


class RoleResponse(BaseModel):
    id: int
    code: str
    name: str
    description: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class PermissionResponse(BaseModel):
    id: int
    code: str
    description: Optional[str]
    module: str
    model_config = ConfigDict(from_attributes=True)


class DoctorProfileCreate(BaseModel):
    clinical_id: str
    department_id: int
    qualification: str
    specialization: str
    medical_council_name: str
    medical_registration_number: str
    registration_valid_till: datetime
    consultation_fee_paise: int
    consultation_duration_minutes: int = 15


class UserCreate(BaseModel):
    login_id: str
    email: EmailStr
    password: str
    full_name: str
    phone_number: Optional[str] = None
    gender: GenderEnum = GenderEnum.OTHER
    role_ids: List[int]
    doctor_profile: Optional[DoctorProfileCreate] = None


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    phone_number: Optional[str] = None
    gender: Optional[GenderEnum] = None


class UserMeResponse(BaseModel):
    id: int
    login_id: str
    email: str
    full_name: str
    status: UserStatusEnum
    is_superuser: bool
    roles: List[RoleResponse]
    model_config = ConfigDict(from_attributes=True)


# -----------------------------------------------------------------------------
# Ward and asset management schemas
# -----------------------------------------------------------------------------
class FloorCreate(BaseModel):
    floor_number: int
    name: str


class FloorUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None


class FloorResponse(BaseModel):
    id: int
    floor_number: int
    name: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class WardCreate(BaseModel):
    floor_id: int
    department_id: int
    code: str
    name: str
    ward_type: WardTypeEnum


class WardUpdate(BaseModel):
    name: Optional[str] = None
    ward_type: Optional[WardTypeEnum] = None
    is_active: Optional[bool] = None


class WardResponse(BaseModel):
    id: int
    code: str
    name: str
    ward_type: WardTypeEnum
    model_config = ConfigDict(from_attributes=True)


class BedSchema(BaseModel):
    id: int
    bed_number: str
    status: BedStatusEnum
    model_config = ConfigDict(from_attributes=True)


class RoomSchema(BaseModel):
    id: int
    room_number: str
    beds: List[BedSchema]
    model_config = ConfigDict(from_attributes=True)


class WardMapResponse(BaseModel):
    id: int
    code: str
    name: str
    ward_type: WardTypeEnum
    rooms: List[RoomSchema]
    model_config = ConfigDict(from_attributes=True)


class RoomCreate(BaseModel):
    ward_id: int
    room_number: str
    room_type: RoomTypeEnum
    bed_capacity: int


class RoomUpdate(BaseModel):
    room_number: Optional[str] = None
    room_type: Optional[RoomTypeEnum] = None
    bed_capacity: Optional[int] = None
    is_active: Optional[bool] = None


class RoomResponse(BaseModel):
    id: int
    ward_id: int
    room_number: str
    room_type: RoomTypeEnum
    bed_capacity: int
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class BedCreate(BaseModel):
    room_id: int
    bed_number: str
    bed_type: BedTypeEnum
    base_daily_rate_paise: int


class BedStatusUpdate(BaseModel):
    status: BedStatusEnum


class ShiftAssignmentCreate(BaseModel):
    ward_id: int
    nurse_user_id: int
    shift_type: ShiftTypeEnum
    shift_date: datetime
    handoff_notes: Optional[str] = None


class ShiftAssignmentResponse(BaseModel):
    id: int
    ward_id: int
    nurse_user_id: int
    shift_type: ShiftTypeEnum
    shift_date: datetime
    handoff_notes: Optional[str] = None
    handed_off_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class ShiftHandoffRequest(BaseModel):
    handoff_notes: str = Field(min_length=1, description="Clinical handoff summary notes")


# -----------------------------------------------------------------------------
# Patient and clinical schemas
# -----------------------------------------------------------------------------
class PatientCreate(BaseModel):
    mrn: Optional[str] = None
    full_name: str
    date_of_birth: datetime
    gender: GenderEnum
    phone_number: Optional[str] = "0000000000"
    abha_number: Optional[str] = None
    email: Optional[EmailStr] = None
    blood_group: Optional[BloodGroupEnum] = None
    address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None


class PatientUpdate(BaseModel):
    full_name: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    gender: Optional[GenderEnum] = None
    phone_number: Optional[str] = None
    abha_number: Optional[str] = None
    email: Optional[EmailStr] = None
    blood_group: Optional[BloodGroupEnum] = None
    address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    is_active: Optional[bool] = None


class PatientResponse(BaseModel):
    id: int
    mrn: str
    full_name: str
    date_of_birth: datetime
    gender: GenderEnum
    phone_number: Optional[str] = None
    abha_number: Optional[str] = None
    email: Optional[str] = None
    blood_group: Optional[BloodGroupEnum] = None
    address: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class PatientAllergyCreate(BaseModel):
    patient_id: Optional[int] = None
    allergy_id: int
    severity: AllergySeverityEnum
    reaction: Optional[str] = None
    reaction_description: Optional[str] = None


class AllergyCreate(BaseModel):
    department_id: int
    code: str
    name: str
    category: AllergyCategoryEnum = AllergyCategoryEnum.DRUG
    default_severity: AllergySeverityEnum = AllergySeverityEnum.MODERATE


AllergyMasterCreate = AllergyCreate


class AllergyUpdate(BaseModel):
    department_id: Optional[int] = None
    code: Optional[str] = None
    name: Optional[str] = None
    category: Optional[AllergyCategoryEnum] = None
    default_severity: Optional[AllergySeverityEnum] = None
    is_active: Optional[bool] = None


class AllergyResponse(BaseModel):
    id: int
    department_id: int
    code: str
    name: str
    category: AllergyCategoryEnum
    default_severity: AllergySeverityEnum
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class PatientAllergyResponse(BaseModel):
    id: int
    allergy_id: int
    severity: AllergySeverityEnum
    reaction_description: Optional[str] = None
    is_active: bool
    allergy: AllergyResponse
    model_config = ConfigDict(from_attributes=True)


class CPOEAllergyCheckRequest(BaseModel):
    patient_id: int
    allergen_keywords: List[str]


AllergyCheckRequest = CPOEAllergyCheckRequest


class TaxonomyResponse(BaseModel):
    items: List[dict]


class ChiefComplaintCreate(BaseModel):
    department_id: int
    code: str
    name: str


class ChiefComplaintUpdate(BaseModel):
    department_id: Optional[int] = None
    code: Optional[str] = None
    name: Optional[str] = None
    is_active: Optional[bool] = None


class ChiefComplaintResponse(BaseModel):
    id: int
    department_id: int
    code: str
    name: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class ComorbidityCreate(BaseModel):
    department_id: int
    code: str
    name: str
    icd10_code: Optional[str] = None


class ComorbidityUpdate(BaseModel):
    department_id: Optional[int] = None
    code: Optional[str] = None
    name: Optional[str] = None
    icd10_code: Optional[str] = None
    is_active: Optional[bool] = None


class ComorbidityResponse(BaseModel):
    id: int
    department_id: int
    code: str
    name: str
    icd10_code: Optional[str] = None
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class DiagnosisCreate(BaseModel):
    department_id: int
    icd10_code: str
    short_description: str
    full_description: str
    is_billable: bool = True
    parent_diagnosis_id: Optional[int] = None


class DiagnosisUpdate(BaseModel):
    department_id: Optional[int] = None
    icd10_code: Optional[str] = None
    short_description: Optional[str] = None
    full_description: Optional[str] = None
    is_billable: Optional[bool] = None
    is_active: Optional[bool] = None


class DiagnosisResponse(BaseModel):
    id: int
    department_id: int
    icd10_code: str
    short_description: str
    full_description: str
    is_billable: bool
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class PhysicalExamCreate(BaseModel):
    department_id: int
    body_system: BodySystemEnum
    code: str
    name: str


class PhysicalExamUpdate(BaseModel):
    department_id: Optional[int] = None
    body_system: Optional[BodySystemEnum] = None
    code: Optional[str] = None
    name: Optional[str] = None
    is_active: Optional[bool] = None


class PhysicalExamResponse(BaseModel):
    id: int
    department_id: int
    body_system: BodySystemEnum
    code: str
    name: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


# --- PHASE 4: CATALOG & MDM SCHEMAS ---
class ServiceCategoryCreate(BaseModel):
    department_id: int
    code: str
    name: str


class ServiceCategoryUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None


class ServiceCategoryResponse(BaseModel):
    id: int
    department_id: int
    code: str
    name: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class ServiceCatalogCreate(BaseModel):
    category_id: int
    code: str
    name: str
    service_type: ServiceTypeEnum
    base_price_paise: int
    loinc_code: Optional[str] = None
    cpt_code: Optional[str] = None
    is_order_set: bool = False


class ServiceCatalogUpdate(BaseModel):
    name: Optional[str] = None
    base_price_paise: Optional[int] = None
    is_active: Optional[bool] = None


class ServiceCatalogResponse(BaseModel):
    id: int
    category_id: int
    code: str
    name: str
    service_type: ServiceTypeEnum
    base_price_paise: int
    is_order_set: bool
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class ServiceBundleMappingCreate(BaseModel):
    parent_service_id: int
    child_service_id: int
    quantity: int = 1


class ServiceBundleMappingResponse(BaseModel):
    parent_service_id: int
    child_service_id: int
    quantity: int
    child_service: ServiceCatalogResponse
    model_config = ConfigDict(from_attributes=True)


# --- PHASE 4: DIAGNOSTIC CPOE SCHEMAS ---
class DiagnosticOrderCreate(BaseModel):
    patient_id: int
    clinical_notes: Optional[str] = None
    ordered_service_ids: List[int]


class DiagnosticTaskResponse(BaseModel):
    id: int
    order_id: int
    service_id: int
    status: TaskStatusEnum
    result_value: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class DiagnosticOrderResponse(BaseModel):
    id: int
    patient_id: int
    ordering_doctor_id: int
    status: OrderStatusEnum
    clinical_notes: Optional[str] = None
    tasks: List[DiagnosticTaskResponse]
    model_config = ConfigDict(from_attributes=True)


class DiagnosticOrderUpdate(BaseModel):
    clinical_notes: Optional[str] = None
    status: Optional[OrderStatusEnum] = None


class TaskResultEntry(BaseModel):
    result_value: str
    result_notes: Optional[str] = None
    status: TaskStatusEnum = TaskStatusEnum.RESULT_ENTERED


# --- DEPARTMENT SCHEMAS ---
class DepartmentCreate(BaseModel):
    code: str
    name: str
    ndhm_speciality_code: Optional[str] = None


class DepartmentUpdate(BaseModel):
    name: Optional[str] = None
    ndhm_speciality_code: Optional[str] = None
    is_active: Optional[bool] = None


class DepartmentResponse(BaseModel):
    id: int
    code: str
    name: str
    ndhm_speciality_code: Optional[str] = None
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


# --- PHASE 5: ENCOUNTER & ADT SCHEMAS ---
class PatientEncounterCreate(BaseModel):
    patient_id: int
    encounter_type: EncounterTypeEnum
    primary_doctor_id: int
    department_id: int
    chief_complaint_notes: Optional[str] = None


class PatientEncounterResponse(BaseModel):
    id: int
    patient_id: int
    encounter_type: EncounterTypeEnum
    status: EncounterStatusEnum
    start_time: datetime
    end_time: Optional[datetime] = None
    primary_doctor_id: int
    department_id: int
    model_config = ConfigDict(from_attributes=True)


class InpatientAdmissionCreate(BaseModel):
    encounter_id: int
    admission_type: AdmissionTypeEnum
    admitting_doctor_id: int
    assigned_bed_id: int


class InpatientAdmissionResponse(BaseModel):
    id: int
    encounter_id: int
    admission_type: AdmissionTypeEnum
    current_bed_id: Optional[int]
    admitting_doctor_id: int
    discharge_time: Optional[datetime] = None
    discharge_disposition: Optional[DischargeDispositionEnum] = None
    model_config = ConfigDict(from_attributes=True)


class BedTransferRequest(BaseModel):
    admission_id: int
    new_bed_id: int
    transfer_reason: str


class BedTransferResponse(BaseModel):
    id: int
    admission_id: int
    from_bed_id: Optional[int]
    to_bed_id: int
    transfer_time: datetime
    transfer_reason: Optional[str]
    model_config = ConfigDict(from_attributes=True)


class ClinicalDischargeRequest(BaseModel):
    discharge_disposition: DischargeDispositionEnum
    discharge_summary: str


class PatientEncounterUpdate(BaseModel):
    primary_doctor_id: Optional[int] = None
    department_id: Optional[int] = None
    chief_complaint_notes: Optional[str] = None


class InpatientAdmissionUpdate(BaseModel):
    admission_type: Optional[AdmissionTypeEnum] = None
    admitting_doctor_id: Optional[int] = None


# --- PHASE 6: BILLING SCHEMAS ---
class InvoiceCreate(BaseModel):
    patient_id: int
    encounter_id: Optional[int] = None
    notes: Optional[str] = None


class InvoiceLineItemCreate(BaseModel):
    service_id: Optional[int] = None
    description: str
    quantity: int = Field(gt=0, description="Quantity must be strictly positive")
    unit_price_paise: int = Field(ge=0, description="Price cannot be negative")


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

# --- PHASE 7: OPD SCHEMAS ---
class AppointmentCreate(BaseModel):
    patient_id: int
    doctor_id: int
    department_id: int
    appointment_time: datetime
    reason_for_visit: Optional[str] = None


class AppointmentUpdate(BaseModel):
    appointment_time: Optional[datetime] = None
    reason_for_visit: Optional[str] = None


class AppointmentStatusUpdate(BaseModel):
    status: AppointmentStatusEnum
    encounter_id: Optional[int] = None


class AppointmentResponse(BaseModel):
    id: int
    patient_id: int
    doctor_id: int
    department_id: int
    appointment_time: datetime
    status: AppointmentStatusEnum
    reason_for_visit: Optional[str]
    encounter_id: Optional[int]
    model_config = ConfigDict(from_attributes=True)


# --- PHASE 8: PHARMACY SCHEMAS ---
class MedicationMasterCreate(BaseModel):
    code: str
    brand_name: str
    generic_name: str
    form: MedicationFormEnum
    strength: str


MedicationCreate = MedicationMasterCreate


class MedicationMasterUpdate(BaseModel):
    brand_name: Optional[str] = None
    generic_name: Optional[str] = None
    form: Optional[MedicationFormEnum] = None
    dosage_form: Optional[MedicationFormEnum] = None
    strength: Optional[str] = None
    is_active: Optional[bool] = None


MedicationUpdate = MedicationMasterUpdate


class MedicationMasterResponse(BaseModel):
    id: int
    code: str
    brand_name: str
    generic_name: str
    form: MedicationFormEnum
    strength: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


MedicationResponse = MedicationMasterResponse


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


DispenseRequest = PrescriptionDispenseRequest

# =====================================================================
# PHASE 9: INSURANCE, TPA, PRE-AUTH & CLAIMS SCHEMAS
# =====================================================================

class InsuranceProviderCreate(BaseModel):
    code: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=2, max_length=150)
    provider_type: InsuranceProviderTypeEnum
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None


class InsuranceProviderResponse(BaseModel):
    id: int
    code: str
    name: str
    provider_type: InsuranceProviderTypeEnum
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
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

# =====================================================================
# PHASE 9D: ADVANCE DEPOSITS & REFUND VOUCHER SCHEMAS
# =====================================================================

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

# =====================================================================
# PHASE 10: ENCOUNTER VITALS, SOAP CLINICAL NOTES & ICD-10 DIAGNOSES
# =====================================================================

class EncounterVitalCreate(BaseModel):
    temperature_f: Optional[float] = Field(default=None, ge=85.0, le=115.0)
    pulse_bpm: Optional[int] = Field(default=None, ge=20, le=300)
    bp_systolic: Optional[int] = Field(default=None, ge=40, le=300)
    bp_diastolic: Optional[int] = Field(default=None, ge=20, le=200)
    respiratory_rate: Optional[int] = Field(default=None, ge=4, le=100)
    spo2_percent: Optional[int] = Field(default=None, ge=30, le=100)
    weight_kg: Optional[float] = Field(default=None, gt=0.0, le=500.0)
    height_cm: Optional[float] = Field(default=None, gt=20.0, le=300.0)
    notes: Optional[str] = Field(default=None, max_length=500)


class EncounterVitalResponse(BaseModel):
    id: int
    encounter_id: int
    temperature_f: Optional[float] = None
    pulse_bpm: Optional[int] = None
    bp_systolic: Optional[int] = None
    bp_diastolic: Optional[int] = None
    respiratory_rate: Optional[int] = None
    spo2_percent: Optional[int] = None
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    bmi: Optional[float] = None
    notes: Optional[str] = None
    created_by_id: Optional[int] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ClinicalNoteCreate(BaseModel):
    subjective_notes: Optional[str] = None
    objective_notes: Optional[str] = None
    assessment_notes: Optional[str] = None
    plan_notes: Optional[str] = None


class ClinicalNoteUpdate(BaseModel):
    subjective_notes: Optional[str] = None
    objective_notes: Optional[str] = None
    assessment_notes: Optional[str] = None
    plan_notes: Optional[str] = None


class ClinicalNoteResponse(BaseModel):
    id: int
    encounter_id: int
    subjective_notes: Optional[str] = None
    objective_notes: Optional[str] = None
    assessment_notes: Optional[str] = None
    plan_notes: Optional[str] = None
    is_signed: bool
    signed_at: Optional[datetime] = None
    signed_by_id: Optional[int] = None
    created_by_id: Optional[int] = None
    updated_by_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EncounterDiagnosisCreate(BaseModel):
    diagnosis_id: int
    diagnosis_type: DiagnosisTypeEnum = DiagnosisTypeEnum.PRIMARY
    clinical_remarks: Optional[str] = Field(default=None, max_length=500)


class EncounterDiagnosisResponse(BaseModel):
    id: int
    encounter_id: int
    diagnosis_id: int
    diagnosis_type: DiagnosisTypeEnum
    clinical_remarks: Optional[str] = None
    created_by_id: Optional[int] = None
    diagnosis: DiagnosisResponse

    model_config = ConfigDict(from_attributes=True)

# =====================================================================
# PHASE 11: INPATIENT NURSING eMAR, FLUID I/O & ER TRIAGE / MLC SCHEMAS
# =====================================================================

class eMARCreate(BaseModel):
    prescription_item_id: int
    status: eMARStatusEnum = eMARStatusEnum.ADMINISTERED
    dose_given: str = Field(min_length=1, max_length=100)
    scheduled_time: Optional[datetime] = None
    notes: Optional[str] = Field(default=None, max_length=500)


class eMARResponse(BaseModel):
    id: int
    encounter_id: int
    prescription_item_id: int
    administered_by_id: int
    scheduled_time: Optional[datetime] = None
    administered_at: datetime
    status: eMARStatusEnum
    dose_given: str
    notes: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class FluidLogCreate(BaseModel):
    direction: FluidDirectionEnum
    fluid_type: str = Field(min_length=2, max_length=100)
    volume_ml: int = Field(gt=0, le=10000)
    notes: Optional[str] = Field(default=None, max_length=255)


class FluidLogResponse(BaseModel):
    id: int
    encounter_id: int
    recorded_by_id: int
    direction: FluidDirectionEnum
    fluid_type: str
    volume_ml: int
    recorded_at: datetime
    notes: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class FluidBalanceSummaryResponse(BaseModel):
    encounter_id: int
    total_intake_ml: int
    total_output_ml: int
    net_balance_ml: int
    logs: List[FluidLogResponse]


class ERTriageCreate(BaseModel):
    acuity_level: TriageAcuityEnum
    chief_complaint: str = Field(min_length=2, max_length=255)
    gcs_score: Optional[int] = Field(default=15, ge=3, le=15)
    pain_scale: Optional[int] = Field(default=0, ge=0, le=10)
    airway_status: Optional[str] = "CLEAR"
    breathing_status: Optional[str] = "NORMAL"
    circulation_status: Optional[str] = "STABLE"
    is_mlc_suspected: bool = False
    triage_notes: Optional[str] = None


class ERTriageResponse(BaseModel):
    id: int
    encounter_id: int
    triaged_by_id: int
    acuity_level: TriageAcuityEnum
    chief_complaint: str
    gcs_score: Optional[int] = None
    pain_scale: Optional[int] = None
    airway_status: Optional[str] = None
    breathing_status: Optional[str] = None
    circulation_status: Optional[str] = None
    is_mlc_suspected: bool
    triage_notes: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class MLCCreate(BaseModel):
    encounter_id: int
    cause_of_injury: MLCCauseEnum
    alleged_history: str = Field(min_length=5)
    identification_marks: str = Field(min_length=3, max_length=255)
    brought_by_name: str = Field(min_length=2, max_length=100)
    brought_by_relation: Optional[str] = None
    brought_by_phone: Optional[str] = None
    police_station_name: str = Field(min_length=3, max_length=150)
    police_constable_buckle_no: Optional[str] = None


class MLCPoliceIntimationRequest(BaseModel):
    police_constable_buckle_no: str = Field(min_length=2, max_length=50)
    fir_number: Optional[str] = Field(default=None, max_length=100)


class MLCResponse(BaseModel):
    id: int
    mlc_number: str
    encounter_id: int
    patient_id: int
    registered_by_doctor_id: int
    cause_of_injury: MLCCauseEnum
    alleged_history: str
    identification_marks: str
    brought_by_name: str
    brought_by_relation: Optional[str] = None
    brought_by_phone: Optional[str] = None
    police_station_name: str
    police_constable_buckle_no: Optional[str] = None
    police_intimated_at: Optional[datetime] = None
    fir_number: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# =====================================================================
# PHASE 12: OPERATION THEATRE (OT), SURGICAL SAFETY & IMPLANT SCHEMAS
# =====================================================================

class OperationTheatreCreate(BaseModel):
    code: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=2, max_length=100)
    theatre_type: OTTheatreTypeEnum
    floor_id: Optional[int] = None
    hourly_rate_paise: int = Field(default=0, ge=0)


class OperationTheatreResponse(BaseModel):
    id: int
    code: str
    name: str
    theatre_type: OTTheatreTypeEnum
    floor_id: Optional[int] = None
    hourly_rate_paise: int
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class SurgicalScheduleCreate(BaseModel):
    encounter_id: int
    theatre_id: int
    procedure_service_id: int
    lead_surgeon_id: int
    anesthetist_id: Optional[int] = None
    scheduled_start_time: datetime
    scheduled_end_time: datetime
    pre_op_diagnosis_notes: Optional[str] = None


class WHOChecklistUpsert(BaseModel):
    patient_identity_confirmed: bool = False
    site_marked: bool = False
    consent_signed: bool = False
    pulse_oximeter_functioning: bool = False
    allergy_checked: bool = False
    antibiotic_prophylaxis_given: bool = False
    sponge_instrument_count_verified: bool = False
    specimen_labeled: bool = True


class WHOChecklistResponse(BaseModel):
    id: int
    surgery_id: int
    verified_by_id: int
    patient_identity_confirmed: bool
    site_marked: bool
    consent_signed: bool
    pulse_oximeter_functioning: bool
    allergy_checked: bool
    antibiotic_prophylaxis_given: bool
    sponge_instrument_count_verified: bool
    specimen_labeled: bool
    model_config = ConfigDict(from_attributes=True)


class OperativeRecordUpsert(BaseModel):
    anesthesia_type: AnesthesiaTypeEnum
    asa_physical_status: int = Field(default=1, ge=1, le=6)
    anesthesia_notes: Optional[str] = None
    operative_findings: str = Field(min_length=3)
    post_op_instructions: Optional[str] = None
    estimated_blood_loss_ml: int = Field(default=0, ge=0)


class OperativeRecordResponse(BaseModel):
    id: int
    surgery_id: int
    anesthesia_type: AnesthesiaTypeEnum
    asa_physical_status: int
    anesthesia_notes: Optional[str] = None
    operative_findings: str
    post_op_instructions: Optional[str] = None
    estimated_blood_loss_ml: int
    model_config = ConfigDict(from_attributes=True)


class SurgicalImplantCreate(BaseModel):
    implant_name: str = Field(min_length=2, max_length=150)
    manufacturer: Optional[str] = None
    serial_or_batch_number: str = Field(min_length=2, max_length=100)
    quantity: int = Field(default=1, gt=0)
    unit_price_paise: int = Field(ge=0)


class SurgicalImplantResponse(BaseModel):
    id: int
    surgery_id: int
    implant_name: str
    manufacturer: Optional[str] = None
    serial_or_batch_number: str
    quantity: int
    unit_price_paise: int
    model_config = ConfigDict(from_attributes=True)


class SurgeryStatusUpdateRequest(BaseModel):
    status: SurgeryStatusEnum


class SurgicalScheduleResponse(BaseModel):
    id: int
    case_number: str
    encounter_id: int
    patient_id: int
    theatre_id: int
    procedure_service_id: int
    lead_surgeon_id: int
    anesthetist_id: Optional[int] = None
    scheduled_start_time: datetime
    scheduled_end_time: datetime
    actual_start_time: Optional[datetime] = None
    actual_end_time: Optional[datetime] = None
    status: SurgeryStatusEnum
    pre_op_diagnosis_notes: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

# =====================================================================
# PHASE 13: BLOOD BANK TRANSFUSION & RADIOLOGY PACS/DICOM SCHEMAS
# =====================================================================

class BloodBagCreate(BaseModel):
    bag_number: str = Field(min_length=3, max_length=50)
    component_type: BloodComponentTypeEnum
    blood_group: BloodGroupEnum
    volume_ml: int = Field(gt=0, le=1000)
    collection_date: datetime
    expiry_date: datetime
    processing_fee_paise: int = Field(default=0, ge=0)


class BloodBagResponse(BaseModel):
    id: int
    bag_number: str
    component_type: BloodComponentTypeEnum
    blood_group: BloodGroupEnum
    volume_ml: int
    collection_date: datetime
    expiry_date: datetime
    processing_fee_paise: int
    status: BloodBagStatusEnum
    model_config = ConfigDict(from_attributes=True)


class BloodCrossMatchCreate(BaseModel):
    encounter_id: int
    blood_bag_id: int
    ordered_by_doctor_id: int


class TransfusionReactionRequest(BaseModel):
    reaction_type: TransfusionReactionTypeEnum
    reaction_notes: str = Field(min_length=5, max_length=500)


class BloodCrossMatchResponse(BaseModel):
    id: int
    encounter_id: int
    patient_id: int
    blood_bag_id: int
    ordered_by_doctor_id: int
    cross_matched_by_id: int
    issued_at: Optional[datetime] = None
    issued_by_id: Optional[int] = None
    reaction_type: TransfusionReactionTypeEnum
    reaction_notes: Optional[str] = None
    blood_bag: BloodBagResponse
    model_config = ConfigDict(from_attributes=True)


class RadiologyStudyUpsert(BaseModel):
    study_instance_uid: str = Field(min_length=5, max_length=128)
    accession_number: str = Field(min_length=3, max_length=64)
    modality: DicomModalityEnum
    pacs_viewer_url: str = Field(min_length=5, max_length=500)
    radiation_dose_mgy: Optional[float] = Field(default=None, ge=0.0)
    radiologist_impression: Optional[str] = None


class RadiologyStudyResponse(BaseModel):
    id: int
    diagnostic_task_id: int
    study_instance_uid: str
    accession_number: str
    modality: DicomModalityEnum
    pacs_viewer_url: str
    radiation_dose_mgy: Optional[float] = None
    radiologist_impression: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)