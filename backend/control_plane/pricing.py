from typing import Any, Dict, List, Optional

MODULE_CATALOG: Dict[str, Dict[str, Any]] = {
    "opd": {
        "code": "opd",
        "name": "OPD Appointments & Queue TV Engine",
        "description": "Doctor schedules, real-time token calling, waiting room WebSocket feeds.",
        "base_annual_price_paise": 2500000,  # ₹25,000/yr
    },
    "ipd_adt": {
        "code": "ipd_adt",
        "name": "IPD, Bed Matrix & Spatial ADT",
        "description": "Floor/ward/bed spatial tracking, patient transfers, clinical discharge workflows.",
        "base_annual_price_paise": 4000000,  # ₹40,000/yr
    },
    "pharmacy": {
        "code": "pharmacy",
        "name": "Pharmacy Formulary & FEFO Inventory",
        "description": "Batch expiry tracking, stock auto-deductions, e-prescriptions & returns.",
        "base_annual_price_paise": 3000000,  # ₹30,000/yr
    },
    "diagnostics": {
        "code": "diagnostics",
        "name": "Laboratory Information System (LIS)",
        "description": "Atomic order sets, specimen rejection, pathologist sign-off, panic alerts.",
        "base_annual_price_paise": 3500000,  # ₹35,000/yr
    },
    "pacs_radiology": {
        "code": "pacs_radiology",
        "name": "Radiology PACS & DICOM Imaging Integration",
        "description": "DICOM StudyInstanceUID attachment, accession numbers, radiation dose audits.",
        "base_annual_price_paise": 4500000,  # ₹45,000/yr
    },
    "billing": {
        "code": "billing",
        "name": "Core Patient Billing & Thermal POS",
        "description": "Auto-charge integration, UPI/Cards, 80mm receipts, deposits & refunds.",
        "base_annual_price_paise": 2500000,  # ₹25,000/yr
    },
    "insurance_tpa": {
        "code": "insurance_tpa",
        "name": "Insurance Pre-Auth & Cashless Claims Adjudication",
        "description": "TPA policies, room rent capping, co-pay calculation, claims settlement.",
        "base_annual_price_paise": 4000000,  # ₹40,000/yr
    },
    "ot_surgery": {
        "code": "ot_surgery",
        "name": "Operation Theatre & Surgical Safety",
        "description": "WHO safety sign-offs, surgeon conflict guards, implant traceability.",
        "base_annual_price_paise": 4500000,  # ₹45,000/yr
    },
    "blood_bank": {
        "code": "blood_bank",
        "name": "Blood Bank & Biological Cross-Matching",
        "description": "ABO/Rh cross-matching guards, hemovigilance quarantine, transfusion auto-billing.",
        "base_annual_price_paise": 3500000,  # ₹35,000/yr
    },
    "er_mlc": {
        "code": "er_mlc",
        "name": "Emergency Triage & Medico-Legal Forensics",
        "description": "5-Level triage prioritization, police intimation certificates, SHO intimation prints.",
        "base_annual_price_paise": 3000000,  # ₹30,000/yr
    },
    "executive_bi": {
        "code": "executive_bi",
        "name": "CFO Revenue Cycle & Clinical Intelligence",
        "description": "AR aging waterfall, bed census radar, doctor productivity metrics.",
        "base_annual_price_paise": 5000000,  # ₹50,000/yr
    },
}

COMMERCIAL_BUNDLES: Dict[str, Dict[str, Any]] = {
    "BUNDLE_OPD_DAYCARE": {
        "name": "Outpatient & Daycare Center Bundle",
        "description": "Ideal for polyclinics and single-specialty day surgery centers.",
        "modules": ["opd", "pharmacy", "billing"],
        "bundle_annual_price_paise": 6500000,  # ₹65,000/yr (Saves ₹15,000)
    },
    "BUNDLE_DIAGNOSTICS_HUB": {
        "name": "Comprehensive Diagnostic & Imaging Hub",
        "description": "Tailored for standalone diagnostic centers and radiology networks.",
        "modules": ["diagnostics", "pacs_radiology", "billing", "pharmacy"],
        "bundle_annual_price_paise": 11000000,  # ₹1,10,000/yr (Saves ₹25,000)
    },
    "BUNDLE_SURGICAL_SUITE": {
        "name": "Surgical Inpatient & Nursing Home Pack",
        "description": "Full inpatient workflow: ADT, Operation Theatres, Pharmacy, Billing & Insurance.",
        "modules": ["ipd_adt", "ot_surgery", "pharmacy", "billing", "insurance_tpa"],
        "bundle_annual_price_paise": 14500000,  # ₹1,45,000/yr (Saves ₹40,000)
    },
    "BUNDLE_TOTAL_HOSPITAL": {
        "name": "Full Enterprise Hospital Flagship",
        "description": "Unrestricted access to all 11 enterprise clinical, surgical, and financial modules.",
        "modules": list(MODULE_CATALOG.keys()),
        "bundle_annual_price_paise": 29500000,  # ₹2,95,000/yr (Saves ₹1,05,000)
    },
}

PER_BED_MONTHLY_RATE_PAISE = 150000  # ₹1,500 / active licensed bed / month


def resolve_licensed_modules(pricing_model: str, selected_modules: Optional[List[str]] = None) -> Dict[str, bool]:
    """Generates the exact entitlement boolean map for storage in TenantRegistry."""
    entitlements = {m: False for m in MODULE_CATALOG.keys()}

    if pricing_model in COMMERCIAL_BUNDLES:
        for m in COMMERCIAL_BUNDLES[pricing_model]["modules"]:
            entitlements[m] = True
    elif selected_modules:
        for m in selected_modules:
            if m in entitlements:
                entitlements[m] = True
    else:
        entitlements = {m: True for m in MODULE_CATALOG.keys()}

    return entitlements


def compute_contract_quote(
    pricing_model: str,
    enabled_modules: Dict[str, bool],
    billing_cycle: str = "ANNUAL_LICENSE",
    quota_max_beds: int = 500,
    custom_fee_paise: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Computes a complete commercial breakdown:
    - a_la_carte_annual_paise: Sum of enabled modules at list price
    - annual_list_paise: Bundle annual price (or à la carte sum)
    - annual_bundle_savings_paise: Discount saved by choosing a bundle
    - billing_period_fee_paise: Exact fee billed per cycle (Monthly, Per-Bed, Annual, or Perpetual)
    - normalized_annual_arr_paise: Annualized Recurring Revenue (ARR) for vendor fleet KPIs
    """
    a_la_carte_annual = sum(
        MODULE_CATALOG[k]["base_annual_price_paise"]
        for k, is_enabled in enabled_modules.items()
        if is_enabled and k in MODULE_CATALOG
    )

    if pricing_model in COMMERCIAL_BUNDLES:
        annual_base = COMMERCIAL_BUNDLES[pricing_model]["bundle_annual_price_paise"]
    else:
        annual_base = a_la_carte_annual

    annual_savings = max(0, a_la_carte_annual - annual_base)
    effective_beds = max(0, quota_max_beds) if enabled_modules.get("ipd_adt", True) else 0

    if custom_fee_paise and custom_fee_paise > 0:
        period_fee = custom_fee_paise
        if billing_cycle in ("MONTHLY_SUBSCRIPTION", "PER_ACTIVE_BED_MONTHLY"):
            normalized_arr = period_fee * 12
        elif billing_cycle == "ONE_TIME_PERPETUAL":
            normalized_arr = int(period_fee * 0.20)  # 20% Annual AMC
        else:
            normalized_arr = period_fee
    else:
        if billing_cycle == "MONTHLY_SUBSCRIPTION":
            # Monthly installment (Annual / 12 rounded to nearest ₹100)
            period_fee = int(round((annual_base / 12) / 10000) * 10000)
            normalized_arr = period_fee * 12
        elif billing_cycle == "PER_ACTIVE_BED_MONTHLY":
            # ₹1,500/bed/month (minimum 10 beds if IPD is enabled, else fallback to monthly module fee)
            if effective_beds > 0:
                period_fee = max(10, effective_beds) * PER_BED_MONTHLY_RATE_PAISE
            else:
                period_fee = int(round((annual_base / 12) / 10000) * 10000)
            normalized_arr = period_fee * 12
        elif billing_cycle == "ONE_TIME_PERPETUAL":
            # 3x Annual License buyout; 20% Annual AMC counted toward ARR
            period_fee = annual_base * 3
            normalized_arr = int(annual_base * 0.20)
        else:
            period_fee = annual_base
            normalized_arr = annual_base

    cycle_labels = {
        "ANNUAL_LICENSE": "per year",
        "MONTHLY_SUBSCRIPTION": "per month",
        "PER_ACTIVE_BED_MONTHLY": f"per month ({effective_beds} beds @ ₹1,500/bed)",
        "ONE_TIME_PERPETUAL": "one-time perpetual buyout (+20% AMC/yr)",
    }

    return {
        "pricing_model": pricing_model,
        "billing_cycle": billing_cycle,
        "billing_period_label": cycle_labels.get(billing_cycle, "per cycle"),
        "billing_period_fee_paise": period_fee,
        "normalized_annual_arr_paise": normalized_arr,
        "a_la_carte_annual_equivalent_paise": a_la_carte_annual,
        "annual_bundle_savings_paise": annual_savings,
        "licensed_beds_quota": effective_beds,
    }


def compute_contract_fee_paise(
    pricing_model: str,
    enabled_modules: Dict[str, bool],
    custom_fee_paise: Optional[int] = None,
    billing_cycle: str = "ANNUAL_LICENSE",
    quota_max_beds: int = 500,
) -> int:
    """Returns the billing period contract fee in paise."""
    quote = compute_contract_quote(
        pricing_model=pricing_model,
        enabled_modules=enabled_modules,
        billing_cycle=billing_cycle,
        quota_max_beds=quota_max_beds,
        custom_fee_paise=custom_fee_paise,
    )
    return quote["billing_period_fee_paise"]