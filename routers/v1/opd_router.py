from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from dependencies import RequirePermission, validate_pagination
from models import (
    Appointment,
    AppointmentStatusEnum,
    DoctorLeave,
    DoctorProfile,
    Patient,
    PatientEncounter,
    User,
)
from schemas.v1.schemas import (
    AppointmentCreate,
    AppointmentResponse,
    AppointmentStatusUpdate,
    AppointmentUpdate,
    DoctorLeaveCreate,
    DoctorLeaveResponse,
    PaginatedResponse,
)
from services.auto_billing import auto_bill_opd_consultation

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
    existing_appts = list(
        db.execute(
            select(Appointment).where(Appointment.doctor_id == doctor_id)
        )
        .scalars()
        .all()
    )
    same_day_count = sum(
        1 for a in existing_appts if day_start <= _to_utc(a.appointment_time) < day_end
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
    _: User = Depends(RequirePermission("opd:write")),
):
    """Schedules a new outpatient appointment, checks doctor leave blackouts, prevents overlap, and assigns a daily queue token."""
    patient = db.get(Patient, payload.patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=400, detail="Invalid or inactive patient.")

    doctor = db.query(DoctorProfile).filter(DoctorProfile.user_id == payload.doctor_id).first()
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
        status=AppointmentStatusEnum.SCHEDULED,
        token_number=token_num,
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    return appointment


@router.get("/appointments", response_model=PaginatedResponse[AppointmentResponse])
def list_appointments(
    doctor_id: Optional[int] = None,
    patient_id: Optional[int] = None,
    date_start: Optional[datetime] = None,
    date_end: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:read")),
):
    page, page_size = validate_pagination(page, page_size)
    query = db.query(Appointment)

    if doctor_id:
        query = query.filter(Appointment.doctor_id == doctor_id)
    if patient_id:
        query = query.filter(Appointment.patient_id == patient_id)
    if date_start:
        query = query.filter(Appointment.appointment_time >= date_start)
    if date_end:
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
        duration = max(doctor.consultation_duration_minutes, 5) if doctor else 15

        if check_schedule_overlap(
            db,
            appointment.doctor_id,
            payload.appointment_time,
            duration,
            exclude_appointment_id=appointment.id,
        ):
            raise HTTPException(status_code=409, detail="Scheduling conflict at the new requested time.")
        appointment.appointment_time = payload.appointment_time

    if payload.reason_for_visit is not None:
        appointment.reason_for_visit = payload.reason_for_visit

    db.commit()
    db.refresh(appointment)
    return appointment


@router.patch("/appointments/{appointment_id}/status", response_model=AppointmentResponse)
def update_appointment_status(
    appointment_id: int,
    payload: AppointmentStatusUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:write")),
):
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")

    valid_transitions = {
        AppointmentStatusEnum.SCHEDULED: [AppointmentStatusEnum.ARRIVED, AppointmentStatusEnum.CANCELLED, AppointmentStatusEnum.NO_SHOW],
        AppointmentStatusEnum.ARRIVED: [AppointmentStatusEnum.IN_CONSULTATION, AppointmentStatusEnum.CANCELLED],
        AppointmentStatusEnum.IN_CONSULTATION: [AppointmentStatusEnum.COMPLETED],
        AppointmentStatusEnum.COMPLETED: [],
        AppointmentStatusEnum.CANCELLED: [],
        AppointmentStatusEnum.NO_SHOW: [],
    }

    if payload.status not in valid_transitions[appointment.status]:
        raise HTTPException(status_code=400, detail=f"Invalid transition from {appointment.status} to {payload.status}.")

    if payload.status == AppointmentStatusEnum.IN_CONSULTATION:
        if not payload.encounter_id:
            raise HTTPException(status_code=400, detail="Must attach an active Encounter ID.")

        encounter = db.get(PatientEncounter, payload.encounter_id)
        if not encounter or encounter.patient_id != appointment.patient_id:
            raise HTTPException(status_code=400, detail="Encounter does not match the appointment patient.")
        appointment.encounter_id = payload.encounter_id
        auto_bill_opd_consultation(db, appointment.patient_id, encounter.id, appointment.doctor_id)

    appointment.status = payload.status
    db.commit()
    db.refresh(appointment)
    return appointment


@router.delete("/appointments/{appointment_id}", status_code=200)
def cancel_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:cancel")),
):
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")

    if appointment.status in [AppointmentStatusEnum.COMPLETED, AppointmentStatusEnum.CANCELLED]:
        raise HTTPException(status_code=400, detail="Appointment is already finalized or cancelled.")

    appointment.status = AppointmentStatusEnum.CANCELLED
    db.commit()
    return {"status": "success", "message": f"Appointment {appointment_id} cancelled successfully."}