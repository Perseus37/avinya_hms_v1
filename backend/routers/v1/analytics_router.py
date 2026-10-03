from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from database import get_db
from dependencies import RequirePermission
from models import (
    AdmissionTypeEnum,
    AllergyCategoryEnum,
    Appointment,
    AppointmentStatusEnum,
    Bed,
    BedStatusEnum,
    BedTransferLog,
    BedTypeEnum,
    BloodBagInventory,
    BloodBagStatusEnum,
    BloodCrossMatchOrder,
    BloodGroupEnum,
    ClinicalNote,
    Department,
    DiagnosisMaster,
    DiagnosisTypeEnum,
    DiagnosticOrder,
    DiagnosticTask,
    DischargeDispositionEnum,
    DoctorProfile,
    ERTriageAssessment,
    EncounterDiagnosis,
    EncounterStatusEnum,
    EncounterTypeEnum,
    EncounterVital,
    FederatedClient,
    FederationTransferLog,
    FluidBalanceLog,
    FluidDirectionEnum,
    GenderEnum,
    InpatientAdmission,
    InsuranceClaim,
    InsurancePlanTariff,
    InsurancePreAuth,
    InventoryBatch,
    Invoice,
    InvoiceLineItem,
    InvoiceStatusEnum,
    MedicationAdministrationLog,
    MedicationFormEnum,
    MedicoLegalCase,
    NursingShiftAssignment,
    OperativeAnesthesiaRecord,
    Patient,
    PatientAllergy,
    PatientDeposit,
    PatientEncounter,
    PaymentReceipt,
    PhysicalRoom,
    PreAuthStatusEnum,
    Prescription,
    PrescriptionItem,
    PrescriptionStatusEnum,
    RadiologyStudyMetadata,
    RefundVoucher,
    ReportTemplate,
    RoomTypeEnum,
    ServiceCatalog,
    SeverityEnum,
    SurgeryStatusEnum,
    SurgicalImplantLog,
    SurgicalSchedule,
    TaskStatusEnum,
    TransfusionReactionTypeEnum,
    User,
    UserStatusEnum,
    WHOSurgicalSafetyChecklist,
    eMARStatusEnum,
)

router = APIRouter(prefix="/api/analytics", tags=["Phase 14: Enterprise Hospital BI & Clinical Intelligence"])


def _to_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _pct(part: int | float, whole: int | float) -> float:
    return round((part / whole) * 100.0, 2) if whole > 0 else 0.0


# =====================================================================
# 1. EXECUTIVE COMMAND CENTER (SPATIAL CENSUS, ALOS, READMISSIONS & ARPOB)
# =====================================================================

@router.get("/executive-command-center")
def get_executive_command_center(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:read", "adt:read", "wards:read", "iam:read")),
) -> Dict[str, Any]:
    beds = list(
        db.execute(
            select(Bed).options(joinedload(Bed.room).joinedload(PhysicalRoom.ward))
        ).scalars().all()
    )
    total_beds = len(beds)
    bed_status_counts = {s.value: 0 for s in BedStatusEnum}
    bed_type_counts = {bt.value: {"total": 0, "occupied": 0} for bt in BedTypeEnum}
    room_type_counts = {rt.value: {"total_beds": 0, "occupied_beds": 0} for rt in RoomTypeEnum}
    ward_type_occupancy: Dict[str, Dict[str, int | float]] = {}

    daily_occupied_bed_revenue_paise = 0
    daily_potential_rack_capacity_paise = 0

    for b in beds:
        bed_status_counts[b.status.value] = bed_status_counts.get(b.status.value, 0) + 1
        bed_type_counts[b.bed_type.value]["total"] += 1
        daily_potential_rack_capacity_paise += b.base_daily_rate_paise

        if b.status == BedStatusEnum.OCCUPIED:
            daily_occupied_bed_revenue_paise += b.base_daily_rate_paise
            bed_type_counts[b.bed_type.value]["occupied"] += 1

        if b.room:
            r_type = b.room.room_type.value
            room_type_counts[r_type]["total_beds"] += 1
            if b.status == BedStatusEnum.OCCUPIED:
                room_type_counts[r_type]["occupied_beds"] += 1

        w_type = b.room.ward.ward_type.value if (b.room and b.room.ward) else "UNASSIGNED"
        if w_type not in ward_type_occupancy:
            ward_type_occupancy[w_type] = {
                "total_beds": 0,
                "occupied_beds": 0,
                "available_beds": 0,
                "occupancy_rate_pct": 0.0,
            }
        ward_type_occupancy[w_type]["total_beds"] += 1
        if b.status == BedStatusEnum.OCCUPIED:
            ward_type_occupancy[w_type]["occupied_beds"] += 1
        elif b.status == BedStatusEnum.AVAILABLE:
            ward_type_occupancy[w_type]["available_beds"] += 1

    for w_stats in ward_type_occupancy.values():
        w_stats["occupancy_rate_pct"] = _pct(w_stats["occupied_beds"], w_stats["total_beds"])

    occupied_beds = bed_status_counts.get("OCCUPIED", 0)
    total_transfers = db.scalar(select(func.count(BedTransferLog.id))) or 0

    # Encounters, ALOS by Admission Type & Readmission Rate
    encounters = list(db.execute(select(PatientEncounter)).scalars().all())
    active_by_type = {t.value: 0 for t in EncounterTypeEnum}
    for e in encounters:
        if e.status == EncounterStatusEnum.ACTIVE:
            active_by_type[e.encounter_type.value] += 1

    admissions = list(
        db.execute(select(InpatientAdmission).options(joinedload(InpatientAdmission.encounter))).scalars().all()
    )
    discharged_admissions = [a for a in admissions if a.discharge_time is not None and a.encounter is not None]
    stay_hours_list: List[float] = []
    los_by_admission_type: Dict[str, List[float]] = {at.value: [] for at in AdmissionTypeEnum}
    disposition_counts = {d.value: 0 for d in DischargeDispositionEnum}
    admissions_per_patient: Dict[int, int] = {}

    for a in admissions:
        if a.encounter:
            pid = a.encounter.patient_id
            admissions_per_patient[pid] = admissions_per_patient.get(pid, 0) + 1

    for a in discharged_admissions:
        if a.discharge_disposition:
            disposition_counts[a.discharge_disposition.value] += 1
        dur_hrs = max(0.1, (_to_utc(a.discharge_time) - _to_utc(a.encounter.start_time)).total_seconds() / 3600.0)
        stay_hours_list.append(dur_hrs)
        los_by_admission_type[a.admission_type.value].append(dur_hrs)

    avg_los_hours = round(sum(stay_hours_list) / len(stay_hours_list), 2) if stay_hours_list else 0.0
    readmitted_patients = sum(1 for cnt in admissions_per_patient.values() if cnt > 1)
    readmission_rate_pct = _pct(readmitted_patients, len(admissions_per_patient))
    ama_walkout_rate_pct = _pct(disposition_counts.get("AGAINST_MEDICAL_ADVICE", 0), len(discharged_admissions))
    inpatient_mortality_rate_pct = _pct(disposition_counts.get("DECEASED", 0), len(discharged_admissions))

    # OPD Funnel
    appointments = list(db.execute(select(Appointment)).scalars().all())
    opd_funnel = {s.value: 0 for s in AppointmentStatusEnum}
    for appt in appointments:
        opd_funnel[appt.status.value] += 1
    total_appts = len(appointments)

    # Unit Economics
    invoices = list(db.execute(select(Invoice).where(Invoice.status != InvoiceStatusEnum.VOID)).scalars().all())
    total_net_revenue_paise = sum(inv.net_amount_paise for inv in invoices)
    arpob_paise = total_net_revenue_paise // occupied_beds if occupied_beds > 0 else 0
    arpu_encounter_paise = total_net_revenue_paise // len(encounters) if encounters else 0

    return {
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
            "total_encounters": len(encounters),
            "active_encounters_by_type": active_by_type,
            "total_ipd_admissions": len(admissions),
            "discharged_ipd_admissions": len(discharged_admissions),
            "average_length_of_stay_hours": avg_los_hours,
            "average_length_of_stay_days": round(avg_los_hours / 24.0, 2),
            "alos_hours_by_admission_type": {
                k: (round(sum(v) / len(v), 2) if v else 0.0) for k, v in los_by_admission_type.items()
            },
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
    }


# =====================================================================
# 2. CFO FINANCIAL REVENUE CYCLE, A/R AGING, MOU TARIFFS & TPA PIPELINE
# =====================================================================

@router.get("/financial-revenue-cycle")
def get_financial_revenue_cycle(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("billing:read")),
) -> Dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    invoices = list(
        db.execute(
            select(Invoice).options(joinedload(Invoice.line_items), joinedload(Invoice.payments))
        )
        .unique()
        .scalars()
        .all()
    )

    non_void_invoices = [i for i in invoices if i.status != InvoiceStatusEnum.VOID]
    gross_billed_paise = sum(i.total_amount_paise for i in non_void_invoices)
    total_discounts_paise = sum(i.discount_amount_paise or 0 for i in non_void_invoices)
    net_billed_paise = sum(i.net_amount_paise for i in non_void_invoices)
    total_collected_paise = sum(i.amount_paid_paise for i in non_void_invoices)

    # Accounts Receivable (A/R) Aging Buckets
    ar_aging_buckets_paise = {
        "0_to_30_days_paise": 0,
        "31_to_60_days_paise": 0,
        "61_to_90_days_paise": 0,
        "over_90_days_paise": 0,
    }
    accounts_receivable_paise = 0
    for i in non_void_invoices:
        if i.status in [InvoiceStatusEnum.UNPAID, InvoiceStatusEnum.PARTIAL]:
            bal = i.net_amount_paise - i.amount_paid_paise
            accounts_receivable_paise += bal
            age_days = max(0, (now_utc - _to_utc(i.created_at)).days) if i.created_at else 0
            if age_days <= 30:
                ar_aging_buckets_paise["0_to_30_days_paise"] += bal
            elif age_days <= 60:
                ar_aging_buckets_paise["31_to_60_days_paise"] += bal
            elif age_days <= 90:
                ar_aging_buckets_paise["61_to_90_days_paise"] += bal
            else:
                ar_aging_buckets_paise["over_90_days_paise"] += bal

    # 8-Stream Revenue Attribution & Top Catalog Services
    streams = {
        "opd_consultations_paise": 0,
        "ipd_bed_stay_paise": 0,
        "diagnostics_lis_ris_paise": 0,
        "pharmacy_dispensing_paise": 0,
        "surgery_and_ot_paise": 0,
        "surgical_implants_paise": 0,
        "blood_bank_paise": 0,
        "other_catalog_services_paise": 0,
    }
    service_revenue_map: Dict[int, int] = {}

    for inv in non_void_invoices:
        for item in inv.line_items:
            desc = item.description or ""
            amt = item.total_price_paise
            if item.service_id:
                service_revenue_map[item.service_id] = service_revenue_map.get(item.service_id, 0) + amt

            if desc.startswith("OPD CONSULTATION:"):
                streams["opd_consultations_paise"] += amt
            elif desc.startswith("IPD BED CHARGE:"):
                streams["ipd_bed_stay_paise"] += amt
            elif desc.startswith("DIAGNOSTICS ["):
                streams["diagnostics_lis_ris_paise"] += amt
            elif desc.startswith("PHARMACY:"):
                streams["pharmacy_dispensing_paise"] += amt
            elif desc.startswith("SURGERY ["):
                streams["surgery_and_ot_paise"] += amt
            elif desc.startswith("SURGICAL IMPLANT ["):
                streams["surgical_implants_paise"] += amt
            elif desc.startswith("BLOOD BANK ["):
                streams["blood_bank_paise"] += amt
            else:
                streams["other_catalog_services_paise"] += amt

    catalog_map = {s.id: s for s in db.execute(select(ServiceCatalog)).scalars().all()}
    top_services = [
        {
            "service_id": sid,
            "code": catalog_map[sid].code if sid in catalog_map else f"SVC-{sid}",
            "name": catalog_map[sid].name if sid in catalog_map else "Unknown",
            "revenue_paise": rev,
        }
        for sid, rev in sorted(service_revenue_map.items(), key=lambda x: x[1], reverse=True)[:10]
    ]

    # Payment Method Mix
    receipts = list(db.execute(select(PaymentReceipt)).scalars().all())
    payment_method_mix: Dict[str, int] = {}
    for r in receipts:
        m = r.payment_method.value
        payment_method_mix[m] = payment_method_mix.get(m, 0) + r.amount_paid_paise

    # Insurance Pre-Auth, Claims & MoU Tariff Contractual Disallowances
    preauths = list(db.execute(select(InsurancePreAuth)).scalars().all())
    preauth_by_status = {s.value: 0 for s in PreAuthStatusEnum}
    for pa in preauths:
        preauth_by_status[pa.status.value] += 1

    claims = list(
        db.execute(
            select(InsuranceClaim).options(
                joinedload(InsuranceClaim.policy).joinedload(
                    InsuranceClaim.policy.property.mapper.class_.provider
                )
            )
        ).scalars().all()
    )
    total_claimed_paise = sum(c.claimed_amount_paise for c in claims)
    total_insurer_payable_paise = sum(c.insurer_payable_paise for c in claims)
    total_patient_copay_paise = sum(c.patient_copay_paise for c in claims)
    total_insurer_settled_paise = sum(c.settled_amount_paise for c in claims)
    unsettled_tpa_receivable_paise = max(0, total_insurer_payable_paise - total_insurer_settled_paise)

    tariffs = list(
        db.execute(select(InsurancePlanTariff).options(joinedload(InsurancePlanTariff.service))).scalars().all()
    )
    configured_mou_savings_per_unit_paise = sum(
        max(0, t.service.base_price_paise - t.tariff_price_paise)
        for t in tariffs
        if t.is_covered and t.service
    )

    tpa_provider_breakdown: Dict[str, Dict[str, int]] = {}
    for c in claims:
        p_name = c.policy.provider.name if (c.policy and c.policy.provider) else "Unknown Provider"
        if p_name not in tpa_provider_breakdown:
            tpa_provider_breakdown[p_name] = {
                "claims_count": 0,
                "insurer_payable_paise": 0,
                "settled_amount_paise": 0,
                "pending_receivable_paise": 0,
            }
        tpa_provider_breakdown[p_name]["claims_count"] += 1
        tpa_provider_breakdown[p_name]["insurer_payable_paise"] += c.insurer_payable_paise
        tpa_provider_breakdown[p_name]["settled_amount_paise"] += c.settled_amount_paise
        tpa_provider_breakdown[p_name]["pending_receivable_paise"] += max(
            0, c.insurer_payable_paise - c.settled_amount_paise
        )

    # Deposits & Refunds
    deposits = list(db.execute(select(PatientDeposit)).scalars().all())
    refunds = list(db.execute(select(RefundVoucher)).scalars().all())

    return {
        "revenue_waterfall": {
            "gross_billed_paise": gross_billed_paise,
            "total_discounts_paise": total_discounts_paise,
            "discount_leakage_rate_pct": _pct(total_discounts_paise, gross_billed_paise),
            "net_billed_paise": net_billed_paise,
            "total_collected_paise": total_collected_paise,
            "collection_efficiency_pct": _pct(total_collected_paise, net_billed_paise),
            "accounts_receivable_paise": accounts_receivable_paise,
            "ar_aging_buckets_paise": ar_aging_buckets_paise,
        },
        "revenue_by_clinical_stream": streams,
        "top_revenue_generating_services": top_services,
        "collections_by_payment_method": payment_method_mix,
        "insurance_and_tpa_pipeline": {
            "total_preauths": len(preauths),
            "preauth_status_breakdown": preauth_by_status,
            "preauth_approval_rate_pct": _pct(preauth_by_status["APPROVED"], len(preauths)),
            "total_claims": len(claims),
            "total_claimed_paise": total_claimed_paise,
            "total_insurer_payable_paise": total_insurer_payable_paise,
            "total_patient_copay_paise": total_patient_copay_paise,
            "total_insurer_settled_paise": total_insurer_settled_paise,
            "claim_settlement_ratio_pct": _pct(total_insurer_settled_paise, total_insurer_payable_paise),
            "unsettled_tpa_receivable_paise": unsettled_tpa_receivable_paise,
            "configured_mou_tariff_mappings_count": len(tariffs),
            "configured_mou_savings_index_paise": configured_mou_savings_per_unit_paise,
            "by_provider": tpa_provider_breakdown,
        },
        "deposits_and_refunds_ledger": {
            "total_deposits_collected_paise": sum(d.amount_paise for d in deposits),
            "active_unadjusted_wallet_liability_paise": sum(d.remaining_balance_paise for d in deposits),
            "total_refund_vouchers_count": len(refunds),
            "total_refunds_paid_paise": sum(rf.refund_amount_paise for rf in refunds),
        },
    }


# =====================================================================
# 3. PATIENT DEMOGRAPHICS, ABHA ADOPTION & EPIDEMIOLOGY PYRAMID
# =====================================================================

@router.get("/patient-demographics-and-epidemiology")
def get_patient_demographics_and_epidemiology(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("patients:read", "opd:read", "adt:read")),
) -> Dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    patients = list(db.execute(select(Patient).where(Patient.is_active == True)).scalars().all())

    age_cohorts = {
        "0_to_12_pediatric": 0,
        "13_to_18_adolescent": 0,
        "19_to_35_young_adult": 0,
        "36_to_60_middle_aged": 0,
        "60_plus_senior_citizen": 0,
    }
    gender_counts = {g.value: 0 for g in GenderEnum}
    blood_group_counts = {bg.value: 0 for bg in BloodGroupEnum}
    abha_linked_count = 0

    for p in patients:
        gender_counts[p.gender.value] = gender_counts.get(p.gender.value, 0) + 1
        if p.blood_group:
            blood_group_counts[p.blood_group.value] += 1
        if p.abha_number:
            abha_linked_count += 1

        age_years = max(0, (now_utc - _to_utc(p.date_of_birth)).days // 365)
        if age_years <= 12:
            age_cohorts["0_to_12_pediatric"] += 1
        elif age_years <= 18:
            age_cohorts["13_to_18_adolescent"] += 1
        elif age_years <= 35:
            age_cohorts["19_to_35_young_adult"] += 1
        elif age_years <= 60:
            age_cohorts["36_to_60_middle_aged"] += 1
        else:
            age_cohorts["60_plus_senior_citizen"] += 1

    # Allergy Risk Stratification
    pt_allergies = list(
        db.execute(select(PatientAllergy).options(joinedload(PatientAllergy.allergy)).where(PatientAllergy.is_active == True)).scalars().all()
    )
    allergy_by_severity = {s.value: 0 for s in SeverityEnum}
    allergy_by_category = {c.value: 0 for c in AllergyCategoryEnum}
    for pa in pt_allergies:
        allergy_by_severity[pa.severity.value] += 1
        if pa.allergy:
            allergy_by_category[pa.allergy.category.value] += 1

    # ICD-10 Diagnosis Type Breakdown
    enc_diags = list(
        db.execute(select(EncounterDiagnosis).options(joinedload(EncounterDiagnosis.diagnosis))).scalars().all()
    )
    diag_by_type = {dt.value: 0 for dt in DiagnosisTypeEnum}
    billable_diag_count = 0
    for ed in enc_diags:
        diag_by_type[ed.diagnosis_type.value] += 1
        if ed.diagnosis and ed.diagnosis.is_billable:
            billable_diag_count += 1

    return {
        "mpi_population_summary": {
            "total_active_patients": len(patients),
            "abha_linked_patients_count": abha_linked_count,
            "abha_adoption_rate_pct": _pct(abha_linked_count, len(patients)),
            "age_cohort_pyramid": age_cohorts,
            "gender_distribution": gender_counts,
            "blood_group_distribution": blood_group_counts,
        },
        "allergy_risk_stratification": {
            "total_charted_allergies": len(pt_allergies),
            "by_severity": allergy_by_severity,
            "by_category": allergy_by_category,
        },
        "icd10_coding_analytics": {
            "total_coded_diagnoses": len(enc_diags),
            "by_diagnosis_type": diag_by_type,
            "insurance_billable_diagnoses_count": billable_diag_count,
            "billable_coding_rate_pct": _pct(billable_diag_count, len(enc_diags)),
        },
    }


# =====================================================================
# 4. NEWS2 CLINICAL VITALS EARLY-WARNING & FLUID BALANCE OVERLOAD RADAR
# =====================================================================

@router.get("/clinical-vitals-and-early-warning")
def get_clinical_vitals_and_early_warning(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("adt:read", "opd:read")),
) -> Dict[str, Any]:
    vitals = list(db.execute(select(EncounterVital)).scalars().all())

    early_warning_alerts = {
        "hypoxia_spo2_lt_92_count": 0,
        "hypertensive_crisis_sys_ge_180_count": 0,
        "hypotension_shock_sys_lt_90_count": 0,
        "tachycardia_pulse_gt_120_count": 0,
        "bradycardia_pulse_lt_50_count": 0,
        "high_pyrexia_temp_ge_102f_count": 0,
    }
    bmi_categories = {
        "underweight_lt_18_5": 0,
        "normal_18_5_to_24_9": 0,
        "overweight_25_to_29_9": 0,
        "obese_ge_30": 0,
    }

    for v in vitals:
        if v.spo2_percent is not None and v.spo2_percent < 92:
            early_warning_alerts["hypoxia_spo2_lt_92_count"] += 1
        if v.bp_systolic is not None:
            if v.bp_systolic >= 180:
                early_warning_alerts["hypertensive_crisis_sys_ge_180_count"] += 1
            elif v.bp_systolic < 90:
                early_warning_alerts["hypotension_shock_sys_lt_90_count"] += 1
        if v.pulse_bpm is not None:
            if v.pulse_bpm > 120:
                early_warning_alerts["tachycardia_pulse_gt_120_count"] += 1
            elif v.pulse_bpm < 50:
                early_warning_alerts["bradycardia_pulse_lt_50_count"] += 1
        if v.temperature_f is not None and v.temperature_f >= 102.0:
            early_warning_alerts["high_pyrexia_temp_ge_102f_count"] += 1

        if v.bmi is not None:
            if v.bmi < 18.5:
                bmi_categories["underweight_lt_18_5"] += 1
            elif v.bmi < 25.0:
                bmi_categories["normal_18_5_to_24_9"] += 1
            elif v.bmi < 30.0:
                bmi_categories["overweight_25_to_29_9"] += 1
            else:
                bmi_categories["obese_ge_30"] += 1

    # Fluid I/O Balance Radar across encounters
    fluid_logs = list(db.execute(select(FluidBalanceLog)).scalars().all())
    net_by_enc: Dict[int, int] = {}
    for fl in fluid_logs:
        delta = fl.volume_ml if fl.direction == FluidDirectionEnum.INTAKE else -fl.volume_ml
        net_by_enc[fl.encounter_id] = net_by_enc.get(fl.encounter_id, 0) + delta

    fluid_overload_encounters = [eid for eid, net in net_by_enc.items() if net >= 2000]
    fluid_deficit_encounters = [eid for eid, net in net_by_enc.items() if net <= -1000]

    return {
        "news2_early_warning_triggers": early_warning_alerts,
        "bmi_nutritional_distribution": bmi_categories,
        "inpatient_fluid_balance_radar": {
            "monitored_encounters_count": len(net_by_enc),
            "fluid_overload_ge_2000ml_encounters": fluid_overload_encounters,
            "fluid_deficit_le_minus_1000ml_encounters": fluid_deficit_encounters,
        },
    }


# =====================================================================
# 5. NABH / JCI CLINICAL QUALITY, SAFETY, eMAR, ER/MLC, OT & LIS/PACS
# =====================================================================

@router.get("/clinical-quality-and-safety")
def get_clinical_quality_and_safety(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("opd:read", "adt:read", "diagnostics:read")),
) -> Dict[str, Any]:
    diag_rows = db.execute(
        select(
            DiagnosisMaster.icd10_code,
            DiagnosisMaster.short_description,
            func.count(EncounterDiagnosis.id).label("cases_count"),
        )
        .join(EncounterDiagnosis, EncounterDiagnosis.diagnosis_id == DiagnosisMaster.id)
        .group_by(DiagnosisMaster.id, DiagnosisMaster.icd10_code, DiagnosisMaster.short_description)
        .order_by(func.count(EncounterDiagnosis.id).desc())
        .limit(10)
    ).all()
    top_icd10_diagnoses = [
        {"icd10_code": r.icd10_code, "short_description": r.short_description, "cases_count": r.cases_count}
        for r in diag_rows
    ]

    notes = list(db.execute(select(ClinicalNote)).scalars().all())
    signed_notes_count = sum(1 for n in notes if n.is_signed)
    vitals = list(db.execute(select(EncounterVital)).scalars().all())
    bmi_values = [v.bmi for v in vitals if v.bmi is not None]

    emar_logs = list(db.execute(select(MedicationAdministrationLog)).scalars().all())
    emar_breakdown = {s.value: 0 for s in eMARStatusEnum}
    for log in emar_logs:
        emar_breakdown[log.status.value] += 1

    triages = list(db.execute(select(ERTriageAssessment)).scalars().all())
    triage_by_acuity: Dict[str, int] = {}
    gcs_scores: List[int] = []
    for t in triages:
        triage_by_acuity[t.acuity_level.value] = triage_by_acuity.get(t.acuity_level.value, 0) + 1
        if t.gcs_score is not None:
            gcs_scores.append(t.gcs_score)

    mlcs = list(db.execute(select(MedicoLegalCase)).scalars().all())
    mlc_by_cause: Dict[str, int] = {}
    police_intimated_count = 0
    for m in mlcs:
        mlc_by_cause[m.cause_of_injury.value] = mlc_by_cause.get(m.cause_of_injury.value, 0) + 1
        if m.police_intimated_at is not None:
            police_intimated_count += 1

    surgeries = list(db.execute(select(SurgicalSchedule)).scalars().all())
    surg_by_status = {s.value: 0 for s in SurgeryStatusEnum}
    for s in surgeries:
        surg_by_status[s.status.value] += 1

    checklists = list(db.execute(select(WHOSurgicalSafetyChecklist)).scalars().all())
    fully_verified_checklists = sum(
        1
        for c in checklists
        if c.patient_identity_confirmed
        and c.site_marked
        and c.consent_signed
        and c.pulse_oximeter_functioning
        and c.allergy_checked
        and c.sponge_instrument_count_verified
    )
    op_records = list(db.execute(select(OperativeAnesthesiaRecord)).scalars().all())
    implants = list(db.execute(select(SurgicalImplantLog)).scalars().all())

    tasks = list(db.execute(select(DiagnosticTask)).scalars().all())
    task_by_status = {s.value: 0 for s in TaskStatusEnum}
    for t in tasks:
        task_by_status[t.status.value] += 1

    dicom_studies = list(db.execute(select(RadiologyStudyMetadata)).scalars().all())
    doses = [d.radiation_dose_mgy for d in dicom_studies if d.radiation_dose_mgy is not None]

    return {
        "epidemiology_top_icd10": top_icd10_diagnoses,
        "emr_compliance": {
            "total_soap_notes": len(notes),
            "signed_soap_notes": signed_notes_count,
            "unsigned_draft_notes": len(notes) - signed_notes_count,
            "medico_legal_sign_off_rate_pct": _pct(signed_notes_count, len(notes)),
            "total_vitals_recorded": len(vitals),
            "average_patient_bmi": round(sum(bmi_values) / len(bmi_values), 2) if bmi_values else 0.0,
        },
        "nursing_emar_safety": {
            "total_scheduled_doses_logged": len(emar_logs),
            "status_breakdown": emar_breakdown,
            "administration_compliance_rate_pct": _pct(emar_breakdown["ADMINISTERED"], len(emar_logs)),
        },
        "emergency_and_mlc_forensics": {
            "total_er_triaged": len(triages),
            "acuity_breakdown": triage_by_acuity,
            "average_gcs_score": round(sum(gcs_scores) / len(gcs_scores), 2) if gcs_scores else 15.0,
            "total_mlc_cases": len(mlcs),
            "mlc_by_cause": mlc_by_cause,
            "police_intimated_count": police_intimated_count,
            "police_intimation_rate_pct": _pct(police_intimated_count, len(mlcs)),
        },
        "operation_theatre_and_surgery": {
            "total_surgeries": len(surgeries),
            "status_breakdown": surg_by_status,
            "who_safety_checklists_logged": len(checklists),
            "who_full_sign_out_compliance_pct": _pct(fully_verified_checklists, len(checklists)),
            "average_surgical_blood_loss_ml": (
                round(sum(r.estimated_blood_loss_ml for r in op_records) / len(op_records), 1)
                if op_records
                else 0.0
            ),
            "total_implants_used": sum(i.quantity for i in implants),
            "total_implant_value_paise": sum(i.quantity * i.unit_price_paise for i in implants),
        },
        "diagnostics_and_pacs": {
            "total_diagnostic_tasks": len(tasks),
            "task_status_breakdown": task_by_status,
            "verification_completion_rate_pct": _pct(task_by_status["VERIFIED"], len(tasks)),
            "pacs_dicom_studies_linked": len(dicom_studies),
            "average_radiation_dose_mgy": round(sum(doses) / len(doses), 2) if doses else 0.0,
        },
    }


# =====================================================================
# 6. PHARMACY & THERAPEUTICS (DISPENSING FILL RATE & FEFO VALUATION)
# =====================================================================

@router.get("/pharmacy-and-therapeutics")
def get_pharmacy_and_therapeutics_analytics(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:read", "billing:read")),
) -> Dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    prescriptions = list(db.execute(select(Prescription)).scalars().all())
    rx_by_status = {s.value: 0 for s in PrescriptionStatusEnum}
    for rx in prescriptions:
        rx_by_status[rx.status.value] += 1

    rx_items = list(
        db.execute(select(PrescriptionItem).options(joinedload(PrescriptionItem.medication))).scalars().all()
    )
    total_requested_qty = sum(i.requested_quantity for i in rx_items)
    total_dispensed_qty = sum(i.dispensed_quantity for i in rx_items)
    by_form = {f.value: 0 for f in MedicationFormEnum}
    for i in rx_items:
        if i.medication:
            by_form[i.medication.form.value] += i.requested_quantity

    batches = list(db.execute(select(InventoryBatch)).scalars().all())
    fefo_expiry_value_buckets_paise = {
        "expiring_0_to_30_days_paise": 0,
        "expiring_31_to_60_days_paise": 0,
        "expiring_61_to_90_days_paise": 0,
        "safe_over_90_days_paise": 0,
    }
    for b in batches:
        val = b.stock_quantity * b.unit_price_paise
        days_left = (_to_utc(b.expiry_date) - now_utc).days
        if days_left <= 30:
            fefo_expiry_value_buckets_paise["expiring_0_to_30_days_paise"] += val
        elif days_left <= 60:
            fefo_expiry_value_buckets_paise["expiring_31_to_60_days_paise"] += val
        elif days_left <= 90:
            fefo_expiry_value_buckets_paise["expiring_61_to_90_days_paise"] += val
        else:
            fefo_expiry_value_buckets_paise["safe_over_90_days_paise"] += val

    return {
        "prescription_funnel": {
            "total_prescriptions": len(prescriptions),
            "by_status": rx_by_status,
            "total_units_prescribed": total_requested_qty,
            "total_units_dispensed": total_dispensed_qty,
            "dispensing_fill_rate_pct": _pct(total_dispensed_qty, total_requested_qty),
            "prescribed_units_by_dosage_form": by_form,
        },
        "fefo_inventory_risk_valuation_paise": fefo_expiry_value_buckets_paise,
    }


# =====================================================================
# 7. DOCTOR & DEPARTMENT PRODUCTIVITY + ATTRIBUTED REVENUE & NMC CREDENTIALS
# =====================================================================

@router.get("/doctor-and-department-productivity")
def get_doctor_and_department_productivity(
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("iam:read", "opd:read", "billing:read")),
) -> Dict[str, Any]:
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
        db.execute(select(Appointment.doctor_id, func.count(Appointment.id)).group_by(Appointment.doctor_id)).all()
    )
    encs_by_doc = dict(
        db.execute(
            select(PatientEncounter.primary_doctor_id, func.count(PatientEncounter.id)).group_by(
                PatientEncounter.primary_doctor_id
            )
        ).all()
    )
    rx_by_doc = dict(
        db.execute(
            select(Prescription.prescribing_doctor_id, func.count(Prescription.id)).group_by(
                Prescription.prescribing_doctor_id
            )
        ).all()
    )
    diag_by_doc = dict(
        db.execute(
            select(DiagnosticOrder.ordering_doctor_id, func.count(DiagnosticOrder.id)).group_by(
                DiagnosticOrder.ordering_doctor_id
            )
        ).all()
    )
    surg_by_doc = dict(
        db.execute(
            select(SurgicalSchedule.lead_surgeon_id, func.count(SurgicalSchedule.id)).group_by(
                SurgicalSchedule.lead_surgeon_id
            )
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
                "estimated_consultation_revenue_paise": encs_cnt * doc.consultation_fee_paise,
            }
        )

    departments = list(db.execute(select(Department)).scalars().all())
    encs_by_dept = dict(
        db.execute(
            select(PatientEncounter.department_id, func.count(PatientEncounter.id)).group_by(
                PatientEncounter.department_id
            )
        ).all()
    )
    appts_by_dept = dict(
        db.execute(select(Appointment.department_id, func.count(Appointment.id)).group_by(Appointment.department_id)).all()
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
        "doctors": doctor_matrix,
        "departments": department_matrix,
    }


# =====================================================================
# 8. SUPPLY CHAIN, BLOOD BANK, PRINT ENGINE & GOVERNANCE / FEDERATION RADAR
# =====================================================================

@router.get("/inventory-and-blood-radar")
def get_inventory_and_blood_radar(
    low_stock_threshold: int = Query(default=20, ge=1, le=1000),
    expiry_warning_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: User = Depends(RequirePermission("pharmacy:read", "diagnostics:read", "iam:read")),
) -> Dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    warning_cutoff = now_utc + timedelta(days=expiry_warning_days)

    batches = list(
        db.execute(select(InventoryBatch).options(joinedload(InventoryBatch.medication))).scalars().all()
    )
    total_stock_units = sum(b.stock_quantity for b in batches)
    total_inventory_valuation_paise = sum(b.stock_quantity * b.unit_price_paise for b in batches)

    low_stock_batches = []
    near_expiry_batches = []
    for b in batches:
        med_name = b.medication.brand_name if b.medication else f"Med #{b.medication_id}"
        exp_utc = _to_utc(b.expiry_date)
        if b.stock_quantity <= low_stock_threshold:
            low_stock_batches.append(
                {
                    "batch_id": b.id,
                    "batch_number": b.batch_number,
                    "medication_name": med_name,
                    "stock_quantity": b.stock_quantity,
                }
            )
        if exp_utc <= warning_cutoff and b.stock_quantity > 0:
            near_expiry_batches.append(
                {
                    "batch_id": b.id,
                    "batch_number": b.batch_number,
                    "medication_name": med_name,
                    "expiry_date": exp_utc.isoformat(),
                    "stock_quantity": b.stock_quantity,
                    "is_already_expired": exp_utc <= now_utc,
                }
            )

    blood_bags = list(db.execute(select(BloodBagInventory)).scalars().all())
    available_by_blood_group = {bg.value: 0 for bg in BloodGroupEnum}
    bag_status_counts = {s.value: 0 for s in BloodBagStatusEnum}

    for bag in blood_bags:
        bag_status_counts[bag.status.value] += 1
        if bag.status == BloodBagStatusEnum.AVAILABLE and _to_utc(bag.expiry_date) > now_utc:
            available_by_blood_group[bag.blood_group.value] += 1

    cross_matches = list(db.execute(select(BloodCrossMatchOrder)).scalars().all())
    issued_transfusions = [cm for cm in cross_matches if cm.issued_at is not None]
    adverse_reactions = [
        cm for cm in issued_transfusions if cm.reaction_type != TransfusionReactionTypeEnum.NONE
    ]

    severe_allergy_patients = (
        db.scalar(
            select(func.count(func.distinct(PatientAllergy.patient_id))).where(
                PatientAllergy.severity.in_([SeverityEnum.SEVERE, SeverityEnum.ANAPHYLACTIC]),
                PatientAllergy.is_active == True,
            )
        )
        or 0
    )
    users = list(db.execute(select(User)).scalars().all())
    locked_users_count = sum(1 for u in users if u.status == UserStatusEnum.LOCKED)
    unrotated_password_users_count = sum(1 for u in users if u.password_changed_at is None)

    shifts = list(db.execute(select(NursingShiftAssignment)).scalars().all())
    completed_handoffs = sum(1 for s in shifts if s.handed_off_at is not None)

    fed_clients_count = db.scalar(select(func.count(FederatedClient.id)).where(FederatedClient.is_active == True)) or 0
    federation_transfers_count = db.scalar(select(func.count(FederationTransferLog.id))) or 0

    templates = list(db.execute(select(ReportTemplate).where(ReportTemplate.is_active == True)).scalars().all())
    custom_templates_count = sum(1 for t in templates if not t.is_system_template)

    return {
        "pharmacy_inventory": {
            "total_batches": len(batches),
            "total_stock_units": total_stock_units,
            "total_inventory_valuation_paise": total_inventory_valuation_paise,
            "low_stock_alerts_count": len(low_stock_batches),
            "low_stock_batches": low_stock_batches,
            "near_or_expired_alerts_count": len(near_expiry_batches),
            "near_or_expired_batches": near_expiry_batches,
        },
        "blood_bank_and_hemovigilance": {
            "total_bags_recorded": len(blood_bags),
            "bag_status_breakdown": bag_status_counts,
            "available_units_by_blood_group": available_by_blood_group,
            "critical_o_negative_shortage": available_by_blood_group["O-"] < 2,
            "total_cross_matches": len(cross_matches),
            "total_transfusions_issued": len(issued_transfusions),
            "adverse_transfusion_reactions_count": len(adverse_reactions),
            "transfusion_reaction_rate_pct": _pct(len(adverse_reactions), len(issued_transfusions)),
        },
        "governance_and_security": {
            "patients_with_severe_or_anaphylactic_allergies": severe_allergy_patients,
            "locked_staff_accounts_count": locked_users_count,
            "unrotated_bootstrap_password_accounts_count": unrotated_password_users_count,
            "total_nursing_shifts": len(shifts),
            "nursing_shift_handoff_completion_pct": _pct(completed_handoffs, len(shifts)),
            "active_federation_partner_nodes": fed_clients_count,
            "federation_outbound_transfer_logs_count": federation_transfers_count,
            "total_active_report_templates": len(templates),
            "custom_tenant_report_templates_count": custom_templates_count,
        },
    }