from pydantic import BaseModel

from backend.domains.integrations.models import ProtocolEnum


class EquipmentCreate(BaseModel):
    department_id: int
    name: str
    manufacturer: str
    device_model: str
    ip_address: str
    protocol: ProtocolEnum
    is_bidirectional: bool = False
