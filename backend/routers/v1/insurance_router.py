from datetime import datetime, timezone
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import RequirePermission
from models import (
    ClaimStatusEnum,
    DiagnosisMaster,
    EncounterStatusEnum,
    InsuranceClaim,
    InsurancePlan,
    InsurancePlanTariff,
    InsurancePreAuth,
    InsuranceProvider,
    Invoice,
    InvoiceStatusEnum,
    Patient,
    PatientEncounter,
    PatientInsurancePolicy,
    PaymentMethodEnum,
    PaymentReceipt,
    PreAuthStatusEnum,
    ServiceCatalog,
    User,
)
from schemas.v1.schemas import (
    InsuranceClaimCreate,
    InsuranceClaimResponse,
    InsuranceClaimSettleRequest,
    InsurancePlanCreate,
    InsurancePlanResponse,
    InsurancePlanTariffResponse,
    InsurancePlanTariffUpsert,
    InsurancePreAuthCreate,
    InsurancePreAuthResponse,
    InsurancePreAuthStatusUpdate,
    InsuranceProviderCreate,
    InsuranceProviderResponse,
    PatientInsurancePolicyCreate,
    PatientInsurancePolicyResponse,
    PlanComparisonBreakdownItem,
    PlanComparisonEstimateRequest,
)

router = APIRouter(prefix="/api/insurance", tags=["Insurance, TPA, Plans, Tariffs & Claims"])


def _gen_ref(prefix: str) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{prefix}-{date_str}-{uuid.uuid4().hex[:10].upper()}"


def _to_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# -----------------------------------------------------------------------------
# 1. Insurance Providers & TPA Master
# -----------------------------------------------------------------------------
@router.post("/providers", response_model=InsuranceProviderResponse, status_code=status.HTTP_201_CREATED)
def create_insurance_provider(
    payload: InsuranceProviderCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:write", "billing:write")),
):
    dup = db.execute(select(InsuranceProvider).where(InsuranceProvider.code == payload.code)).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail=f"Insurance provider '{payload.code}' already exists.")

    provider = InsuranceProvider(**payload.model_dump(), is_active=True)
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


@router.get("/providers", response_model=List[InsuranceProviderResponse])
def list_insurance_providers(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:read", "billing:read")),
):
    return list(db.execute(select(InsuranceProvider).order_by(InsuranceProvider.id.asc())).scalars().all())


# -----------------------------------------------------------------------------
# 2. Insurer Plans & Plan-Specific Tariff Rate Cards (Schedule of Charges)
# -----------------------------------------------------------------------------
@router.post("/plans", response_model=InsurancePlanResponse, status_code=status.HTTP_201_CREATED)
def create_insurance_plan(
    payload: InsurancePlanCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:write", "billing:write")),
):
    provider = db.get(InsuranceProvider, payload.provider_id)
    if not provider or not provider.is_active:
        raise HTTPException(status_code=404, detail="Active insurance provider not found.")

    dup = db.execute(select(InsurancePlan).where(InsurancePlan.code == payload.code)).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail=f"Insurance plan '{payload.code}' already exists.")

    plan = InsurancePlan(**payload.model_dump(), is_active=True)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


@router.get("/plans", response_model=List[InsurancePlanResponse])
def list_insurance_plans(
    provider_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:read", "billing:read")),
):
    stmt = select(InsurancePlan).where(InsurancePlan.is_active == True)
    if provider_id is not None:
        stmt = stmt.where(InsurancePlan.provider_id == provider_id)
    return list(db.execute(stmt.order_by(InsurancePlan.id.asc())).scalars().all())


@router.post(
    "/plans/{plan_id}/tariffs",
    response_model=InsurancePlanTariffResponse,
    status_code=status.HTTP_201_CREATED,
)
def upsert_plan_service_tariff(
    plan_id: int,
    payload: InsurancePlanTariffUpsert,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:write", "billing:write")),
):
    """Configures or updates a plan-specific negotiated tariff rate and coverage flag for a ServiceCatalog item."""
    plan = db.get(InsurancePlan, plan_id)
    if not plan or not plan.is_active:
        raise HTTPException(status_code=404, detail="Active insurance plan not found.")

    service = db.get(ServiceCatalog, payload.service_id)
    if not service or not service.is_active:
        raise HTTPException(status_code=404, detail="Active service catalog item not found.")

    existing = db.execute(
        select(InsurancePlanTariff).where(
            InsurancePlanTariff.plan_id == plan_id,
            InsurancePlanTariff.service_id == payload.service_id,
        )
    ).scalar_one_or_none()

    if existing:
        existing.tariff_price_paise = payload.tariff_price_paise
        existing.is_covered = payload.is_covered
        db.commit()
        db.refresh(existing)
        return existing

    tariff = InsurancePlanTariff(
        plan_id=plan_id,
        service_id=payload.service_id,
        tariff_price_paise=payload.tariff_price_paise,
        is_covered=payload.is_covered,
    )
    db.add(tariff)
    db.commit()
    db.refresh(tariff)
    return tariff


@router.get("/plans/{plan_id}/tariffs", response_model=List[InsurancePlanTariffResponse])
def list_plan_tariffs(
    plan_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("mdm:read", "billing:read")),
):
    stmt = (
        select(InsurancePlanTariff)
        .where(InsurancePlanTariff.plan_id == plan_id)
        .order_by(InsurancePlanTariff.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


@router.post("/plans/compare-estimate", response_model=List[PlanComparisonBreakdownItem])
def compare_insurance_plans_estimate(
    payload: PlanComparisonEstimateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:read", "opd:read", "adt:read")),
):
    """
    Compares Hospital Rack Rate vs. one or more Insurance Plans for a given basket of services
    and IPD bed stay, returning Mou savings, room-rent excess, non-covered exclusions,
    insurer payable, and patient out-of-pocket estimate.
    """
    services = [db.get(ServiceCatalog, sid) for sid in payload.service_ids]
    if any(s is None for s in services):
        raise HTTPException(status_code=404, detail="One or more service_ids not found in catalog.")

    rack_services_paise = sum(s.base_price_paise for s in services if s)
    rack_bed_paise = payload.bed_daily_rate_paise * payload.stay_days
    hospital_rack_total = rack_services_paise + rack_bed_paise

    results: List[PlanComparisonBreakdownItem] = []

    for pid in payload.plan_ids:
        plan = db.get(InsurancePlan, pid)
        if not plan or not plan.is_active:
            raise HTTPException(status_code=404, detail=f"Active insurance plan ID {pid} not found.")

        tariffs_map = {
            t.service_id: t
            for t in db.execute(select(InsurancePlanTariff).where(InsurancePlanTariff.plan_id == plan.id)).scalars().all()
        }

        plan_services_total = 0
        non_covered_paise = 0
        eligible_services_paise = 0

        for s in services:
            t = tariffs_map.get(s.id)
            if t:
                if not t.is_covered:
                    # Excluded service: billed at rack rate (or tariff) and paid 100% by patient
                    plan_services_total += s.base_price_paise
                    non_covered_paise += s.base_price_paise
                else:
                    eff_price = t.tariff_price_paise if plan.apply_tariff_automatically else s.base_price_paise
                    plan_services_total += eff_price
                    eligible_services_paise += min(eff_price, t.tariff_price_paise)
                    if eff_price > t.tariff_price_paise:
                        non_covered_paise += eff_price - t.tariff_price_paise
            else:
                plan_services_total += s.base_price_paise
                eligible_services_paise += s.base_price_paise

        # Room rent cap calculation
        if plan.max_room_rent_daily_paise is not None and payload.bed_daily_rate_paise > plan.max_room_rent_daily_paise:
            covered_daily = plan.max_room_rent_daily_paise
            excess_daily = payload.bed_daily_rate_paise - plan.max_room_rent_daily_paise
        else:
            covered_daily = payload.bed_daily_rate_paise
            excess_daily = 0

        eligible_bed_paise = covered_daily * payload.stay_days
        room_rent_excess_paise = excess_daily * payload.stay_days

        negotiated_plan_total = plan_services_total + rack_bed_paise
        mou_savings = max(0, hospital_rack_total - negotiated_plan_total)

        total_eligible_for_insurer = eligible_services_paise + eligible_bed_paise
        insurer_payable = (total_eligible_for_insurer * (100 - plan.default_copay_percentage)) // 100
        patient_oop = negotiated_plan_total - insurer_payable

        results.append(
            PlanComparisonBreakdownItem(
                plan_id=plan.id,
                plan_code=plan.code,
                plan_name=plan.name,
                hospital_rack_total_paise=hospital_rack_total,
                negotiated_plan_total_paise=negotiated_plan_total,
                mou_tariff_savings_paise=mou_savings,
                non_covered_excluded_paise=non_covered_paise,
                room_rent_excess_paise=room_rent_excess_paise,
                estimated_insurer_payable_paise=insurer_payable,
                estimated_patient_out_of_pocket_paise=patient_oop,
            )
        )

    return results


# -----------------------------------------------------------------------------
# 3. Patient Insurance Policies
# -----------------------------------------------------------------------------
@router.post("/policies", response_model=PatientInsurancePolicyResponse, status_code=status.HTTP_201_CREATED)
def create_patient_policy(
    payload: PatientInsurancePolicyCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("patients:write", "billing:write")),
):
    patient = db.get(Patient, payload.patient_id)
    if not patient or not patient.is_active:
        raise HTTPException(status_code=404, detail="Active patient not found.")

    provider = db.get(InsuranceProvider, payload.provider_id)
    if not provider or not provider.is_active:
        raise HTTPException(status_code=404, detail="Active insurance provider not found.")

    effective_copay = payload.copay_percentage if payload.copay_percentage is not None else 0
    if payload.plan_id is not None:
        plan = db.get(InsurancePlan, payload.plan_id)
        if not plan or not plan.is_active or plan.provider_id != provider.id:
            raise HTTPException(status_code=400, detail="Invalid or mismatched insurance plan for this provider.")
        if payload.copay_percentage is None:
            effective_copay = plan.default_copay_percentage

    if _to_utc(payload.valid_till) <= _to_utc(payload.valid_from):
        raise HTTPException(status_code=400, detail="Policy valid_till must be after valid_from.")

    dup = db.execute(
        select(PatientInsurancePolicy).where(
            PatientInsurancePolicy.provider_id == payload.provider_id,
            PatientInsurancePolicy.policy_number == payload.policy_number,
        )
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail="Policy number already registered for this provider.")

    data = payload.model_dump()
    data["copay_percentage"] = effective_copay
    policy = PatientInsurancePolicy(**data, is_active=True)
    db.add(policy)
    db.commit()

    stmt = (
        select(PatientInsurancePolicy)
        .options(joinedload(PatientInsurancePolicy.provider))
        .where(PatientInsurancePolicy.id == policy.id)
    )
    return db.execute(stmt).scalar_one()


@router.get("/patients/{patient_id}/policies", response_model=List[PatientInsurancePolicyResponse])
def list_patient_policies(
    patient_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("patients:read", "billing:read")),
):
    stmt = (
        select(PatientInsurancePolicy)
        .options(joinedload(PatientInsurancePolicy.provider))
        .where(PatientInsurancePolicy.patient_id == patient_id, PatientInsurancePolicy.is_active == True)
        .order_by(PatientInsurancePolicy.id.desc())
    )
    return list(db.execute(stmt).scalars().all())


# -----------------------------------------------------------------------------
# 4. Cashless Pre-Authorization Workflow
# -----------------------------------------------------------------------------
@router.post("/preauths", response_model=InsurancePreAuthResponse, status_code=status.HTTP_201_CREATED)
def submit_cashless_preauth(
    payload: InsurancePreAuthCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:write", "adt:write")),
):
    enc = db.get(PatientEncounter, payload.encounter_id)
    if not enc or enc.status != EncounterStatusEnum.ACTIVE:
        raise HTTPException(status_code=400, detail="Cashless pre-authorization requires an ACTIVE encounter.")

    policy = db.get(PatientInsurancePolicy, payload.policy_id)
    if not policy or not policy.is_active or policy.patient_id != enc.patient_id:
        raise HTTPException(status_code=400, detail="Valid active insurance policy for this patient is required.")

    now_utc = datetime.now(timezone.utc)
    if _to_utc(policy.valid_till) < now_utc:
        raise HTTPException(status_code=400, detail="Cannot submit pre-authorization: patient insurance policy has expired.")

    if payload.requested_amount_paise > policy.sum_insured_paise:
        raise HTTPException(
            status_code=400,
            detail=f"Requested pre-auth amount ({payload.requested_amount_paise}) exceeds policy sum insured ({policy.sum_insured_paise}).",
        )

    diagnosis = db.get(DiagnosisMaster, payload.primary_diagnosis_id)
    if not diagnosis or not diagnosis.is_active:
        raise HTTPException(status_code=404, detail="Primary ICD-10 diagnosis not found.")
    if not diagnosis.is_billable:
        raise HTTPException(
            status_code=400,
            detail=f"ICD-10 code '{diagnosis.icd10_code}' is marked non-billable and cannot be used for insurance pre-authorization.",
        )

    preauth = InsurancePreAuth(
        encounter_id=payload.encounter_id,
        policy_id=payload.policy_id,
        primary_diagnosis_id=payload.primary_diagnosis_id,
        preauth_number=_gen_ref("PA"),
        status=PreAuthStatusEnum.SUBMITTED,
        requested_amount_paise=payload.requested_amount_paise,
        approved_amount_paise=0,
        clinical_justification=payload.clinical_justification,
    )
    db.add(preauth)
    db.commit()
    db.refresh(preauth)
    return preauth


@router.patch("/preauths/{preauth_id}/status", response_model=InsurancePreAuthResponse)
def update_preauth_status(
    preauth_id: int,
    payload: InsurancePreAuthStatusUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:write")),
):
    preauth = db.get(InsurancePreAuth, preauth_id)
    if not preauth:
        raise HTTPException(status_code=404, detail="Pre-authorization record not found.")

    if payload.status == PreAuthStatusEnum.APPROVED:
        if not payload.approved_amount_paise or payload.approved_amount_paise <= 0:
            raise HTTPException(status_code=400, detail="Approved pre-authorization requires a positive approved_amount_paise.")
        if payload.approved_amount_paise > preauth.requested_amount_paise:
            raise HTTPException(status_code=400, detail="Approved amount cannot exceed requested amount.")
        preauth.approved_amount_paise = payload.approved_amount_paise

    preauth.status = payload.status
    if payload.tpa_reference_number is not None:
        preauth.tpa_reference_number = payload.tpa_reference_number
    if payload.tpa_query_remarks is not None:
        preauth.tpa_query_remarks = payload.tpa_query_remarks

    db.commit()
    db.refresh(preauth)
    return preauth


# -----------------------------------------------------------------------------
# 5. Smart Line-by-Line Claim Adjudication & Settlement
# -----------------------------------------------------------------------------
@router.post("/claims", response_model=InsuranceClaimResponse, status_code=status.HTTP_201_CREATED)
def submit_insurance_claim(
    payload: InsuranceClaimCreate,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:write")),
):
    stmt = (
        select(Invoice)
        .options(joinedload(Invoice.line_items))
        .where(Invoice.id == payload.invoice_id)
    )
    invoice = db.execute(stmt).unique().scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status not in [InvoiceStatusEnum.UNPAID, InvoiceStatusEnum.PARTIAL]:
        raise HTTPException(status_code=400, detail="Insurance claims can only be submitted for finalized UNPAID/PARTIAL invoices.")

    existing_claim = db.execute(select(InsuranceClaim).where(InsuranceClaim.invoice_id == invoice.id)).scalar_one_or_none()
    if existing_claim:
        raise HTTPException(status_code=409, detail="An insurance claim already exists for this invoice.")

    policy = (
        db.execute(
            select(PatientInsurancePolicy)
            .options(joinedload(PatientInsurancePolicy.plan))
            .where(PatientInsurancePolicy.id == payload.policy_id)
        )
        .scalar_one_or_none()
    )
    if not policy or not policy.is_active or policy.patient_id != invoice.patient_id:
        raise HTTPException(status_code=400, detail="Valid active insurance policy for this patient is required.")

    outstanding_paise = invoice.net_amount_paise - invoice.amount_paid_paise
    coverage_cap_paise = policy.sum_insured_paise

    if payload.preauth_id is not None:
        preauth = db.get(InsurancePreAuth, payload.preauth_id)
        if not preauth or preauth.policy_id != policy.id:
            raise HTTPException(status_code=400, detail="Pre-authorization does not match policy.")
        if preauth.status != PreAuthStatusEnum.APPROVED:
            raise HTTPException(status_code=400, detail="Linked pre-authorization must be in APPROVED state.")
        coverage_cap_paise = min(coverage_cap_paise, preauth.approved_amount_paise)

    # Line-by-line tariff, exclusion, and room-rent cap adjudication when policy is linked to an InsurancePlan
    disallowed_paise = 0
    if policy.plan and policy.plan.is_active:
        plan = policy.plan
        tariffs_map = {
            t.service_id: t
            for t in db.execute(select(InsurancePlanTariff).where(InsurancePlanTariff.plan_id == plan.id)).scalars().all()
        }
        for item in invoice.line_items:
            if item.service_id and item.service_id in tariffs_map:
                t = tariffs_map[item.service_id]
                if not t.is_covered:
                    disallowed_paise += item.total_price_paise
                elif item.unit_price_paise > t.tariff_price_paise:
                    disallowed_paise += (item.unit_price_paise - t.tariff_price_paise) * item.quantity
            elif item.description.startswith("IPD BED CHARGE:") and plan.max_room_rent_daily_paise is not None:
                if item.unit_price_paise > plan.max_room_rent_daily_paise:
                    disallowed_paise += (item.unit_price_paise - plan.max_room_rent_daily_paise) * item.quantity

    # FIN-001: Calculate insurer payable based on gross net_amount_paise before advance deposits are deducted
    eligible_pool_paise = max(0, invoice.net_amount_paise - disallowed_paise)
    insurer_share_before_cap = (eligible_pool_paise * (100 - policy.copay_percentage)) // 100
    insurer_payable_paise = min(insurer_share_before_cap, coverage_cap_paise)
    
    # Copay is the remaining portion of the original bill
    patient_copay_paise = invoice.net_amount_paise - insurer_payable_paise

    claim = InsuranceClaim(
        invoice_id=invoice.id,
        policy_id=policy.id,
        preauth_id=payload.preauth_id,
        claim_number=_gen_ref("CLM"),
        status=ClaimStatusEnum.SUBMITTED,
        claimed_amount_paise=outstanding_paise,
        insurer_payable_paise=insurer_payable_paise,
        patient_copay_paise=patient_copay_paise,
        settled_amount_paise=0,
        remarks=payload.remarks,
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return claim


@router.patch("/claims/{claim_id}/settle", response_model=InsuranceClaimResponse)
def settle_insurance_claim(
    claim_id: int,
    payload: InsuranceClaimSettleRequest,
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:write", "billing:pay")),
):
    stmt = select(InsuranceClaim).where(InsuranceClaim.id == claim_id).with_for_update()
    claim = db.execute(stmt).scalar_one_or_none()
    if not claim:
        raise HTTPException(status_code=404, detail="Insurance claim not found.")
    if claim.status == ClaimStatusEnum.SETTLED:
        raise HTTPException(status_code=409, detail="Insurance claim is already settled.")

    if payload.settled_amount_paise > claim.insurer_payable_paise:
        raise HTTPException(
            status_code=400,
            detail=f"Settled amount ({payload.settled_amount_paise}) cannot exceed approved insurer payable ({claim.insurer_payable_paise}).",
        )

    inv_stmt = select(Invoice).where(Invoice.id == claim.invoice_id).with_for_update()
    invoice = db.execute(inv_stmt).scalar_one()

    if invoice.amount_paid_paise + payload.settled_amount_paise > invoice.net_amount_paise:
        raise HTTPException(status_code=400, detail="Settlement amount exceeds remaining invoice balance.")

    receipt = PaymentReceipt(
        invoice_id=invoice.id,
        receipt_number=_gen_ref("REC-INS"),
        amount_paid_paise=payload.settled_amount_paise,
        payment_method=PaymentMethodEnum.INSURANCE,
        transaction_reference=payload.settlement_utr_reference,
    )
    db.add(receipt)

    invoice.amount_paid_paise += payload.settled_amount_paise
    if invoice.amount_paid_paise == invoice.net_amount_paise:
        invoice.status = InvoiceStatusEnum.PAID
    else:
        invoice.status = InvoiceStatusEnum.PARTIAL

    claim.status = ClaimStatusEnum.SETTLED
    claim.settled_amount_paise = payload.settled_amount_paise
    claim.settlement_utr_reference = payload.settlement_utr_reference
    if payload.remarks:
        claim.remarks = payload.remarks

    db.commit()
    db.refresh(claim)
    return claim