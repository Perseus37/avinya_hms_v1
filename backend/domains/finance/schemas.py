from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from backend.domains.finance.models import (
    ExpenseCategoryEnum,
    ExpenseStatusEnum,
    RecurringFrequencyEnum,
)


class ExpenseDraft(BaseModel):
    category: ExpenseCategoryEnum
    title: str = Field(min_length=2, max_length=150)
    amount_paise: int = Field(gt=0)
    vendor_name: Optional[str] = None
    due_date: Optional[datetime] = None


class ExpenseAdjudicate(BaseModel):
    status: ExpenseStatusEnum
    rejection_reason: Optional[str] = None


class RecurringTemplateCreate(BaseModel):
    category: ExpenseCategoryEnum
    title: str = Field(min_length=2)
    amount_paise: int = Field(gt=0)
    vendor_name: Optional[str] = None
    frequency: RecurringFrequencyEnum
    next_generation_date: datetime
    auto_approve: bool = False
