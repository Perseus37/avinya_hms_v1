import calendar
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, case, extract, func, or_, select
from sqlalchemy.orm import Session, joinedload

from backend.core.database import get_db
from backend.core.dependencies import RequirePermission
from backend.domains.analytics.schemas import (
    AnalyticsDashboardResponse,
    AnalyticsTrendResponse,
    AnalyticsWindow,
    AppointmentPerformanceResponse,
    ComparableMetric,
    DailyAnalyticsPoint,
    OperationalThroughputResponse,
    ReceivablesSnapshot,
    StatusCount,
)
from backend.domains.billing.models import Invoice, InvoiceLineItem, InvoiceStatusEnum, PatientDeposit, PaymentReceipt, RefundVoucher
from backend.domains.bloodbank.models import BloodBagInventory, BloodBagStatusEnum, BloodCrossMatchOrder, RadiologyStudyMetadata, TransfusionReactionTypeEnum
from backend.domains.clinical.models import ClinicalNote, ERTriageAssessment, EncounterDiagnosis, EncounterVital, FluidBalanceLog, FluidDirectionEnum, MedicoLegalCase
from backend.domains.encounters.models import AdmissionTypeEnum, Appointment, AppointmentStatusEnum, AppointmentStatusHistory, BedTransferLog, DiagnosisTypeEnum, DischargeDispositionEnum, EncounterStatusEnum, EncounterTypeEnum, InpatientAdmission, PatientEncounter, ServiceDemandRequest
from backend.domains.facility.models import Bed, BedStatusEnum, BedTypeEnum, NursingShiftAssignment, PhysicalRoom, RoomTypeEnum, Ward
from backend.domains.identity.models import BloodGroupEnum, Department, DoctorProfile, GenderEnum, User, UserStatusEnum
from backend.domains.insurance.models import (
    InsuranceClaim,
    InsurancePlanTariff,
    InsurancePreAuth,
    InsuranceProvider,
    PatientInsurancePolicy,
    PreAuthStatusEnum,
)
from backend.domains.patients.models import AllergyCategoryEnum, AllergyMaster, DiagnosisMaster, Patient, PatientAllergy, SeverityEnum
from backend.domains.pharmacy.models import InventoryBatch, MedicationAdministrationLog, MedicationFormEnum, MedicationMaster, Prescription, PrescriptionItem, PrescriptionStatusEnum, eMARStatusEnum
from backend.domains.reports.models import ReportTemplate
from backend.domains.surgery.models import OperativeAnesthesiaRecord, SurgeryStatusEnum, SurgicalImplantLog, SurgicalSchedule, WHOSurgicalSafetyChecklist
from backend.domains.diagnostics.models import (
    DiagnosticOrder,
    DiagnosticTask,
    ServiceCatalog,
    TaskStatusEnum,
)
from backend.domains.encounters.models import (
    ServiceDemandReasonEnum,
    ServiceDemandSourceEnum,
    ServiceDemandStatusEnum,
)
from backend.domains.federation.models import FederatedClient, FederationTransferLog


def _disable_analytics_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


router = APIRouter(
    prefix="/api/analytics",
    tags=["Phase 14: Enterprise Hospital BI & Clinical Intelligence"],
    dependencies=[Depends(_disable_analytics_caching)],
)


def _to_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _pct(part: int | float, whole: int | float) -> float:
    return round((part / whole) * 100.0, 2) if whole > 0 else 0.0


def _seconds_between(start, end, dialect_name: str):
    if dialect_name == "sqlite":
        return (func.julianday(end) - func.julianday(start)) * 86400.0
    return extract("epoch", end - start)


MAX_ANALYTICS_WINDOW_DAYS = 366
STATUS_HISTORY_BACKFILL_REASON = (
    "Status at migration; prior transitions unavailable"
)


def _resolve_analytics_window(
    start_date: Optional[date], end_date: Optional[date]
) -> tuple[date, date, datetime, datetime]:
    today = datetime.now(timezone.utc).date()
    resolved_end = end_date or today
    resolved_start = start_date or (resolved_end - timedelta(days=29))
    if resolved_end > today:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Analytics end_date cannot be in the future.",
        )
    if resolved_start > resolved_end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Analytics start_date must be on or before end_date.",
        )
    if (resolved_end - resolved_start).days + 1 > MAX_ANALYTICS_WINDOW_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Analytics windows cannot exceed {MAX_ANALYTICS_WINDOW_DAYS} days.",
        )
    start_at = datetime.combine(resolved_start, time.min, tzinfo=timezone.utc)
    end_at = datetime.combine(
        resolved_end + timedelta(days=1), time.min, tzinfo=timezone.utc
    )
    return resolved_start, resolved_end, start_at, end_at


def _window_metadata(
    start_date: date,
    end_date: date,
    *,
    comparison_start_date: Optional[date] = None,
    comparison_end_date: Optional[date] = None,
) -> AnalyticsWindow:
    return AnalyticsWindow(
        start_date=start_date,
        end_date=end_date,
        comparison_start_date=comparison_start_date,
        comparison_end_date=comparison_end_date,
        generated_at=datetime.now(timezone.utc),
    )


def _count_in_window(db: Session, model, timestamp_column, start_at, end_at) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(model)
            .where(timestamp_column >= start_at, timestamp_column < end_at)
        )
        or 0
    )


def _financial_window_totals(
    db: Session, start_at: datetime, end_at: datetime
) -> tuple[int, int]:
    net_billed = func.coalesce(
        func.sum(Invoice.total_amount_paise - Invoice.discount_amount_paise), 0
    )
    billed_value = db.scalar(
        select(net_billed).where(
            Invoice.created_at >= start_at,
            Invoice.created_at < end_at,
            Invoice.status.in_(
                [InvoiceStatusEnum.UNPAID, InvoiceStatusEnum.PARTIAL, InvoiceStatusEnum.PAID]
            ),
        )
    )
    paid_value = db.scalar(
        select(func.coalesce(func.sum(PaymentReceipt.amount_paid_paise), 0))
        .select_from(PaymentReceipt)
        .join(Invoice, PaymentReceipt.invoice_id == Invoice.id)
        .where(
            PaymentReceipt.created_at >= start_at,
            PaymentReceipt.created_at < end_at,
            Invoice.status != InvoiceStatusEnum.VOID,
        )
    )
    return int(billed_value or 0), int(paid_value or 0)


def _comparable_metric(current: int, previous: int) -> ComparableMetric:
    change_pct = (
        round(((current - previous) / abs(previous)) * 100, 2)
        if previous != 0
        else None
    )
    return ComparableMetric(
        value=current,
        previous_period_value=previous,
        change_pct=change_pct,
    )


@router.get("/dashboard", response_model=AnalyticsDashboardResponse)
def get_analytics_dashboard(
    response: Response,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:read")),
) -> AnalyticsDashboardResponse:
    """Return bounded-period financial and activity KPIs with equal-period comparisons."""
    start_day, end_day, start_at, end_at = _resolve_analytics_window(
        start_date, end_date
    )
    window_days = (end_day - start_day).days + 1
    previous_start = start_at - timedelta(days=window_days)
    previous_end = start_at
    _disable_analytics_caching(response)

    current_billed, current_collected = _financial_window_totals(
        db, start_at, end_at
    )
    previous_billed, previous_collected = _financial_window_totals(
        db, previous_start, previous_end
    )
    current_encounters = _count_in_window(
        db, PatientEncounter, PatientEncounter.start_time, start_at, end_at
    )
    previous_encounters = _count_in_window(
        db,
        PatientEncounter,
        PatientEncounter.start_time,
        previous_start,
        previous_end,
    )
    current_appointments = _count_in_window(
        db, Appointment, Appointment.appointment_time, start_at, end_at
    )
    previous_appointments = _count_in_window(
        db, Appointment, Appointment.appointment_time, previous_start, previous_end
    )
    current_new_patients = _count_in_window(
        db, Patient, Patient.created_at, start_at, end_at
    )
    previous_new_patients = _count_in_window(
        db, Patient, Patient.created_at, previous_start, previous_end
    )
    as_of = datetime.now(timezone.utc)
    balance_expression = (
        Invoice.total_amount_paise
        - Invoice.discount_amount_paise
        - Invoice.amount_paid_paise
    )
    bucket_ages = [
        (
            "0_to_30_days_paise",
            Invoice.created_at >= as_of - timedelta(days=30),
        ),
        (
            "31_to_60_days_paise",
            (Invoice.created_at < as_of - timedelta(days=30))
            & (Invoice.created_at >= as_of - timedelta(days=60)),
        ),
        (
            "61_to_90_days_paise",
            (Invoice.created_at < as_of - timedelta(days=60))
            & (Invoice.created_at >= as_of - timedelta(days=90)),
        ),
        ("over_90_days_paise", Invoice.created_at < as_of - timedelta(days=90)),
    ]
    receivables_row = db.execute(
        select(
            func.coalesce(
                func.sum(balance_expression),
                0,
            ).label("outstanding_paise"),
            func.count(Invoice.id).label("outstanding_invoice_count"),
            *[
                func.coalesce(
                    func.sum(
                        case(
                            (
                                condition,
                                balance_expression,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label(bucket_name)
                for bucket_name, condition in bucket_ages
            ],
        )
        .where(
            Invoice.status.in_([InvoiceStatusEnum.UNPAID, InvoiceStatusEnum.PARTIAL])
        )
    ).one()
    receivables_buckets = {
        bucket_name: int(getattr(receivables_row, bucket_name) or 0)
        for bucket_name, _ in bucket_ages
    }

    return AnalyticsDashboardResponse(
        window=_window_metadata(
            start_day,
            end_day,
            comparison_start_date=previous_start.date(),
            comparison_end_date=(previous_end - timedelta(days=1)).date(),
        ),
        financial={
            "net_billed_paise": _comparable_metric(
                current_billed, previous_billed
            ),
            "payments_received_paise": _comparable_metric(
                current_collected, previous_collected
            ),
        },
        volume={
            "new_patients": _comparable_metric(
                current_new_patients, previous_new_patients
            ),
            "encounters_started": _comparable_metric(
                current_encounters, previous_encounters
            ),
            "appointments_scheduled": _comparable_metric(
                current_appointments, previous_appointments
            ),
        },
        receivables=ReceivablesSnapshot(
            as_of=as_of,
            age_basis="days since invoice record creation; invoice due dates are not currently stored for patient invoices",
            outstanding_paise=int(receivables_row.outstanding_paise or 0),
            outstanding_invoice_count=int(
                receivables_row.outstanding_invoice_count or 0
            ),
            invoice_age_buckets_paise=receivables_buckets,
        ),
    )


def _daily_aggregate(
    db: Session,
    model,
    timestamp_column,
    start_at: datetime,
    end_at: datetime,
    value_expression=None,
):
    day_expression = func.date(timestamp_column)
    aggregate = func.count(model.id) if value_expression is None else func.sum(value_expression)
    rows = db.execute(
        select(day_expression, aggregate)
        .where(timestamp_column >= start_at, timestamp_column < end_at)
        .group_by(day_expression)
    ).all()
    return {
        (row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0]))): int(
            row[1] or 0
        )
        for row in rows
    }


def _analytics_bucket_bounds(
    day: date,
    granularity: Literal["day", "week", "month", "quarter", "half_year", "year"],
) -> tuple[date, date]:
    if granularity == "day":
        return day, day
    if granularity == "week":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=6)
    if granularity == "month":
        start = day.replace(day=1)
        return start, date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])
    if granularity == "quarter":
        start_month = ((day.month - 1) // 3) * 3 + 1
        end_month = start_month + 2
        return (
            date(day.year, start_month, 1),
            date(day.year, end_month, calendar.monthrange(day.year, end_month)[1]),
        )
    if granularity == "half_year":
        if day.month <= 6:
            return date(day.year, 1, 1), date(day.year, 6, 30)
        return date(day.year, 7, 1), date(day.year, 12, 31)
    return date(day.year, 1, 1), date(day.year, 12, 31)


@router.get("/trends", response_model=AnalyticsTrendResponse)
def get_analytics_trends(
    response: Response,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    granularity: Literal["day", "week", "month", "quarter", "half_year", "year"] = Query(
        default="day"
    ),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:read")),
) -> AnalyticsTrendResponse:
    """Return time-bucketed UTC billing, collection, encounter and appointment series."""
    start_day, end_day, start_at, end_at = _resolve_analytics_window(
        start_date, end_date
    )
    _disable_analytics_caching(response)
    invoice_filter = (
        Invoice.created_at >= start_at,
        Invoice.created_at < end_at,
        Invoice.status.in_(
            [InvoiceStatusEnum.UNPAID, InvoiceStatusEnum.PARTIAL, InvoiceStatusEnum.PAID]
        ),
    )
    billed_rows = db.execute(
        select(
            func.date(Invoice.created_at),
            func.sum(Invoice.total_amount_paise - Invoice.discount_amount_paise),
        )
        .where(*invoice_filter)
        .group_by(func.date(Invoice.created_at))
    ).all()
    billed_by_day = {
        (row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0]))):
        int(row[1] or 0)
        for row in billed_rows
    }

    payment_rows = db.execute(
        select(
            func.date(PaymentReceipt.created_at),
            func.sum(PaymentReceipt.amount_paid_paise),
        )
        .join(Invoice, PaymentReceipt.invoice_id == Invoice.id)
        .where(
            PaymentReceipt.created_at >= start_at,
            PaymentReceipt.created_at < end_at,
            Invoice.status != InvoiceStatusEnum.VOID,
        )
        .group_by(func.date(PaymentReceipt.created_at))
    ).all()
    payments_by_day = {
        (row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0]))):
        int(row[1] or 0)
        for row in payment_rows
    }
    encounters_by_day = _daily_aggregate(
        db,
        PatientEncounter,
        PatientEncounter.start_time,
        start_at,
        end_at,
    )
    appointments_by_day = _daily_aggregate(
        db,
        Appointment,
        Appointment.appointment_time,
        start_at,
        end_at,
    )

    buckets = []
    bucket_start_by_day = {}
    cursor = start_day
    while cursor <= end_day:
        natural_start, natural_end = _analytics_bucket_bounds(cursor, granularity)
        bucket_start = max(start_day, natural_start)
        bucket_end = min(end_day, natural_end)
        buckets.append(
            {
                "date": bucket_start,
                "bucket_end": bucket_end,
                "net_billed_paise": 0,
                "payments_received_paise": 0,
                "encounters_started": 0,
                "appointments_scheduled": 0,
            }
        )
        for offset in range((bucket_end - bucket_start).days + 1):
            bucket_start_by_day[bucket_start + timedelta(days=offset)] = len(buckets) - 1
        cursor = bucket_end + timedelta(days=1)

    daily_values = (
        (billed_by_day, "net_billed_paise"),
        (payments_by_day, "payments_received_paise"),
        (encounters_by_day, "encounters_started"),
        (appointments_by_day, "appointments_scheduled"),
    )
    for values_by_day, metric in daily_values:
        for day, value in values_by_day.items():
            bucket_index = bucket_start_by_day.get(day)
            if bucket_index is not None:
                buckets[bucket_index][metric] += value

    series = []
    for bucket in buckets:
        series.append(
            DailyAnalyticsPoint(
                **bucket,
            )
        )
    return AnalyticsTrendResponse(
        window=_window_metadata(start_day, end_day),
        granularity=granularity,
        definitions={
            "net_billed_paise": "Net finalized invoices created in each UTC calendar bucket; excludes draft and void invoices.",
            "payments_received_paise": "Payment receipts recorded in each UTC calendar bucket, excluding receipts attached to void invoices.",
            "encounters_started": "Patient encounters with start_time in each UTC calendar bucket, across all encounter statuses.",
            "appointments_scheduled": "Appointments with appointment_time in each UTC calendar bucket, across all statuses.",
            "bucket_boundaries": "date and bucket_end are inclusive UTC calendar dates; first and last buckets are clipped to the requested date window.",
        },
        series=series,
    )


@router.get(
    "/operational-throughput",
    response_model=OperationalThroughputResponse,
)
def get_operational_throughput(
    response: Response,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read")),
) -> OperationalThroughputResponse:
    """Summarize dated encounter, appointment and diagnostic workflow activity."""
    start_day, end_day, start_at, end_at = _resolve_analytics_window(
        start_date, end_date
    )
    _disable_analytics_caching(response)
    encounter_rows = db.execute(
        select(PatientEncounter.encounter_type, func.count(PatientEncounter.id))
        .where(
            PatientEncounter.start_time >= start_at,
            PatientEncounter.start_time < end_at,
        )
        .group_by(PatientEncounter.encounter_type)
    ).all()
    appointment_rows = db.execute(
        select(Appointment.status, func.count(Appointment.id))
        .where(
            Appointment.appointment_time >= start_at,
            Appointment.appointment_time < end_at,
        )
        .group_by(Appointment.status)
    ).all()
    task_rows = db.execute(
        select(DiagnosticTask.status, func.count(DiagnosticTask.id))
        .where(
            DiagnosticTask.created_at >= start_at,
            DiagnosticTask.created_at < end_at,
        )
        .group_by(DiagnosticTask.status)
    ).all()
    encounters_by_type = {
        str(encounter_type.value): int(count)
        for encounter_type, count in encounter_rows
    }
    appointments_by_status = {
        str(appointment_status.value): int(count)
        for appointment_status, count in appointment_rows
    }
    tasks_by_status = {str(task_status.value): int(count) for task_status, count in task_rows}
    return OperationalThroughputResponse(
        window=_window_metadata(start_day, end_day),
        encounters_started=sum(encounters_by_type.values()),
        encounters_by_type=[
            StatusCount(status=key, count=value)
            for key, value in sorted(encounters_by_type.items())
        ],
        appointments_by_status=[
            StatusCount(status=key, count=value)
            for key, value in sorted(appointments_by_status.items())
        ],
        diagnostic_tasks_by_status=[
            StatusCount(status=key, count=value)
            for key, value in sorted(tasks_by_status.items())
        ],
        data_notes=[
            "Encounter activity is bucketed by start_time; encounter counts include planned, active, discharged, and cancelled records.",
            "Appointment activity is bucketed by appointment_time and grouped by current appointment status.",
            "Diagnostic task activity is bucketed by record creation time and grouped by current task status; it is not a completed-work timestamp.",
            "All window boundaries and daily buckets use UTC. Counts are aggregated and contain no patient identifiers.",
        ],
    )


@router.get(
    "/appointment-performance",
    response_model=AppointmentPerformanceResponse,
)
def get_appointment_performance(
    response: Response,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:read")),
) -> AppointmentPerformanceResponse:
    """Summarize appointment outcomes and booking lead time without patient data."""
    start_day, end_day, start_at, end_at = _resolve_analytics_window(
        start_date, end_date
    )
    _disable_analytics_caching(response)

    if db.get_bind().dialect.name == "sqlite":
        lead_seconds = (
            func.julianday(Appointment.appointment_time)
            - func.julianday(Appointment.created_at)
        ) * 86400
    else:
        lead_seconds = extract(
            "epoch", Appointment.appointment_time - Appointment.created_at
        )

    summary = db.execute(
        select(
            func.count(Appointment.id).label("scheduled"),
            func.sum(
                case((Appointment.encounter_id.is_not(None), 1), else_=0)
            ).label("with_encounter"),
            func.sum(
                case((Appointment.status == AppointmentStatusEnum.COMPLETED, 1), else_=0)
            ).label("completed"),
            func.sum(
                case((Appointment.status == AppointmentStatusEnum.NO_SHOW, 1), else_=0)
            ).label("no_show"),
            func.sum(
                case((Appointment.status == AppointmentStatusEnum.CANCELLED, 1), else_=0)
            ).label("cancelled"),
            func.avg(
                case(
                    (Appointment.created_at <= Appointment.appointment_time, lead_seconds),
                    else_=None,
                )
            ).label("average_lead_seconds"),
        ).where(
            Appointment.appointment_time >= start_at,
            Appointment.appointment_time < end_at,
        )
    ).one()
    status_rows = db.execute(
        select(Appointment.status, func.count(Appointment.id))
        .where(
            Appointment.appointment_time >= start_at,
            Appointment.appointment_time < end_at,
        )
        .group_by(Appointment.status)
    ).all()
    observed_transition_rows = db.execute(
        select(
            AppointmentStatusHistory.to_status,
            func.count(
                func.distinct(AppointmentStatusHistory.appointment_id)
            ).label("appointment_count"),
        )
        .join(
            Appointment,
            Appointment.id == AppointmentStatusHistory.appointment_id,
        )
        .where(
            Appointment.appointment_time >= start_at,
            Appointment.appointment_time < end_at,
            AppointmentStatusHistory.from_status.is_not(None),
            AppointmentStatusHistory.reason.is_distinct_from(
                STATUS_HISTORY_BACKFILL_REASON
            ),
        )
        .group_by(AppointmentStatusHistory.to_status)
    ).all()
    observed_appointments = int(
        db.scalar(
            select(
                func.count(
                    func.distinct(AppointmentStatusHistory.appointment_id)
                )
            )
            .join(
                Appointment,
                Appointment.id == AppointmentStatusHistory.appointment_id,
            )
            .where(
                Appointment.appointment_time >= start_at,
                Appointment.appointment_time < end_at,
                AppointmentStatusHistory.from_status.is_not(None),
                AppointmentStatusHistory.reason.is_distinct_from(
                    STATUS_HISTORY_BACKFILL_REASON
                ),
            )
        )
        or 0
    )
    recorded_transition_counts = {
        status_value.value: 0 for status_value in AppointmentStatusEnum
    }
    for to_status, appointment_count in observed_transition_rows:
        recorded_transition_counts[to_status] = int(appointment_count)

    scheduled = int(summary.scheduled or 0)
    with_encounter = int(summary.with_encounter or 0)

    def rate(count: int) -> Optional[float]:
        return round(count * 100.0 / scheduled, 2) if scheduled else None

    average_lead_seconds = summary.average_lead_seconds
    return AppointmentPerformanceResponse(
        window=_window_metadata(start_day, end_day),
        scheduled_appointments=scheduled,
        appointments_with_encounters=with_encounter,
        appointments_without_encounters=scheduled - with_encounter,
        status_breakdown=[
            StatusCount(status=str(appointment_status.value), count=int(count))
            for appointment_status, count in sorted(
                status_rows, key=lambda row: row[0].value
            )
        ],
        recorded_status_transition_counts=[
            StatusCount(status=status_name, count=count)
            for status_name, count in sorted(recorded_transition_counts.items())
        ],
        appointments_with_observed_status_transitions=observed_appointments,
        completed_rate_pct=rate(int(summary.completed or 0)),
        no_show_rate_pct=rate(int(summary.no_show or 0)),
        cancelled_rate_pct=rate(int(summary.cancelled or 0)),
        average_booking_lead_hours=(
            round(float(average_lead_seconds) / 3600.0, 2)
            if average_lead_seconds is not None
            else None
        ),
        data_notes=[
            "Appointments are selected by scheduled appointment_time; status breakdown and outcome rates use each appointment's current status.",
            "Recorded status transition counts use status-history events observed by the application. Pre-migration rows contain only the current status snapshot and are excluded from transition counts; historical transitions before deployment cannot be reconstructed.",
            "Outcome rates use all appointments in the window as the denominator, including future/today slots and cancellations; interpret them as a current operational snapshot, not a finalized cohort conversion rate.",
            "Booking lead time is the average time from record creation to scheduled appointment_time and excludes records created after their scheduled time.",
            "Linked encounters are counted by encounter_id presence only; this does not imply the appointment was completed or that the encounter began in the selected window.",
            "Results are aggregate-only and contain no patient or staff identifiers.",
        ],
    )


@router.get("/care-timings")
def get_care_timing_analytics(
    response: Response,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:read", "adt:read")),
) -> Dict[str, Any]:
    """Aggregate observed OPD and inpatient stage timings without patient data."""
    start_day, end_day, start_at, end_at = _resolve_analytics_window(
        start_date, end_date
    )
    _disable_analytics_caching(response)
    dialect_name = db.get_bind().dialect.name

    status_times = (
        select(
            AppointmentStatusHistory.appointment_id.label("appointment_id"),
            func.min(
                case(
                    (
                        AppointmentStatusHistory.to_status
                        == AppointmentStatusEnum.ARRIVED.value,
                        AppointmentStatusHistory.changed_at,
                    ),
                    else_=None,
                )
            ).label("arrived_at"),
            func.min(
                case(
                    (
                        AppointmentStatusHistory.to_status
                        == AppointmentStatusEnum.IN_CONSULTATION.value,
                        AppointmentStatusHistory.changed_at,
                    ),
                    else_=None,
                )
            ).label("consultation_started_at"),
            func.min(
                case(
                    (
                        AppointmentStatusHistory.to_status
                        == AppointmentStatusEnum.COMPLETED.value,
                        AppointmentStatusHistory.changed_at,
                    ),
                    else_=None,
                )
            ).label("consultation_completed_at"),
        )
        .where(
            AppointmentStatusHistory.reason.is_distinct_from(
                STATUS_HISTORY_BACKFILL_REASON
            )
        )
        .group_by(AppointmentStatusHistory.appointment_id)
        .subquery()
    )

    wait_seconds = _seconds_between(
        status_times.c.arrived_at,
        status_times.c.consultation_started_at,
        dialect_name,
    )
    consult_seconds = _seconds_between(
        status_times.c.consultation_started_at,
        status_times.c.consultation_completed_at,
        dialect_name,
    )
    scheduled_start_delta_seconds = _seconds_between(
        Appointment.appointment_time,
        status_times.c.consultation_started_at,
        dialect_name,
    )
    appointment_timing_rows = (
        select(
            Appointment.id,
            Appointment.planned_duration_minutes,
            wait_seconds.label("wait_seconds"),
            consult_seconds.label("consult_seconds"),
            scheduled_start_delta_seconds.label("scheduled_start_delta_seconds"),
        )
        .outerjoin(
            status_times,
            status_times.c.appointment_id == Appointment.id,
        )
        .where(
            Appointment.appointment_time >= start_at,
            Appointment.appointment_time < end_at,
        )
        .subquery()
    )
    actual = appointment_timing_rows.c.consult_seconds
    planned_seconds = appointment_timing_rows.c.planned_duration_minutes * 60.0
    appointment_summary = db.execute(
        select(
            func.count(appointment_timing_rows.c.id).label("scheduled_count"),
            func.count(
                case((appointment_timing_rows.c.wait_seconds.is_not(None), 1))
            ).label("wait_sample_count"),
            func.avg(
                case(
                    (
                        appointment_timing_rows.c.wait_seconds >= 0,
                        appointment_timing_rows.c.wait_seconds / 60.0,
                    ),
                    else_=None,
                )
            ).label("average_wait_minutes"),
            func.min(
                case(
                    (
                        appointment_timing_rows.c.wait_seconds >= 0,
                        appointment_timing_rows.c.wait_seconds / 60.0,
                    ),
                    else_=None,
                )
            ).label("minimum_wait_minutes"),
            func.max(
                case(
                    (
                        appointment_timing_rows.c.wait_seconds >= 0,
                        appointment_timing_rows.c.wait_seconds / 60.0,
                    ),
                    else_=None,
                )
            ).label("maximum_wait_minutes"),
            func.count(
                case((appointment_timing_rows.c.consult_seconds.is_not(None), 1))
            ).label("completed_consultation_sample_count"),
            func.avg(
                case(
                    (
                        appointment_timing_rows.c.consult_seconds >= 0,
                        appointment_timing_rows.c.consult_seconds / 60.0,
                    ),
                    else_=None,
                )
            ).label("average_consultation_minutes"),
            func.min(
                case(
                    (
                        appointment_timing_rows.c.consult_seconds >= 0,
                        appointment_timing_rows.c.consult_seconds / 60.0,
                    ),
                    else_=None,
                )
            ).label("minimum_consultation_minutes"),
            func.max(
                case(
                    (
                        appointment_timing_rows.c.consult_seconds >= 0,
                        appointment_timing_rows.c.consult_seconds / 60.0,
                    ),
                    else_=None,
                )
            ).label("maximum_consultation_minutes"),
            func.count(
                case(
                    (
                        (actual.is_not(None))
                        & (appointment_timing_rows.c.planned_duration_minutes.is_not(None)),
                        1,
                    )
                )
            ).label("planned_comparison_sample_count"),
            func.avg(
                case(
                    (
                        (actual.is_not(None))
                        & (appointment_timing_rows.c.planned_duration_minutes.is_not(None)),
                        (actual - planned_seconds) / 60.0,
                    ),
                    else_=None,
                )
            ).label("average_actual_minus_planned_minutes"),
            func.sum(
                case(
                    (
                        (actual >= 0)
                        & (appointment_timing_rows.c.planned_duration_minutes.is_not(None))
                        & (actual > planned_seconds),
                        1,
                    ),
                    else_=0,
                )
            ).label("over_planned_count"),
            func.sum(
                case(
                    (
                        (actual >= 0)
                        & (appointment_timing_rows.c.planned_duration_minutes.is_not(None))
                        & (actual < planned_seconds),
                        1,
                    ),
                    else_=0,
                )
            ).label("under_planned_count"),
            func.avg(
                case(
                    (
                        appointment_timing_rows.c.scheduled_start_delta_seconds.is_not(None),
                        appointment_timing_rows.c.scheduled_start_delta_seconds / 60.0,
                    ),
                    else_=None,
                )
            ).label("average_scheduled_to_consult_start_delta_minutes"),
        )
    ).one()

    now_utc = datetime.now(timezone.utc)
    stay_end = func.coalesce(InpatientAdmission.discharge_time, now_utc)
    stay_days = _seconds_between(
        InpatientAdmission.created_at, stay_end, dialect_name
    ) / 86400.0
    encounter_to_admission_minutes = _seconds_between(
        PatientEncounter.start_time,
        InpatientAdmission.created_at,
        dialect_name,
    ) / 60.0
    inpatient_summary = db.execute(
        select(
            func.count(InpatientAdmission.id).label("admission_count"),
            func.sum(
                case((InpatientAdmission.discharge_time.is_not(None), 1), else_=0)
            ).label("discharged_count"),
            func.sum(
                case((InpatientAdmission.discharge_time.is_(None), 1), else_=0)
            ).label("active_count"),
            func.avg(
                case(
                    (
                        (InpatientAdmission.discharge_time.is_not(None))
                        & (stay_days >= 0),
                        stay_days,
                    ),
                    else_=None,
                )
            ).label("average_completed_stay_days"),
            func.min(
                case(
                    (
                        (InpatientAdmission.discharge_time.is_not(None))
                        & (stay_days >= 0),
                        stay_days,
                    ),
                    else_=None,
                )
            ).label("minimum_completed_stay_days"),
            func.max(
                case(
                    (
                        (InpatientAdmission.discharge_time.is_not(None))
                        & (stay_days >= 0),
                        stay_days,
                    ),
                    else_=None,
                )
            ).label("maximum_completed_stay_days"),
            func.avg(
                case(
                    (
                        (InpatientAdmission.discharge_time.is_(None))
                        & (stay_days >= 0),
                        stay_days,
                    ),
                    else_=None,
                )
            ).label("average_current_stay_days"),
            func.avg(
                case(
                    (
                        encounter_to_admission_minutes >= 0,
                        encounter_to_admission_minutes,
                    ),
                    else_=None,
                )
            ).label("average_encounter_to_admission_minutes"),
        )
        .join(
            PatientEncounter,
            PatientEncounter.id == InpatientAdmission.encounter_id,
        )
        .where(
            InpatientAdmission.created_at >= start_at,
            InpatientAdmission.created_at < end_at,
        )
    ).one()

    first_bed_assignment = (
        select(
            BedTransferLog.admission_id.label("admission_id"),
            func.min(BedTransferLog.transfer_time).label("first_assignment_at"),
        )
        .where(BedTransferLog.from_bed_id.is_(None))
        .group_by(BedTransferLog.admission_id)
        .subquery()
    )
    assignment_minutes = _seconds_between(
        InpatientAdmission.created_at,
        first_bed_assignment.c.first_assignment_at,
        dialect_name,
    ) / 60.0
    assignment_summary = db.execute(
        select(
            func.count(InpatientAdmission.id).label("assignment_sample_count"),
            func.avg(
                case((assignment_minutes >= 0, assignment_minutes), else_=None)
            ).label("average_admission_to_bed_assignment_minutes"),
            func.max(
                case((assignment_minutes >= 0, assignment_minutes), else_=None)
            ).label("maximum_admission_to_bed_assignment_minutes"),
        )
        .outerjoin(
            first_bed_assignment,
            first_bed_assignment.c.admission_id == InpatientAdmission.id,
        )
        .where(
            InpatientAdmission.created_at >= start_at,
            InpatientAdmission.created_at < end_at,
        )
    ).one()

    transfer_sequence = (
        select(
            BedTransferLog.id.label("transfer_id"),
            BedTransferLog.admission_id.label("admission_id"),
            BedTransferLog.to_bed_id.label("bed_id"),
            BedTransferLog.transfer_time.label("stage_start"),
            func.lead(BedTransferLog.transfer_time)
            .over(
                partition_by=BedTransferLog.admission_id,
                order_by=(BedTransferLog.transfer_time, BedTransferLog.id),
            )
            .label("next_stage_start"),
        )
        .join(
            InpatientAdmission,
            InpatientAdmission.id == BedTransferLog.admission_id,
        )
        .where(
            InpatientAdmission.created_at >= start_at,
            InpatientAdmission.created_at < end_at,
        )
        .subquery()
    )
    transfer_end = func.coalesce(
        transfer_sequence.c.next_stage_start,
        InpatientAdmission.discharge_time,
        now_utc,
    )
    bed_stage_minutes = _seconds_between(
        transfer_sequence.c.stage_start, transfer_end, dialect_name
    ) / 60.0
    bed_stage_rows = db.execute(
        select(
            Bed.bed_type,
            func.count(BedTransferLog.id).label("stage_count"),
            func.avg(bed_stage_minutes).label("average_stage_minutes"),
            func.max(bed_stage_minutes).label("maximum_stage_minutes"),
        )
        .join(
            BedTransferLog,
            BedTransferLog.to_bed_id == Bed.id,
        )
        .join(
            transfer_sequence,
            (transfer_sequence.c.admission_id == BedTransferLog.admission_id)
            & (transfer_sequence.c.transfer_id == BedTransferLog.id)
            & (transfer_sequence.c.bed_id == BedTransferLog.to_bed_id)
            & (transfer_sequence.c.stage_start == BedTransferLog.transfer_time),
        )
        .join(
            InpatientAdmission,
            InpatientAdmission.id == transfer_sequence.c.admission_id,
        )
        .group_by(Bed.bed_type)
        .order_by(Bed.bed_type)
    ).all()

    def rounded(value, digits=2):
        return round(float(value), digits) if value is not None else None

    return {
        "window": _window_metadata(start_day, end_day),
        "outpatient": {
            "appointments_in_scheduled_window": int(
                appointment_summary.scheduled_count or 0
            ),
            "observed_arrival_to_consult_wait": {
                "sample_count": int(appointment_summary.wait_sample_count or 0),
                "average_minutes": rounded(appointment_summary.average_wait_minutes),
                "minimum_minutes": rounded(appointment_summary.minimum_wait_minutes),
                "maximum_minutes": rounded(appointment_summary.maximum_wait_minutes),
            },
            "completed_consultation_duration": {
                "sample_count": int(
                    appointment_summary.completed_consultation_sample_count or 0
                ),
                "average_minutes": rounded(
                    appointment_summary.average_consultation_minutes
                ),
                "minimum_minutes": rounded(
                    appointment_summary.minimum_consultation_minutes
                ),
                "maximum_minutes": rounded(
                    appointment_summary.maximum_consultation_minutes
                ),
            },
            "actual_vs_planned_consultation": {
                "sample_count": int(
                    appointment_summary.planned_comparison_sample_count or 0
                ),
                "average_actual_minus_planned_minutes": rounded(
                    appointment_summary.average_actual_minus_planned_minutes
                ),
                "completed_consultations_over_planned": int(
                    appointment_summary.over_planned_count or 0
                ),
                "completed_consultations_under_planned": int(
                    appointment_summary.under_planned_count or 0
                ),
            },
            "scheduled_to_consult_start": {
                "sample_count": int(
                    appointment_summary.completed_consultation_sample_count or 0
                ),
                "average_minutes_after_scheduled_time": rounded(
                    appointment_summary.average_scheduled_to_consult_start_delta_minutes
                ),
                "interpretation": "Negative values indicate consultation started before the scheduled appointment time.",
            },
        },
        "inpatient": {
            "admissions_started_in_window": int(
                inpatient_summary.admission_count or 0
            ),
            "discharged_admissions": int(inpatient_summary.discharged_count or 0),
            "active_admissions_as_of_generated_at": int(
                inpatient_summary.active_count or 0
            ),
            "completed_length_of_stay_days": {
                "sample_count": int(inpatient_summary.discharged_count or 0),
                "average_days": rounded(
                    inpatient_summary.average_completed_stay_days
                ),
                "minimum_days": rounded(
                    inpatient_summary.minimum_completed_stay_days
                ),
                "maximum_days": rounded(
                    inpatient_summary.maximum_completed_stay_days
                ),
            },
            "active_stay_days_as_of_generated_at": {
                "sample_count": int(inpatient_summary.active_count or 0),
                "average_days": rounded(inpatient_summary.average_current_stay_days),
            },
            "encounter_start_to_admission_minutes": {
                "average_minutes": rounded(
                    inpatient_summary.average_encounter_to_admission_minutes
                )
            },
            "admission_to_initial_bed_assignment": {
                "sample_count": int(assignment_summary.assignment_sample_count or 0),
                "average_minutes": rounded(
                    assignment_summary.average_admission_to_bed_assignment_minutes
                ),
                "maximum_minutes": rounded(
                    assignment_summary.maximum_admission_to_bed_assignment_minutes
                ),
            },
            "bed_stage_duration_by_bed_type": [
                {
                    "bed_type": bed_type.value,
                    "stage_count": int(stage_count or 0),
                    "average_minutes": rounded(average_minutes),
                    "maximum_minutes": rounded(maximum_minutes),
                }
                for bed_type, stage_count, average_minutes, maximum_minutes
                in bed_stage_rows
            ],
        },
        "data_notes": [
            "OPD wait is measured from the first recorded ARRIVED event to first IN_CONSULTATION; consultation duration is first IN_CONSULTATION to first COMPLETED event. Missing, reversed, or pre-history events are excluded from duration averages.",
            "Planned consultation duration is snapshotted from the doctor's configured slot duration when booked and refreshed on reschedule. Legacy appointments have no planned-duration snapshot and are excluded from actual-versus-planned comparisons.",
            "Actual duration includes all time between consultation start and completion, not only face-to-face physician time. Breaks, interruptions, documentation, and delayed status entry can affect it.",
            "Scheduled-to-consult-start values compare the appointment time to the IN_CONSULTATION status event; negative means early. It is not a patient punctuality measure.",
            "IPD length of stay runs from admission record creation to discharge; active stays are elapsed snapshots at generated_at. The encounter-start-to-admission interval includes all time since encounter creation and is not necessarily a bed-wait interval.",
            "Bed-stage duration uses bed assignment/transfer timestamps and ends at the next transfer, discharge, or generated_at. It includes all care and operational activity while assigned to that bed and is not staff utilization.",
            "All measures are UTC, grouped by scheduled appointment or admission creation date, aggregated in SQL, and omit patient and staff identifiers. Audit/history measures are unavailable before the relevant records were instrumented.",
        ],
    }


# =====================================================================
# 1. EXECUTIVE COMMAND CENTER (SPATIAL CENSUS, ALOS, READMISSIONS & ARPOB)
# =====================================================================

@router.get("/executive-command-center")
def get_executive_command_center(
    response: Response,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:read", "adt:read", "wards:read", "iam:read")),
) -> Dict[str, Any]:
    start_day, end_day, start_at, end_at = _resolve_analytics_window(
        start_date, end_date
    )
    _disable_analytics_caching(response)

    bed_status_counts = {s.value: 0 for s in BedStatusEnum}
    bed_type_counts = {bt.value: {"total": 0, "occupied": 0} for bt in BedTypeEnum}
    room_type_counts = {rt.value: {"total_beds": 0, "occupied_beds": 0} for rt in RoomTypeEnum}
    bed_status_rows = db.execute(
        select(Bed.status, func.count(Bed.id)).group_by(Bed.status)
    ).all()
    for bed_status, count in bed_status_rows:
        bed_status_counts[bed_status.value] = int(count)
    occupied_beds = bed_status_counts.get("OCCUPIED", 0)
    bed_totals = db.execute(
        select(
            func.count(Bed.id).label("total"),
            func.coalesce(func.sum(Bed.base_daily_rate_paise), 0).label("capacity"),
            func.coalesce(
                func.sum(
                    case(
                        (Bed.status == BedStatusEnum.OCCUPIED, Bed.base_daily_rate_paise),
                        else_=0,
                    )
                ),
                0,
            ).label("occupied_rate"),
        )
    ).one()
    total_beds = int(bed_totals.total or 0)
    daily_potential_rack_capacity_paise = int(bed_totals.capacity or 0)
    daily_occupied_bed_revenue_paise = int(bed_totals.occupied_rate or 0)

    for bed_type, count, occupied_count in db.execute(
        select(
            Bed.bed_type,
            func.count(Bed.id),
            func.sum(case((Bed.status == BedStatusEnum.OCCUPIED, 1), else_=0)),
        ).group_by(Bed.bed_type)
    ).all():
        bed_type_counts[bed_type.value] = {
            "total": int(count),
            "occupied": int(occupied_count or 0),
        }
    for room_type, count, occupied_count in db.execute(
        select(
            PhysicalRoom.room_type,
            func.count(Bed.id),
            func.sum(case((Bed.status == BedStatusEnum.OCCUPIED, 1), else_=0)),
        )
        .join(PhysicalRoom, PhysicalRoom.id == Bed.room_id)
        .group_by(PhysicalRoom.room_type)
    ).all():
        room_type_counts[room_type.value] = {
            "total_beds": int(count),
            "occupied_beds": int(occupied_count or 0),
        }
    ward_type_occupancy: Dict[str, Dict[str, int | float]] = {}
    for ward_type, count, occupied_count, available_count in db.execute(
        select(
            Ward.ward_type,
            func.count(Bed.id),
            func.sum(case((Bed.status == BedStatusEnum.OCCUPIED, 1), else_=0)),
            func.sum(case((Bed.status == BedStatusEnum.AVAILABLE, 1), else_=0)),
        )
        .join(PhysicalRoom, PhysicalRoom.id == Bed.room_id)
        .join(Ward, Ward.id == PhysicalRoom.ward_id)
        .group_by(Ward.ward_type)
    ).all():
        occupied = int(occupied_count or 0)
        total = int(count)
        ward_type_occupancy[ward_type.value] = {
            "total_beds": total,
            "occupied_beds": occupied,
            "available_beds": int(available_count or 0),
            "occupancy_rate_pct": _pct(occupied, total),
        }

    total_transfers = int(
        db.scalar(
            select(func.count(BedTransferLog.id)).where(
                BedTransferLog.transfer_time >= start_at,
                BedTransferLog.transfer_time < end_at,
            )
        )
        or 0
    )

    total_encounters = int(
        db.scalar(
            select(func.count(PatientEncounter.id)).where(
                PatientEncounter.start_time >= start_at,
                PatientEncounter.start_time < end_at,
            )
        )
        or 0
    )
    active_rows = db.execute(
        select(PatientEncounter.encounter_type, func.count(PatientEncounter.id))
        .where(
            PatientEncounter.status == EncounterStatusEnum.ACTIVE,
            PatientEncounter.start_time >= start_at,
            PatientEncounter.start_time < end_at,
        )
        .group_by(PatientEncounter.encounter_type)
    ).all()
    active_by_type = {t.value: 0 for t in EncounterTypeEnum}
    for encounter_type, count in active_rows:
        active_by_type[encounter_type.value] = int(count)

    admissions_started = int(
        db.scalar(
            select(func.count(InpatientAdmission.id))
            .join(PatientEncounter, PatientEncounter.id == InpatientAdmission.encounter_id)
            .where(
                PatientEncounter.start_time >= start_at,
                PatientEncounter.start_time < end_at,
            )
        )
        or 0
    )
    if db.get_bind().dialect.name == "sqlite":
        stay_hours = (
            func.julianday(InpatientAdmission.discharge_time)
            - func.julianday(PatientEncounter.start_time)
        ) * 24
    else:
        stay_hours = extract(
            "epoch", InpatientAdmission.discharge_time - PatientEncounter.start_time
        ) / 3600
    valid_stay_hours = case((stay_hours < 0.1, 0.1), else_=stay_hours)
    discharged_filters = (
        InpatientAdmission.discharge_time.is_not(None),
        InpatientAdmission.discharge_time >= start_at,
        InpatientAdmission.discharge_time < end_at,
    )
    los_rows = db.execute(
        select(
            InpatientAdmission.admission_type,
            func.count(InpatientAdmission.id),
            func.avg(valid_stay_hours),
        )
        .join(PatientEncounter, PatientEncounter.id == InpatientAdmission.encounter_id)
        .where(*discharged_filters)
        .group_by(InpatientAdmission.admission_type)
    ).all()
    los_by_admission_type = {at.value: 0.0 for at in AdmissionTypeEnum}
    discharged_admissions_count = 0
    total_stay_hours = 0.0
    for admission_type, count, average_hours in los_rows:
        discharged_admissions_count += int(count)
        los_by_admission_type[admission_type.value] = round(float(average_hours or 0), 2)
        total_stay_hours += float(average_hours or 0) * int(count)
    avg_los_hours = (
        round(total_stay_hours / discharged_admissions_count, 2)
        if discharged_admissions_count
        else 0.0
    )
    disposition_counts = {d.value: 0 for d in DischargeDispositionEnum}
    disposition_rows = db.execute(
        select(
            InpatientAdmission.discharge_disposition,
            func.count(InpatientAdmission.id),
        )
        .where(*discharged_filters)
        .group_by(InpatientAdmission.discharge_disposition)
    ).all()
    for disposition, count in disposition_rows:
        if disposition:
            disposition_counts[disposition.value] = int(count)
    admission_patient_counts = (
        select(
            PatientEncounter.patient_id.label("patient_id"),
            func.count(InpatientAdmission.id).label("admission_count"),
        )
        .select_from(InpatientAdmission)
        .join(PatientEncounter, PatientEncounter.id == InpatientAdmission.encounter_id)
        .where(
            PatientEncounter.start_time >= start_at,
            PatientEncounter.start_time < end_at,
        )
        .group_by(PatientEncounter.patient_id)
        .subquery()
    )
    readmission_summary = db.execute(
        select(
            func.count().label("patients"),
            func.sum(
                case((admission_patient_counts.c.admission_count > 1, 1), else_=0)
            ).label("readmitted"),
        )
    ).one()
    readmission_rate_pct = _pct(
        int(readmission_summary.readmitted or 0),
        int(readmission_summary.patients or 0),
    )
    ama_walkout_rate_pct = _pct(
        disposition_counts.get("AGAINST_MEDICAL_ADVICE", 0),
        discharged_admissions_count,
    )
    inpatient_mortality_rate_pct = _pct(
        disposition_counts.get("DECEASED", 0), discharged_admissions_count
    )

    opd_funnel = {s.value: 0 for s in AppointmentStatusEnum}
    for appointment_status, count in db.execute(
        select(Appointment.status, func.count(Appointment.id))
        .where(
            Appointment.appointment_time >= start_at,
            Appointment.appointment_time < end_at,
        )
        .group_by(Appointment.status)
    ).all():
        opd_funnel[appointment_status.value] = int(count)
    total_appts = sum(opd_funnel.values())

    total_net_revenue_paise = int(
        db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        Invoice.total_amount_paise - Invoice.discount_amount_paise
                    ),
                    0,
                )
            ).where(
                Invoice.created_at >= start_at,
                Invoice.created_at < end_at,
                Invoice.status.in_(
                    [
                        InvoiceStatusEnum.UNPAID,
                        InvoiceStatusEnum.PARTIAL,
                        InvoiceStatusEnum.PAID,
                    ]
                ),
            )
        )
        or 0
    )
    arpob_paise = total_net_revenue_paise // occupied_beds if occupied_beds > 0 else 0
    arpu_encounter_paise = total_net_revenue_paise // total_encounters if total_encounters > 0 else 0

    return {
        "window": _window_metadata(start_day, end_day),
        "bed_census": {
            "total_beds": total_beds,
            "status_breakdown": bed_status_counts,
            "overall_occupancy_rate_pct": _pct(occupied_beds, total_beds),
            "daily_occupied_bed_rate_accrual_paise": daily_occupied_bed_revenue_paise,
            "daily_potential_rack_capacity_paise": daily_potential_rack_capacity_paise,
            "bed_turnover_events_count": total_transfers,
            "by_ward_type": ward_type_occupancy,
            "by_room_type": room_type_counts,
            "by_bed_type": bed_type_counts,
        },
        "encounters_and_ipd_alos": {
            "total_encounters": total_encounters,
            "active_encounters_by_type": active_by_type,
            "total_ipd_admissions": admissions_started,
            "discharged_ipd_admissions": discharged_admissions_count,
            "average_length_of_stay_hours": avg_los_hours,
            "average_length_of_stay_days": round(avg_los_hours / 24.0, 2),
            "alos_hours_by_admission_type": los_by_admission_type,
            "discharge_dispositions": disposition_counts,
            "readmission_rate_pct": readmission_rate_pct,
            "ama_walkout_rate_pct": ama_walkout_rate_pct,
            "inpatient_mortality_rate_pct": inpatient_mortality_rate_pct,
        },
        "opd_funnel": {
            "total_appointments": total_appts,
            "status_breakdown": opd_funnel,
            "completion_rate_pct": _pct(opd_funnel["COMPLETED"], total_appts),
            "no_show_rate_pct": _pct(opd_funnel["NO_SHOW"], total_appts),
        },
        "unit_economics": {
            "total_net_billed_paise": total_net_revenue_paise,
            "arpob_paise": arpob_paise,
            "arpu_per_encounter_paise": arpu_encounter_paise,
        },
        "data_notes": [
            "Bed census, occupied-bed daily rate, and rack capacity are current snapshots; bed transfers are counted by transfer_time in the selected period.",
            "Encounter, admission-start, appointment, and invoice metrics use start_time, encounter start_time, appointment_time, and invoice creation time respectively.",
            "Discharge counts and length of stay use discharge_time in the selected window; average stay is calculated from encounter start to discharge.",
            "The readmission rate is a same-window repeat-admission proxy, not a clinically defined 30-day readmission rate.",
            "Revenue per occupied bed divides period net billings by current occupied beds; it is not standard ARPOB, which requires occupied bed-days.",
        ],
    }


# =====================================================================
# 2. CFO FINANCIAL REVENUE CYCLE, A/R AGING, MOU TARIFFS & TPA PIPELINE
# =====================================================================

@router.get("/financial-revenue-cycle")
def get_financial_revenue_cycle(
    response: Response,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:read")),
) -> Dict[str, Any]:
    start_day, end_day, start_at, end_at = _resolve_analytics_window(
        start_date, end_date
    )
    _disable_analytics_caching(response)
    now_utc = datetime.now(timezone.utc)
    billable_statuses = [
        InvoiceStatusEnum.UNPAID,
        InvoiceStatusEnum.PARTIAL,
        InvoiceStatusEnum.PAID,
    ]
    non_void_window = (
        Invoice.created_at >= start_at,
        Invoice.created_at < end_at,
        Invoice.status != InvoiceStatusEnum.VOID,
    )
    financial_row = db.execute(
        select(
            func.coalesce(func.sum(Invoice.total_amount_paise), 0).label("gross"),
            func.coalesce(func.sum(Invoice.discount_amount_paise), 0).label("discounts"),
            func.coalesce(
                func.sum(Invoice.total_amount_paise - Invoice.discount_amount_paise), 0
            ).label("net"),
        ).where(*non_void_window, Invoice.status.in_(billable_statuses))
    ).one()
    gross_billed_paise = int(financial_row.gross or 0)
    total_discounts_paise = int(financial_row.discounts or 0)
    net_billed_paise = int(financial_row.net or 0)

    aging_cutoffs = [
        ("0_to_30_days_paise", Invoice.created_at >= now_utc - timedelta(days=30)),
        (
            "31_to_60_days_paise",
            (Invoice.created_at < now_utc - timedelta(days=30))
            & (Invoice.created_at >= now_utc - timedelta(days=60)),
        ),
        (
            "61_to_90_days_paise",
            (Invoice.created_at < now_utc - timedelta(days=60))
            & (Invoice.created_at >= now_utc - timedelta(days=90)),
        ),
        ("over_90_days_paise", Invoice.created_at < now_utc - timedelta(days=90)),
    ]
    balance = Invoice.total_amount_paise - Invoice.discount_amount_paise - Invoice.amount_paid_paise
    ar_row = db.execute(
        select(
            func.coalesce(func.sum(balance), 0).label("total"),
            *[
                func.coalesce(
                    func.sum(case((condition, balance), else_=0)), 0
                ).label(bucket_name)
                for bucket_name, condition in aging_cutoffs
            ],
        ).where(
            Invoice.status.in_(
                [InvoiceStatusEnum.UNPAID, InvoiceStatusEnum.PARTIAL]
            )
        )
    ).one()
    accounts_receivable_paise = int(ar_row.total or 0)
    ar_aging_buckets_paise = {
        bucket_name: int(getattr(ar_row, bucket_name) or 0)
        for bucket_name, _ in aging_cutoffs
    }

    stream_prefixes = [
        ("opd_consultations_paise", "OPD CONSULTATION:%"),
        ("ipd_bed_stay_paise", "IPD BED CHARGE:%"),
        ("diagnostics_lis_ris_paise", "DIAGNOSTICS [%"),
        ("pharmacy_dispensing_paise", "PHARMACY:%"),
        ("surgery_and_ot_paise", "SURGERY [%"),
        ("surgical_implants_paise", "SURGICAL IMPLANT [%"),
        ("blood_bank_paise", "BLOOD BANK [%"),
    ]
    line_amount = InvoiceLineItem.total_price_paise
    stream_select = [
        func.coalesce(
            func.sum(
                case(
                    (InvoiceLineItem.description.like(prefix), line_amount),
                    else_=0,
                )
            ),
            0,
        ).label(name)
        for name, prefix in stream_prefixes
    ]
    stream_select.append(
        func.coalesce(
            func.sum(
                case(
                    (
                        ~InvoiceLineItem.description.like("OPD CONSULTATION:%")
                        & ~InvoiceLineItem.description.like("IPD BED CHARGE:%")
                        & ~InvoiceLineItem.description.like("DIAGNOSTICS [%")
                        & ~InvoiceLineItem.description.like("PHARMACY:%")
                        & ~InvoiceLineItem.description.like("SURGERY [%")
                        & ~InvoiceLineItem.description.like("SURGICAL IMPLANT [%")
                        & ~InvoiceLineItem.description.like("BLOOD BANK [%"),
                        line_amount,
                    ),
                    else_=0,
                )
            ),
            0,
        ).label("other_catalog_services_paise")
    )
    stream_row = db.execute(
        select(*stream_select)
        .join(Invoice, Invoice.id == InvoiceLineItem.invoice_id)
        .where(*non_void_window, Invoice.status.in_(billable_statuses))
    ).one()
    streams = {
        name: int(getattr(stream_row, name) or 0)
        for name, _ in stream_prefixes
    }
    streams["other_catalog_services_paise"] = int(
        stream_row.other_catalog_services_paise or 0
    )

    top_service_rows = db.execute(
        select(
            InvoiceLineItem.service_id,
            ServiceCatalog.code,
            ServiceCatalog.name,
            func.sum(InvoiceLineItem.total_price_paise).label("revenue"),
        )
        .join(Invoice, Invoice.id == InvoiceLineItem.invoice_id)
        .join(ServiceCatalog, ServiceCatalog.id == InvoiceLineItem.service_id)
        .where(*non_void_window, Invoice.status.in_(billable_statuses))
        .group_by(InvoiceLineItem.service_id, ServiceCatalog.code, ServiceCatalog.name)
        .order_by(func.sum(InvoiceLineItem.total_price_paise).desc())
        .limit(10)
    ).all()
    top_services = [
        {
            "service_id": row.service_id,
            "code": row.code,
            "name": row.name,
            "revenue_paise": int(row.revenue or 0),
        }
        for row in top_service_rows
    ]

    collected_total = db.scalar(
        select(func.coalesce(func.sum(PaymentReceipt.amount_paid_paise), 0))
        .join(Invoice, Invoice.id == PaymentReceipt.invoice_id)
        .where(
            PaymentReceipt.created_at >= start_at,
            PaymentReceipt.created_at < end_at,
            Invoice.status != InvoiceStatusEnum.VOID,
        )
    )
    payment_method_rows = db.execute(
        select(
            PaymentReceipt.payment_method,
            func.sum(PaymentReceipt.amount_paid_paise),
        )
        .join(Invoice, Invoice.id == PaymentReceipt.invoice_id)
        .where(
            PaymentReceipt.created_at >= start_at,
            PaymentReceipt.created_at < end_at,
            Invoice.status != InvoiceStatusEnum.VOID,
        )
        .group_by(PaymentReceipt.payment_method)
    ).all()
    payment_method_mix = {
        str(method.value): int(amount or 0)
        for method, amount in payment_method_rows
    }

    preauth_rows = db.execute(
        select(InsurancePreAuth.status, func.count(InsurancePreAuth.id))
        .where(
            InsurancePreAuth.created_at >= start_at,
            InsurancePreAuth.created_at < end_at,
        )
        .group_by(InsurancePreAuth.status)
    ).all()
    preauth_by_status = {status.value: 0 for status in PreAuthStatusEnum}
    for preauth_status, count in preauth_rows:
        preauth_by_status[preauth_status.value] = int(count)
    preauth_count = sum(preauth_by_status.values())

    claim_summary = db.execute(
        select(
            func.count(InsuranceClaim.id).label("count"),
            func.coalesce(func.sum(InsuranceClaim.claimed_amount_paise), 0).label("claimed"),
            func.coalesce(func.sum(InsuranceClaim.insurer_payable_paise), 0).label("payable"),
            func.coalesce(func.sum(InsuranceClaim.patient_copay_paise), 0).label("copay"),
            func.coalesce(func.sum(InsuranceClaim.settled_amount_paise), 0).label("settled"),
        ).where(
            InsuranceClaim.created_at >= start_at,
            InsuranceClaim.created_at < end_at,
        )
    ).one()
    claim_provider_rows = db.execute(
        select(
            InsuranceProvider.name,
            func.count(InsuranceClaim.id).label("claims_count"),
            func.sum(InsuranceClaim.insurer_payable_paise).label("payable"),
            func.sum(InsuranceClaim.settled_amount_paise).label("settled"),
            func.sum(
                case(
                    (
                        InsuranceClaim.insurer_payable_paise
                        > InsuranceClaim.settled_amount_paise,
                        InsuranceClaim.insurer_payable_paise
                        - InsuranceClaim.settled_amount_paise,
                    ),
                    else_=0,
                )
            ).label("pending"),
        )
        .join(
            PatientInsurancePolicy,
            PatientInsurancePolicy.id == InsuranceClaim.policy_id,
        )
        .join(
            InsuranceProvider,
            InsuranceProvider.id == PatientInsurancePolicy.provider_id,
        )
        .where(
            InsuranceClaim.created_at >= start_at,
            InsuranceClaim.created_at < end_at,
        )
        .group_by(InsuranceProvider.name)
    ).all()
    claim_provider_breakdown = {
        name: {
            "claims_count": int(count or 0),
            "insurer_payable_paise": int(payable or 0),
            "settled_amount_paise": int(settled or 0),
            "pending_receivable_paise": int(pending or 0),
        }
        for name, count, payable, settled, pending in claim_provider_rows
    }
    total_claimed_paise = int(claim_summary.claimed or 0)
    total_insurer_payable_paise = int(claim_summary.payable or 0)
    total_patient_copay_paise = int(claim_summary.copay or 0)
    total_insurer_settled_paise = int(claim_summary.settled or 0)
    unsettled_tpa_receivable_paise = max(
        0, total_insurer_payable_paise - total_insurer_settled_paise
    )

    tariff_summary = db.execute(
        select(
            func.count(InsurancePlanTariff.id).label("count"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            InsurancePlanTariff.is_covered.is_(True),
                            case(
                                (
                                    ServiceCatalog.base_price_paise
                                    > InsurancePlanTariff.tariff_price_paise,
                                    ServiceCatalog.base_price_paise
                                    - InsurancePlanTariff.tariff_price_paise,
                                ),
                                else_=0,
                            ),
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("savings_index"),
        )
        .join(
            ServiceCatalog,
            ServiceCatalog.id == InsurancePlanTariff.service_id,
        )
    ).one()
    current_deposit_liability = db.scalar(
        select(func.coalesce(func.sum(PatientDeposit.remaining_balance_paise), 0))
    )
    deposits_collected = db.scalar(
        select(func.coalesce(func.sum(PatientDeposit.amount_paise), 0)).where(
            PatientDeposit.created_at >= start_at,
            PatientDeposit.created_at < end_at,
        )
    )
    refund_summary = db.execute(
        select(
            func.count(RefundVoucher.id),
            func.coalesce(func.sum(RefundVoucher.refund_amount_paise), 0),
        ).where(
            RefundVoucher.created_at >= start_at,
            RefundVoucher.created_at < end_at,
        )
    ).one()

    return {
        "window": _window_metadata(start_day, end_day),
        "revenue_waterfall": {
            "gross_billed_paise": gross_billed_paise,
            "total_discounts_paise": total_discounts_paise,
            "discount_leakage_rate_pct": _pct(total_discounts_paise, gross_billed_paise),
            "net_billed_paise": net_billed_paise,
            "total_collected_paise": int(collected_total or 0),
            "collection_efficiency_pct": _pct(int(collected_total or 0), net_billed_paise),
            "accounts_receivable_paise": accounts_receivable_paise,
            "ar_aging_buckets_paise": ar_aging_buckets_paise,
        },
        "revenue_by_clinical_stream": streams,
        "top_revenue_generating_services": top_services,
        "collections_by_payment_method": payment_method_mix,
        "insurance_and_tpa_pipeline": {
            "total_preauths": preauth_count,
            "preauth_status_breakdown": preauth_by_status,
            "preauth_approval_rate_pct": _pct(preauth_by_status["APPROVED"], preauth_count),
            "total_claims": int(claim_summary.count or 0),
            "total_claimed_paise": total_claimed_paise,
            "total_insurer_payable_paise": total_insurer_payable_paise,
            "total_patient_copay_paise": total_patient_copay_paise,
            "total_insurer_settled_paise": total_insurer_settled_paise,
            "claim_settlement_ratio_pct": _pct(total_insurer_settled_paise, total_insurer_payable_paise),
            "unsettled_tpa_receivable_paise": unsettled_tpa_receivable_paise,
            "configured_mou_tariff_mappings_count": int(tariff_summary.count or 0),
            "configured_mou_savings_index_paise": int(tariff_summary.savings_index or 0),
            "by_provider": claim_provider_breakdown,
        },
        "deposits_and_refunds_ledger": {
            "total_deposits_collected_paise": int(deposits_collected or 0),
            "active_unadjusted_wallet_liability_paise": int(current_deposit_liability or 0),
            "total_refund_vouchers_count": int(refund_summary[0] or 0),
            "total_refunds_paid_paise": int(refund_summary[1] or 0),
        },
        "data_notes": [
            "Invoice billings and line-item streams use invoice creation date in the requested UTC window and exclude draft and void invoices.",
            "Collections, deposits, and refunds use their own record creation dates in the requested UTC window; void-invoice receipts are excluded.",
            "Receivables and deposit liability are current as-of snapshots, not period flows. Receivable aging uses days since invoice record creation because patient-invoice due dates are not stored.",
            "Claim amounts and settlement totals reflect each in-window claim's current mutable state; settlement events have no independent history or timestamp.",
            "Insurance tariffs are the current configured rate-card snapshot, not period transactions. Their savings index is a per-unit configured difference, not realized savings.",
        ],
    }


# =====================================================================
# 3. PATIENT DEMOGRAPHICS, ABHA ADOPTION & EPIDEMIOLOGY PYRAMID
# =====================================================================

@router.get("/patient-demographics-and-epidemiology")
def get_patient_demographics_and_epidemiology(
    response: Response,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("patients:read", "opd:read", "adt:read")),
) -> Dict[str, Any]:
    start_day, end_day, start_at, end_at = _resolve_analytics_window(
        start_date, end_date
    )
    _disable_analytics_caching(response)
    now_utc = datetime.now(timezone.utc)
    today = now_utc.date()

    def years_ago(years: int) -> datetime:
        year = today.year - years
        day = min(today.day, calendar.monthrange(year, today.month)[1])
        return datetime(
            year, today.month, day, tzinfo=timezone.utc
        )

    age_conditions = {
        "0_to_12_pediatric": (Patient.date_of_birth > years_ago(13))
        & (Patient.date_of_birth <= now_utc),
        "13_to_18_adolescent": (Patient.date_of_birth <= years_ago(13))
        & (Patient.date_of_birth > years_ago(19)),
        "19_to_35_young_adult": (Patient.date_of_birth <= years_ago(19))
        & (Patient.date_of_birth > years_ago(36)),
        "36_to_59_middle_aged": (Patient.date_of_birth <= years_ago(36))
        & (Patient.date_of_birth > years_ago(60)),
        "60_plus_senior_citizen": Patient.date_of_birth <= years_ago(60),
    }
    population_row = db.execute(
        select(
            func.count(Patient.id).label("total"),
            func.sum(case((Patient.abha_number.is_not(None), 1), else_=0)).label(
                "abha_linked"
            ),
            func.sum(case((Patient.date_of_birth > now_utc, 1), else_=0)).label(
                "future_dob"
            ),
            *[
                func.sum(case((condition, 1), else_=0)).label(name)
                for name, condition in age_conditions.items()
            ],
        ).where(Patient.is_active.is_(True))
    ).one()
    gender_rows = db.execute(
        select(Patient.gender, func.count(Patient.id))
        .where(Patient.is_active.is_(True))
        .group_by(Patient.gender)
    ).all()
    blood_group_rows = db.execute(
        select(Patient.blood_group, func.count(Patient.id))
        .where(
            Patient.is_active.is_(True),
            Patient.blood_group.is_not(None),
        )
        .group_by(Patient.blood_group)
    ).all()
    total_active_patients = int(population_row.total or 0)
    age_cohorts = {
        name: int(getattr(population_row, name) or 0)
        for name in age_conditions
    }
    gender_counts = {gender.value: 0 for gender in GenderEnum}
    for gender, count in gender_rows:
        gender_counts[gender.value] = int(count)
    blood_group_counts = {group.value: 0 for group in BloodGroupEnum}
    for blood_group, count in blood_group_rows:
        blood_group_counts[blood_group.value] = int(count)

    allergy_rows = db.execute(
        select(PatientAllergy.severity, func.count(PatientAllergy.id))
        .where(PatientAllergy.is_active.is_(True))
        .group_by(PatientAllergy.severity)
    ).all()
    allergy_by_severity = {severity.value: 0 for severity in SeverityEnum}
    for severity, count in allergy_rows:
        allergy_by_severity[severity.value] = int(count)
    allergy_category_rows = db.execute(
        select(AllergyMaster.category, func.count(PatientAllergy.id))
        .join(AllergyMaster, AllergyMaster.id == PatientAllergy.allergy_id)
        .where(PatientAllergy.is_active.is_(True))
        .group_by(AllergyMaster.category)
    ).all()
    allergy_by_category = {category.value: 0 for category in AllergyCategoryEnum}
    for category, count in allergy_category_rows:
        allergy_by_category[category.value] = int(count)
    active_allergy_count = sum(allergy_by_severity.values())

    diagnosis_rows = db.execute(
        select(
            EncounterDiagnosis.diagnosis_type,
            func.count(EncounterDiagnosis.id).label("count"),
            func.sum(case((DiagnosisMaster.is_billable.is_(True), 1), else_=0)).label(
                "billable_count"
            ),
        )
        .join(
            DiagnosisMaster,
            DiagnosisMaster.id == EncounterDiagnosis.diagnosis_id,
        )
        .where(
            EncounterDiagnosis.created_at >= start_at,
            EncounterDiagnosis.created_at < end_at,
        )
        .group_by(EncounterDiagnosis.diagnosis_type)
    ).all()
    diag_by_type = {diagnosis_type.value: 0 for diagnosis_type in DiagnosisTypeEnum}
    total_coded_diagnoses = 0
    billable_diag_count = 0
    for diagnosis_type, count, billable_count in diagnosis_rows:
        diag_by_type[diagnosis_type.value] = int(count)
        total_coded_diagnoses += int(count)
        billable_diag_count += int(billable_count or 0)

    diagnosis_cohort_rows = db.execute(
        select(
            DiagnosisMaster.icd10_code,
            DiagnosisMaster.short_description,
            func.count(EncounterDiagnosis.id).label("record_count"),
            func.count(func.distinct(PatientEncounter.patient_id)).label(
                "unique_patient_count"
            ),
        )
        .join(
            EncounterDiagnosis,
            EncounterDiagnosis.diagnosis_id == DiagnosisMaster.id,
        )
        .join(
            PatientEncounter,
            PatientEncounter.id == EncounterDiagnosis.encounter_id,
        )
        .where(
            EncounterDiagnosis.created_at >= start_at,
            EncounterDiagnosis.created_at < end_at,
        )
        .group_by(DiagnosisMaster.id)
        .order_by(
            func.count(func.distinct(PatientEncounter.patient_id)).desc(),
            func.count(EncounterDiagnosis.id).desc(),
            DiagnosisMaster.icd10_code.asc(),
        )
        .limit(20)
    ).all()

    patient_visit_rows = db.execute(
        select(
            Patient.gender,
            func.count(func.distinct(Patient.id)).label("unique_patients"),
        )
        .join(Appointment, Appointment.patient_id == Patient.id)
        .where(
            Appointment.appointment_time >= start_at,
            Appointment.appointment_time < end_at,
        )
        .group_by(Patient.gender)
    ).all()
    patients_seen_by_gender = {gender.value: 0 for gender in GenderEnum}
    for gender, patient_count in patient_visit_rows:
        patients_seen_by_gender[gender.value] = int(patient_count)

    age_cohort_expression = case(
        *[
            (condition, cohort_name)
            for cohort_name, condition in age_conditions.items()
        ],
        else_="future_birth_date",
    )
    patient_visit_cohort_rows = db.execute(
        select(
            age_cohort_expression.label("age_cohort"),
            Patient.gender,
            func.count(func.distinct(Patient.id)).label("unique_patients"),
        )
        .join(Appointment, Appointment.patient_id == Patient.id)
        .where(
            Appointment.appointment_time >= start_at,
            Appointment.appointment_time < end_at,
        )
        .group_by(age_cohort_expression, Patient.gender)
    ).all()
    visit_age_gender_cross_tab = {
        cohort_name: {gender.value: 0 for gender in GenderEnum}
        for cohort_name in (*age_conditions.keys(), "future_birth_date")
    }
    for cohort_name, gender, patient_count in patient_visit_cohort_rows:
        visit_age_gender_cross_tab[cohort_name][gender.value] = int(patient_count)

    appointments_by_department_rows = db.execute(
        select(
            Department.id,
            Department.name,
            func.count(Appointment.id).label("appointment_count"),
            func.count(func.distinct(Appointment.patient_id)).label(
                "unique_patient_count"
            ),
            func.sum(
                case(
                    (Appointment.status == AppointmentStatusEnum.COMPLETED, 1),
                    else_=0,
                )
            ).label("completed_count"),
            func.sum(
                case(
                    (Appointment.status == AppointmentStatusEnum.CANCELLED, 1),
                    else_=0,
                )
            ).label("cancelled_count"),
            func.sum(
                case(
                    (Appointment.status == AppointmentStatusEnum.NO_SHOW, 1),
                    else_=0,
                )
            ).label("no_show_count"),
        )
        .join(Department, Department.id == Appointment.department_id)
        .where(
            Appointment.appointment_time >= start_at,
            Appointment.appointment_time < end_at,
        )
        .group_by(Department.id, Department.name)
        .order_by(func.count(Appointment.id).desc(), Department.name.asc())
    ).all()

    new_patient_rows = db.execute(
        select(Patient.gender, func.count(Patient.id))
        .where(Patient.created_at >= start_at, Patient.created_at < end_at)
        .group_by(Patient.gender)
    ).all()
    new_patient_registrations_by_gender = {
        gender.value: 0 for gender in GenderEnum
    }
    for gender, patient_count in new_patient_rows:
        new_patient_registrations_by_gender[gender.value] = int(patient_count)

    return {
        "window": _window_metadata(start_day, end_day),
        "population_as_of": now_utc,
        "mpi_population_summary": {
            "total_active_patients": total_active_patients,
            "abha_linked_patients_count": int(population_row.abha_linked or 0),
            "abha_adoption_rate_pct": _pct(
                int(population_row.abha_linked or 0), total_active_patients
            ),
            "age_cohort_pyramid": age_cohorts,
            "gender_distribution": gender_counts,
            "blood_group_distribution": blood_group_counts,
            "future_birth_dates_excluded_from_age_cohorts": int(
                population_row.future_dob or 0
            ),
        },
        "allergy_risk_stratification": {
            "total_charted_allergies": active_allergy_count,
            "by_severity": allergy_by_severity,
            "by_category": allergy_by_category,
            "as_of": now_utc,
        },
        "icd10_coding_analytics": {
            "total_coded_diagnoses": total_coded_diagnoses,
            "by_diagnosis_type": diag_by_type,
            "insurance_billable_diagnoses_count": billable_diag_count,
            "billable_coding_rate_pct": _pct(
                billable_diag_count, total_coded_diagnoses
            ),
        },
        "service_access_and_demand": {
            "unique_patients_with_appointments_by_gender": patients_seen_by_gender,
            "unique_patients_with_appointments_by_current_age_and_gender": (
                visit_age_gender_cross_tab
            ),
            "appointments_by_department": [
                {
                    "department_id": row.id,
                    "department_name": row.name,
                    "appointment_count": int(row.appointment_count or 0),
                    "unique_patient_count": int(row.unique_patient_count or 0),
                    "completed_count": int(row.completed_count or 0),
                    "cancelled_count": int(row.cancelled_count or 0),
                    "no_show_count": int(row.no_show_count or 0),
                }
                for row in appointments_by_department_rows
            ],
            "new_patient_registrations_by_gender": (
                new_patient_registrations_by_gender
            ),
            "window": _window_metadata(start_day, end_day),
            "interpretation": (
                "Appointments measure recorded service requests, not all unmet "
                "community need; cancelled and no-show visits are not counted "
                "as completed consultations."
            ),
        },
        "diagnosis_patient_cohorts": [
            {
                "icd10_code": row.icd10_code,
                "diagnosis": row.short_description,
                "diagnosis_record_count": int(row.record_count or 0),
                "unique_patient_count": int(row.unique_patient_count or 0),
                "window": _window_metadata(start_day, end_day),
            }
            for row in diagnosis_cohort_rows
        ],
        "data_notes": [
            "Population demographics, ABHA linkage, and age cohorts are current snapshots of active patients, independent of the selected diagnosis window.",
            "Age uses calendar-year birthday boundaries in UTC; the non-overlapping cohorts are 0-12, 13-18, 19-35, 36-59, and 60+. Patients with future birth dates are excluded from age cohorts and counted separately.",
            "Allergy counts are current active allergy records, not newly reported allergy events in the selected period.",
            "Diagnosis coding metrics include diagnosis records created in the requested UTC window and classify billability using the diagnosis catalog's current setting.",
            "Diagnosis patient cohorts count distinct patients with coded records in the selected window; they are service-record cohorts, not population prevalence estimates.",
            "Appointment demand includes only appointments recorded in Avinya HMS. It cannot quantify people who never contacted the hospital or services unavailable to them.",
            "Unique-patient gender counts use patients with appointments in the window; registration counts use patient-record creation time and do not include patient identifiers.",
            "Age-by-gender appointment cohorts use the patient's current recorded date of birth, not age at historical appointment time.",
            "All results are aggregates and contain no patient identifiers.",
        ],
    }


# =====================================================================
# 4. RECORDED SERVICE DEMAND AND REFERRAL GAPS
# =====================================================================

@router.get("/service-demand")
def get_service_demand_analytics(
    response: Response,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(
        RequirePermission("executive_bi:read", "opd:read", "diagnostics:read")
    ),
) -> Dict[str, Any]:
    start_day, end_day, start_at, end_at = _resolve_analytics_window(
        start_date, end_date
    )
    _disable_analytics_caching(response)
    window_filter = (
        ServiceDemandRequest.created_at >= start_at,
        ServiceDemandRequest.created_at < end_at,
    )

    reason_rows = db.execute(
        select(ServiceDemandRequest.reason, func.count(ServiceDemandRequest.id))
        .where(*window_filter)
        .group_by(ServiceDemandRequest.reason)
    ).all()
    source_rows = db.execute(
        select(ServiceDemandRequest.source, func.count(ServiceDemandRequest.id))
        .where(*window_filter)
        .group_by(ServiceDemandRequest.source)
    ).all()
    status_rows = db.execute(
        select(ServiceDemandRequest.status, func.count(ServiceDemandRequest.id))
        .where(*window_filter)
        .group_by(ServiceDemandRequest.status)
    ).all()
    department_rows = db.execute(
        select(
            Department.id,
            Department.name,
            func.count(ServiceDemandRequest.id).label("request_count"),
            func.count(
                func.distinct(ServiceDemandRequest.patient_id)
            ).label("unique_patient_count"),
        )
        .join(
            Department,
            Department.id == ServiceDemandRequest.department_id,
        )
        .where(*window_filter)
        .group_by(Department.id, Department.name)
        .order_by(
            func.count(ServiceDemandRequest.id).desc(), Department.name.asc()
        )
    ).all()
    service_rows = db.execute(
        select(
            ServiceCatalog.id,
            ServiceCatalog.code,
            ServiceCatalog.name,
            func.count(ServiceDemandRequest.id).label("request_count"),
            func.count(
                func.distinct(ServiceDemandRequest.patient_id)
            ).label("unique_patient_count"),
        )
        .join(ServiceCatalog, ServiceCatalog.id == ServiceDemandRequest.service_id)
        .where(*window_filter)
        .group_by(ServiceCatalog.id, ServiceCatalog.code, ServiceCatalog.name)
        .order_by(func.count(ServiceDemandRequest.id).desc(), ServiceCatalog.code)
        .limit(20)
    ).all()
    current_open_count = int(
        db.scalar(
            select(func.count(ServiceDemandRequest.id)).where(
                ServiceDemandRequest.status == ServiceDemandStatusEnum.OPEN
            )
        )
        or 0
    )
    uncatalogued_count = int(
        db.scalar(
            select(func.count(ServiceDemandRequest.id)).where(
                *window_filter,
                ServiceDemandRequest.service_id.is_(None),
            )
        )
        or 0
    )
    return {
        "window": _window_metadata(start_day, end_day),
        "requests_by_reason": {
            reason.value: 0 for reason in ServiceDemandReasonEnum
        }
        | {reason.value: int(count) for reason, count in reason_rows},
        "requests_by_source": {
            source.value: 0 for source in ServiceDemandSourceEnum
        }
        | {source.value: int(count) for source, count in source_rows},
        "requests_by_current_status": {
            status_value.value: 0 for status_value in ServiceDemandStatusEnum
        }
        | {status_value.value: int(count) for status_value, count in status_rows},
        "requests_by_department": [
            {
                "department_id": department_id,
                "department_name": department_name,
                "request_count": int(request_count or 0),
                "unique_patient_count": int(unique_patient_count or 0),
            }
            for department_id, department_name, request_count, unique_patient_count
            in department_rows
        ],
        "requests_by_catalog_service": [
            {
                "service_id": service_id,
                "service_code": code,
                "service_name": name,
                "request_count": int(request_count or 0),
                "unique_patient_count": int(unique_patient_count or 0),
            }
            for service_id, code, name, request_count, unique_patient_count
            in service_rows
        ],
        "uncatalogued_request_count": uncatalogued_count,
        "current_open_request_count": current_open_count,
        "data_notes": [
            "Windowed counts include demand requests created in the UTC date window; status is the request's current status, not status as-of the event date.",
            "Current open requests are an as-of snapshot and may include requests created outside the selected window.",
            "Uncatalogued requests are counted without returning free-text service descriptions to avoid exposing accidentally entered patient information.",
            "These data represent demand recorded by staff, not all community need, and are not a substitute for referral completion or patient outcome tracking.",
            "Unique-patient counts count distinct linked patient records; requests without a patient link are included in request totals only.",
        ],
    }


# =====================================================================
# 5. NEWS2 CLINICAL VITALS EARLY-WARNING & FLUID BALANCE OVERLOAD RADAR
# =====================================================================

@router.get("/clinical-vitals-and-early-warning")
def get_clinical_vitals_and_early_warning(
    response: Response,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read", "opd:read")),
) -> Dict[str, Any]:
    start_day, end_day, start_at, end_at = _resolve_analytics_window(
        start_date, end_date
    )
    _disable_analytics_caching(response)

    vital_alert_conditions = {
        "hypoxia_spo2_lt_92_count": EncounterVital.spo2_percent < 92,
        "hypertensive_crisis_sys_ge_180_count": EncounterVital.bp_systolic >= 180,
        "hypotension_shock_sys_lt_90_count": EncounterVital.bp_systolic < 90,
        "tachycardia_pulse_gt_120_count": EncounterVital.pulse_bpm > 120,
        "bradycardia_pulse_lt_50_count": EncounterVital.pulse_bpm < 50,
        "high_pyrexia_temp_ge_102f_count": EncounterVital.temperature_f >= 102.0,
    }
    bmi_conditions = {
        "underweight_lt_18_5": (EncounterVital.bmi.is_not(None))
        & (EncounterVital.bmi < 18.5),
        "normal_18_5_to_24_9": (EncounterVital.bmi >= 18.5)
        & (EncounterVital.bmi < 25.0),
        "overweight_25_to_29_9": (EncounterVital.bmi >= 25.0)
        & (EncounterVital.bmi < 30.0),
        "obese_ge_30": EncounterVital.bmi >= 30.0,
    }
    vital_row = db.execute(
        select(
            func.count(EncounterVital.id).label("total_vitals"),
            *[
                func.coalesce(
                    func.sum(case((condition, 1), else_=0)), 0
                ).label(name)
                for name, condition in {
                    **vital_alert_conditions,
                    **bmi_conditions,
                }.items()
            ],
        ).where(
            EncounterVital.created_at >= start_at,
            EncounterVital.created_at < end_at,
        )
    ).one()

    net_fluid_by_encounter = db.execute(
        select(
            FluidBalanceLog.encounter_id,
            func.sum(
                case(
                    (
                        FluidBalanceLog.direction == FluidDirectionEnum.INTAKE,
                        FluidBalanceLog.volume_ml,
                    ),
                    else_=-FluidBalanceLog.volume_ml,
                )
            ),
        )
        .where(
            FluidBalanceLog.recorded_at >= start_at,
            FluidBalanceLog.recorded_at < end_at,
        )
        .group_by(FluidBalanceLog.encounter_id)
    ).all()
    net_balances = [int(row[1] or 0) for row in net_fluid_by_encounter]

    return {
        "window": _window_metadata(start_day, end_day),
        "news2_early_warning_triggers": {
            name: int(getattr(vital_row, name) or 0)
            for name in vital_alert_conditions
        },
        "bmi_nutritional_distribution": {
            name: int(getattr(vital_row, name) or 0)
            for name in bmi_conditions
        },
        "vitals_recorded_count": int(vital_row.total_vitals or 0),
        "inpatient_fluid_balance_radar": {
            "monitored_encounters_count": len(net_balances),
            "net_intake_ge_2000ml_encounters_count": sum(
                1 for balance in net_balances if balance >= 2000
            ),
            "net_output_ge_1000ml_encounters_count": sum(
                1 for balance in net_balances if balance <= -1000
            ),
        },
        "data_notes": [
            "Vital sign thresholds are screening aggregates, not a validated NEWS2 score or clinical alert; they do not replace clinician review.",
            "Fluid totals are grouped per encounter within this date window and are not a rolling observation window or a clinical diagnosis of overload/dehydration.",
            "Vital readings are windowed by record creation time; fluid logs are windowed by recorded_at. This endpoint is not real-time surveillance or alerting.",
        ],
    }


# =====================================================================
# 5. NABH / JCI CLINICAL QUALITY, SAFETY, eMAR, ER/MLC, OT & LIS/PACS
# =====================================================================

@router.get("/clinical-quality-and-safety")
def get_clinical_quality_and_safety(
    response: Response,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:read", "adt:read", "diagnostics:read")),
) -> Dict[str, Any]:
    start_day, end_day, start_at, end_at = _resolve_analytics_window(
        start_date, end_date
    )
    _disable_analytics_caching(response)
    diag_rows = db.execute(
        select(
            DiagnosisMaster.icd10_code,
            DiagnosisMaster.short_description,
            func.count(EncounterDiagnosis.id).label("cases_count"),
        )
        .join(EncounterDiagnosis, EncounterDiagnosis.diagnosis_id == DiagnosisMaster.id)
        .where(
            EncounterDiagnosis.created_at >= start_at,
            EncounterDiagnosis.created_at < end_at,
        )
        .group_by(DiagnosisMaster.id, DiagnosisMaster.icd10_code, DiagnosisMaster.short_description)
        .order_by(func.count(EncounterDiagnosis.id).desc())
        .limit(10)
    ).all()
    top_icd10_diagnoses = [
        {"icd10_code": r.icd10_code, "short_description": r.short_description, "cases_count": r.cases_count}
        for r in diag_rows
    ]

    note_summary = db.execute(
        select(
            func.count(ClinicalNote.id).label("total"),
            func.sum(case((ClinicalNote.is_signed.is_(True), 1), else_=0)).label(
                "signed"
            ),
        ).where(
            ClinicalNote.created_at >= start_at,
            ClinicalNote.created_at < end_at,
        )
    ).one()
    note_count = int(note_summary.total or 0)
    signed_notes_count = int(note_summary.signed or 0)
    vital_summary = db.execute(
        select(
            func.count(EncounterVital.id),
            func.avg(EncounterVital.bmi),
        ).where(
            EncounterVital.created_at >= start_at,
            EncounterVital.created_at < end_at,
        )
    ).one()

    emar_breakdown = {s.value: 0 for s in eMARStatusEnum}
    emar_rows = db.execute(
        select(
            MedicationAdministrationLog.status,
            func.count(MedicationAdministrationLog.id),
        )
        .where(
            MedicationAdministrationLog.administered_at >= start_at,
            MedicationAdministrationLog.administered_at < end_at,
        )
        .group_by(MedicationAdministrationLog.status)
    ).all()
    for status_value, count in emar_rows:
        emar_breakdown[status_value.value] = int(count)
    emar_count = sum(emar_breakdown.values())

    triage_rows = db.execute(
        select(ERTriageAssessment.acuity_level, func.count(ERTriageAssessment.id))
        .where(
            ERTriageAssessment.created_at >= start_at,
            ERTriageAssessment.created_at < end_at,
        )
        .group_by(ERTriageAssessment.acuity_level)
    ).all()
    triage_by_acuity = {
        acuity.value: int(count) for acuity, count in triage_rows
    }
    triage_count = sum(triage_by_acuity.values())
    average_gcs = db.scalar(
        select(func.avg(ERTriageAssessment.gcs_score)).where(
            ERTriageAssessment.created_at >= start_at,
            ERTriageAssessment.created_at < end_at,
            ERTriageAssessment.gcs_score.is_not(None),
        )
    )

    mlc_rows = db.execute(
        select(
            MedicoLegalCase.cause_of_injury,
            func.count(MedicoLegalCase.id),
            func.sum(
                case((MedicoLegalCase.police_intimated_at.is_not(None), 1), else_=0)
            ),
        )
        .where(
            MedicoLegalCase.created_at >= start_at,
            MedicoLegalCase.created_at < end_at,
        )
        .group_by(MedicoLegalCase.cause_of_injury)
    ).all()
    mlc_by_cause: Dict[str, int] = {}
    police_intimated_count = 0
    mlc_count = 0
    for cause, count, intimated in mlc_rows:
        mlc_by_cause[cause.value] = int(count)
        mlc_count += int(count)
        police_intimated_count += int(intimated or 0)

    surg_by_status = {s.value: 0 for s in SurgeryStatusEnum}
    surgery_rows = db.execute(
        select(SurgicalSchedule.status, func.count(SurgicalSchedule.id))
        .where(
            SurgicalSchedule.created_at >= start_at,
            SurgicalSchedule.created_at < end_at,
        )
        .group_by(SurgicalSchedule.status)
    ).all()
    for surgery_status, count in surgery_rows:
        surg_by_status[surgery_status.value] = int(count)
    surgery_count = sum(surg_by_status.values())

    complete_checklist = (
        WHOSurgicalSafetyChecklist.patient_identity_confirmed.is_(True)
        & WHOSurgicalSafetyChecklist.site_marked.is_(True)
        & WHOSurgicalSafetyChecklist.consent_signed.is_(True)
        & WHOSurgicalSafetyChecklist.pulse_oximeter_functioning.is_(True)
        & WHOSurgicalSafetyChecklist.allergy_checked.is_(True)
        & WHOSurgicalSafetyChecklist.sponge_instrument_count_verified.is_(True)
    )
    checklist_row = db.execute(
        select(
            func.count(WHOSurgicalSafetyChecklist.id).label("total"),
            func.sum(case((complete_checklist, 1), else_=0)).label("complete"),
        )
        .join(
            SurgicalSchedule,
            SurgicalSchedule.id == WHOSurgicalSafetyChecklist.surgery_id,
        )
        .where(
            SurgicalSchedule.created_at >= start_at,
            SurgicalSchedule.created_at < end_at,
        )
    ).one()
    checklist_count = int(checklist_row.total or 0)
    op_record_summary = db.execute(
        select(
            func.count(OperativeAnesthesiaRecord.id),
            func.avg(OperativeAnesthesiaRecord.estimated_blood_loss_ml),
        ).where(
            OperativeAnesthesiaRecord.created_at >= start_at,
            OperativeAnesthesiaRecord.created_at < end_at,
        )
    ).one()
    implant_summary = db.execute(
        select(
            func.coalesce(func.sum(SurgicalImplantLog.quantity), 0),
            func.coalesce(
                func.sum(
                    SurgicalImplantLog.quantity * SurgicalImplantLog.unit_price_paise
                ),
                0,
            ),
        ).where(
            SurgicalImplantLog.created_at >= start_at,
            SurgicalImplantLog.created_at < end_at,
        )
    ).one()

    task_by_status = {s.value: 0 for s in TaskStatusEnum}
    task_rows = db.execute(
        select(DiagnosticTask.status, func.count(DiagnosticTask.id))
        .where(
            DiagnosticTask.created_at >= start_at,
            DiagnosticTask.created_at < end_at,
        )
        .group_by(DiagnosticTask.status)
    ).all()
    for task_status, count in task_rows:
        task_by_status[task_status.value] = int(count)
    task_count = sum(task_by_status.values())
    dicom_summary = db.execute(
        select(
            func.count(RadiologyStudyMetadata.id),
            func.avg(RadiologyStudyMetadata.radiation_dose_mgy),
        )
        .join(
            DiagnosticTask,
            DiagnosticTask.id == RadiologyStudyMetadata.diagnostic_task_id,
        )
        .where(
            DiagnosticTask.created_at >= start_at,
            DiagnosticTask.created_at < end_at,
        )
    ).one()

    return {
        "window": _window_metadata(start_day, end_day),
        "epidemiology_top_icd10": top_icd10_diagnoses,
        "emr_compliance": {
            "total_soap_notes": note_count,
            "signed_soap_notes": signed_notes_count,
            "unsigned_draft_notes": note_count - signed_notes_count,
            "medico_legal_sign_off_rate_pct": _pct(signed_notes_count, note_count),
            "total_vitals_recorded": int(vital_summary[0] or 0),
            "average_patient_bmi": (
                round(float(vital_summary[1]), 2)
                if vital_summary[1] is not None
                else None
            ),
        },
        "nursing_emar_safety": {
            "total_scheduled_doses_logged": emar_count,
            "status_breakdown": emar_breakdown,
            "administration_compliance_rate_pct": _pct(
                emar_breakdown["ADMINISTERED"], emar_count
            ),
        },
        "emergency_and_mlc_forensics": {
            "total_er_triaged": triage_count,
            "acuity_breakdown": triage_by_acuity,
            "average_gcs_score": (
                round(float(average_gcs), 2) if average_gcs is not None else None
            ),
            "total_mlc_cases": mlc_count,
            "mlc_by_cause": mlc_by_cause,
            "police_intimated_count": police_intimated_count,
            "police_intimation_rate_pct": _pct(police_intimated_count, mlc_count),
        },
        "operation_theatre_and_surgery": {
            "total_surgeries": surgery_count,
            "status_breakdown": surg_by_status,
            "who_safety_checklists_logged": checklist_count,
            "who_full_sign_out_compliance_pct": _pct(
                int(checklist_row.complete or 0), checklist_count
            ),
            "average_surgical_blood_loss_ml": (
                round(float(op_record_summary[1]), 1)
                if op_record_summary[1] is not None
                else None
            ),
            "total_implants_used": int(implant_summary[0] or 0),
            "total_implant_value_paise": int(implant_summary[1] or 0),
        },
        "diagnostics_and_pacs": {
            "total_diagnostic_tasks": task_count,
            "task_status_breakdown": task_by_status,
            "verification_completion_rate_pct": _pct(
                task_by_status["VERIFIED"], task_count
            ),
            "pacs_dicom_studies_linked": int(dicom_summary[0] or 0),
            "average_radiation_dose_mgy": (
                round(float(dicom_summary[1]), 2)
                if dicom_summary[1] is not None
                else None
            ),
        },
        "data_notes": [
            "Diagnosis, note, triage, MLC, surgery, implant, diagnostic-task, and imaging cohorts use their record creation date within the UTC window; eMAR uses administered_at.",
            "Status distributions use each record's current status, not historical status transitions.",
            "Signed-note rate counts signed notes and is not a measure of legal or clinical-documentation compliance.",
            "WHO checklist percentage measures the listed Boolean fields among logged checklists; it is not an independently validated accreditation score.",
            "Averages are null when there are no qualifying measurements; thresholds and cohort summaries are not clinical alerts.",
        ],
    }


# =====================================================================
# 6. PHARMACY & THERAPEUTICS (DISPENSING FILL RATE & FEFO VALUATION)
# =====================================================================

@router.get("/pharmacy-and-therapeutics")
def get_pharmacy_and_therapeutics_analytics(
    response: Response,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:read", "billing:read")),
) -> Dict[str, Any]:
    start_day, end_day, start_at, end_at = _resolve_analytics_window(
        start_date, end_date
    )
    _disable_analytics_caching(response)
    now_utc = datetime.now(timezone.utc)
    rx_by_status = {s.value: 0 for s in PrescriptionStatusEnum}
    prescription_rows = db.execute(
        select(Prescription.status, func.count(Prescription.id))
        .where(
            Prescription.created_at >= start_at,
            Prescription.created_at < end_at,
        )
        .group_by(Prescription.status)
    ).all()
    for prescription_status, count in prescription_rows:
        rx_by_status[prescription_status.value] = int(count)
    total_prescriptions = sum(rx_by_status.values())

    quantity_row = db.execute(
        select(
            func.coalesce(func.sum(PrescriptionItem.requested_quantity), 0),
            func.coalesce(func.sum(PrescriptionItem.dispensed_quantity), 0),
        ).where(
            PrescriptionItem.created_at >= start_at,
            PrescriptionItem.created_at < end_at,
        )
    ).one()
    total_requested_qty = int(quantity_row[0] or 0)
    total_dispensed_qty = int(quantity_row[1] or 0)
    by_form = {f.value: 0 for f in MedicationFormEnum}
    form_rows = db.execute(
        select(
            MedicationMaster.form,
            func.sum(PrescriptionItem.requested_quantity),
        )
        .join(MedicationMaster, MedicationMaster.id == PrescriptionItem.medication_id)
        .where(
            PrescriptionItem.created_at >= start_at,
            PrescriptionItem.created_at < end_at,
        )
        .group_by(MedicationMaster.form)
    ).all()
    for medication_form, quantity in form_rows:
        by_form[medication_form.value] = int(quantity or 0)

    inventory_value = InventoryBatch.stock_quantity * InventoryBatch.unit_price_paise
    expiry_buckets = [
        (
            "expiring_0_to_30_days_paise",
            InventoryBatch.expiry_date < now_utc + timedelta(days=31),
        ),
        (
            "expiring_31_to_60_days_paise",
            (InventoryBatch.expiry_date >= now_utc + timedelta(days=31))
            & (InventoryBatch.expiry_date < now_utc + timedelta(days=61)),
        ),
        (
            "expiring_61_to_90_days_paise",
            (InventoryBatch.expiry_date >= now_utc + timedelta(days=61))
            & (InventoryBatch.expiry_date < now_utc + timedelta(days=91)),
        ),
        (
            "safe_over_90_days_paise",
            InventoryBatch.expiry_date >= now_utc + timedelta(days=91),
        ),
    ]
    inventory_row = db.execute(
        select(
            func.count(InventoryBatch.id).label("batch_count"),
            func.coalesce(func.sum(InventoryBatch.stock_quantity), 0).label("stock"),
            func.coalesce(func.sum(inventory_value), 0).label("valuation"),
            *[
                func.coalesce(
                    func.sum(case((condition, inventory_value), else_=0)), 0
                ).label(bucket_name)
                for bucket_name, condition in expiry_buckets
            ],
        )
    ).one()
    fefo_expiry_value_buckets_paise = {
        bucket_name: int(getattr(inventory_row, bucket_name) or 0)
        for bucket_name, _ in expiry_buckets
    }

    return {
        "window": _window_metadata(start_day, end_day),
        "prescription_funnel": {
            "total_prescriptions": total_prescriptions,
            "by_status": rx_by_status,
            "total_units_prescribed": total_requested_qty,
            "total_units_dispensed": total_dispensed_qty,
            "dispensing_fill_rate_pct": _pct(total_dispensed_qty, total_requested_qty),
            "prescribed_units_by_dosage_form": by_form,
        },
        "inventory_snapshot": {
            "as_of": now_utc,
            "total_batches": int(inventory_row.batch_count or 0),
            "total_stock_units": int(inventory_row.stock or 0),
            "total_inventory_valuation_paise": int(inventory_row.valuation or 0),
        },
        "fefo_inventory_risk_valuation_paise": fefo_expiry_value_buckets_paise,
        "data_notes": [
            "Prescription counts use prescription creation time; requested and dispensed unit totals and dosage-form mix use prescription-item creation time in the selected UTC window.",
            "Inventory quantity, valuation, and expiry exposure are a current snapshot, independent of the requested prescription window.",
            "Expiry buckets classify inventory by expiry date and are exposure estimates, not recorded write-offs or realized losses.",
            "Results are aggregates and contain no patient or prescriber identifiers.",
        ],
    }


# =====================================================================
# 7. DOCTOR & DEPARTMENT PRODUCTIVITY + ATTRIBUTED REVENUE & NMC CREDENTIALS
# =====================================================================

@router.get("/doctor-and-department-productivity")
def get_doctor_and_department_productivity(
    response: Response,
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("executive_bi:read")),
) -> Dict[str, Any]:
    start_day, end_day, start_at, end_at = _resolve_analytics_window(
        start_date, end_date
    )
    _disable_analytics_caching(response)
    now_utc = datetime.now(timezone.utc)
    doctors = list(
        db.execute(
            select(DoctorProfile).options(
                joinedload(DoctorProfile.user),
                joinedload(DoctorProfile.department),
            )
        )
        .scalars()
        .all()
    )

    appts_by_doc = dict(
        db.execute(
            select(Appointment.doctor_id, func.count(Appointment.id))
            .where(
                Appointment.appointment_time >= start_at,
                Appointment.appointment_time < end_at,
            )
            .group_by(Appointment.doctor_id)
        ).all()
    )
    encounter_rows = db.execute(
        select(
            PatientEncounter.primary_doctor_id,
            func.count(PatientEncounter.id).label("encounters"),
            func.sum(
                case(
                    (PatientEncounter.encounter_type == EncounterTypeEnum.OPD, 1),
                    else_=0,
                )
            ).label("opd_encounters"),
        )
        .where(
            PatientEncounter.start_time >= start_at,
            PatientEncounter.start_time < end_at,
        )
        .group_by(PatientEncounter.primary_doctor_id)
    ).all()
    encs_by_doc = {doctor_id: int(count) for doctor_id, count, _ in encounter_rows}
    opd_encs_by_doc = {
        doctor_id: int(count or 0) for doctor_id, _, count in encounter_rows
    }
    rx_by_doc = dict(
        db.execute(
            select(Prescription.prescribing_doctor_id, func.count(Prescription.id))
            .where(
                Prescription.created_at >= start_at,
                Prescription.created_at < end_at,
            )
            .group_by(Prescription.prescribing_doctor_id)
        ).all()
    )
    diag_by_doc = dict(
        db.execute(
            select(DiagnosticOrder.ordering_doctor_id, func.count(DiagnosticOrder.id))
            .where(
                DiagnosticOrder.created_at >= start_at,
                DiagnosticOrder.created_at < end_at,
            )
            .group_by(DiagnosticOrder.ordering_doctor_id)
        ).all()
    )
    surg_by_doc = dict(
        db.execute(
            select(SurgicalSchedule.lead_surgeon_id, func.count(SurgicalSchedule.id))
            .where(
                SurgicalSchedule.status == SurgeryStatusEnum.COMPLETED,
                SurgicalSchedule.actual_start_time >= start_at,
                SurgicalSchedule.actual_start_time < end_at,
            )
            .group_by(SurgicalSchedule.lead_surgeon_id)
        ).all()
    )

    doctor_matrix = []
    for doc in doctors:
        uid = doc.user_id
        appts_cnt = appts_by_doc.get(uid, 0)
        encs_cnt = encs_by_doc.get(uid, 0)
        reg_valid_utc = _to_utc(doc.registration_valid_till)
        doctor_matrix.append(
            {
                "doctor_user_id": uid,
                "full_name": doc.user.full_name if doc.user else "Unknown",
                "clinical_id": doc.clinical_id,
                "medical_registration_number": doc.medical_registration_number,
                "is_registration_expired": reg_valid_utc <= now_utc,
                "specialization": doc.specialization,
                "department_name": doc.department.name if doc.department else "General",
                "consultation_fee_paise": doc.consultation_fee_paise,
                "opd_appointments_count": appts_cnt,
                "encounters_led_count": encs_cnt,
                "prescriptions_authored_count": rx_by_doc.get(uid, 0),
                "diagnostic_orders_placed_count": diag_by_doc.get(uid, 0),
                "surgeries_performed_count": surg_by_doc.get(uid, 0),
                "estimated_consultation_revenue_paise": opd_encs_by_doc.get(uid, 0)
                * doc.consultation_fee_paise,
            }
        )

    departments = list(db.execute(select(Department)).scalars().all())
    encs_by_dept = dict(
        db.execute(
            select(PatientEncounter.department_id, func.count(PatientEncounter.id)).group_by(
                PatientEncounter.department_id
            ).where(
                PatientEncounter.start_time >= start_at,
                PatientEncounter.start_time < end_at,
            )
        ).all()
    )
    appts_by_dept = dict(
        db.execute(
            select(Appointment.department_id, func.count(Appointment.id))
            .where(
                Appointment.appointment_time >= start_at,
                Appointment.appointment_time < end_at,
            )
            .group_by(Appointment.department_id)
        ).all()
    )

    department_matrix = [
        {
            "department_id": d.id,
            "code": d.code,
            "name": d.name,
            "total_encounters": encs_by_dept.get(d.id, 0),
            "total_opd_appointments": appts_by_dept.get(d.id, 0),
        }
        for d in departments
    ]

    return {
        "window": _window_metadata(start_day, end_day),
        "doctors": doctor_matrix,
        "departments": department_matrix,
        "data_notes": [
            "Appointment counts use scheduled appointment_time; encounter counts use start_time; prescriptions and diagnostic orders use creation time.",
            "Surgery counts include completed cases with actual_start_time in the window.",
            "Estimated consultation revenue is OPD encounter count multiplied by the doctor's current configured consultation fee; it is not reconciled billing revenue.",
            "Doctor roster fields are identifiable workforce data and are returned only to users with executive_bi:read.",
        ],
    }


# =====================================================================
# 8. SUPPLY CHAIN, BLOOD BANK, PRINT ENGINE & GOVERNANCE / FEDERATION RADAR
# =====================================================================

@router.get("/inventory-and-blood-radar")
def get_inventory_and_blood_radar(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:read", "diagnostics:read", "iam:read")),
) -> Dict[str, Any]:
    from backend.domains.policies.router import get_or_create_policy

    policy = get_or_create_policy(db)
    effective_low_stock = policy.low_stock_threshold_units
    effective_expiry_days = policy.fefo_expiry_warning_days

    now_utc = datetime.now(timezone.utc)
    alert_row_limit = policy.inventory_alert_detail_limit
    low_stock_limit = func.coalesce(
        MedicationMaster.low_stock_threshold_units,
        effective_low_stock,
    )
    configured_expiry_days = db.scalars(
        select(MedicationMaster.fefo_expiry_warning_days)
        .where(MedicationMaster.fefo_expiry_warning_days.is_not(None))
        .distinct()
    ).all()
    expiry_conditions = [
        and_(
                MedicationMaster.fefo_expiry_warning_days.is_(None),
                InventoryBatch.expiry_date
                <= now_utc + timedelta(days=effective_expiry_days),
        )
    ]
    expiry_conditions.extend(
        and_(
            MedicationMaster.fefo_expiry_warning_days == warning_days,
            InventoryBatch.expiry_date <= now_utc + timedelta(days=warning_days),
        )
        for warning_days in configured_expiry_days
    )
    expiry_condition = or_(*expiry_conditions)
    expiry_warning_limit = func.coalesce(
        MedicationMaster.fefo_expiry_warning_days,
        effective_expiry_days,
    )
    inventory_value = InventoryBatch.stock_quantity * InventoryBatch.unit_price_paise
    inventory_summary = db.execute(
        select(
            func.count(InventoryBatch.id).label("batch_count"),
            func.coalesce(func.sum(InventoryBatch.stock_quantity), 0).label("stock"),
            func.coalesce(func.sum(inventory_value), 0).label("valuation"),
            func.sum(
                case((InventoryBatch.stock_quantity <= low_stock_limit, 1), else_=0)
            ).label("low_stock_count"),
            func.sum(
                case(
                    (
                        expiry_condition
                        & (InventoryBatch.stock_quantity > 0),
                        1,
                    ),
                    else_=0,
                )
            ).label("near_expiry_count"),
        )
        .select_from(InventoryBatch)
        .join(MedicationMaster, MedicationMaster.id == InventoryBatch.medication_id)
    ).one()
    low_stock_rows = db.execute(
        select(
            InventoryBatch.id,
            InventoryBatch.batch_number,
            MedicationMaster.brand_name,
            InventoryBatch.stock_quantity,
            low_stock_limit.label("effective_low_stock_threshold_units"),
        )
        .join(MedicationMaster, MedicationMaster.id == InventoryBatch.medication_id)
        .where(InventoryBatch.stock_quantity <= low_stock_limit)
        .order_by(
            InventoryBatch.stock_quantity,
            InventoryBatch.expiry_date,
            InventoryBatch.id,
        )
        .limit(alert_row_limit)
    ).all()
    near_expiry_rows = db.execute(
        select(
            InventoryBatch.id,
            InventoryBatch.batch_number,
            MedicationMaster.brand_name,
            InventoryBatch.expiry_date,
            InventoryBatch.stock_quantity,
            expiry_warning_limit.label("effective_expiry_warning_days"),
        )
        .join(MedicationMaster, MedicationMaster.id == InventoryBatch.medication_id)
        .where(
            expiry_condition,
            InventoryBatch.stock_quantity > 0,
        )
        .order_by(InventoryBatch.expiry_date, InventoryBatch.id)
        .limit(alert_row_limit)
    ).all()
    low_stock_batches = [
        {
            "batch_id": batch_id,
            "batch_number": batch_number,
            "medication_name": medication_name,
            "stock_quantity": int(stock_quantity),
            "low_stock_threshold_units": int(effective_threshold),
        }
        for (
            batch_id,
            batch_number,
            medication_name,
            stock_quantity,
            effective_threshold,
        ) in low_stock_rows
    ]
    near_expiry_batches = [
        {
            "batch_id": batch_id,
            "batch_number": batch_number,
            "medication_name": medication_name,
            "expiry_date": _to_utc(expiry_date).isoformat(),
            "stock_quantity": int(stock_quantity),
            "is_already_expired": _to_utc(expiry_date) <= now_utc,
            "expiry_warning_days": int(effective_warning_days),
        }
        for (
            batch_id,
            batch_number,
            medication_name,
            expiry_date,
            stock_quantity,
            effective_warning_days,
        ) in near_expiry_rows
    ]

    available_by_blood_group = {bg.value: 0 for bg in BloodGroupEnum}
    bag_status_counts = {s.value: 0 for s in BloodBagStatusEnum}
    bag_status_rows = db.execute(
        select(BloodBagInventory.status, func.count(BloodBagInventory.id))
        .group_by(BloodBagInventory.status)
    ).all()
    for bag_status, count in bag_status_rows:
        bag_status_counts[bag_status.value] = int(count)
    blood_group_rows = db.execute(
        select(BloodBagInventory.blood_group, func.count(BloodBagInventory.id))
        .where(
            BloodBagInventory.status == BloodBagStatusEnum.AVAILABLE,
            BloodBagInventory.expiry_date > now_utc,
        )
        .group_by(BloodBagInventory.blood_group)
    ).all()
    for blood_group, count in blood_group_rows:
        available_by_blood_group[blood_group.value] = int(count)

    cross_match_summary = db.execute(
        select(
            func.count(BloodCrossMatchOrder.id).label("total"),
            func.sum(
                case((BloodCrossMatchOrder.issued_at.is_not(None), 1), else_=0)
            ).label("issued"),
            func.sum(
                case(
                    (
                        BloodCrossMatchOrder.issued_at.is_not(None)
                        & (
                            BloodCrossMatchOrder.reaction_type
                            != TransfusionReactionTypeEnum.NONE
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("adverse"),
        )
    ).one()
    severe_allergy_patients = int(
        db.scalar(
            select(func.count(func.distinct(PatientAllergy.patient_id))).where(
                PatientAllergy.severity.in_([SeverityEnum.SEVERE, SeverityEnum.ANAPHYLACTIC]),
                PatientAllergy.is_active.is_(True),
            )
        )
        or 0
    )
    user_summary = db.execute(
        select(
            func.sum(case((User.status == UserStatusEnum.LOCKED, 1), else_=0)),
            func.sum(case((User.password_changed_at.is_(None), 1), else_=0)),
        )
    ).one()
    shift_summary = db.execute(
        select(
            func.count(NursingShiftAssignment.id),
            func.sum(
                case((NursingShiftAssignment.handed_off_at.is_not(None), 1), else_=0)
            ),
        )
    ).one()
    fed_clients_count = int(
        db.scalar(
            select(func.count(FederatedClient.id)).where(
                FederatedClient.is_active.is_(True)
            )
        )
        or 0
    )
    federation_transfers_count = int(
        db.scalar(select(func.count(FederationTransferLog.id))) or 0
    )
    template_summary = db.execute(
        select(
            func.count(ReportTemplate.id),
            func.sum(
                case((ReportTemplate.is_system_template.is_(False), 1), else_=0)
            ),
        ).where(ReportTemplate.is_active.is_(True))
    ).one()

    return {
        "pharmacy_inventory": {
            "as_of": now_utc,
            "hospital_default_low_stock_threshold_units": policy.low_stock_threshold_units,
            "hospital_default_expiry_warning_days": policy.fefo_expiry_warning_days,
            "alert_detail_limit": alert_row_limit,
            "total_batches": int(inventory_summary.batch_count or 0),
            "total_stock_units": int(inventory_summary.stock or 0),
            "total_inventory_valuation_paise": int(inventory_summary.valuation or 0),
            "low_stock_alerts_count": int(inventory_summary.low_stock_count or 0),
            "low_stock_batches": low_stock_batches,
            "low_stock_alerts_returned_count": len(low_stock_batches),
            "low_stock_alerts_truncated": int(inventory_summary.low_stock_count or 0)
            > alert_row_limit,
            "near_or_expired_alerts_count": int(inventory_summary.near_expiry_count or 0),
            "near_or_expired_batches": near_expiry_batches,
            "near_or_expired_alerts_returned_count": len(near_expiry_batches),
            "near_or_expired_alerts_truncated": int(
                inventory_summary.near_expiry_count or 0
            )
            > alert_row_limit,
        },
        "blood_bank_and_hemovigilance": {
            "total_bags_recorded": sum(bag_status_counts.values()),
            "bag_status_breakdown": bag_status_counts,
            "available_units_by_blood_group": available_by_blood_group,
            "critical_o_negative_shortage": available_by_blood_group["O-"] < policy.o_negative_critical_threshold_units,
            "total_cross_matches": int(cross_match_summary.total or 0),
            "total_transfusions_issued": int(cross_match_summary.issued or 0),
            "adverse_transfusion_reactions_count": int(cross_match_summary.adverse or 0),
            "transfusion_reaction_rate_pct": _pct(
                int(cross_match_summary.adverse or 0),
                int(cross_match_summary.issued or 0),
            ),
        },
        "governance_and_security": {
            "patients_with_severe_or_anaphylactic_allergies": severe_allergy_patients,
            "locked_staff_accounts_count": int(user_summary[0] or 0),
            "unrotated_bootstrap_password_accounts_count": int(user_summary[1] or 0),
            "total_nursing_shifts": int(shift_summary[0] or 0),
            "nursing_shift_handoff_completion_pct": _pct(
                int(shift_summary[1] or 0), int(shift_summary[0] or 0)
            ),
            "active_federation_partner_nodes": fed_clients_count,
            "federation_outbound_transfer_logs_count": federation_transfers_count,
            "total_active_report_templates": int(template_summary[0] or 0),
            "custom_tenant_report_templates_count": int(template_summary[1] or 0),
        },
        "data_notes": [
            "Inventory and blood-bank measures are current snapshots; medication-specific low-stock and expiry-warning thresholds override hospital defaults, while each hospital configures its own alert-detail limit. Total alert counts and truncation flags distinguish counts from returned detail.",
            "Blood availability excludes expired units; status and transfusion-reaction metrics use current recorded state, not a time-windowed event ledger.",
            "Governance figures are current configuration/account snapshots, except federation transfer count, which is a lifetime log count.",
            "This route includes identifiable medication batch details and must remain permission-gated.",
        ],
    }