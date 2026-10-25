from backend.domains.service_ops.biomedical_waste_router import bmw_router
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.dependencies import RequirePermission
from backend.domains.identity.models import User
from backend.domains.patients.models import Patient
from backend.domains.service_ops.models import (
    AmbulanceDispatch,
    AmbulanceVehicle,
    CssdInstrumentSet,
    CssdSterilizationBatch,
    DietaryOrder,
    MortuaryCase,
    ServiceWorkflowEvent,
)
from backend.domains.privacy.runtime import evaluate_tenant_patient_data_uses

ambulance_router = APIRouter(prefix="/api/ambulance", tags=["Ambulance Operations"])
dietary_router = APIRouter(prefix="/api/dietary", tags=["Dietary Services"])
cssd_router = APIRouter(prefix="/api/cssd", tags=["Central Sterile Services"])
mortuary_router = APIRouter(prefix="/api/mortuary", tags=["Mortuary Services"])


def _clean_text(value: str, minimum: int = 1) -> str:
    value = value.strip()
    if len(value) < minimum:
        raise ValueError(f"Value must contain at least {minimum} non-space characters.")
    return value


class VehicleCreate(BaseModel):
    registration_number: str = Field(min_length=3, max_length=20)
    call_sign: str = Field(min_length=2, max_length=30)
    vehicle_type: Literal["BLS", "ALS", "PATIENT_TRANSPORT", "NEONATAL"]

    @field_validator("registration_number", "call_sign")
    @classmethod
    def strip_identifiers(cls, value: str) -> str:
        return _clean_text(value)


class DispatchCreate(BaseModel):
    patient_id: Optional[int] = Field(default=None, gt=0)
    vehicle_id: int = Field(gt=0)
    pickup_location: str = Field(min_length=2, max_length=255)
    destination: str = Field(min_length=2, max_length=255)
    acuity: Literal["ROUTINE", "URGENT", "EMERGENCY"]
    notes: Optional[str] = Field(default=None, max_length=1000)


class TransitionRequest(BaseModel):
    status: str
    note: Optional[str] = Field(default=None, max_length=1000)


class DietaryOrderCreate(BaseModel):
    patient_id: int = Field(gt=0)
    diet_type: str = Field(min_length=2, max_length=50)
    meal: Literal["BREAKFAST", "LUNCH", "DINNER", "SNACK"]
    scheduled_for: datetime
    allergy_notes: Optional[str] = Field(default=None, max_length=1000)
    instructions: Optional[str] = Field(default=None, max_length=1000)


class InstrumentSetCreate(BaseModel):
    code: str = Field(min_length=2, max_length=30)
    name: str = Field(min_length=2, max_length=100)


class SterilizationBatchCreate(BaseModel):
    batch_number: str = Field(min_length=3, max_length=40)
    instrument_set_id: int = Field(gt=0)
    sterilizer_id: str = Field(min_length=2, max_length=40)
    cycle_type: Literal["STEAM", "ETO", "PLASMA", "OTHER"]


class SterilizationCycleResult(BaseModel):
    chemical_indicator_passed: bool
    biological_indicator_passed: bool
    notes: Optional[str] = Field(default=None, max_length=1000)


class MortuaryReceipt(BaseModel):
    patient_id: Optional[int] = Field(default=None, gt=0)
    case_reference: str = Field(min_length=3, max_length=40)
    deceased_name: str = Field(min_length=2, max_length=100)
    source_department: Optional[str] = Field(default=None, max_length=100)
    custody_location: str = Field(min_length=2, max_length=100)


class MortuaryRelease(BaseModel):
    recipient_name: str = Field(min_length=2, max_length=100)
    identity_verified: bool
    witness_name: str = Field(min_length=2, max_length=100)


def _record_event(
    db: Session,
    *,
    domain: str,
    record_id: int,
    actor: User,
    from_status: Optional[str],
    to_status: str,
    note: Optional[str] = None,
) -> None:
    db.add(
        ServiceWorkflowEvent(
            domain=domain,
            record_id=record_id,
            actor_user_id=actor.id,
            from_status=from_status,
            to_status=to_status,
            note=note.strip() if note else None,
        )
    )


def _list_events(db: Session, domain: str, record_id: int) -> list[dict]:
    events = db.execute(
        select(ServiceWorkflowEvent)
        .where(
            ServiceWorkflowEvent.domain == domain,
            ServiceWorkflowEvent.record_id == record_id,
        )
        .order_by(ServiceWorkflowEvent.id)
    ).scalars()
    return [
        {
            "id": event.id,
            "from_status": event.from_status,
            "to_status": event.to_status,
            "actor_user_id": event.actor_user_id,
            "note": event.note,
            "occurred_at": event.occurred_at,
        }
        for event in events
    ]


@ambulance_router.post("/vehicles", status_code=status.HTTP_201_CREATED)
def create_ambulance_vehicle(
    payload: VehicleCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("ambulance:write")),
):
    vehicle = AmbulanceVehicle(
        registration_number=payload.registration_number.upper(),
        call_sign=payload.call_sign.upper(),
        vehicle_type=payload.vehicle_type,
    )
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return vehicle


@ambulance_router.get("/vehicles")
def list_ambulance_vehicles(
    active_only: bool = True,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("ambulance:read")),
):
    statement = select(AmbulanceVehicle).order_by(AmbulanceVehicle.call_sign)
    if active_only:
        statement = statement.where(AmbulanceVehicle.is_active.is_(True))
    return {"items": db.execute(statement).scalars().all()}


@ambulance_router.post("/dispatches", status_code=status.HTTP_201_CREATED)
def dispatch_ambulance(
    payload: DispatchCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("ambulance:write")),
):
    vehicle = db.execute(
        select(AmbulanceVehicle)
        .where(AmbulanceVehicle.id == payload.vehicle_id)
        .with_for_update()
    ).scalar_one_or_none()
    if not vehicle or not vehicle.is_active:
        raise HTTPException(status_code=404, detail="Active ambulance vehicle not found.")
    if payload.patient_id:
        patient = db.get(Patient, payload.patient_id)
        if not patient or not patient.is_active:
            raise HTTPException(status_code=404, detail="Active patient not found.")
    active_dispatch = db.execute(
        select(AmbulanceDispatch.id).where(
            AmbulanceDispatch.vehicle_id == vehicle.id,
            AmbulanceDispatch.status.in_(("DISPATCHED", "ARRIVED")),
        )
    ).scalar_one_or_none()
    if active_dispatch:
        raise HTTPException(status_code=409, detail="Vehicle already has an active dispatch.")
    dispatch = AmbulanceDispatch(
        patient_id=payload.patient_id,
        vehicle_id=vehicle.id,
        pickup_location=payload.pickup_location.strip(),
        destination=payload.destination.strip(),
        acuity=payload.acuity,
        notes=payload.notes.strip() if payload.notes else None,
        status="DISPATCHED",
    )
    db.add(dispatch)
    db.flush()
    _record_event(
        db,
        domain="ambulance",
        record_id=dispatch.id,
        actor=actor,
        from_status=None,
        to_status="DISPATCHED",
    )
    db.commit()
    db.refresh(dispatch)
    return dispatch


@ambulance_router.get(
    "/dispatches",
    openapi_extra={"x-privacy-data-use-point": "ambulance.dispatches.read"},
)
def list_ambulance_dispatches(
    dispatch_status: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("ambulance:read")),
):
    statement = select(AmbulanceDispatch).order_by(AmbulanceDispatch.id.desc())
    if dispatch_status:
        statement = statement.where(AmbulanceDispatch.status == dispatch_status.upper())
    items = db.execute(statement.limit(limit)).scalars().all()
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="ambulance.dispatches.read",
        patient_ids=[item.patient_id for item in items if item.patient_id is not None],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit one or more patient disclosures.")
    return {"items": items}


@ambulance_router.patch("/dispatches/{dispatch_id}/status")
def transition_ambulance_dispatch(
    dispatch_id: int,
    payload: TransitionRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("ambulance:write")),
):
    dispatch = db.execute(
        select(AmbulanceDispatch)
        .where(AmbulanceDispatch.id == dispatch_id)
        .with_for_update()
    ).scalar_one_or_none()
    if not dispatch:
        raise HTTPException(status_code=404, detail="Ambulance dispatch not found.")
    allowed = {
        "DISPATCHED": {"ARRIVED", "CANCELLED"},
        "ARRIVED": {"COMPLETED", "CANCELLED"},
    }
    new_status = payload.status.upper()
    if new_status not in allowed.get(dispatch.status, set()):
        raise HTTPException(status_code=409, detail="Invalid ambulance dispatch status transition.")
    old_status = dispatch.status
    dispatch.status = new_status
    now = datetime.now(timezone.utc)
    if new_status == "ARRIVED":
        dispatch.arrived_at = now
    if new_status in {"COMPLETED", "CANCELLED"}:
        dispatch.completed_at = now
    _record_event(
        db,
        domain="ambulance",
        record_id=dispatch.id,
        actor=actor,
        from_status=old_status,
        to_status=new_status,
        note=payload.note,
    )
    db.commit()
    db.refresh(dispatch)
    return dispatch


@ambulance_router.get(
    "/dispatches/{dispatch_id}/history",
    openapi_extra={"x-privacy-data-use-point": "ambulance.dispatch.history.read"},
)
def ambulance_dispatch_history(
    dispatch_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("ambulance:read")),
):
    dispatch = db.get(AmbulanceDispatch, dispatch_id)
    if not dispatch:
        raise HTTPException(status_code=404, detail="Ambulance dispatch not found.")
    if dispatch.patient_id is not None:
        decisions = evaluate_tenant_patient_data_uses(
            db,
            data_use_point="ambulance.dispatch.history.read",
            patient_ids=[dispatch.patient_id],
            actor_user_id=actor.id,
        )
        if any(not decision.allowed for decision in decisions):
            raise HTTPException(status_code=403, detail="Privacy policy does not permit this patient disclosure.")
    return {"items": _list_events(db, "ambulance", dispatch_id)}


@dietary_router.post("/orders", status_code=status.HTTP_201_CREATED)
def create_dietary_order(
    payload: DietaryOrderCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("dietary:write")),
):
    patient = db.get(Patient, payload.patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=404, detail="Active patient not found.")
    scheduled = payload.scheduled_for
    if scheduled.tzinfo is None:
        raise HTTPException(status_code=422, detail="scheduled_for must include a timezone.")
    order = DietaryOrder(
        patient_id=patient.id,
        diet_type=payload.diet_type.strip(),
        meal=payload.meal,
        scheduled_for=scheduled,
        allergy_notes=payload.allergy_notes.strip() if payload.allergy_notes else None,
        instructions=payload.instructions.strip() if payload.instructions else None,
        status="ORDERED",
    )
    db.add(order)
    db.flush()
    _record_event(
        db, domain="dietary", record_id=order.id, actor=actor,
        from_status=None, to_status="ORDERED",
    )
    db.commit()
    db.refresh(order)
    return order


@dietary_router.get(
    "/orders",
    openapi_extra={"x-privacy-data-use-point": "dietary.orders.read"},
)
def list_dietary_orders(
    order_status: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("dietary:read")),
):
    statement = select(DietaryOrder).order_by(DietaryOrder.scheduled_for, DietaryOrder.id)
    if order_status:
        statement = statement.where(DietaryOrder.status == order_status.upper())
    items = db.execute(statement.limit(limit)).scalars().all()
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="dietary.orders.read",
        patient_ids=[item.patient_id for item in items],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit one or more patient disclosures.")
    return {"items": items}


@dietary_router.patch("/orders/{order_id}/status")
def transition_dietary_order(
    order_id: int,
    payload: TransitionRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("dietary:write")),
):
    order = db.execute(
        select(DietaryOrder).where(DietaryOrder.id == order_id).with_for_update()
    ).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Dietary order not found.")
    allowed = {"ORDERED": {"PREPARED", "CANCELLED"}, "PREPARED": {"DELIVERED", "CANCELLED"}}
    new_status = payload.status.upper()
    if new_status not in allowed.get(order.status, set()):
        raise HTTPException(status_code=409, detail="Invalid dietary order status transition.")
    old_status = order.status
    order.status = new_status
    now = datetime.now(timezone.utc)
    if new_status == "PREPARED":
        order.prepared_at = now
    elif new_status == "DELIVERED":
        order.delivered_at = now
    _record_event(
        db, domain="dietary", record_id=order.id, actor=actor,
        from_status=old_status, to_status=new_status, note=payload.note,
    )
    db.commit()
    db.refresh(order)
    return order


@dietary_router.get(
    "/orders/{order_id}/history",
    openapi_extra={"x-privacy-data-use-point": "dietary.order.history.read"},
)
def dietary_order_history(
    order_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("dietary:read")),
):
    order = db.get(DietaryOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Dietary order not found.")
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="dietary.order.history.read",
        patient_ids=[order.patient_id],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit this patient disclosure.")
    return {"items": _list_events(db, "dietary", order_id)}


@cssd_router.post("/instrument-sets", status_code=status.HTTP_201_CREATED)
def create_cssd_instrument_set(
    payload: InstrumentSetCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("cssd:write")),
):
    item = CssdInstrumentSet(code=payload.code.strip().upper(), name=payload.name.strip())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@cssd_router.get("/instrument-sets")
def list_cssd_instrument_sets(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("cssd:read")),
):
    return {
        "items": db.execute(
            select(CssdInstrumentSet).where(CssdInstrumentSet.is_active.is_(True))
            .order_by(CssdInstrumentSet.code)
        ).scalars().all()
    }


@cssd_router.post("/batches", status_code=status.HTTP_201_CREATED)
def create_cssd_batch(
    payload: SterilizationBatchCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("cssd:write")),
):
    instrument_set = db.get(CssdInstrumentSet, payload.instrument_set_id)
    if not instrument_set or not instrument_set.is_active:
        raise HTTPException(status_code=404, detail="Active instrument set not found.")
    batch = CssdSterilizationBatch(
        batch_number=payload.batch_number.strip().upper(),
        instrument_set_id=instrument_set.id,
        sterilizer_id=payload.sterilizer_id.strip(),
        cycle_type=payload.cycle_type,
        status="LOADED",
    )
    db.add(batch)
    db.flush()
    _record_event(
        db, domain="cssd", record_id=batch.id, actor=actor,
        from_status=None, to_status="LOADED",
    )
    db.commit()
    db.refresh(batch)
    return batch


@cssd_router.post("/batches/{batch_id}/complete-cycle")
def complete_cssd_cycle(
    batch_id: int,
    payload: SterilizationCycleResult,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("cssd:write")),
):
    batch = db.execute(
        select(CssdSterilizationBatch)
        .where(CssdSterilizationBatch.id == batch_id)
        .with_for_update()
    ).scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=404, detail="CSSD batch not found.")
    if batch.status != "LOADED":
        raise HTTPException(status_code=409, detail="CSSD cycle was already completed.")
    old_status = batch.status
    batch.chemical_indicator_passed = payload.chemical_indicator_passed
    batch.biological_indicator_passed = payload.biological_indicator_passed
    batch.cycle_completed_at = datetime.now(timezone.utc)
    batch.status = (
        "RELEASED"
        if payload.chemical_indicator_passed and payload.biological_indicator_passed
        else "QUARANTINED"
    )
    batch.release_notes = payload.notes.strip() if payload.notes else None
    _record_event(
        db, domain="cssd", record_id=batch.id, actor=actor,
        from_status=old_status, to_status=batch.status, note=payload.notes,
    )
    db.commit()
    db.refresh(batch)
    return batch


@cssd_router.get("/batches")
def list_cssd_batches(
    batch_status: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("cssd:read")),
):
    statement = select(CssdSterilizationBatch).order_by(CssdSterilizationBatch.id.desc())
    if batch_status:
        statement = statement.where(CssdSterilizationBatch.status == batch_status.upper())
    return {"items": db.execute(statement.limit(limit)).scalars().all()}


@mortuary_router.post("/cases", status_code=status.HTTP_201_CREATED)
def receive_mortuary_case(
    payload: MortuaryReceipt,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("mortuary:write")),
):
    if payload.patient_id:
        patient = db.get(Patient, payload.patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Linked patient not found.")
    case = MortuaryCase(
        patient_id=payload.patient_id,
        case_reference=payload.case_reference.strip().upper(),
        deceased_name=payload.deceased_name.strip(),
        source_department=payload.source_department.strip() if payload.source_department else None,
        custody_location=payload.custody_location.strip(),
        status="RECEIVED",
    )
    db.add(case)
    db.flush()
    _record_event(
        db, domain="mortuary", record_id=case.id, actor=actor,
        from_status=None, to_status="RECEIVED",
    )
    db.commit()
    db.refresh(case)
    return case


@mortuary_router.get(
    "/cases",
    openapi_extra={"x-privacy-data-use-point": "mortuary.cases.read"},
)
def list_mortuary_cases(
    case_status: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("mortuary:read")),
):
    statement = select(MortuaryCase).order_by(MortuaryCase.id.desc())
    if case_status:
        statement = statement.where(MortuaryCase.status == case_status.upper())
    items = db.execute(statement.limit(limit)).scalars().all()
    decisions = evaluate_tenant_patient_data_uses(
        db,
        data_use_point="mortuary.cases.read",
        patient_ids=[item.patient_id for item in items if item.patient_id is not None],
        actor_user_id=actor.id,
    )
    if any(not decision.allowed for decision in decisions):
        raise HTTPException(status_code=403, detail="Privacy policy does not permit one or more patient disclosures.")
    return {"items": items}


@mortuary_router.post("/cases/{case_id}/release")
def release_mortuary_case(
    case_id: int,
    payload: MortuaryRelease,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("mortuary:write")),
):
    case = db.execute(
        select(MortuaryCase).where(MortuaryCase.id == case_id).with_for_update()
    ).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Mortuary case not found.")
    if case.status != "RECEIVED":
        raise HTTPException(status_code=409, detail="Mortuary case is not awaiting release.")
    if payload.identity_verified is not True:
        raise HTTPException(status_code=422, detail="Recipient identity must be verified.")
    case.status = "RELEASED"
    case.released_at = datetime.now(timezone.utc)
    case.release_recipient = payload.recipient_name.strip()
    case.release_identity_verified = True
    case.release_witness = payload.witness_name.strip()
    _record_event(
        db, domain="mortuary", record_id=case.id, actor=actor,
        from_status="RECEIVED", to_status="RELEASED",
    )
    db.commit()
    db.refresh(case)
    return case


@mortuary_router.get(
    "/cases/{case_id}/history",
    openapi_extra={"x-privacy-data-use-point": "mortuary.case.history.read"},
)
def mortuary_case_history(
    case_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("mortuary:read")),
):
    case = db.get(MortuaryCase, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Mortuary case not found.")
    if case.patient_id is not None:
        decisions = evaluate_tenant_patient_data_uses(
            db,
            data_use_point="mortuary.case.history.read",
            patient_ids=[case.patient_id],
            actor_user_id=actor.id,
        )
        if any(not decision.allowed for decision in decisions):
            raise HTTPException(status_code=403, detail="Privacy policy does not permit this patient disclosure.")
    return {"items": _list_events(db, "mortuary", case_id)}
