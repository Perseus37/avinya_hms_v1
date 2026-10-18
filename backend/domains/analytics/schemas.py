from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AnalyticsWindow(BaseModel):
    start_date: date
    end_date: date
    comparison_start_date: Optional[date] = None
    comparison_end_date: Optional[date] = None
    timezone: str = "UTC"
    generated_at: datetime
    definition_version: str = "1.0"


class ComparableMetric(BaseModel):
    value: int
    previous_period_value: int
    change_pct: Optional[float]


class ReceivablesSnapshot(BaseModel):
    as_of: datetime
    age_basis: str
    outstanding_paise: int = Field(ge=0)
    outstanding_invoice_count: int = Field(ge=0)
    invoice_age_buckets_paise: dict[str, int]


class AnalyticsDashboardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window: AnalyticsWindow
    financial: dict[str, ComparableMetric]
    volume: dict[str, ComparableMetric]
    receivables: ReceivablesSnapshot


class DailyAnalyticsPoint(BaseModel):
    date: date
    bucket_end: date
    net_billed_paise: int = Field(ge=0)
    payments_received_paise: int = Field(ge=0)
    encounters_started: int = Field(ge=0)
    appointments_scheduled: int = Field(ge=0)


class AnalyticsTrendResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window: AnalyticsWindow
    granularity: str
    definitions: dict[str, str]
    series: list[DailyAnalyticsPoint]


class StatusCount(BaseModel):
    status: str
    count: int = Field(ge=0)


class OperationalThroughputResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window: AnalyticsWindow
    encounters_started: int = Field(ge=0)
    encounters_by_type: list[StatusCount]
    appointments_by_status: list[StatusCount]
    diagnostic_tasks_by_status: list[StatusCount]
    data_notes: list[str]


class AppointmentPerformanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window: AnalyticsWindow
    scheduled_appointments: int = Field(ge=0)
    appointments_with_encounters: int = Field(ge=0)
    appointments_without_encounters: int = Field(ge=0)
    status_breakdown: list[StatusCount]
    recorded_status_transition_counts: list[StatusCount]
    appointments_with_observed_status_transitions: int = Field(ge=0)
    completed_rate_pct: Optional[float] = Field(default=None, ge=0, le=100)
    no_show_rate_pct: Optional[float] = Field(default=None, ge=0, le=100)
    cancelled_rate_pct: Optional[float] = Field(default=None, ge=0, le=100)
    average_booking_lead_hours: Optional[float] = Field(default=None, ge=0)
    data_notes: list[str]
