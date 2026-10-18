from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.audit import AuditMixin
from backend.core.database import Base
from backend.domains.identity.models import User


class ExpenseCategoryEnum(str, PyEnum):
    PAYROLL = "PAYROLL"
    UTILITY = "UTILITY"
    MAINTENANCE = "MAINTENANCE"
    MEDICAL_SUPPLIES = "MEDICAL_SUPPLIES"
    PHARMACY_STOCK = "PHARMACY_STOCK"
    EQUIPMENT = "EQUIPMENT"
    OTHER = "OTHER"


class ExpenseStatusEnum(str, PyEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class RecurringFrequencyEnum(str, PyEnum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUALLY = "ANNUALLY"


class ExpenseRecord(AuditMixin, Base):
    """Accounts-payable ledger entry with a maker-checker approval workflow."""

    __tablename__ = "finance_expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    category: Mapped[ExpenseCategoryEnum] = mapped_column(
        Enum(ExpenseCategoryEnum), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ExpenseStatusEnum] = mapped_column(
        Enum(ExpenseStatusEnum),
        default=ExpenseStatusEnum.PENDING_APPROVAL,
        nullable=False,
    )
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(255))
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    incurred_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), index=True
    )
    vendor_name: Mapped[Optional[str]] = mapped_column(String(150))
    receipt_reference: Mapped[Optional[str]] = mapped_column(String(100))
    recurring_template_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("finance_recurring_expenses.id", ondelete="SET NULL")
    )

    approved_by: Mapped[Optional["User"]] = relationship(
        foreign_keys=[approved_by_id]
    )
    template: Mapped[Optional["RecurringExpenseTemplate"]] = relationship(
        back_populates="generated_expenses"
    )


class RecurringExpenseTemplate(AuditMixin, Base):
    """Template that generates recurring expenses for approval."""

    __tablename__ = "finance_recurring_expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    category: Mapped[ExpenseCategoryEnum] = mapped_column(
        Enum(ExpenseCategoryEnum), nullable=False
    )
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    vendor_name: Mapped[Optional[str]] = mapped_column(String(150))
    frequency: Mapped[RecurringFrequencyEnum] = mapped_column(
        Enum(RecurringFrequencyEnum), nullable=False
    )
    next_generation_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    generated_expenses: Mapped[List["ExpenseRecord"]] = relationship(
        back_populates="template"
    )
