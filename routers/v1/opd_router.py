from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies import RequirePermission, validate_pagination
from models import (
    Appointment,
    AppointmentStatusEnum,
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
    PaginatedResponse,
)

router = APIRouter(prefix="/api/opd", tags=["Outpatient Scheduling (OPD)"])


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


@router.post("/appointments", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
def book_appointment(
    payload: AppointmentCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:write")),
):
    """Schedules a new outpatient appointment and prevents doctor double-booking dynamically."""
    patient = db.get(Patient, payload.patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=400, detail="Invalid or inactive patient.")

    doctor = db.query(DoctorProfile).filter(DoctorProfile.user_id == payload.doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=400, detail="Invalid doctor ID.")

    duration = max(doctor.consultation_duration_minutes, 5)
    if check_schedule_overlap(db, payload.doctor_id, payload.appointment_time, duration):
        raise HTTPException(status_code=409, detail=f"Scheduling conflict detected at {payload.appointment_time}.")

    appointment = Appointment(**payload.model_dump(), status=AppointmentStatusEnum.SCHEDULED)
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
    """Retrieves appointments, filterable by doctor, patient, or date range."""
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
    """Reschedules an appointment or updates the visit reasoning."""
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")

    if appointment.status not in [AppointmentStatusEnum.SCHEDULED, AppointmentStatusEnum.ARRIVED]:
        raise HTTPException(status_code=400, detail="Cannot reschedule an appointment that is already in progress or completed.")

    if payload.appointment_time is not None:
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
    """Advances the patient through the waiting room pipeline (ARRIVED -> IN_CONSULTATION -> COMPLETED)."""
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
    """Cancels a scheduled appointment."""
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")

    if appointment.status in [AppointmentStatusEnum.COMPLETED, AppointmentStatusEnum.CANCELLED]:
        raise HTTPException(status_code=400, detail="Appointment is already finalized or cancelled.")

    appointment.status = AppointmentStatusEnum.CANCELLED
    db.commit()
    return {"status": "success", "message": f"Appointment {appointment_id} cancelled successfully."}