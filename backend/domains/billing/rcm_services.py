"""
Revenue Cycle Management (RCM) & Charge Master (CDM) Automation Services:
1. Multi-Tariff MoU Rate Card Resolution Engine
2. Event-Driven Clinical Charge Posting Engine
3. Automated Inpatient Midnight Census Charge Run
4. Surgery Completion Automated Billing Posting
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import secrets
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.domains.billing.models import (
    ChargeCaptureEvent,
    Invoice,
    InvoiceLineItem,
    InvoiceStatusEnum,
)
from backend.domains.diagnostics.models import ServiceCatalog
from backend.domains.encounters.models import InpatientAdmission, PatientEncounter
from backend.domains.facility.models import Bed
from backend.domains.insurance.models import InsurancePlanTariff, PatientInsurancePolicy
from backend.domains.surgery.models import SurgicalSchedule

logger = logging.getLogger("avinya.billing.rcm_services")


def resolve_service_tariff(
    session: Session,
    encounter_id: int,
    service_id: int,
    quantity: int = 1,
) -> Dict[str, Any]:
    """
    Multi-Tariff Resolution Engine:
    Resolves the effective unit price, coverage eligibility, and GST tax for a given service item.
    Inspects active patient insurance plans, corporate rate cards, and room-rent caps.
    """
    if quantity < 1:
        raise HTTPException(status_code=400, detail="Quantity must be at least 1.")

    service = session.get(ServiceCatalog, service_id)
    if not service:
        raise HTTPException(status_code=404, detail=f"Service catalog item {service_id} not found.")

    encounter = session.get(PatientEncounter, encounter_id)
    if not encounter:
        raise HTTPException(status_code=404, detail=f"Encounter {encounter_id} not found.")

    base_price = service.base_price_paise
    effective_unit_price = base_price
    tariff_source = "BASE_CATALOG"
    is_covered = True
    plan_code: Optional[str] = None
    plan_name: Optional[str] = None

    # Inspect active insurance policy for patient
    policy = (
        session.query(PatientInsurancePolicy)
        .filter(
            PatientInsurancePolicy.patient_id == encounter.patient_id,
            PatientInsurancePolicy.is_active == True,  # noqa: E712
        )
        .order_by(PatientInsurancePolicy.id.desc())
        .first()
    )

    if policy and policy.plan_id:
        plan = policy.plan
        if plan and plan.is_active and plan.apply_tariff_automatically:
            plan_code = plan.code
            plan_name = plan.name

            # Query plan-specific negotiated tariff
            tariff = (
                session.query(InsurancePlanTariff)
                .filter(
                    InsurancePlanTariff.plan_id == plan.id,
                    InsurancePlanTariff.service_id == service.id,
                )
                .first()
            )

            if tariff:
                effective_unit_price = tariff.tariff_price_paise
                is_covered = tariff.is_covered
                tariff_source = "INSURANCE_PLAN_TARIFF"

            # Check room rent cap if service is room/bed accommodation
            is_room_item = "ROOM" in service.code.upper() or "BED" in service.code.upper()
            if is_room_item and plan.max_room_rent_daily_paise is not None:
                if effective_unit_price > plan.max_room_rent_daily_paise:
                    effective_unit_price = plan.max_room_rent_daily_paise
                    tariff_source = "PLAN_ROOM_RENT_CAP"

    total_price = effective_unit_price * quantity
    gst_rate = getattr(service, "gst_rate_percent", 0) or 0
    tax_amount = int(round(total_price * gst_rate / 100))

    return {
        "service_id": service.id,
        "service_code": service.code,
        "service_name": service.name,
        "base_price_paise": base_price,
        "effective_unit_price_paise": effective_unit_price,
        "quantity": quantity,
        "total_price_paise": total_price,
        "gst_rate_percent": gst_rate,
        "tax_amount_paise": tax_amount,
        "tariff_source": tariff_source,
        "is_covered": is_covered,
        "plan_code": plan_code,
        "plan_name": plan_name,
    }


def get_or_create_draft_invoice(
    session: Session,
    encounter_id: int,
    user_id: Optional[int] = None,
) -> Invoice:
    """Finds an existing open DRAFT invoice for the encounter, or creates a new one."""
    stmt = (
        select(Invoice)
        .where(
            Invoice.encounter_id == encounter_id,
            Invoice.status == InvoiceStatusEnum.DRAFT,
        )
        .order_by(Invoice.id.desc())
    )
    invoice = session.execute(stmt).scalars().first()
    if invoice:
        return invoice

    encounter = session.get(PatientEncounter, encounter_id)
    if not encounter:
        raise HTTPException(status_code=404, detail=f"Encounter {encounter_id} not found.")

    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    inv_number = f"INV-{date_str}-{secrets.token_hex(4).upper()}"

    new_invoice = Invoice(
        patient_id=encounter.patient_id,
        encounter_id=encounter.id,
        invoice_number=inv_number,
        status=InvoiceStatusEnum.DRAFT,
        total_amount_paise=0,
        discount_amount_paise=0,
        amount_paid_paise=0,
        tax_amount_paise=0,
        cgst_amount_paise=0,
        sgst_amount_paise=0,
        igst_amount_paise=0,
        is_tax_exempt=True,
        notes="Automated RCM Clinical Charge Master Invoice",
        created_by_id=user_id,
    )
    session.add(new_invoice)
    session.flush()
    return new_invoice


def recalculate_invoice_totals(invoice: Invoice, is_interstate: bool = False) -> None:
    """Synchronizes invoice total amount, GST components, and tax exemptions from line items (N-30)."""
    total_line_amount = sum(item.total_price_paise for item in invoice.line_items)
    total_tax_amount = sum(item.tax_amount_paise for item in invoice.line_items)

    invoice.total_amount_paise = total_line_amount
    invoice.tax_amount_paise = total_tax_amount

    interstate = is_interstate or getattr(invoice, "is_interstate", False)
    if interstate:
        invoice.igst_amount_paise = total_tax_amount
        invoice.cgst_amount_paise = 0
        invoice.sgst_amount_paise = 0
    else:
        invoice.cgst_amount_paise = total_tax_amount // 2
        invoice.sgst_amount_paise = total_tax_amount - invoice.cgst_amount_paise
        invoice.igst_amount_paise = 0

    invoice.is_tax_exempt = (total_tax_amount == 0)


def post_clinical_charge(
    session: Session,
    encounter_id: int,
    event_type: str,
    source_reference: str,
    description: str,
    unit_price_paise: Optional[int] = None,
    quantity: int = 1,
    service_id: Optional[int] = None,
    gst_rate_percent: int = 0,
    hsn_sac_code: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Posts an automated clinical charge to the encounter invoice.
    Enforces idempotency using source_reference to prevent double-billing of clinical events.
    """
    if quantity < 1:
        raise HTTPException(status_code=400, detail="Quantity must be at least 1.")

    # 1. Idempotency Check
    existing_charge = (
        session.execute(
            select(ChargeCaptureEvent).where(ChargeCaptureEvent.source_reference == source_reference)
        )
        .scalars()
        .first()
    )
    if existing_charge:
        raise HTTPException(
            status_code=409,
            detail=f"Charge already posted for clinical event source reference '{source_reference}'. Duplicate billing prevented.",
        )

    encounter = session.get(PatientEncounter, encounter_id)
    if not encounter:
        raise HTTPException(status_code=404, detail=f"Encounter {encounter_id} not found.")

    # 2. Tariff Resolution
    tariff_applied = "MANUAL_OR_EVENT_PRICE"
    is_covered = True
    effective_unit_price = unit_price_paise

    if service_id is not None and effective_unit_price is None:
        resolved = resolve_service_tariff(session, encounter_id, service_id, quantity)
        effective_unit_price = resolved["effective_unit_price_paise"]
        tariff_applied = resolved["tariff_source"]
        is_covered = resolved["is_covered"]
        gst_rate_percent = resolved["gst_rate_percent"]
    elif effective_unit_price is None:
        effective_unit_price = 0

    total_price = effective_unit_price * quantity
    tax_amount = int(round(total_price * gst_rate_percent / 100))

    # 3. Attach Line Item to Draft Invoice
    invoice = get_or_create_draft_invoice(session, encounter_id, user_id=user_id)

    line_item = InvoiceLineItem(
        invoice_id=invoice.id,
        service_id=service_id,
        description=description,
        quantity=quantity,
        unit_price_paise=effective_unit_price,
        total_price_paise=total_price,
        gst_rate_percent=gst_rate_percent,
        tax_amount_paise=tax_amount,
        hsn_sac_code=hsn_sac_code,
        created_by_id=user_id,
    )
    session.add(line_item)
    session.flush()

    recalculate_invoice_totals(invoice)

    # 4. Insert Charge Capture Audit Event
    charge_event = ChargeCaptureEvent(
        encounter_id=encounter_id,
        patient_id=encounter.patient_id,
        invoice_id=invoice.id,
        invoice_line_item_id=line_item.id,
        event_type=event_type,
        source_reference=source_reference,
        service_id=service_id,
        description=description,
        quantity=quantity,
        unit_price_paise=effective_unit_price,
        tax_amount_paise=tax_amount,
        total_charge_paise=total_price + tax_amount,
        tariff_applied=tariff_applied,
        is_insurance_covered=is_covered,
        created_by_id=user_id,
    )
    session.add(charge_event)
    session.commit()
    session.refresh(charge_event)
    session.refresh(invoice)

    logger.info(
        "RCM Charge posted: %s, Ref: %s, Encounter: %d, Total: %d paise",
        event_type,
        source_reference,
        encounter_id,
        total_price + tax_amount,
    )

    return {
        "charge_event_id": charge_event.id,
        "invoice_id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "invoice_line_item_id": line_item.id,
        "source_reference": source_reference,
        "description": description,
        "quantity": quantity,
        "unit_price_paise": effective_unit_price,
        "tax_amount_paise": tax_amount,
        "total_charge_paise": total_price + tax_amount,
        "tariff_applied": tariff_applied,
        "is_insurance_covered": is_covered,
        "invoice_total_amount_paise": invoice.total_amount_paise,
        "invoice_net_amount_paise": invoice.net_amount_paise,
        "posted_at": charge_event.posted_at,
    }


def execute_midnight_census_charge_run(
    session: Session,
    census_date: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Automated Inpatient Midnight Census Run:
    Scans all currently active IPD admissions and posts daily room/bed accommodation charges to open invoices.
    Applies plan room-rent caps if the patient is covered under an insurance MoU.
    """
    target_date = census_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Find active IPD admissions (not discharged and assigned to a bed)
    active_admissions = (
        session.query(InpatientAdmission)
        .join(PatientEncounter, InpatientAdmission.encounter_id == PatientEncounter.id)
        .filter(
            InpatientAdmission.discharge_time.is_(None),
            InpatientAdmission.current_bed_id.isnot(None),
            PatientEncounter.status != "DISCHARGED",
        )
        .all()
    )

    charges_posted_count = 0
    already_posted_count = 0
    total_revenue_posted = 0
    details: List[Dict[str, Any]] = []

    for adm in active_admissions:
        encounter = adm.encounter
        bed = session.get(Bed, adm.current_bed_id)
        if not bed:
            continue

        source_ref = f"CENSUS-{encounter.id}-{bed.id}-{target_date}"

        # Idempotency check: Skip if already charged for this bed on target_date
        existing = (
            session.execute(
                select(ChargeCaptureEvent).where(ChargeCaptureEvent.source_reference == source_ref)
            )
            .scalars()
            .first()
        )
        if existing:
            already_posted_count += 1
            details.append({
                "encounter_id": encounter.id,
                "bed_id": bed.id,
                "bed_number": bed.bed_number,
                "status": "ALREADY_POSTED",
                "source_reference": source_ref,
            })
            continue

        # Resolve daily rate (considering insurance room rent cap)
        daily_rate = bed.base_daily_rate_paise
        tariff_note = "BED_BASE_DAILY_RATE"

        policy = (
            session.query(PatientInsurancePolicy)
            .filter(
                PatientInsurancePolicy.patient_id == encounter.patient_id,
                PatientInsurancePolicy.is_active == True,  # noqa: E712
            )
            .first()
        )
        if policy and policy.plan and policy.plan.max_room_rent_daily_paise is not None:
            if daily_rate > policy.plan.max_room_rent_daily_paise:
                daily_rate = policy.plan.max_room_rent_daily_paise
                tariff_note = "PLAN_ROOM_RENT_CAP"

        description = f"Midnight Census Room Accommodation - Bed {bed.bed_number} ({target_date})"
        result = post_clinical_charge(
            session=session,
            encounter_id=encounter.id,
            event_type="ROOM_BED_CHARGE",
            source_reference=source_ref,
            description=description,
            unit_price_paise=daily_rate,
            quantity=1,
            user_id=user_id,
        )

        charges_posted_count += 1
        total_revenue_posted += daily_rate
        details.append({
            "encounter_id": encounter.id,
            "bed_id": bed.id,
            "bed_number": bed.bed_number,
            "daily_rate_paise": daily_rate,
            "tariff_applied": tariff_note,
            "status": "POSTED",
            "source_reference": source_ref,
            "invoice_number": result["invoice_number"],
        })

    return {
        "census_date": target_date,
        "active_ipd_encounters_count": len(active_admissions),
        "charges_posted_count": charges_posted_count,
        "already_posted_count": already_posted_count,
        "total_revenue_posted_paise": total_revenue_posted,
        "details": details,
    }


def post_surgery_completion_charges(
    session: Session,
    surgery_id: int,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Automatically posts surgery facility charges upon OT case completion:
    1. Hourly Operation Theatre utilization fee (hourly_rate * duration_hours)
    2. Surgical procedure fee from service catalog / tariff
    """
    surgery = session.get(SurgicalSchedule, surgery_id)
    if not surgery:
        raise HTTPException(status_code=404, detail=f"Surgical schedule {surgery_id} not found.")

    theatre = surgery.theatre
    encounter_id = surgery.encounter_id

    # Calculate actual duration or fallback to scheduled
    start_time = surgery.actual_start_time or surgery.scheduled_start_time
    end_time = surgery.actual_end_time or surgery.scheduled_end_time
    duration_hours = max(0.5, (end_time - start_time).total_seconds() / 3600.0)

    # 1. OT Theatre Facility Hourly Charge
    ot_hourly_rate = theatre.hourly_rate_paise if theatre else 0
    ot_total_charge = int(round(ot_hourly_rate * duration_hours))
    ot_ref = f"SURGERY-OT-{surgery.id}"

    ot_result = post_clinical_charge(
        session=session,
        encounter_id=encounter_id,
        event_type="SURGERY_COMPLETED",
        source_reference=ot_ref,
        description=f"Operation Theatre Facility Charge - {theatre.name if theatre else 'OT'} ({duration_hours:.1f} hrs)",
        unit_price_paise=ot_total_charge,
        quantity=1,
        user_id=user_id,
    )

    # 2. Procedure Fee from Service Catalog
    proc_ref = f"SURGERY-PROC-{surgery.id}"
    proc_result = post_clinical_charge(
        session=session,
        encounter_id=encounter_id,
        event_type="SURGERY_COMPLETED",
        source_reference=proc_ref,
        description=f"Surgical Procedure Fee - Case {surgery.case_number}",
        service_id=surgery.procedure_service_id,
        quantity=1,
        user_id=user_id,
    )

    return {
        "surgery_id": surgery.id,
        "case_number": surgery.case_number,
        "charges_posted_count": 2,
        "total_charges_posted_paise": ot_total_charge + proc_result["total_charge_paise"],
        "details": [ot_result, proc_result],
    }
