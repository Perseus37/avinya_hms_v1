from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.audit import AuditMixin
from backend.core.database import Base
from backend.domains.identity.models import User


class AttendanceRecord(AuditMixin, Base):
    __tablename__ = "attendance_records"
    __table_args__ = (
        UniqueConstraint("user_id", "work_date", name="uq_attendance_user_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    work_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    check_in: Mapped[time | None] = mapped_column(Time)
    check_out: Mapped[time | None] = mapped_column(Time)
    note: Mapped[str | None] = mapped_column(Text)


class LeaveRequest(AuditMixin, Base):
    __tablename__ = "leave_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    leave_type: Mapped[str] = mapped_column(String(40), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    decision_notes: Mapped[str | None] = mapped_column(Text)
    decided_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SalaryProfile(Base):
    __tablename__ = "salary_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", "effective_from", name="uq_salary_profile_effective"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    monthly_fixed_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PayrollPeriod(Base):
    __tablename__ = "payroll_periods"
    __table_args__ = (UniqueConstraint("period_key", name="uq_payroll_period_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period_key: Mapped[str] = mapped_column(String(7), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    working_days: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finalized_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PayrollLine(AuditMixin, Base):
    __tablename__ = "payroll_lines"
    __table_args__ = (
        UniqueConstraint("period_id", "user_id", name="uq_payroll_line_period_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period_id: Mapped[int] = mapped_column(
        ForeignKey("payroll_periods.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    salary_profile_id: Mapped[int] = mapped_column(
        ForeignKey("salary_profiles.id"), nullable=False,
        index=True,
    )
    monthly_fixed_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_days: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    working_days: Mapped[int] = mapped_column(Integer, nullable=False)
    prorated_fixed_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    additional_earnings_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    deductions_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    gross_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    net_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    calculation_note: Mapped[str] = mapped_column(Text, nullable=False)

    # Statutory Indian deductions and employer contributions (M-05)
    pf_employee_paise: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pf_employer_paise: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    esi_employee_paise: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    esi_employer_paise: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    professional_tax_paise: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    tds_paise: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    statutory_details_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship()
    period: Mapped["PayrollPeriod"] = relationship()


class PayrollCalculationEvent(Base):
    __tablename__ = "payroll_calculation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    line_id: Mapped[int] = mapped_column(
        ForeignKey("payroll_lines.id"), nullable=False, index=True
    )
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    input_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    output_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
