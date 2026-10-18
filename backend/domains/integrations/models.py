from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.audit import AuditMixin
from backend.core.database import Base


class ProtocolEnum(str, PyEnum):
    HL7_V2 = "HL7_V2"
    DICOM = "DICOM"


class EquipmentMaster(AuditMixin, Base):
    """Registry of physical laboratory analyzers and imaging modalities."""

    __tablename__ = "equipment_master"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    manufacturer: Mapped[str] = mapped_column(String(100), nullable=False)
    device_model: Mapped[str] = mapped_column(String(100), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(50), nullable=False)
    protocol: Mapped[ProtocolEnum] = mapped_column(Enum(ProtocolEnum), nullable=False)
    is_bidirectional: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)

    department: Mapped["Department"] = relationship()


class HL7IntegrationLog(AuditMixin, Base):
    """Audit trail of raw HL7/DICOM payloads received from machines."""

    __tablename__ = "hl7_integration_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    equipment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("equipment_master.id")
    )
    message_control_id: Mapped[str] = mapped_column(String(100), index=True)
    raw_payload: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_order_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    error_details: Mapped[Optional[str]] = mapped_column(Text)

    equipment: Mapped[Optional["EquipmentMaster"]] = relationship()
