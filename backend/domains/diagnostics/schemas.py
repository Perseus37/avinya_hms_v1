"""Pydantic schemas owned by the Avinya HMS domain."""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, computed_field

from backend.domains.diagnostics.models import OrderStatusEnum, ServiceTypeEnum, TaskStatusEnum

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
    result_notes: Optional[str] = None
    is_critical_panic: bool = False
    rejection_reason: Optional[str] = None
    delta_check_flag: Optional[str] = None
    previous_result_value: Optional[str] = None
    delta_percentage: Optional[float] = None
    model_config = ConfigDict(from_attributes=True)

    @computed_field
    @property
    def is_preliminary(self) -> bool:
        return self.status == TaskStatusEnum.IN_PROGRESS and bool(self.result_value)


class LabDeltaCheckConfigCreate(BaseModel):
    service_id: int
    parameter_name: str
    max_absolute_delta: Optional[float] = None
    max_percentage_delta: Optional[float] = None
    lookback_hours: int = 48
    severity: str = "WARNING"


class LabDeltaCheckConfigResponse(BaseModel):
    id: int
    service_id: int
    parameter_name: str
    max_absolute_delta: Optional[float] = None
    max_percentage_delta: Optional[float] = None
    lookback_hours: int
    severity: str
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
    result_value: Optional[str] = None
    result_notes: Optional[str] = None
    status: TaskStatusEnum = TaskStatusEnum.RESULT_ENTERED
    is_critical_panic: bool = False
    rejection_reason: Optional[str] = None
