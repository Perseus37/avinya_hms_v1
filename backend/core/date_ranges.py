from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status


MAX_RECORD_WINDOW_DAYS = 366


def resolve_record_date_window(
    start_date: Optional[date], end_date: Optional[date]
) -> tuple[Optional[datetime], Optional[datetime]]:
    if (start_date is None) != (end_date is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="start_date and end_date must be provided together.",
        )
    if start_date is None or end_date is None:
        return None, None
    if start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="start_date must be on or before end_date.",
        )
    if (end_date - start_date).days + 1 > MAX_RECORD_WINDOW_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Date windows cannot exceed {MAX_RECORD_WINDOW_DAYS} days.",
        )
    start_at = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_at = datetime.combine(
        end_date + timedelta(days=1), time.min, tzinfo=timezone.utc
    )
    return start_at, end_at
