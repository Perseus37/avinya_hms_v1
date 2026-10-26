from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import select

from backend.core.database import get_db
from backend.core.dependencies import RequirePermission
from backend.domains.identity.models import User
from backend.domains.finance.models import (
    ExpenseCategoryEnum,
    ExpenseRecord,
    ExpenseStatusEnum,
    RecurringExpenseTemplate,
    RecurringFrequencyEnum,
)

from backend.domains.finance.schemas import (
    ExpenseAdjudicate,
    ExpenseDraft,
    RecurringTemplateCreate,
)

router = APIRouter(prefix="/api/finance/ap", tags=["Accounts Payable & Expenses"])

# --- Endpoints ---
@router.post("/expenses", status_code=status.HTTP_201_CREATED)
def draft_expense(
    payload: ExpenseDraft,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("finance:write"))
):
    """Accountant proposes an expense. It enters PENDING_APPROVAL state."""
    expense = ExpenseRecord(
        **payload.model_dump(),
        status=ExpenseStatusEnum.PENDING_APPROVAL,
        created_by_id=current_user.id
    )
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return expense

@router.patch("/expenses/{expense_id}/adjudicate")
def adjudicate_expense(
    expense_id: int,
    payload: ExpenseAdjudicate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("finance:approve"))
):
    """CFO/Admin approves or rejects a pending expense."""
    expense = db.get(ExpenseRecord, expense_id)
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found.")
    
    if expense.status != ExpenseStatusEnum.PENDING_APPROVAL:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot adjudicate an expense in '{expense.status.value}' state. Only PENDING_APPROVAL expenses can be adjudicated.",
        )
    
    if payload.status == ExpenseStatusEnum.REJECTED and not payload.rejection_reason:
        raise HTTPException(status_code=400, detail="Rejection reason is mandatory.")

    expense.status = payload.status
    if payload.status == ExpenseStatusEnum.APPROVED:
        expense.approved_by_id = current_user.id
        expense.approved_at = datetime.now(timezone.utc)
    elif payload.status == ExpenseStatusEnum.REJECTED:
        expense.rejection_reason = payload.rejection_reason

    expense.updated_by_id = current_user.id
    db.commit()
    db.refresh(expense)
    return expense

@router.post("/expenses/{expense_id}/mark-paid")
def mark_expense_paid(
    expense_id: int,
    receipt_reference: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("finance:pay"))
):
    """Records the actual cash/bank outflow for an APPROVED expense."""
    expense = db.get(ExpenseRecord, expense_id)
    if not expense or expense.status != ExpenseStatusEnum.APPROVED:
        raise HTTPException(status_code=400, detail="Only APPROVED expenses can be paid.")
    
    expense.status = ExpenseStatusEnum.PAID
    expense.receipt_reference = receipt_reference
    expense.updated_by_id = current_user.id
    db.commit()
    return expense

@router.post("/recurring-templates", status_code=status.HTTP_201_CREATED)
def create_recurring_expense(
    payload: RecurringTemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequirePermission("finance:write"))
):
    """Sets up a periodic expense schedule (e.g., Monthly Rent)."""
    template = RecurringExpenseTemplate(
        **payload.model_dump(),
        created_by_id=current_user.id
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template

@router.post("/trigger-recurring")
def process_due_recurring_expenses(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("system:execute"))
):
    """
    Evaluates all active recurring templates. If the next_generation_date has passed,
    it generates the expense record and rolls the template's date forward.
    (This is designed to be hit by an external cron/scheduler).
    """
    from dateutil.relativedelta import relativedelta
    now = datetime.now(timezone.utc)
    
    # ARCH-007: Apply skip_locked=True to prevent multiple API nodes from duplicating cron jobs
    templates = db.execute(
        select(RecurringExpenseTemplate)
        .where(RecurringExpenseTemplate.is_active == True, RecurringExpenseTemplate.next_generation_date <= now)
        .with_for_update(skip_locked=True)
    ).scalars().all()

    generated_count = 0
    for t in templates:
        # Generate the expense
        status_val = ExpenseStatusEnum.APPROVED if t.auto_approve else ExpenseStatusEnum.PENDING_APPROVAL
        expense = ExpenseRecord(
            category=t.category,
            title=f"{t.title} - {now.strftime('%b %Y')}",
            amount_paise=t.amount_paise,
            vendor_name=t.vendor_name,
            status=status_val,
            recurring_template_id=t.id,
            due_date=now + relativedelta(days=7) # Default 7 day grace period
        )
        db.add(expense)
        generated_count += 1
        
        # Roll the date forward
        if t.frequency == RecurringFrequencyEnum.DAILY:
            t.next_generation_date += relativedelta(days=1)
        elif t.frequency == RecurringFrequencyEnum.WEEKLY:
            t.next_generation_date += relativedelta(weeks=1)
        elif t.frequency == RecurringFrequencyEnum.MONTHLY:
            t.next_generation_date += relativedelta(months=1)
        elif t.frequency == RecurringFrequencyEnum.QUARTERLY:
            t.next_generation_date += relativedelta(months=3)
        elif t.frequency == RecurringFrequencyEnum.ANNUALLY:
            t.next_generation_date += relativedelta(years=1)

    db.commit()
    return {"status": "success", "expenses_generated": generated_count}