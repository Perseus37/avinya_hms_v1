from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.audit import AuditMixin
from backend.core.database import Base


class AmbulanceVehicle(AuditMixin, Base):
    __tablename__ = "ambulance_vehicles"
    __table_args__ = (
        UniqueConstraint("registration_number", name="uq_ambulance_registration"),
        UniqueConstraint("call_sign", name="uq_ambulance_call_sign"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    registration_number: Mapped[str] = mapped_column(String(20), nullable=False)
    call_sign: Mapped[str] = mapped_column(String(30), nullable=False)
    vehicle_type: Mapped[str] = mapped_column(String(30), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)


class AmbulanceDispatch(AuditMixin, Base):
    __tablename__ = "ambulance_dispatches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patients.id"))
    vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("ambulance_vehicles.id"), nullable=False, index=True
    )
    pickup_location: Mapped[str] = mapped_column(String(255), nullable=False)
    destination: Mapped[str] = mapped_column(String(255), nullable=False)
    acuity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DISPATCHED")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    dispatched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    arrived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class DietaryOrder(AuditMixin, Base):
    __tablename__ = "dietary_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    diet_type: Mapped[str] = mapped_column(String(50), nullable=False)
    meal: Mapped[str] = mapped_column(String(20), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    allergy_notes: Mapped[Optional[str]] = mapped_column(Text)
    instructions: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ORDERED")
    prepared_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class CssdInstrumentSet(AuditMixin, Base):
    __tablename__ = "cssd_instrument_sets"
    __table_args__ = (UniqueConstraint("code", name="uq_cssd_instrument_set_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)


class CssdSterilizationBatch(AuditMixin, Base):
    __tablename__ = "cssd_sterilization_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_number: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    instrument_set_id: Mapped[int] = mapped_column(
        ForeignKey("cssd_instrument_sets.id"), nullable=False, index=True
    )
    sterilizer_id: Mapped[str] = mapped_column(String(40), nullable=False)
    cycle_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="LOADED")
    cycle_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    cycle_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    chemical_indicator_passed: Mapped[Optional[bool]]
    biological_indicator_passed: Mapped[Optional[bool]]
    release_notes: Mapped[Optional[str]] = mapped_column(Text)


class MortuaryCase(AuditMixin, Base):
    __tablename__ = "mortuary_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[Optional[int]] = mapped_column(ForeignKey("patients.id"), index=True)
    case_reference: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    deceased_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_department: Mapped[Optional[str]] = mapped_column(String(100))
    custody_location: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="RECEIVED")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    release_recipient: Mapped[Optional[str]] = mapped_column(String(100))
    release_identity_verified: Mapped[Optional[bool]]
    release_witness: Mapped[Optional[str]] = mapped_column(String(100))


class ServiceWorkflowEvent(Base):
    __tablename__ = "service_workflow_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    record_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
