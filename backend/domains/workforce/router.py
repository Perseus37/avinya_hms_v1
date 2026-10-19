import calendar
import json
from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.dependencies import RequirePermission
from backend.domains.identity.models import User, UserStatusEnum
from backend.domains.workforce.models import (
    AttendanceRecord,
    LeaveRequest,
    PayrollCalculationEvent,
    PayrollLine,
    PayrollPeriod,
    SalaryProfile,
)

router = APIRouter(prefix="/api/workforce", tags=["Workforce"])


class AttendanceCreate(BaseModel):
    user_id: int = Field(gt=0)
    work_date: date
    status: Literal["PRESENT", "HALF_DAY", "ABSENT", "ON_LEAVE", "HOLIDAY", "REST_DAY"]
    check_in: time | None = None
    check_out: time | None = None
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_times(self):
        if (self.check_in is None) != (self.check_out is None):
            raise ValueError("check_in and check_out must be provided together.")
        if self.check_in is not None and self.check_out <= self.check_in:
            raise ValueError("check_out must be later than check_in.")
        return self

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None):
        if value is not None and not value.strip():
            raise ValueError("note cannot be blank.")
        return value.strip() if value is not None else None


class LeaveRequestCreate(BaseModel):
    user_id: int = Field(gt=0)
    leave_type: Literal["CASUAL", "SICK", "EARNED", "UNPAID", "OTHER"]
    start_date: date
    end_date: date
    reason: str = Field(min_length=3, max_length=2000)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date.")
        return self

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str):
        value = value.strip()
        if not value:
            raise ValueError("reason cannot be blank.")
        return value


class LeaveDecision(BaseModel):
    status: Literal["APPROVED", "REJECTED"]
    decision_notes: str = Field(min_length=3, max_length=2000)

    @field_validator("decision_notes")
    @classmethod
    def normalize_note(cls, value: str):
        value = value.strip()
        if not value:
            raise ValueError("decision_notes cannot be blank.")
        return value


class SalaryProfileCreate(BaseModel):
    user_id: int = Field(gt=0)
    monthly_fixed_paise: int = Field(gt=0, le=2_000_000_000)
    effective_from: date
    note: str = Field(min_length=3, max_length=1000)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str):
        value = value.strip()
        if not value:
            raise ValueError("note cannot be blank.")
        return value


class PayrollPeriodCreate(BaseModel):
    period_key: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")
    working_days: int = Field(gt=0, le=31)


class PayrollLineCreate(BaseModel):
    user_id: int = Field(gt=0)
    paid_days: Decimal = Field(ge=0, max_digits=5, decimal_places=2)
    additional_earnings_paise: int = Field(default=0, ge=0, le=2_000_000_000)
    deductions_paise: int = Field(default=0, ge=0, le=2_000_000_000)
    calculation_note: str = Field(min_length=3, max_length=2000)

    @field_validator("calculation_note")
    @classmethod
    def normalize_note(cls, value: str):
        value = value.strip()
        if not value:
            raise ValueError("calculation_note cannot be blank.")
        return value


def _active_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if not user or user.status != UserStatusEnum.ACTIVE:
        raise HTTPException(status_code=404, detail="Active staff user not found.")
    return user


def _period_dates(period_key: str) -> tuple[date, date]:
    year, month = (int(part) for part in period_key.split("-"))
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


@router.post("/attendance", status_code=201)
def record_attendance(
    payload: AttendanceCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("hr:write")),
):
    _active_user(db, payload.user_id)
    record = AttendanceRecord(**payload.model_dump())
    record.created_by_id = actor.id
    record.updated_by_id = actor.id
    db.add(record)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Attendance already exists for this staff member and date."
        ) from exc
    db.refresh(record)
    return record


@router.get("/attendance")
def list_attendance(
    from_date: date,
    to_date: date,
    user_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("hr:read")),
):
    if to_date < from_date:
        raise HTTPException(status_code=422, detail="to_date cannot be before from_date.")
    statement = select(AttendanceRecord).where(
        AttendanceRecord.work_date >= from_date,
        AttendanceRecord.work_date <= to_date,
    )
    if user_id is not None:
        statement = statement.where(AttendanceRecord.user_id == user_id)
    items = db.execute(
        statement.order_by(AttendanceRecord.work_date, AttendanceRecord.user_id).limit(limit)
    ).scalars().all()
    return {"items": items}


@router.post("/leave-requests", status_code=201)
def create_leave_request(
    payload: LeaveRequestCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("hr:write")),
):
    _active_user(db, payload.user_id)
    overlapping = db.execute(
        select(LeaveRequest.id).where(
            LeaveRequest.user_id == payload.user_id,
            LeaveRequest.status.in_(("PENDING", "APPROVED")),
            LeaveRequest.start_date <= payload.end_date,
            LeaveRequest.end_date >= payload.start_date,
        )
    ).scalar_one_or_none()
    if overlapping:
        raise HTTPException(status_code=409, detail="Leave dates overlap an existing request.")
    request = LeaveRequest(**payload.model_dump(), created_by_id=actor.id, updated_by_id=actor.id)
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


@router.get("/leave-requests")
def list_leave_requests(
    request_status: str | None = Query(default=None, alias="status"),
    user_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("hr:read")),
):
    statement = select(LeaveRequest).order_by(LeaveRequest.start_date, LeaveRequest.id)
    if request_status:
        statement = statement.where(LeaveRequest.status == request_status.upper())
    if user_id is not None:
        statement = statement.where(LeaveRequest.user_id == user_id)
    return {"items": db.execute(statement.limit(limit)).scalars().all()}


@router.patch("/leave-requests/{request_id}/decision")
def decide_leave_request(
    request_id: int,
    payload: LeaveDecision,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("hr:approve")),
):
    request = db.execute(
        select(LeaveRequest).where(LeaveRequest.id == request_id).with_for_update()
    ).scalar_one_or_none()
    if not request:
        raise HTTPException(status_code=404, detail="Leave request not found.")
    if request.status != "PENDING":
        raise HTTPException(status_code=409, detail="Leave request has already been decided.")
    if request.user_id == actor.id:
        raise HTTPException(status_code=403, detail="Staff cannot decide their own leave request.")
    request.status = payload.status
    request.decision_notes = payload.decision_notes
    request.decided_by_user_id = actor.id
    request.decided_at = datetime.now(timezone.utc)
    request.updated_by_id = actor.id
    db.commit()
    db.refresh(request)
    return request


@router.post("/salary-profiles", status_code=201)
def create_salary_profile(
    payload: SalaryProfileCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("hr:write")),
):
    _active_user(db, payload.user_id)
    latest = db.execute(
        select(SalaryProfile)
        .where(SalaryProfile.user_id == payload.user_id)
        .order_by(SalaryProfile.effective_from.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest and payload.effective_from <= latest.effective_from:
        raise HTTPException(
            status_code=409,
            detail="Salary changes must have a later effective date; existing profiles are immutable.",
        )
    profile = SalaryProfile(**payload.model_dump(), created_by_user_id=actor.id)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/salary-profiles")
def list_salary_profiles(
    user_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("payroll:read")),
):
    statement = select(SalaryProfile).order_by(
        SalaryProfile.user_id, SalaryProfile.effective_from.desc()
    )
    if user_id is not None:
        statement = statement.where(SalaryProfile.user_id == user_id)
    return {"items": db.execute(statement).scalars().all()}


@router.post("/payroll-periods", status_code=201)
def create_payroll_period(
    payload: PayrollPeriodCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("hr:payroll")),
):
    start_date, end_date = _period_dates(payload.period_key)
    period = PayrollPeriod(
        period_key=payload.period_key,
        start_date=start_date,
        end_date=end_date,
        working_days=payload.working_days,
        created_by_user_id=actor.id,
    )
    db.add(period)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Payroll period already exists.") from exc
    db.refresh(period)
    return period


@router.get("/payroll-periods")
def list_payroll_periods(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("payroll:read")),
):
    return {
        "items": db.execute(
            select(PayrollPeriod).order_by(PayrollPeriod.period_key.desc()).limit(limit)
        ).scalars().all()
    }


@router.post("/payroll-periods/{period_id}/lines", status_code=201)
def calculate_payroll_line(
    period_id: int,
    payload: PayrollLineCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("hr:payroll")),
):
    period = db.execute(
        select(PayrollPeriod).where(PayrollPeriod.id == period_id).with_for_update()
    ).scalar_one_or_none()
    if not period:
        raise HTTPException(status_code=404, detail="Payroll period not found.")
    if period.status != "OPEN":
        raise HTTPException(status_code=409, detail="Finalized payroll periods are immutable.")
    _active_user(db, payload.user_id)
    if payload.paid_days > period.working_days:
        raise HTTPException(status_code=422, detail="paid_days cannot exceed working_days.")
    profile = db.execute(
        select(SalaryProfile)
        .where(
            SalaryProfile.user_id == payload.user_id,
            SalaryProfile.effective_from <= period.start_date,
        )
        .order_by(SalaryProfile.effective_from.desc())
        .limit(1)
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(
            status_code=409, detail="No salary profile is effective for this payroll period."
        )

    prorated = int(
        (Decimal(profile.monthly_fixed_paise) * payload.paid_days / Decimal(period.working_days))
        .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    gross = prorated + payload.additional_earnings_paise
    net = gross - payload.deductions_paise
    if net < 0:
        raise HTTPException(status_code=422, detail="Deductions cannot exceed gross earnings.")

    line = db.execute(
        select(PayrollLine)
        .where(PayrollLine.period_id == period.id, PayrollLine.user_id == payload.user_id)
        .with_for_update()
    ).scalar_one_or_none()
    input_snapshot = {
        "salary_profile_id": profile.id,
        "monthly_fixed_paise": profile.monthly_fixed_paise,
        "paid_days": str(payload.paid_days),
        "working_days": period.working_days,
        "additional_earnings_paise": payload.additional_earnings_paise,
        "deductions_paise": payload.deductions_paise,
        "calculation_note": payload.calculation_note,
    }
    values = {
        **input_snapshot,
        "paid_days": payload.paid_days,
        "prorated_fixed_paise": prorated,
        "gross_paise": gross,
        "net_paise": net,
    }
    values.pop("calculation_note")
    if line is None:
        line = PayrollLine(
            period_id=period.id,
            user_id=payload.user_id,
            calculation_note=payload.calculation_note,
            created_by_id=actor.id,
            updated_by_id=actor.id,
            **values,
        )
        db.add(line)
        db.flush()
    else:
        for field, value in values.items():
            setattr(line, field, value)
        line.calculation_note = payload.calculation_note
        line.updated_by_id = actor.id
    db.add(
        PayrollCalculationEvent(
            line_id=line.id,
            actor_user_id=actor.id,
            input_snapshot=json.dumps(input_snapshot, sort_keys=True),
            output_snapshot=json.dumps(
                {
                    "prorated_fixed_paise": prorated,
                    "gross_paise": gross,
                    "net_paise": net,
                },
                sort_keys=True,
            ),
        )
    )
    db.commit()
    db.refresh(line)
    return line


@router.get("/payroll-periods/{period_id}/lines")
def list_payroll_lines(
    period_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("payroll:read")),
):
    if not db.get(PayrollPeriod, period_id):
        raise HTTPException(status_code=404, detail="Payroll period not found.")
    return {
        "items": db.execute(
            select(PayrollLine)
            .where(PayrollLine.period_id == period_id)
            .order_by(PayrollLine.user_id)
        ).scalars().all()
    }


@router.get("/payroll-lines/{line_id}/history")
def payroll_calculation_history(
    line_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("payroll:read")),
):
    if not db.get(PayrollLine, line_id):
        raise HTTPException(status_code=404, detail="Payroll line not found.")
    return {
        "items": db.execute(
            select(PayrollCalculationEvent)
            .where(PayrollCalculationEvent.line_id == line_id)
            .order_by(PayrollCalculationEvent.id)
        ).scalars().all()
    }


@router.post("/payroll-periods/{period_id}/finalize")
def finalize_payroll_period(
    period_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(RequirePermission("hr:approve")),
):
    period = db.execute(
        select(PayrollPeriod).where(PayrollPeriod.id == period_id).with_for_update()
    ).scalar_one_or_none()
    if not period:
        raise HTTPException(status_code=404, detail="Payroll period not found.")
    if period.status != "OPEN":
        raise HTTPException(status_code=409, detail="Payroll period has already been finalized.")
    if period.created_by_user_id == actor.id:
        raise HTTPException(
            status_code=403,
            detail="Payroll must be finalized by someone other than its preparer.",
        )
    if db.execute(
        select(PayrollLine.id).where(PayrollLine.period_id == period_id).limit(1)
    ).scalar_one_or_none() is None:
        raise HTTPException(status_code=409, detail="Cannot finalize a period with no payroll lines.")
    prepared_by_actor = db.execute(
        select(PayrollLine.id)
        .where(
            PayrollLine.period_id == period_id,
            (PayrollLine.created_by_id == actor.id)
            | (PayrollLine.updated_by_id == actor.id),
        )
        .limit(1)
    ).scalar_one_or_none()
    if prepared_by_actor is not None:
        raise HTTPException(
            status_code=403,
            detail="Payroll must be finalized by someone other than its preparer.",
        )
    period.status = "FINALIZED"
    period.finalized_by_user_id = actor.id
    period.finalized_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(period)
    return period
