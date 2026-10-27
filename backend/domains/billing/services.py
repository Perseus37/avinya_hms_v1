from datetime import datetime, timezone
import math
from typing import List, Optional
import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.domains.billing.models import Invoice, InvoiceLineItem, InvoiceStatusEnum
from backend.domains.bloodbank.models import BloodCrossMatchOrder
from backend.domains.diagnostics.models import DiagnosticOrder, ServiceCatalog
from backend.domains.encounters.models import BedTransferLog, EncounterStatusEnum, InpatientAdmission, PatientEncounter
from backend.domains.identity.models import DoctorProfile
from backend.domains.insurance.models import PatientInsurancePolicy, InsurancePlanTariff
from backend.domains.surgery.models import SurgicalSchedule


def _generate_inv_number() -> str:
    date_prefix = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"INV-{date_prefix}-{uuid.uuid4().hex[:12].upper()}"


def get_or_create_draft_encounter_invoice(
    db: Session,
    patient_id: int,
    encounter_id: Optional[int] = None,
) -> Invoice:
    """Finds an existing DRAFT invoice for the patient/encounter, or creates a new DRAFT invoice."""
    if encounter_id is None:
        active_enc = (
            db.execute(
                select(PatientEncounter)
                .where(
                    PatientEncounter.patient_id == patient_id,
                    PatientEncounter.status == EncounterStatusEnum.ACTIVE,
                )
                .order_by(PatientEncounter.id.desc())
            )
            .scalars()
            .first()
        )
        if active_enc:
            encounter_id = active_enc.id

    stmt = select(Invoice).where(
        Invoice.patient_id == patient_id,
        Invoice.status == InvoiceStatusEnum.DRAFT,
    )
    if encounter_id is not None:
        stmt = stmt.where(Invoice.encounter_id == encounter_id)

    stmt = stmt.order_by(Invoice.id.desc()).with_for_update()
    invoice = db.execute(stmt).scalars().first()

    if not invoice:
        invoice = Invoice(
            invoice_number=_generate_inv_number(),
            patient_id=patient_id,
            encounter_id=encounter_id,
            status=InvoiceStatusEnum.DRAFT,
            total_amount_paise=0,
            discount_amount_paise=0,
            amount_paid_paise=0,
            notes=f"Auto-generated billing ledger for Patient #{patient_id}",
        )
        db.add(invoice)
        db.flush()
    return invoice


def auto_bill_opd_consultation(db: Session, patient_id: int, encounter_id: int, doctor_user_id: int) -> None:
    """Appends the doctor's consultation_fee_paise to the encounter's DRAFT invoice (idempotent per encounter)."""
    doc_profile = (
        db.execute(
            select(DoctorProfile)
            .options(joinedload(DoctorProfile.user))
            .where(DoctorProfile.user_id == doctor_user_id)
        )
        .scalar_one_or_none()
    )
    if not doc_profile or doc_profile.consultation_fee_paise <= 0:
        return

    invoice = get_or_create_draft_encounter_invoice(db, patient_id, encounter_id)
    desc = f"OPD CONSULTATION: {doc_profile.user.full_name} ({doc_profile.specialization})"

    # CLIN-003: Use a wildcard LIKE match to decouple idempotency from volatile doctor profile strings
    existing = (
        db.execute(
            select(InvoiceLineItem).where(
                InvoiceLineItem.invoice_id == invoice.id,
                InvoiceLineItem.description.like("OPD CONSULTATION:%"),
            )
        )
        .scalars()
        .first()
    )
    if existing:
        return

    fee = doc_profile.consultation_fee_paise
    line_item = InvoiceLineItem(
        invoice_id=invoice.id,
        service_id=None,
        description=desc,
        quantity=1,
        unit_price_paise=fee,
        total_price_paise=fee,
    )
    db.add(line_item)
    invoice.total_amount_paise += fee


def auto_bill_ipd_bed_stay(db: Session, admission: InpatientAdmission, discharge_time: datetime) -> None:
    """
    Calculates bed occupancy duration across all BedTransferLog segments for the admission
    and appends each bed's daily rate charge to the encounter's DRAFT invoice.
    """
    logs = list(
        db.execute(
            select(BedTransferLog)
            .options(joinedload(BedTransferLog.to_bed))
            .where(BedTransferLog.admission_id == admission.id)
            .order_by(BedTransferLog.transfer_time.asc(), BedTransferLog.id.asc())
        )
        .scalars()
        .all()
    )
    if not logs or not admission.encounter:
        return

    invoice = get_or_create_draft_encounter_invoice(
        db, admission.encounter.patient_id, admission.encounter_id
    )

    def _to_utc(dt: datetime) -> datetime:
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    end_utc = _to_utc(discharge_time)

    for idx, log in enumerate(logs):
        bed = log.to_bed
        if not bed or bed.base_daily_rate_paise <= 0:
            continue

        seg_start = _to_utc(log.transfer_time)
        seg_end = _to_utc(logs[idx + 1].transfer_time) if (idx + 1 < len(logs)) else end_utc

        elapsed_hours = max(0.0, (seg_end - seg_start).total_seconds() / 3600.0)
        billable_days = max(1, math.ceil(elapsed_hours / 24.0))
        
        # FIN-002: Use the locked-in historical rate from BedTransferLog, falling back if it was recorded as 0
        historical_rate = getattr(log, "unit_price_paise", 0)
        if historical_rate <= 0:
            historical_rate = bed.base_daily_rate_paise
            
        total_paise = billable_days * historical_rate

        desc = f"IPD BED CHARGE: Bed {bed.bed_number} ({bed.bed_type.value}) [Transfer #{log.id}]"
        existing = db.execute(
            select(InvoiceLineItem).where(
                InvoiceLineItem.invoice_id == invoice.id,
                InvoiceLineItem.description == desc,
            )
        ).scalars().first()
        if existing:
            continue

        line_item = InvoiceLineItem(
            invoice_id=invoice.id,
            service_id=None,
            description=desc,
            quantity=billable_days,
            unit_price_paise=historical_rate,
            total_price_paise=total_paise,
        )
        db.add(line_item)
        invoice.total_amount_paise += total_paise

def resolve_patient_service_price_paise(db: Session, patient_id: int, service: ServiceCatalog) -> int:
    """
    Returns the negotiated InsurancePlanTariff price if the patient holds an active policy
    with a plan where apply_tariff_automatically=True and is_covered=True; otherwise returns
    the hospital's standard rack rate (service.base_price_paise).
    """
    now_utc = datetime.now(timezone.utc)
    policies = list(
        db.execute(
            select(PatientInsurancePolicy)
            .options(joinedload(PatientInsurancePolicy.plan))
            .where(
                PatientInsurancePolicy.patient_id == patient_id,
                PatientInsurancePolicy.is_active == True,
                PatientInsurancePolicy.plan_id.is_not(None),
            )
            .order_by(PatientInsurancePolicy.id.desc())
        )
        .scalars()
        .all()
    )

    for pol in policies:
        valid_till_utc = pol.valid_till if pol.valid_till.tzinfo else pol.valid_till.replace(tzinfo=timezone.utc)
        if valid_till_utc < now_utc or not pol.plan or not pol.plan.is_active:
            continue
        if not pol.plan.apply_tariff_automatically:
            continue

        tariff = db.execute(
            select(InsurancePlanTariff).where(
                InsurancePlanTariff.plan_id == pol.plan_id,
                InsurancePlanTariff.service_id == service.id,
            )
        ).scalar_one_or_none()
        if tariff and tariff.is_covered:
            return tariff.tariff_price_paise

    return service.base_price_paise

def auto_bill_diagnostic_order(db: Session, order: DiagnosticOrder, ordered_service_ids: List[int]) -> None:
    """
    Automatically bills each ordered diagnostic service or package onto the patient's DRAFT invoice.
    Charges parent package price once (without double-billing exploded child tasks).
    """
    if not ordered_service_ids:
        return

    invoice = get_or_create_draft_encounter_invoice(db, order.patient_id)

    for svc_id in ordered_service_ids:
        svc = db.get(ServiceCatalog, svc_id)
        if not svc or svc.base_price_paise <= 0:
            continue

        price = resolve_patient_service_price_paise(db, order.patient_id, svc)
        if price <= 0:
            continue
        desc = f"DIAGNOSTICS [Order #{order.id}]: {svc.name} ({svc.code})"
        line_item = InvoiceLineItem(
            invoice_id=invoice.id,
            service_id=svc.id,
            description=desc,
            quantity=1,
            unit_price_paise=price,
            total_price_paise=price,
        )
        db.add(line_item)
        invoice.total_amount_paise += price


def reverse_cancelled_diagnostic_order_billing(db: Session, order: DiagnosticOrder) -> None:
    """Removes auto-billed diagnostic line items from a DRAFT invoice when the diagnostic order is cancelled."""
    from backend.domains.billing.rcm_services import recalculate_invoice_totals

    prefix = f"DIAGNOSTICS [Order #{order.id}]:"
    draft_invoices = list(
        db.execute(
            select(Invoice)
            .where(Invoice.patient_id == order.patient_id, Invoice.status == InvoiceStatusEnum.DRAFT)
            .with_for_update()
        )
        .scalars()
        .all()
    )
    for inv in draft_invoices:
        items = list(
            db.execute(
                select(InvoiceLineItem).where(
                    InvoiceLineItem.invoice_id == inv.id,
                    InvoiceLineItem.description.like(f"{prefix}%"),
                )
            )
            .scalars()
            .all()
        )
        for item in items:
            if item in inv.line_items:
                inv.line_items.remove(item)
            db.delete(item)
        db.flush()
        recalculate_invoice_totals(inv)
        if inv.discount_amount_paise > inv.total_amount_paise:
            inv.discount_amount_paise = inv.total_amount_paise

def auto_bill_completed_surgery(db: Session, surgery: SurgicalSchedule) -> None:
    """
    Upon surgery completion:
    1. Auto-bills the primary surgical procedure (respecting negotiated InsurancePlanTariff if active).
    2. Auto-bills the OperationTheatre hourly facility charge based on scheduled/actual duration.
    3. Auto-bills all recorded SurgicalImplantLog prosthetics/stents with their serial numbers.
    """
    invoice = get_or_create_draft_encounter_invoice(db, surgery.patient_id, surgery.encounter_id)
    case_prefix = f"SURGERY [Case #{surgery.case_number}]:"

    # Idempotency guard: ensure surgery is only billed once
    already_billed = (
        db.execute(
            select(InvoiceLineItem).where(
                InvoiceLineItem.invoice_id == invoice.id,
                InvoiceLineItem.description.like(f"{case_prefix}%"),
            )
        )
        .scalars()
        .first()
    )
    if already_billed:
        return

    # 1. Primary Surgical Procedure Fee (with Plan Tariff lookup)
    proc_svc = surgery.procedure_service
    if proc_svc and proc_svc.base_price_paise > 0:
        proc_price = resolve_patient_service_price_paise(db, surgery.patient_id, proc_svc)
        if proc_price > 0:
            db.add(
                InvoiceLineItem(
                    invoice_id=invoice.id,
                    service_id=proc_svc.id,
                    description=f"{case_prefix} {proc_svc.name} ({proc_svc.code})",
                    quantity=1,
                    unit_price_paise=proc_price,
                    total_price_paise=proc_price,
                )
            )
            invoice.total_amount_paise += proc_price

    # 2. Operation Theatre Facility Hourly Charge
    theatre = surgery.theatre
    if theatre and theatre.hourly_rate_paise > 0:
        def _to_utc(dt: datetime) -> datetime:
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

        start_t = _to_utc(surgery.actual_start_time or surgery.scheduled_start_time)
        end_t = _to_utc(surgery.actual_end_time or surgery.scheduled_end_time)
        hours = max(1, math.ceil(max(0.1, (end_t - start_t).total_seconds() / 3600.0)))
        ot_total = hours * theatre.hourly_rate_paise

        db.add(
            InvoiceLineItem(
                invoice_id=invoice.id,
                service_id=None,
                description=f"{case_prefix} OT Room Charge - {theatre.name} ({theatre.code})",
                quantity=hours,
                unit_price_paise=theatre.hourly_rate_paise,
                total_price_paise=ot_total,
            )
        )
        invoice.total_amount_paise += ot_total

    # 3. Surgical Implants / Prosthetics / Stents
    for imp in surgery.implants:
        if imp.unit_price_paise <= 0:
            continue
        imp_total = imp.quantity * imp.unit_price_paise
        db.add(
            InvoiceLineItem(
                invoice_id=invoice.id,
                service_id=None,
                description=(
                    f"SURGICAL IMPLANT [Case #{surgery.case_number}]: "
                    f"{imp.implant_name} (S/N: {imp.serial_or_batch_number})"
                ),
                quantity=imp.quantity,
                unit_price_paise=imp.unit_price_paise,
                total_price_paise=imp_total,
            )
        )
        invoice.total_amount_paise += imp_total

def auto_bill_blood_transfusion_issue(db: Session, cross_match: BloodCrossMatchOrder) -> None:
    """Automatically bills the blood bag processing fee onto the encounter's DRAFT invoice upon issue."""
    bag = cross_match.blood_bag
    if not bag or bag.processing_fee_paise <= 0:
        return

    invoice = get_or_create_draft_encounter_invoice(db, cross_match.patient_id, cross_match.encounter_id)
    desc = (
        f"BLOOD BANK [Bag #{bag.bag_number}]: "
        f"{bag.component_type.value} ({bag.blood_group.value}, {bag.volume_ml}mL)"
    )

    existing = (
        db.execute(
            select(InvoiceLineItem).where(
                InvoiceLineItem.invoice_id == invoice.id,
                InvoiceLineItem.description == desc,
            )
        )
        .scalars()
        .first()
    )
    if existing:
        return

    fee = bag.processing_fee_paise
    db.add(
        InvoiceLineItem(
            invoice_id=invoice.id,
            service_id=None,
            description=desc,
            quantity=1,
            unit_price_paise=fee,
            total_price_paise=fee,
        )
    )
    invoice.total_amount_paise += fee