import json
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional, List
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("avinya.pricing")


def monthly_fee_from_annual_paise(annual_fee_paise: int) -> int:
    """Return the monthly amount rounded to the nearest whole rupee, half up."""
    annual_amount = Decimal(max(0, annual_fee_paise))
    monthly_rupees = (annual_amount / Decimal(12) / Decimal(100)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return int(monthly_rupees * 100)


def normalize_contract_arr_paise(contract_fee_paise: int, billing_cycle: str) -> int:
    """Convert a recurring contract price to annual recurring revenue in paise.

    Perpetual licenses are non-recurring and therefore contribute zero ARR;
    their contract value is reported separately by fleet analytics.
    """
    fee = max(0, int(contract_fee_paise or 0))
    if billing_cycle in {"MONTHLY_SUBSCRIPTION", "PER_ACTIVE_BED_MONTHLY"}:
        return fee * 12
    if billing_cycle == "ONE_TIME_PERPETUAL":
        return 0
    return fee


# -------------------------------------------------------------------------
# DATABASE CATALOG RETRIEVERS
# -------------------------------------------------------------------------

def get_module_catalog(db: Session) -> Dict[str, Any]:
    """Dynamically fetches all active commercial modules from the Control Plane DB."""
    records = db.execute(text(
        "SELECT code, name, base_annual_price_paise FROM module_catalog "
        "WHERE is_active = true ORDER BY code"
    )).mappings().all()
    return {row["code"]: dict(row) for row in records}

def get_bundle_catalog(db: Session) -> Dict[str, Any]:
    """Dynamically fetches all active commercial bundles and their module compositions."""
    records = db.execute(text(
        "SELECT code, name, modules_json, bundle_annual_price_paise "
        "FROM bundle_catalog WHERE is_active = true ORDER BY code"
    )).mappings().all()

    bundles = {}
    for row in records:
        b = dict(row)
        b["modules"] = json.loads(b.pop("modules_json", "[]"))
        bundles[row["code"]] = b

    # Inject system-level fallback bundles
    bundles["CUSTOM_A_LA_CARTE"] = {"name": "Custom À La Carte", "modules": [], "bundle_annual_price_paise": 0}
    bundles["SANDBOX_UAT"] = {"name": "UAT Sandbox Environment", "modules": list(get_module_catalog(db).keys()), "bundle_annual_price_paise": 0}
    return bundles

def get_blueprint_catalog(db: Session) -> Dict[str, Any]:
    """Dynamically fetches hospital spatial topologies from the Control Plane DB."""
    records = db.execute(text(
        "SELECT key, name, description, beds, floors_count, theatres_count "
        "FROM blueprint_catalog WHERE is_active = true ORDER BY key"
    )).mappings().all()
    return {row["key"]: dict(row) for row in records}

# -------------------------------------------------------------------------
# DYNAMIC CALCULATION ENGINE
# -------------------------------------------------------------------------

def compute_contract_fee_paise(
    db: Session,
    pricing_model: str,
    enabled_modules: Dict[str, bool],
    custom_fee_paise: Optional[int],
    billing_cycle: str,
    quota_max_beds: int = 0
) -> int:
    """
    Computes the exact commercial contract fee based on live database bundles, modules, cycles, and overrides.
    Returns the value in Paise (INR * 100).
    """
    # 1. Custom Override Check
    if custom_fee_paise is not None and custom_fee_paise >= 0:
        return custom_fee_paise

    # 2. Fetch Live DB Catalogs
    module_catalog = get_module_catalog(db)
    bundle_catalog = get_bundle_catalog(db)

    # 3. Bundle Resolution & Entitlement Enforcement
    bundle_obj = bundle_catalog.get(pricing_model)
    if not bundle_obj:
        pricing_model = "CUSTOM_A_LA_CARTE"
        bundle_obj = bundle_catalog["CUSTOM_A_LA_CARTE"]

    is_all = enabled_modules.get("all") is True

    if pricing_model != "CUSTOM_A_LA_CARTE":
        base_annual = bundle_obj["bundle_annual_price_paise"]
        active_codes = bundle_obj["modules"]
    else:
        # Resolve A La Carte pricing safely handling the {"all": true} payload
        if is_all:
            active_codes = list(module_catalog.keys())
        else:
            active_codes = [code for code, is_enabled in enabled_modules.items() if is_enabled]

        base_annual = sum(module_catalog.get(code, {}).get("base_annual_price_paise", 0) for code in active_codes)

    # 4. Billing Cycle Conversion
    if billing_cycle == "MONTHLY_SUBSCRIPTION":
        return monthly_fee_from_annual_paise(base_annual)

    elif billing_cycle == "PER_ACTIVE_BED_MONTHLY":
        has_ipd = "ipd_adt" in active_codes
        active_beds = max(0, quota_max_beds) if has_ipd else 0

        if active_beds > 0:
            # Standard metric: ₹1,500 per bed per month (150,000 paise)
            return max(10, active_beds) * 150000
        else:
            # Fallback to standard monthly if IPD is disabled or beds are 0
            return monthly_fee_from_annual_paise(base_annual)

    elif billing_cycle == "ONE_TIME_PERPETUAL":
        # Perpetual license is 3x the annual cost
        return base_annual * 3

    # Default to ANNUAL_LICENSE
    return base_annual
