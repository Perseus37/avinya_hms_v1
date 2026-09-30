"""Pydantic schemas for the Avinya HMS API.

Schemas are grouped by the same business domains used in the routes so request
and response payloads stay easy to follow and maintain.
"""

from pydantic import BaseModel, EmailStr, ConfigDict
from datetime import datetime
from typing import Optional, Generic, TypeVar, List
from models import (
    GenderEnum,
    UserStatusEnum,
    WardTypeEnum,
    RoomTypeEnum,
    BedTypeEnum,
    BedStatusEnum,
    AllergyCategoryEnum,
    AllergySeverityEnum,
    BodySystemEnum,
    ShiftTypeEnum,
    ServiceTypeEnum, 
    OrderStatusEnum, 
    TaskStatusEnum
)
# -----------------------------------------------------------------------------
# Shared generic schemas
# -----------------------------------------------------------------------------
T = TypeVar("T")

class PaginatedResponse(BaseModel, Generic[T]):
    success: bool = True
    total: int
    page: int
    page_size: int
    items: List[T]

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
    consultation_duration_minutes: int = 15  # Default fallback

class UserCreate(BaseModel):
    login_id: str
    email: EmailStr
    password: str
    full_name: str
    phone_number: Optional[str] = None
    gender: GenderEnum = GenderEnum.OTHER
    role_ids: List[int]
    doctor_profile: Optional[DoctorProfileCreate] = None


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


class WardResponse(BaseModel):
    id: int
    code: str
    name: str
    ward_type: WardTypeEnum
    model_config = ConfigDict(from_attributes=True)


class FloorResponse(BaseModel):
    id: int
    floor_number: int
    name: str
    is_active: bool
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


# -----------------------------------------------------------------------------
# Patient and clinical schemas
# -----------------------------------------------------------------------------
class PatientCreate(BaseModel):
    mrn: str
    full_name: str
    date_of_birth: datetime
    gender: GenderEnum


class PatientUpdate(BaseModel):
    full_name: Optional[str] = None
    date_of_birth: Optional[datetime] = None
    gender: Optional[GenderEnum] = None
    is_active: Optional[bool] = None


class PatientResponse(BaseModel):
    id: int
    mrn: str
    full_name: str
    date_of_birth: datetime
    gender: GenderEnum
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class PatientAllergyCreate(BaseModel):
    patient_id: int
    allergy_id: int
    severity: AllergySeverityEnum
    reaction_description: Optional[str] = None


class AllergyMasterCreate(BaseModel):
    department_id: int
    code: str
    name: str
    category: AllergyCategoryEnum
    default_severity: AllergySeverityEnum


class AllergyCheckRequest(BaseModel):
    patient_id: int
    allergen_keywords: List[str]


class TaxonomyResponse(BaseModel):
    items: List[dict]


class ChiefComplaintCreate(BaseModel):
    department_id: int
    code: str
    name: str


class ComorbidityCreate(BaseModel):
    department_id: int
    code: str
    name: str
    icd10_code: Optional[str] = None


class DiagnosisCreate(BaseModel):
    department_id: int
    icd10_code: str
    short_description: str
    full_description: str
    is_billable: bool = True
    parent_diagnosis_id: Optional[int] = None


class PhysicalExamCreate(BaseModel):
    department_id: int
    body_system: BodySystemEnum
    code: str
    name: str

# --- PHASE 4: CATALOG & MDM SCHEMAS ---
class ServiceCategoryCreate(BaseModel):
    department_id: int
    code: str
    name: str

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
    
class ServiceCategoryUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None

class ServiceCatalogUpdate(BaseModel):
    name: Optional[str] = None
    base_price_paise: Optional[int] = None
    is_active: Optional[bool] = None

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
    """
    The engine will resolve this list: if an ID is an 'order_set', 
    it will explode it into child IDs. If it is a direct test, it creates a task.
    """

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
    ndhm_speciality_code: Optional[str]
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

# --- ROLE & PERMISSION SCHEMAS ---
class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None

class PermissionResponse(BaseModel):
    id: int
    code: str
    description: Optional[str]
    module: str
    model_config = ConfigDict(from_attributes=True)

# --- USER SCHEMAS ---
class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    phone_number: Optional[str] = None
    gender: Optional[GenderEnum] = None

class PatientAllergyResponse(BaseModel):
    id: int
    allergy_id: int
    severity: AllergySeverityEnum
    reaction_description: Optional[str]
    is_active: bool
    allergy: AllergyMasterCreate # Nests the master data details
    model_config = ConfigDict(from_attributes=True)

from models import EncounterTypeEnum, EncounterStatusEnum, AdmissionTypeEnum, DischargeDispositionEnum

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

from models import InvoiceStatusEnum, PaymentMethodEnum

# --- PHASE 6: BILLING SCHEMAS ---
class InvoiceCreate(BaseModel):
    patient_id: int
    encounter_id: Optional[int] = None
    notes: Optional[str] = None

class InvoiceLineItemCreate(BaseModel):
    service_id: Optional[int] = None
    description: str
    quantity: int = 1
    unit_price_paise: int

class InvoiceUpdate(BaseModel):
    notes: Optional[str] = None

class PaymentReceiptCreate(BaseModel):
    amount_paid_paise: int
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
    amount_paid_paise: int
    line_items: List[InvoiceLineItemResponse]
    payments: List[PaymentReceiptResponse]
    model_config = ConfigDict(from_attributes=True)

from models import AppointmentStatusEnum

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
    encounter_id: Optional[int] = None # Attached when status moves to IN_CONSULTATION

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

from models import MedicationFormEnum, PrescriptionStatusEnum

# --- PHASE 8: PHARMACY SCHEMAS ---
class MedicationMasterCreate(BaseModel):
    code: str
    brand_name: str
    generic_name: str
    form: MedicationFormEnum
    strength: str

class MedicationMasterUpdate(BaseModel):
    brand_name: Optional[str] = None
    generic_name: Optional[str] = None
    is_active: Optional[bool] = None

class InventoryBatchCreate(BaseModel):
    medication_id: int
    batch_number: str
    expiry_date: datetime
    stock_quantity: int
    unit_price_paise: int

class InventoryStockAdjustment(BaseModel):
    quantity_adjustment: int  # Negative for write-offs, positive for restocks

class PrescriptionItemCreate(BaseModel):
    medication_id: int
    dosage_instruction: str
    requested_quantity: int

class PrescriptionCreate(BaseModel):
    encounter_id: int
    clinical_notes: Optional[str] = None
    items: List[PrescriptionItemCreate]

class MedicationMasterResponse(BaseModel):
    id: int
    code: str
    brand_name: str
    generic_name: str
    form: MedicationFormEnum
    strength: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

class InventoryBatchResponse(BaseModel):
    id: int
    medication_id: int
    batch_number: str
    expiry_date: datetime
    stock_quantity: int
    unit_price_paise: int
    medication: MedicationMasterResponse
    model_config = ConfigDict(from_attributes=True)

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
    quantity_to_dispense: int

class PrescriptionDispenseRequest(BaseModel):
    items: List[DispenseItem]

class InventoryBatchUpdate(BaseModel):
    batch_number: Optional[str] = None
    expiry_date: Optional[datetime] = None
    unit_price_paise: Optional[int] = None