from datetime import date, datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select,func
from sqlalchemy.orm import Session

from backend.domains.policies.router import get_or_create_policy
from backend.core.database import get_db, resolve_tenant_context
from backend.core.dependencies import RequirePermission, validate_pagination
from backend.core.date_ranges import resolve_record_date_window
from backend.domains.encounters.models import Appointment, AppointmentStatusEnum, AppointmentStatusHistory, AppointmentWaitlistEntry, AppointmentWaitlistHistory, AppointmentWaitlistStatusEnum, DoctorLeave, PatientEncounter, ServiceDemandReasonEnum, ServiceDemandRequest, ServiceDemandStatusEnum, ServiceDemandStatusHistory
from backend.domains.identity.models import DoctorProfile, User
from backend.domains.patients.models import Patient
from backend.schemas.common import PaginatedResponse
from backend.domains.encounters.schemas import AppointmentCreate, AppointmentResponse, AppointmentStatusHistoryResponse, AppointmentStatusUpdate, AppointmentUpdate, AppointmentWaitlistBook, AppointmentWaitlistCreate, AppointmentWaitlistHistoryResponse, AppointmentWaitlistResponse, DoctorLeaveCreate, DoctorLeaveResponse, ServiceDemandCreate, ServiceDemandResponse, ServiceDemandStatusHistoryResponse, ServiceDemandUpdate
from backend.domains.billing.services import auto_bill_opd_consultation
from backend.domains.diagnostics.models import ServiceCatalog
from backend.domains.identity.models import Department
from backend.core.event_bus import publish_tenant_event

router = APIRouter(prefix="/api/opd", tags=["Outpatient Scheduling (OPD), Tokens & Doctor Leaves"])


def _to_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def check_doctor_on_leave(db: Session, doctor_id: int, appointment_time: datetime) -> Optional[DoctorLeave]:
    appt_utc = _to_utc(appointment_time)
    leaves = list(
        db.execute(
            select(DoctorLeave).where(
                DoctorLeave.doctor_id == doctor_id,
                DoctorLeave.is_active == True,
            )
        )
        .scalars()
        .all()
    )
    for lv in leaves:
        if _to_utc(lv.start_time) <= appt_utc <= _to_utc(lv.end_time):
            return lv
    return None


def check_schedule_overlap(
    db: Session,
    doctor_id: int,
    appointment_time: datetime,
    duration: int,
    exclude_appointment_id: Optional[int] = None,
) -> bool:
    """Helper to detect scheduling conflicts, optionally excluding the appointment being rescheduled."""
    time_lower_bound = appointment_time - timedelta(minutes=duration)
    time_upper_bound = appointment_time + timedelta(minutes=duration)

    query = db.query(Appointment).filter(
        Appointment.doctor_id == doctor_id,
        Appointment.status.in_([AppointmentStatusEnum.SCHEDULED, AppointmentStatusEnum.ARRIVED]),
        Appointment.appointment_time > time_lower_bound,
        Appointment.appointment_time < time_upper_bound,
    )
    if exclude_appointment_id is not None:
        query = query.filter(Appointment.id != exclude_appointment_id)

    return query.first() is not None


def _compute_next_daily_token(db: Session, doctor_id: int, appointment_time: datetime) -> int:
    appt_utc = _to_utc(appointment_time)
    day_start = appt_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    same_day_count = (
        db.scalar(
            select(func.count(Appointment.id)).where(
                Appointment.doctor_id == doctor_id,
                Appointment.appointment_time >= day_start,
                Appointment.appointment_time < day_end,
            )
        )
        or 0
    )
    return same_day_count + 1


@router.post("/doctors/leaves", response_model=DoctorLeaveResponse, status_code=status.HTTP_201_CREATED)
def register_doctor_leave_blackout(
    payload: DoctorLeaveCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:write", "iam:write")),
):
    if _to_utc(payload.end_time) <= _to_utc(payload.start_time):
        raise HTTPException(status_code=400, detail="Leave end_time must be after start_time.")

    leave = DoctorLeave(**payload.model_dump(), is_active=True)
    db.add(leave)
    db.commit()
    db.refresh(leave)
    return leave


@router.get("/doctors/{doctor_id}/leaves", response_model=List[DoctorLeaveResponse])
def list_doctor_leaves(
    doctor_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:read")),
):
    stmt = (
        select(DoctorLeave)
        .where(DoctorLeave.doctor_id == doctor_id, DoctorLeave.is_active == True)
        .order_by(DoctorLeave.start_time.asc())
    )
    return list(db.execute(stmt).scalars().all())


@router.post("/appointments", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
def book_appointment(
    payload: AppointmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("opd:write")),
):
    """Schedules a new outpatient appointment, checks doctor leave blackouts, prevents overlap, and assigns a daily queue token."""
    patient = db.get(Patient, payload.patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=400, detail="Invalid or inactive patient.")

    # ARCH-003: Lock the doctor profile row using FOR UPDATE to serialize concurrent bookings
    from sqlalchemy import select
    doctor = db.execute(
        select(DoctorProfile)
        .where(DoctorProfile.user_id == payload.doctor_id)
        .with_for_update()
    ).scalar_one_or_none()

    if not doctor:
        raise HTTPException(status_code=400, detail="Invalid doctor ID.")

    active_leave = check_doctor_on_leave(db, payload.doctor_id, payload.appointment_time)
    if active_leave:
        raise HTTPException(
            status_code=409,
            detail=f"Doctor is unavailable / on leave during this window ({active_leave.reason}).",
        )

    duration = max(doctor.consultation_duration_minutes, 5)
    if check_schedule_overlap(db, payload.doctor_id, payload.appointment_time, duration):
        raise HTTPException(status_code=409, detail=f"Scheduling conflict detected at {payload.appointment_time}.")

    token_num = _compute_next_daily_token(db, payload.doctor_id, payload.appointment_time)
    appointment = Appointment(
        **payload.model_dump(),
        planned_duration_minutes=duration,
        status=AppointmentStatusEnum.SCHEDULED,
        token_number=token_num,
    )
    db.add(appointment)
    db.flush()
    db.add(
        AppointmentStatusHistory(
            appointment_id=appointment.id,
            from_status=None,
            to_status=AppointmentStatusEnum.SCHEDULED.value,
            changed_by_id=current_user.id,
            reason="Appointment booked",
        )
    )
    db.commit()
    db.refresh(appointment)
    return appointment


@router.get("/appointments", response_model=PaginatedResponse[AppointmentResponse])
def list_appointments(
    doctor_id: Optional[int] = None,
    patient_id: Optional[int] = None,
    date_start: Optional[datetime] = None,
    date_end: Optional[datetime] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:read")),
):
    page, page_size = validate_pagination(page, page_size)
    window_start, window_end = resolve_record_date_window(start_date, end_date)
    if window_start is not None and (date_start is not None or date_end is not None):
        raise HTTPException(
            status_code=422,
            detail="Use either start_date/end_date or date_start/date_end, not both.",
        )
    if date_start and date_end and _to_utc(date_start) > _to_utc(date_end):
        raise HTTPException(status_code=422, detail="date_start must be on or before date_end.")
    if date_start and date_end and _to_utc(date_end) - _to_utc(date_start) > timedelta(days=366):
        raise HTTPException(status_code=422, detail="Appointment date windows cannot exceed 366 days.")
    query = db.query(Appointment)

    if doctor_id:
        query = query.filter(Appointment.doctor_id == doctor_id)
    if patient_id:
        query = query.filter(Appointment.patient_id == patient_id)
    if window_start is not None and window_end is not None:
        query = query.filter(
            Appointment.appointment_time >= window_start,
            Appointment.appointment_time < window_end,
        )
    elif date_start and date_end:
        query = query.filter(Appointment.appointment_time >= date_start)
        query = query.filter(Appointment.appointment_time <= date_end)

    query = query.order_by(Appointment.appointment_time.asc())
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/appointments/{appointment_id}", response_model=AppointmentResponse)
def get_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:read")),
):
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return appointment


@router.put("/appointments/{appointment_id}", response_model=AppointmentResponse)
def reschedule_appointment(
    appointment_id: int,
    payload: AppointmentUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:write")),
):
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")

    if appointment.status not in [AppointmentStatusEnum.SCHEDULED, AppointmentStatusEnum.ARRIVED]:
        raise HTTPException(status_code=400, detail="Cannot reschedule an appointment that is already in progress or completed.")

    if payload.appointment_time is not None:
        active_leave = check_doctor_on_leave(db, appointment.doctor_id, payload.appointment_time)
        if active_leave:
            raise HTTPException(
                status_code=409,
                detail=f"Doctor is unavailable / on leave during this window ({active_leave.reason}).",
            )

        doctor = db.query(DoctorProfile).filter(DoctorProfile.user_id == appointment.doctor_id).first()
        default_dur = get_or_create_policy(db).default_consultation_duration_minutes
        duration = max(doctor.consultation_duration_minutes or default_dur, 5) if doctor else default_dur

        if check_schedule_overlap(
            db,
            appointment.doctor_id,
            payload.appointment_time,
            duration,
            exclude_appointment_id=appointment.id,
        ):
            raise HTTPException(status_code=409, detail="Scheduling conflict at the new requested time.")
        appointment.appointment_time = payload.appointment_time
        if doctor is not None:
            appointment.planned_duration_minutes = max(
                doctor.consultation_duration_minutes or default_dur, 5
            )

    if payload.reason_for_visit is not None:
        appointment.reason_for_visit = payload.reason_for_visit

    db.commit()
    db.refresh(appointment)
    return appointment

@router.patch("/appointments/{appointment_id}/status", response_model=AppointmentResponse)
def update_appointment_status(
    appointment_id: int,
    payload: AppointmentStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("opd:write")),
):
    appointment = db.execute(
        select(Appointment)
        .where(Appointment.id == appointment_id)
        .with_for_update()
    ).scalar_one_or_none()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")

    valid_transitions = {
        AppointmentStatusEnum.SCHEDULED: [
            AppointmentStatusEnum.ARRIVED,
            AppointmentStatusEnum.CANCELLED,
            AppointmentStatusEnum.NO_SHOW,
        ],
        AppointmentStatusEnum.ARRIVED: [
            AppointmentStatusEnum.IN_CONSULTATION,
            AppointmentStatusEnum.CANCELLED,
        ],
        AppointmentStatusEnum.IN_CONSULTATION: [AppointmentStatusEnum.COMPLETED],
        AppointmentStatusEnum.COMPLETED: [],
        AppointmentStatusEnum.CANCELLED: [],
        AppointmentStatusEnum.NO_SHOW: [],
    }

    if payload.status not in valid_transitions[appointment.status]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid transition from {appointment.status} to {payload.status}.",
        )

    if payload.status == AppointmentStatusEnum.IN_CONSULTATION:
        if not payload.encounter_id:
            raise HTTPException(status_code=400, detail="Must attach an active Encounter ID.")
        encounter = db.get(PatientEncounter, payload.encounter_id)
        if not encounter or encounter.patient_id != appointment.patient_id:
            raise HTTPException(status_code=400, detail="Encounter does not match the appointment patient.")
        appointment.encounter_id = payload.encounter_id
        auto_bill_opd_consultation(db, appointment.patient_id, encounter.id, appointment.doctor_id)

    previous_status = appointment.status
    appointment.status = payload.status
    db.add(
        AppointmentStatusHistory(
            appointment_id=appointment.id,
            from_status=previous_status.value,
            to_status=payload.status.value,
            changed_by_id=current_user.id,
            reason=payload.reason,
        )
    )
    db.commit()
    db.refresh(appointment)

    if payload.status == AppointmentStatusEnum.IN_CONSULTATION:
        tenant = resolve_tenant_context(request)
        doctor = appointment.doctor
        publish_tenant_event(
            tenant_slug=tenant.subdomain_slug,
            stream="waiting_room",
            event_type="TOKEN_CALLED_IN_CONSULTATION",
            payload={
                "appointment_id": appointment.id,
                "token_number": appointment.token_number,
                "doctor_id": appointment.doctor_id,
                "doctor_name": doctor.full_name if doctor else "Consulting Doctor",
                "department_id": appointment.department_id,
            },
        )

    return appointment

@router.delete("/appointments/{appointment_id}", status_code=200)
def cancel_appointment(
    appointment_id: int,
    reason: Optional[str] = Query(default=None, max_length=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("opd:cancel")),
):
    appointment = db.execute(
        select(Appointment)
        .where(Appointment.id == appointment_id)
        .with_for_update()
    ).scalar_one_or_none()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")

    if appointment.status in [AppointmentStatusEnum.COMPLETED, AppointmentStatusEnum.CANCELLED]:
        raise HTTPException(status_code=400, detail="Appointment is already finalized or cancelled.")

    previous_status = appointment.status
    appointment.status = AppointmentStatusEnum.CANCELLED
    db.add(
        AppointmentStatusHistory(
            appointment_id=appointment.id,
            from_status=previous_status.value,
            to_status=AppointmentStatusEnum.CANCELLED.value,
            changed_by_id=current_user.id,
            reason=reason or "Cancelled by authorized user",
        )
    )
    db.commit()
    return {"status": "success", "message": f"Appointment {appointment_id} cancelled successfully."}


@router.get(
    "/appointments/{appointment_id}/status-history",
    response_model=list[AppointmentStatusHistoryResponse],
)
def list_appointment_status_history(
    appointment_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:read")),
):
    if db.get(Appointment, appointment_id) is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return list(
        db.execute(
            select(AppointmentStatusHistory)
            .where(AppointmentStatusHistory.appointment_id == appointment_id)
            .order_by(
                AppointmentStatusHistory.changed_at.asc(),
                AppointmentStatusHistory.id.asc(),
            )
        )
        .scalars()
        .all()
    )


@router.post(
    "/appointment-waitlist",
    response_model=AppointmentWaitlistResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_appointment_waitlist_entry(
    payload: AppointmentWaitlistCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("opd:write")),
):
    patient = db.get(Patient, payload.patient_id)
    if patient is None or not patient.is_active:
        raise HTTPException(status_code=404, detail="Active patient not found.")
    if db.get(Department, payload.department_id) is None:
        raise HTTPException(status_code=404, detail="Department not found.")
    doctor = db.execute(
        select(DoctorProfile).where(DoctorProfile.user_id == payload.doctor_id)
    ).scalar_one_or_none()
    if doctor is None:
        raise HTTPException(status_code=404, detail="Doctor not found.")

    entry = AppointmentWaitlistEntry(
        **payload.model_dump(),
        status=AppointmentWaitlistStatusEnum.WAITING,
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
    )
    db.add(entry)
    db.flush()
    db.add(
        AppointmentWaitlistHistory(
            waitlist_entry_id=entry.id,
            from_status=None,
            to_status=AppointmentWaitlistStatusEnum.WAITING.value,
            changed_by_id=current_user.id,
            reason="Patient added to appointment waitlist",
        )
    )
    db.commit()
    db.refresh(entry)
    return entry


@router.get(
    "/appointment-waitlist",
    response_model=PaginatedResponse[AppointmentWaitlistResponse],
)
def list_appointment_waitlist(
    doctor_id: Optional[int] = None,
    patient_id: Optional[int] = None,
    request_status: Optional[AppointmentWaitlistStatusEnum] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(AppointmentWaitlistEntry)
    if doctor_id is not None:
        query = query.filter(AppointmentWaitlistEntry.doctor_id == doctor_id)
    if patient_id is not None:
        query = query.filter(AppointmentWaitlistEntry.patient_id == patient_id)
    if request_status is not None:
        query = query.filter(AppointmentWaitlistEntry.status == request_status)
    query = query.order_by(
        AppointmentWaitlistEntry.requested_start_at.asc(),
        AppointmentWaitlistEntry.created_at.asc(),
        AppointmentWaitlistEntry.id.asc(),
    )
    total = query.count()
    entries = query.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": entries,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post(
    "/appointment-waitlist/{entry_id}/book",
    response_model=AppointmentResponse,
)
def book_appointment_from_waitlist(
    entry_id: int,
    payload: AppointmentWaitlistBook,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("opd:write")),
):
    entry = db.execute(
        select(AppointmentWaitlistEntry)
        .where(AppointmentWaitlistEntry.id == entry_id)
        .with_for_update()
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Waitlist entry not found.")
    if entry.status != AppointmentWaitlistStatusEnum.WAITING:
        raise HTTPException(
            status_code=409,
            detail="Only a waiting entry can be booked.",
        )
    appointment_time = _to_utc(payload.appointment_time)
    if not (
        _to_utc(entry.requested_start_at)
        <= appointment_time
        <= _to_utc(entry.requested_end_at)
    ):
        raise HTTPException(
            status_code=422,
            detail="appointment_time must fall within the requested waitlist window.",
        )
    patient = db.get(Patient, entry.patient_id)
    if patient is None or not patient.is_active:
        raise HTTPException(status_code=409, detail="Waitlist patient is no longer active.")
    doctor = db.execute(
        select(DoctorProfile)
        .where(DoctorProfile.user_id == entry.doctor_id)
        .with_for_update()
    ).scalar_one_or_none()
    if doctor is None:
        raise HTTPException(status_code=409, detail="Waitlist doctor is no longer available.")
    active_leave = check_doctor_on_leave(db, entry.doctor_id, appointment_time)
    if active_leave:
        raise HTTPException(
            status_code=409,
            detail=f"Doctor is unavailable / on leave during this window ({active_leave.reason}).",
        )
    duration = max(doctor.consultation_duration_minutes, 5)
    if check_schedule_overlap(db, entry.doctor_id, appointment_time, duration):
        raise HTTPException(
            status_code=409,
            detail="Scheduling conflict detected at the requested appointment time.",
        )

    appointment = Appointment(
        patient_id=entry.patient_id,
        doctor_id=entry.doctor_id,
        department_id=entry.department_id,
        appointment_time=appointment_time,
        reason_for_visit=entry.reason_for_visit,
        planned_duration_minutes=max(doctor.consultation_duration_minutes, 5),
        status=AppointmentStatusEnum.SCHEDULED,
        token_number=_compute_next_daily_token(db, entry.doctor_id, appointment_time),
        created_by_id=current_user.id,
        updated_by_id=current_user.id,
    )
    db.add(appointment)
    db.flush()
    db.add(
        AppointmentStatusHistory(
            appointment_id=appointment.id,
            from_status=None,
            to_status=AppointmentStatusEnum.SCHEDULED.value,
            changed_by_id=current_user.id,
            reason=f"Booked from appointment waitlist entry {entry.id}",
        )
    )
    entry.status = AppointmentWaitlistStatusEnum.BOOKED
    entry.appointment_id = appointment.id
    entry.updated_by_id = current_user.id
    db.add(
        AppointmentWaitlistHistory(
            waitlist_entry_id=entry.id,
            from_status=AppointmentWaitlistStatusEnum.WAITING.value,
            to_status=AppointmentWaitlistStatusEnum.BOOKED.value,
            changed_by_id=current_user.id,
            reason=f"Appointment {appointment.id} booked",
        )
    )
    db.commit()
    db.refresh(appointment)
    return appointment


@router.post(
    "/appointment-waitlist/{entry_id}/cancel",
    response_model=AppointmentWaitlistResponse,
)
def cancel_appointment_waitlist_entry(
    entry_id: int,
    reason: Optional[str] = Query(default=None, max_length=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("opd:write")),
):
    entry = db.execute(
        select(AppointmentWaitlistEntry)
        .where(AppointmentWaitlistEntry.id == entry_id)
        .with_for_update()
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Waitlist entry not found.")
    if entry.status != AppointmentWaitlistStatusEnum.WAITING:
        raise HTTPException(
            status_code=409,
            detail="Only a waiting entry can be cancelled.",
        )
    entry.status = AppointmentWaitlistStatusEnum.CANCELLED
    entry.updated_by_id = current_user.id
    db.add(
        AppointmentWaitlistHistory(
            waitlist_entry_id=entry.id,
            from_status=AppointmentWaitlistStatusEnum.WAITING.value,
            to_status=AppointmentWaitlistStatusEnum.CANCELLED.value,
            changed_by_id=current_user.id,
            reason=reason,
        )
    )
    db.commit()
    db.refresh(entry)
    return entry


@router.get(
    "/appointment-waitlist/{entry_id}/history",
    response_model=list[AppointmentWaitlistHistoryResponse],
)
def list_appointment_waitlist_history(
    entry_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:read")),
):
    if db.get(AppointmentWaitlistEntry, entry_id) is None:
        raise HTTPException(status_code=404, detail="Waitlist entry not found.")
    return list(
        db.execute(
            select(AppointmentWaitlistHistory)
            .where(AppointmentWaitlistHistory.waitlist_entry_id == entry_id)
            .order_by(
                AppointmentWaitlistHistory.changed_at.asc(),
                AppointmentWaitlistHistory.id.asc(),
            )
        )
        .scalars()
        .all()
    )


@router.post(
    "/service-demand",
    response_model=ServiceDemandResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_service_demand_request(
    payload: ServiceDemandCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        RequirePermission("opd:write", "diagnostics:write")
    ),
):
    if payload.patient_id is not None:
        patient = db.get(Patient, payload.patient_id)
        if not patient or not patient.is_active:
            raise HTTPException(status_code=404, detail="Active patient not found.")
    if payload.department_id is not None and db.get(
        Department, payload.department_id
    ) is None:
        raise HTTPException(status_code=404, detail="Department not found.")
    if payload.service_id is not None:
        service = db.get(ServiceCatalog, payload.service_id)
        if not service or not service.is_active:
            raise HTTPException(status_code=404, detail="Active service not found.")
    demand = ServiceDemandRequest(
        **payload.model_dump(),
        status=ServiceDemandStatusEnum.OPEN,
        requested_by_id=current_user.id,
    )
    db.add(demand)
    db.flush()
    db.add(
        ServiceDemandStatusHistory(
            request_id=demand.id,
            from_status=None,
            to_status=ServiceDemandStatusEnum.OPEN.value,
            changed_by_id=current_user.id,
            reason="Service demand recorded",
        )
    )
    db.commit()
    db.refresh(demand)
    return demand


@router.get(
    "/service-demand",
    response_model=PaginatedResponse[ServiceDemandResponse],
)
def list_service_demand_requests(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    department_id: Optional[int] = None,
    reason: Optional[ServiceDemandReasonEnum] = None,
    request_status: Optional[ServiceDemandStatusEnum] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:read", "diagnostics:read")),
):
    page, page_size = validate_pagination(page, page_size)
    start_at, end_at = resolve_record_date_window(start_date, end_date)
    query = db.query(ServiceDemandRequest)
    if start_at is not None and end_at is not None:
        query = query.filter(
            ServiceDemandRequest.created_at >= start_at,
            ServiceDemandRequest.created_at < end_at,
        )
    if department_id is not None:
        query = query.filter(ServiceDemandRequest.department_id == department_id)
    if reason is not None:
        query = query.filter(ServiceDemandRequest.reason == reason)
    if request_status is not None:
        query = query.filter(ServiceDemandRequest.status == request_status)
    query = query.order_by(
        ServiceDemandRequest.created_at.desc(), ServiceDemandRequest.id.desc()
    )
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.patch(
    "/service-demand/{demand_id}",
    response_model=ServiceDemandResponse,
)
def update_service_demand_request(
    demand_id: int,
    payload: ServiceDemandUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        RequirePermission("opd:write", "diagnostics:write")
    ),
):
    demand = db.execute(
        select(ServiceDemandRequest)
        .where(ServiceDemandRequest.id == demand_id)
        .with_for_update()
    ).scalar_one_or_none()
    if demand is None:
        raise HTTPException(status_code=404, detail="Service demand request not found.")
    transitions = {
        ServiceDemandStatusEnum.OPEN: {
            ServiceDemandStatusEnum.REFERRED_OUT,
            ServiceDemandStatusEnum.SERVICE_AVAILABLE,
            ServiceDemandStatusEnum.RESOLVED,
            ServiceDemandStatusEnum.CLOSED,
        },
        ServiceDemandStatusEnum.REFERRED_OUT: {
            ServiceDemandStatusEnum.RESOLVED,
            ServiceDemandStatusEnum.CLOSED,
        },
        ServiceDemandStatusEnum.SERVICE_AVAILABLE: {
            ServiceDemandStatusEnum.RESOLVED,
            ServiceDemandStatusEnum.CLOSED,
        },
        ServiceDemandStatusEnum.RESOLVED: {ServiceDemandStatusEnum.CLOSED},
        ServiceDemandStatusEnum.CLOSED: set(),
    }
    if payload.status != demand.status and payload.status not in transitions[demand.status]:
        raise HTTPException(
            status_code=409,
            detail=f"Invalid service-demand transition from {demand.status.value} to {payload.status.value}.",
        )
    if (
        payload.status == ServiceDemandStatusEnum.REFERRED_OUT
        and not (
            payload.external_facility_name
            or demand.external_facility_name
        )
    ):
        raise HTTPException(
            status_code=422,
            detail="external_facility_name is required when referring the request out.",
        )
    previous_status = demand.status
    demand.status = payload.status
    if "external_facility_name" in payload.model_fields_set:
        demand.external_facility_name = payload.external_facility_name
    if "resolution_notes" in payload.model_fields_set:
        demand.resolution_notes = payload.resolution_notes
    if previous_status != payload.status:
        if payload.status in {
            ServiceDemandStatusEnum.RESOLVED,
            ServiceDemandStatusEnum.CLOSED,
        }:
            demand.resolved_at = datetime.now(timezone.utc)
        else:
            demand.resolved_at = None
    demand.updated_by_id = current_user.id
    if previous_status != payload.status:
        db.add(
            ServiceDemandStatusHistory(
                request_id=demand.id,
                from_status=previous_status.value,
                to_status=payload.status.value,
                changed_by_id=current_user.id,
                reason=payload.reason,
            )
        )
    db.commit()
    db.refresh(demand)
    return demand


@router.get(
    "/service-demand/{demand_id}/history",
    response_model=list[ServiceDemandStatusHistoryResponse],
)
def list_service_demand_status_history(
    demand_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:read", "diagnostics:read")),
):
    if db.get(ServiceDemandRequest, demand_id) is None:
        raise HTTPException(
            status_code=404, detail="Service demand request not found."
        )
    return list(
        db.execute(
            select(ServiceDemandStatusHistory)
            .where(ServiceDemandStatusHistory.request_id == demand_id)
            .order_by(
                ServiceDemandStatusHistory.changed_at.asc(),
                ServiceDemandStatusHistory.id.asc(),
            )
        )
        .scalars()
        .all()
    )