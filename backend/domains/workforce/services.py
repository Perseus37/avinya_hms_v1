"""Statutory Indian Payroll Deduction & Compliance Engine for Avinya HMS (M-05).

Calculates Indian statutory deductions (Employees' Provident Fund [EPF],
Employees' State Insurance [ESI], Professional Tax [PT], and Tax Deducted at Source [TDS])
and employer matching contributions.
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional


# Indian Statutory Constants
STATUTORY_PF_WAGE_CEILING_PAISE = 1_500_000  # ₹15,000 per month
STATUTORY_ESI_GROSS_THRESHOLD_PAISE = 2_100_000  # ₹21,000 per month
STANDARD_PROFESSIONAL_TAX_PAISE = 20_000  # ₹200 per month
PROFESSIONAL_TAX_MIN_GROSS_PAISE = 1_500_000  # ₹15,000 per month

EPF_EMPLOYEE_RATE = Decimal("0.12")
EPF_EMPLOYER_RATE = Decimal("0.12")
EPF_EMPLOYER_EPS_RATE = Decimal("0.0833")  # 8.33% to EPS
EPF_EMPLOYER_EPF_RATE = Decimal("0.0367")  # 3.67% to EPF

ESI_EMPLOYEE_RATE = Decimal("0.0075")  # 0.75%
ESI_EMPLOYER_RATE = Decimal("0.0325")  # 3.25%


def calculate_indian_statutory_deductions(
    *,
    gross_paise: int,
    prorated_fixed_paise: int,
    pf_opt_out: bool = False,
    uncapped_pf: bool = False,
    custom_tds_paise: int = 0,
    state: str = "KA",
) -> dict[str, Any]:
    """
    Computes statutory deductions and employer contributions under Indian labour law.

    Returns:
        dict containing employee deductions (PF, ESI, PT, TDS), employer contributions,
        and statutory wage base metadata.
    """
    # 1. Employees' Provident Fund (EPF)
    if pf_opt_out:
        pf_wage_base = 0
        pf_employee = 0
        pf_employer = 0
        pf_eps = 0
        pf_epf_employer = 0
    else:
        if uncapped_pf:
            pf_wage_base = prorated_fixed_paise
        else:
            pf_wage_base = min(prorated_fixed_paise, STATUTORY_PF_WAGE_CEILING_PAISE)

        pf_employee = int(
            (Decimal(pf_wage_base) * EPF_EMPLOYEE_RATE).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        pf_employer = int(
            (Decimal(pf_wage_base) * EPF_EMPLOYER_RATE).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        pf_eps = int(
            (Decimal(min(pf_wage_base, STATUTORY_PF_WAGE_CEILING_PAISE)) * EPF_EMPLOYER_EPS_RATE).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        pf_epf_employer = max(0, pf_employer - pf_eps)

    # 2. Employees' State Insurance (ESI)
    if gross_paise <= STATUTORY_ESI_GROSS_THRESHOLD_PAISE:
        esi_wage_base = gross_paise
        esi_employee = int(
            (Decimal(esi_wage_base) * ESI_EMPLOYEE_RATE).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        esi_employer = int(
            (Decimal(esi_wage_base) * ESI_EMPLOYER_RATE).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    else:
        esi_wage_base = 0
        esi_employee = 0
        esi_employer = 0

    # 3. Professional Tax (PT)
    if gross_paise >= PROFESSIONAL_TAX_MIN_GROSS_PAISE:
        pt_paise = STANDARD_PROFESSIONAL_TAX_PAISE
    else:
        pt_paise = 0

    # 4. Tax Deducted at Source (TDS)
    tds_paise = max(0, int(custom_tds_paise))

    total_employee_statutory = pf_employee + esi_employee + pt_paise + tds_paise
    total_employer_statutory = pf_employer + esi_employer

    return {
        "pf_employee_paise": pf_employee,
        "pf_employer_paise": pf_employer,
        "pf_eps_paise": pf_eps,
        "pf_epf_employer_paise": pf_epf_employer,
        "pf_wage_base_paise": pf_wage_base,
        "esi_employee_paise": esi_employee,
        "esi_employer_paise": esi_employer,
        "esi_wage_base_paise": esi_wage_base,
        "professional_tax_paise": pt_paise,
        "tds_paise": tds_paise,
        "total_employee_statutory_paise": total_employee_statutory,
        "total_employer_statutory_paise": total_employer_statutory,
        "state": state.upper(),
        "regime": "IN_STATUTORY_2026",
    }

