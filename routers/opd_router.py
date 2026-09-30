from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select

from database import get_db
from dependencies import RequirePermission, get_current_user
from models import User, Appointment, AppointmentStatusEnum, Patient, Department
from schemas import (
    PaginatedResponse, AppointmentCreate, AppointmentResponse, 
    AppointmentUpdate, AppointmentStatusUpdate
)

router = APIRouter(prefix="/api/opd", tags=["Outpatient Scheduling (OPD)"])

# -----------------------------------------------------------------------------
# Appointment Booking & Retrieval
# -----------------------------------------------------------------------------

from datetime import timedelta
from models import DoctorProfile

@router.post("/appointments", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
def book_appointment(payload: AppointmentCreate, db: Session = Depends(get_db), _: User = Depends(RequirePermission("manage_clinical_mdm"))):
    """Schedules a new outpatient appointment and prevents doctor double-booking dynamically."""
    patient = db.get(Patient, payload.patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=400, detail="Invalid or inactive patient.")
        
    doctor_profile = db.query(DoctorProfile).filter(DoctorProfile.user_id == payload.doctor_id).first()
    if not doctor_profile:
        raise HTTPException(status_code=400, detail="Invalid doctor ID or user lacks a clinical profile.")

    # --- DYNAMIC OVERLAP CHECK ---
    duration = doctor_profile.consultation_duration_minutes
    
    # An overlap occurs if an existing appointment starts strictly AFTER (New - Duration) 
    # AND strictly BEFORE (New + Duration). 
    # Strict inequalities allow perfect back-to-back booking.
    time_lower_bound = payload.appointment_time - timedelta(minutes=duration)
    time_upper_bound = payload.appointment_time + timedelta(minutes=duration)
    
    overlap = db.query(Appointment).filter(
        Appointment.doctor_id == payload.doctor_id,
        Appointment.status.in_([AppointmentStatusEnum.SCHEDULED, AppointmentStatusEnum.ARRIVED]),
        Appointment.appointment_time > time_lower_bound,
        Appointment.appointment_time < time_upper_bound
    ).first()
    
    if overlap:
        raise HTTPException(
            status_code=409, 
            detail=f"Scheduling conflict: Doctor has a {duration}-minute block already booked at {overlap.appointment_time}."
        )

    appointment = Appointment(
        patient_id=payload.patient_id,
        doctor_id=payload.doctor_id,
        department_id=payload.department_id,
        appointment_time=payload.appointment_time,
        reason_for_visit=payload.reason_for_visit,
        status=AppointmentStatusEnum.SCHEDULED
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    return appointment

@router.get("/appointments", response_model=PaginatedResponse[AppointmentResponse])
def list_appointments(doctor_id: Optional[int] = None, patient_id: Optional[int] = None, date_start: Optional[datetime] = None, date_end: Optional[datetime] = None, page: int = 1, page_size: int = 50, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Retrieves appointments, filterable by doctor, patient, or date range."""
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
def get_appointment(appointment_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Fetches details of a specific appointment."""
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return appointment

# -----------------------------------------------------------------------------
# Modifications & Status Handoffs
# -----------------------------------------------------------------------------

@router.put("/appointments/{appointment_id}", response_model=AppointmentResponse)
def reschedule_appointment(appointment_id: int, payload: AppointmentUpdate, db: Session = Depends(get_db), _: User = Depends(RequirePermission("manage_clinical_mdm"))):
    """Reschedules an appointment or updates the visit reasoning."""
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")
        
    if appointment.status not in [AppointmentStatusEnum.SCHEDULED, AppointmentStatusEnum.ARRIVED]:
        raise HTTPException(status_code=400, detail="Cannot reschedule an appointment that is already in progress or completed.")

    if payload.appointment_time is not None:
        appointment.appointment_time = payload.appointment_time
    if payload.reason_for_visit is not None:
        appointment.reason_for_visit = payload.reason_for_visit

    db.commit()
    db.refresh(appointment)
    return appointment

@router.patch("/appointments/{appointment_id}/status", response_model=AppointmentResponse)
def update_appointment_status(appointment_id: int, payload: AppointmentStatusUpdate, db: Session = Depends(get_db), _: User = Depends(RequirePermission("manage_clinical_mdm"))):
    """Advances the patient through the waiting room pipeline (ARRIVED -> IN_CONSULTATION -> COMPLETED)."""
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")

    if payload.status == AppointmentStatusEnum.IN_CONSULTATION and not payload.encounter_id:
        raise HTTPException(status_code=400, detail="Must attach an active Encounter ID when moving patient to IN_CONSULTATION.")

    appointment.status = payload.status
    if payload.encounter_id is not None:
        appointment.encounter_id = payload.encounter_id

    db.commit()
    db.refresh(appointment)
    return appointment

@router.delete("/appointments/{appointment_id}", status_code=200)
def cancel_appointment(appointment_id: int, db: Session = Depends(get_db), _: User = Depends(RequirePermission("manage_clinical_mdm"))):
    """Cancels a scheduled appointment."""
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found.")
        
    if appointment.status in [AppointmentStatusEnum.COMPLETED, AppointmentStatusEnum.CANCELLED]:
        raise HTTPException(status_code=400, detail="Appointment is already finalized or cancelled.")

    appointment.status = AppointmentStatusEnum.CANCELLED
    db.commit()
    return {"status": "success", "message": f"Appointment {appointment_id} cancelled successfully."}