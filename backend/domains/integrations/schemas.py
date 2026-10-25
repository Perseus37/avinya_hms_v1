from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict

from backend.domains.integrations.models import ProtocolEnum


class EquipmentCreate(BaseModel):
    department_id: int
    name: str
    manufacturer: str
    device_model: str
    ip_address: str
    protocol: ProtocolEnum
    is_bidirectional: bool = False


class EquipmentResponse(EquipmentCreate):
    id: int
    is_active: bool
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class EquipmentTestMappingCreate(BaseModel):
    equipment_id: int
    analyzer_test_code: str
    analyzer_test_name: Optional[str] = None
    service_id: int
    units: Optional[str] = None
    is_active: bool = True


class EquipmentTestMappingResponse(EquipmentTestMappingCreate):
    id: int
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class HL7MessageIngestRequest(BaseModel):
    raw_message: str
    equipment_id: Optional[int] = None


class HL7ObservationDetail(BaseModel):
    set_id: str
    test_code: str
    test_name: Optional[str] = None
    value: str
    units: Optional[str] = None
    reference_range: Optional[str] = None
    abnormal_flag: Optional[str] = None
    status: Optional[str] = None


class HL7TaskResult(BaseModel):
    task_id: int
    service_code: Optional[str] = None
    status: str
    is_critical_panic: bool
    delta_check_flag: Optional[str] = None
    result_value: Optional[str] = None


class HL7MessageIngestResponse(BaseModel):
    message_control_id: str
    status: str
    ack_hl7: str
    matched_tasks_count: int
    panic_alerts_count: int
    tasks: List[HL7TaskResult] = []
    error_details: Optional[str] = None
