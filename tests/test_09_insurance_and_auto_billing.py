from datetime import datetime, timedelta, timezone
from io import BytesIO

from pypdf import PdfReader


def _setup_patient_and_encounter(client, doctor_headers, encounter_type="IPD"):
    p = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={"full_name": "Insurance Test Patient", "date_of_birth": "1982-08-15T00:00:00Z", "gender": "MALE"},
    ).json()
    enc = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={"patient_id": p["id"], "encounter_type": encounter_type, "primary_doctor_id": 2, "department_id": 1},
    ).json()
    return p, enc


def test_01_create_insurance_provider_and_patient_policy(client, superuser_headers, doctor_headers, billing_headers):
    """Scenario 1: Create TPA provider and attach an active policy with sum_insured_paise and copay_percentage."""
    prov_res = client.post(
        "/v1/api/insurance/providers",
        headers=superuser_headers,
        json={"code": "TPA_MEDIASSIST", "name": "MediAssist India TPA", "provider_type": "TPA", "contact_email": "claims@mediassist.in"},
    )
    assert prov_res.status_code == 201
    prov_id = prov_res.json()["id"]

    p = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "Policy Holder", "date_of_birth": "1990-01-01T00:00:00Z", "gender": "FEMALE"}).json()
    now = datetime.now(timezone.utc)
    pol_res = client.post(
        "/v1/api/insurance/policies",
        headers=billing_headers,
        json={
            "patient_id": p["id"],
            "provider_id": prov_id,
            "policy_number": "STAR-2026-998877",
            "member_id": "MEM-001",
            "sum_insured_paise": 50000000,
            "copay_percentage": 20,
            "valid_from": (now - timedelta(days=30)).isoformat(),
            "valid_till": (now + timedelta(days=335)).isoformat(),
        },
    )
    assert pol_res.status_code == 201
    assert pol_res.json()["copay_percentage"] == 20


def test_02_block_preauth_on_expired_policy_or_exceeding_sum_insured(client, superuser_headers, doctor_headers, billing_headers):
    """Scenario 2: Submitting a pre-auth on an expired policy or exceeding sum_insured_paise returns 400."""
    prov = client.post("/v1/api/insurance/providers", headers=superuser_headers, json={"code": "INS_STAR", "name": "Star Health", "provider_type": "INSURER"}).json()
    p, enc = _setup_patient_and_encounter(client, doctor_headers)
    now = datetime.now(timezone.utc)

    exp_pol = client.post(
        "/v1/api/insurance/policies",
        headers=billing_headers,
        json={
            "patient_id": p["id"],
            "provider_id": prov["id"],
            "policy_number": "POL-EXPIRED-01",
            "member_id": "MEM-EXP",
            "sum_insured_paise": 10000000,
            "copay_percentage": 10,
            "valid_from": (now - timedelta(days=400)).isoformat(),
            "valid_till": (now - timedelta(days=5)).isoformat(),
        },
    ).json()

    res_exp = client.post(
        "/v1/api/insurance/preauths",
        headers=billing_headers,
        json={"encounter_id": enc["id"], "policy_id": exp_pol["id"], "primary_diagnosis_id": 1, "requested_amount_paise": 500000},
    )
    assert res_exp.status_code == 400
    assert "expired" in res_exp.json()["message"].lower()

    act_pol = client.post(
        "/v1/api/insurance/policies",
        headers=billing_headers,
        json={
            "patient_id": p["id"],
            "provider_id": prov["id"],
            "policy_number": "POL-SMALL-CAP",
            "member_id": "MEM-CAP",
            "sum_insured_paise": 1000000,
            "copay_percentage": 0,
            "valid_from": (now - timedelta(days=10)).isoformat(),
            "valid_till": (now + timedelta(days=200)).isoformat(),
        },
    ).json()

    res_cap = client.post(
        "/v1/api/insurance/preauths",
        headers=billing_headers,
        json={"encounter_id": enc["id"], "policy_id": act_pol["id"], "primary_diagnosis_id": 1, "requested_amount_paise": 2500000},
    )
    assert res_cap.status_code == 400
    assert "exceeds policy sum insured" in res_cap.json()["message"].lower()


def test_03_block_preauth_with_non_billable_icd10_diagnosis(client, superuser_headers, doctor_headers, billing_headers):
    """Scenario 3: Creating a pre-auth linked to a DiagnosisMaster where is_billable=False returns 400."""
    non_billable_diag = client.post(
        "/v1/api/clinical/diagnoses",
        headers=superuser_headers,
        json={
            "department_id": 1,
            "icd10_code": "Z00.0",
            "short_description": "General adult medical exam",
            "full_description": "Routine general health checkup (excluded by insurance)",
            "is_billable": False,
        },
    ).json()

    prov = client.post("/v1/api/insurance/providers", headers=superuser_headers, json={"code": "TPA_VIDAL", "name": "Vidal Health", "provider_type": "TPA"}).json()
    p, enc = _setup_patient_and_encounter(client, doctor_headers)
    now = datetime.now(timezone.utc)
    pol = client.post(
        "/v1/api/insurance/policies",
        headers=billing_headers,
        json={
            "patient_id": p["id"],
            "provider_id": prov["id"],
            "policy_number": "POL-VIDAL-01",
            "member_id": "MEM-V1",
            "sum_insured_paise": 20000000,
            "copay_percentage": 0,
            "valid_from": (now - timedelta(days=10)).isoformat(),
            "valid_till": (now + timedelta(days=200)).isoformat(),
        },
    ).json()

    res = client.post(
        "/v1/api/insurance/preauths",
        headers=billing_headers,
        json={"encounter_id": enc["id"], "policy_id": pol["id"], "primary_diagnosis_id": non_billable_diag["id"], "requested_amount_paise": 500000},
    )
    assert res.status_code == 400
    assert "non-billable" in res.json()["message"].lower()


def test_04_cashless_preauth_lifecycle_submit_query_and_approve(client, superuser_headers, doctor_headers, billing_headers):
    """Scenario 4: Walk a pre-auth through SUBMITTED -> QUERY_RAISED -> APPROVED with approved_amount_paise."""
    prov_res = client.post(
        "/v1/api/insurance/providers",
        headers=superuser_headers,
        json={
            "code": "TPA_FHPL",
            "name": "FHPL TPA",
            "provider_type": "TPA",
            "document_requirements": ["Employer referral letter", "Signed treatment estimate"],
        },
    )
    assert prov_res.status_code == 201
    prov = prov_res.json()
    assert prov["document_requirements"] == ["Employer referral letter", "Signed treatment estimate"]
    p, enc = _setup_patient_and_encounter(client, doctor_headers)
    now = datetime.now(timezone.utc)
    pol = client.post(
        "/v1/api/insurance/policies",
        headers=billing_headers,
        json={
            "patient_id": p["id"],
            "provider_id": prov["id"],
            "policy_number": "POL-FHPL-01",
            "member_id": "MEM-F1",
            "sum_insured_paise": 30000000,
            "copay_percentage": 10,
            "valid_from": (now - timedelta(days=10)).isoformat(),
            "valid_till": (now + timedelta(days=200)).isoformat(),
        },
    ).json()

    pa = client.post(
        "/v1/api/insurance/preauths",
        headers=billing_headers,
        json={"encounter_id": enc["id"], "policy_id": pol["id"], "primary_diagnosis_id": 1, "requested_amount_paise": 5000000, "clinical_justification": "Acute pneumonia requiring IV antibiotics"},
    ).json()
    assert pa["status"] == "SUBMITTED"
    preauth_pdf = client.get(
        f"/v1/api/insurance/preauths/{pa['id']}/documents.pdf",
        headers=billing_headers,
    )
    assert preauth_pdf.status_code == 200
    assert preauth_pdf.content.startswith(b"%PDF-")
    preauth_text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(preauth_pdf.content)).pages)
    assert "Employer referral letter" in preauth_text

    q_res = client.patch(
        f"/v1/api/insurance/preauths/{pa['id']}/status",
        headers=billing_headers,
        json={"status": "QUERY_RAISED", "tpa_query_remarks": "Please upload chest X-ray and WBC count report"},
    )
    assert q_res.status_code == 200
    assert q_res.json()["status"] == "QUERY_RAISED"

    app_res = client.patch(
        f"/v1/api/insurance/preauths/{pa['id']}/status",
        headers=billing_headers,
        json={"status": "APPROVED", "approved_amount_paise": 4500000, "tpa_reference_number": "FHPL-APP-9921"},
    )
    assert app_res.status_code == 200
    assert app_res.json()["status"] == "APPROVED"
    assert app_res.json()["approved_amount_paise"] == 4500000


def test_05_create_claim_settle_via_insurance_and_collect_patient_copay(client, superuser_headers, doctor_headers, billing_headers):
    """Scenario 5: Full Cashless Claim Split (80% Insurer / 20% Co-Pay) & Settlement."""
    cat = client.post("/v1/api/diagnostics/categories", headers=superuser_headers, json={"department_id": 1, "code": "CAT_IPD_PKG", "name": "IPD Procedures"}).json()
    svc = client.post("/v1/api/diagnostics/catalog", headers=superuser_headers, json={"category_id": cat["id"], "code": "PROC_ICU_CARE", "name": "Intensive Care Bundle", "service_type": "PROCEDURE", "base_price_paise": 1000000, "is_order_set": False}).json()

    prov = client.post("/v1/api/insurance/providers", headers=superuser_headers, json={"code": "INS_HDFC", "name": "HDFC ERGO", "provider_type": "INSURER"}).json()
    p, enc = _setup_patient_and_encounter(client, doctor_headers)
    now = datetime.now(timezone.utc)
    pol = client.post(
        "/v1/api/insurance/policies",
        headers=billing_headers,
        json={
            "patient_id": p["id"],
            "provider_id": prov["id"],
            "policy_number": "HDFC-80-20",
            "member_id": "MEM-H1",
            "sum_insured_paise": 50000000,
            "copay_percentage": 20,
            "valid_from": (now - timedelta(days=10)).isoformat(),
            "valid_till": (now + timedelta(days=200)).isoformat(),
        },
    ).json()

    pa = client.post(
        "/v1/api/insurance/preauths",
        headers=billing_headers,
        json={"encounter_id": enc["id"], "policy_id": pol["id"], "primary_diagnosis_id": 1, "requested_amount_paise": 1000000},
    ).json()
    client.patch(f"/v1/api/insurance/preauths/{pa['id']}/status", headers=billing_headers, json={"status": "APPROVED", "approved_amount_paise": 1000000, "tpa_reference_number": "HDFC-PA-01"})

    inv = client.post("/v1/api/billing/invoices", headers=billing_headers, json={"patient_id": p["id"], "encounter_id": enc["id"]}).json()
    client.post(f"/v1/api/billing/invoices/{inv['id']}/items", headers=billing_headers, json={"service_id": svc["id"], "description": "ICU Care", "quantity": 1, "unit_price_paise": 1000000})
    client.patch(f"/v1/api/billing/invoices/{inv['id']}/finalize", headers=billing_headers)

    clm_res = client.post(
        "/v1/api/insurance/claims",
        headers=billing_headers,
        json={"invoice_id": inv["id"], "policy_id": pol["id"], "preauth_id": pa["id"]},
    )
    assert clm_res.status_code == 201
    clm = clm_res.json()
    assert clm["insurer_payable_paise"] == 800000
    assert clm["patient_copay_paise"] == 200000
    claim_pdf = client.get(
        f"/v1/api/insurance/claims/{clm['id']}/documents.pdf",
        headers=billing_headers,
    )
    assert claim_pdf.status_code == 200
    assert claim_pdf.content.startswith(b"%PDF-")
    claim_text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(claim_pdf.content)).pages)
    assert "INSURANCE CLAIM SUPPORTING SUMMARY" in claim_text

    settle_res = client.patch(
        f"/v1/api/insurance/claims/{clm['id']}/settle",
        headers=billing_headers,
        json={"settled_amount_paise": 800000, "settlement_utr_reference": "NEFT-HDFC-UTR9988"},
    )
    assert settle_res.status_code == 200
    assert settle_res.json()["status"] == "SETTLED"

    inv_after_ins = client.get(f"/v1/api/billing/invoices/{inv['id']}", headers=billing_headers).json()
    assert inv_after_ins["status"] == "PARTIAL"
    assert inv_after_ins["amount_paid_paise"] == 800000
    assert inv_after_ins["payments"][0]["payment_method"] == "INSURANCE"

    copay_res = client.post(
        f"/v1/api/billing/invoices/{inv['id']}/payments",
        headers=billing_headers,
        json={"amount_paid_paise": 200000, "payment_method": "UPI", "transaction_reference": "UPI-COPAY-01"},
    )
    assert copay_res.status_code == 201
    inv_final = client.get(f"/v1/api/billing/invoices/{inv['id']}", headers=billing_headers).json()
    assert inv_final["status"] == "PAID"
    assert inv_final["amount_paid_paise"] == 1000000


def test_06_opd_in_consultation_auto_bills_doctor_consultation_fee_and_blocks_tampering(client, doctor_headers, billing_headers, superuser_headers):
    """Scenario 6: Transitioning OPD appointment to IN_CONSULTATION auto-bills DoctorProfile.consultation_fee_paise (50000 paise) and blocks manual deletion."""
    p, enc = _setup_patient_and_encounter(client, doctor_headers, encounter_type="OPD")

    appt = client.post(
        "/v1/api/opd/appointments",
        headers=doctor_headers,
        json={"patient_id": p["id"], "doctor_id": 2, "department_id": 1, "appointment_time": "2026-11-01T10:00:00Z"},
    ).json()
    client.patch(f"/v1/api/opd/appointments/{appt['id']}/status", headers=doctor_headers, json={"status": "ARRIVED"})
    client.patch(
        f"/v1/api/opd/appointments/{appt['id']}/status",
        headers=doctor_headers,
        json={"status": "IN_CONSULTATION", "encounter_id": enc["id"]},
    )

    invoices = client.get(f"/v1/api/billing/invoices?patient_id={p['id']}", headers=billing_headers).json()["items"]
    assert len(invoices) == 1
    inv = invoices[0]
    assert inv["total_amount_paise"] == 50000
    assert inv["line_items"][0]["description"].startswith("OPD CONSULTATION:")

    item_id = inv["line_items"][0]["id"]
    del_res = client.delete(f"/v1/api/billing/invoices/{inv['id']}/items/{item_id}", headers=superuser_headers)
    assert del_res.status_code == 403


def test_07_ipd_multi_bed_transfer_and_discharge_auto_bills_bed_daily_rates(client, superuser_headers, doctor_headers, billing_headers):
    """Scenario 7: IPD multi-bed transfer (Standard -> ICU) and discharge auto-bills both bed rates (700,000 paise) to DRAFT invoice."""
    f_id = client.post("/v1/api/ip/floors", headers=superuser_headers, json={"floor_number": 9, "name": "Ninth Floor"}).json()["floor_id"]
    w_id = client.post("/v1/api/ip/wards", headers=superuser_headers, json={"floor_id": f_id, "department_id": 1, "code": "W-AUTOBILL", "name": "AutoBill Ward", "ward_type": "ICU"}).json()["id"]
    r_id = client.post("/v1/api/ip/rooms", headers=superuser_headers, json={"ward_id": w_id, "room_number": "901", "room_type": "DOUBLE", "bed_capacity": 2}).json()["room_id"]

    bed_std_id = client.post("/v1/api/ip/beds", headers=superuser_headers, json={"room_id": r_id, "bed_number": "901-STD", "bed_type": "STANDARD", "base_daily_rate_paise": 200000}).json()["bed_id"]
    bed_icu_id = client.post("/v1/api/ip/beds", headers=superuser_headers, json={"room_id": r_id, "bed_number": "901-ICU", "bed_type": "ICU", "base_daily_rate_paise": 500000}).json()["bed_id"]

    p, enc = _setup_patient_and_encounter(client, doctor_headers, encounter_type="IPD")
    adm = client.post(
        "/v1/api/adt/admissions",
        headers=doctor_headers,
        json={"encounter_id": enc["id"], "admission_type": "EMERGENCY", "admitting_doctor_id": 2, "assigned_bed_id": bed_std_id},
    ).json()

    client.post(
        "/v1/api/adt/transfers",
        headers=doctor_headers,
        json={"admission_id": adm["id"], "new_bed_id": bed_icu_id, "transfer_reason": "Escalated to ICU"},
    )

    disc_res = client.post(
        f"/v1/api/adt/admissions/{adm['id']}/discharge",
        headers=superuser_headers,
        json={"discharge_disposition": "ROUTINE_DISCHARGE", "discharge_summary": "Recovered and discharged"},
    )
    assert disc_res.status_code == 200

    invoices = client.get(f"/v1/api/billing/invoices?patient_id={p['id']}", headers=billing_headers).json()["items"]
    assert len(invoices) == 1
    inv = invoices[0]
    assert inv["total_amount_paise"] == 700000
    descriptions = [li["description"] for li in inv["line_items"]]
    assert any("901-STD" in d for d in descriptions)
    assert any("901-ICU" in d for d in descriptions)


def test_08_diagnostics_package_order_auto_bills_bundle_price_and_reverses_on_cancel(client, superuser_headers, doctor_headers, billing_headers):
    """
    Scenario 8: Ordering a Diagnostic Package (PKG_LFT @ ₹800 = 80,000 paise) explodes into 2 child lab tasks
    but auto-bills the parent package price (80,000 paise) ONCE on the patient's DRAFT invoice.
    Cancelling the diagnostic order automatically reverses the 80,000 paise charge!
    """
    cat = client.post("/v1/api/diagnostics/categories", headers=superuser_headers, json={"department_id": 1, "code": "CAT_DIAG_AB", "name": "Biochemistry"}).json()
    t1 = client.post("/v1/api/diagnostics/catalog", headers=superuser_headers, json={"category_id": cat["id"], "code": "LFT_BILI", "name": "Serum Bilirubin", "service_type": "LAB", "base_price_paise": 50000, "is_order_set": False}).json()
    t2 = client.post("/v1/api/diagnostics/catalog", headers=superuser_headers, json={"category_id": cat["id"], "code": "LFT_SGPT", "name": "ALT / SGPT", "service_type": "LAB", "base_price_paise": 50000, "is_order_set": False}).json()
    pkg = client.post("/v1/api/diagnostics/catalog", headers=superuser_headers, json={"category_id": cat["id"], "code": "PKG_LFT", "name": "Liver Function Panel", "service_type": "PACKAGE", "base_price_paise": 80000, "is_order_set": True}).json()

    client.post("/v1/api/diagnostics/catalog/bundle", headers=superuser_headers, json={"parent_service_id": pkg["id"], "child_service_id": t1["id"], "quantity": 1})
    client.post("/v1/api/diagnostics/catalog/bundle", headers=superuser_headers, json={"parent_service_id": pkg["id"], "child_service_id": t2["id"], "quantity": 1})

    p, _ = _setup_patient_and_encounter(client, doctor_headers, encounter_type="OPD")
    order = client.post("/v1/api/diagnostics/orders", headers=doctor_headers, json={"patient_id": p["id"], "ordered_service_ids": [pkg["id"]]}).json()
    assert len(order["tasks"]) == 2

    # Verify DRAFT invoice was auto-billed 80,000 paise (parent package price, NOT 100,000 sum of children)
    inv = client.get(f"/v1/api/billing/invoices?patient_id={p['id']}", headers=billing_headers).json()["items"][0]
    assert inv["total_amount_paise"] == 80000
    assert "PKG_LFT" in inv["line_items"][0]["description"]

    # Manual deletion of auto-billed diagnostic line item is blocked (403)
    item_id = inv["line_items"][0]["id"]
    assert client.delete(f"/v1/api/billing/invoices/{inv['id']}/items/{item_id}", headers=superuser_headers).status_code == 403

    # Cancelling the diagnostic order automatically reverses the line item from the DRAFT invoice!
    cancel_res = client.delete(f"/v1/api/diagnostics/orders/{order['id']}", headers=doctor_headers)
    assert cancel_res.status_code == 200

    inv_after = client.get(f"/v1/api/billing/invoices/{inv['id']}", headers=billing_headers).json()
    assert inv_after["total_amount_paise"] == 0
    assert len(inv_after["line_items"]) == 0


def test_09_invoice_discount_requires_elevated_permission_and_reduces_net_payable(client, superuser_headers, doctor_headers, billing_headers):
    """
    Scenario 9: Standard cashier (billing_headers) is blocked (403) from applying discounts;
    Authorized manager (superuser_headers) applies a 25% senior-citizen discount (₹250 off ₹1,000),
    reducing net_amount_paise to ₹750 (75,000 paise) and settling the invoice upon paying ₹750.
    """
    cat = client.post("/v1/api/diagnostics/categories", headers=superuser_headers, json={"department_id": 1, "code": "CAT_DISC", "name": "Discount Cat"}).json()
    svc = client.post("/v1/api/diagnostics/catalog", headers=superuser_headers, json={"category_id": cat["id"], "code": "SVC_MRI_DISC", "name": "Brain Scan", "service_type": "RADIOLOGY", "base_price_paise": 100000, "is_order_set": False}).json()

    p, enc = _setup_patient_and_encounter(client, doctor_headers, encounter_type="OPD")
    inv = client.post("/v1/api/billing/invoices", headers=billing_headers, json={"patient_id": p["id"], "encounter_id": enc["id"]}).json()
    client.post(f"/v1/api/billing/invoices/{inv['id']}/items", headers=billing_headers, json={"service_id": svc["id"], "description": "Brain Scan", "quantity": 1, "unit_price_paise": 100000})
    client.patch(f"/v1/api/billing/invoices/{inv['id']}/finalize", headers=billing_headers)

    # 1. Standard billing clerk without billing:reverse/billing:void cannot grant discounts (403)
    unauth_disc = client.patch(
        f"/v1/api/billing/invoices/{inv['id']}/discount",
        headers=billing_headers,
        json={"discount_amount_paise": 25000, "discount_reason": "Unauthorized cashier discount"},
    )
    assert unauth_disc.status_code == 403

    # 2. Authorized manager applies 25,000 paise discount -> net_amount_paise becomes 75,000 paise
    auth_disc = client.patch(
        f"/v1/api/billing/invoices/{inv['id']}/discount",
        headers=superuser_headers,
        json={"discount_amount_paise": 25000, "discount_reason": "Senior Citizen 25% Concession"},
    )
    assert auth_disc.status_code == 200
    assert auth_disc.json()["total_amount_paise"] == 100000
    assert auth_disc.json()["discount_amount_paise"] == 25000
    assert auth_disc.json()["net_amount_paise"] == 75000

    # 3. Attempting to pay the old gross amount (100,000 paise) is blocked as an overpayment (400)!
    overpay = client.post(
        f"/v1/api/billing/invoices/{inv['id']}/payments",
        headers=billing_headers,
        json={"amount_paid_paise": 100000, "payment_method": "CASH"},
    )
    assert overpay.status_code == 400

    # 4. Paying the exact net_amount_paise (75,000 paise) transitions the invoice to PAID!
    pay_res = client.post(
        f"/v1/api/billing/invoices/{inv['id']}/payments",
        headers=billing_headers,
        json={"amount_paid_paise": 75000, "payment_method": "CASH"},
    )
    assert pay_res.status_code == 201
    inv_paid = client.get(f"/v1/api/billing/invoices/{inv['id']}", headers=billing_headers).json()
    assert inv_paid["status"] == "PAID"

def test_10_plan_specific_tariff_auto_overrides_hospital_rack_rate_during_billing(
    client, superuser_headers, doctor_headers, billing_headers
):
    """
    Scenario 10: Hospital Rack Rate for Cardiac CT is ₹8,000 (800,000 paise),
    but Star Gold Plan has a negotiated tariff of ₹5,500 (550,000 paise).
    When a patient on Star Gold Plan orders or is billed for Cardiac CT,
    the invoice automatically bills the ₹5,500 Plan Tariff Rate!
    """
    cat = client.post("/v1/api/diagnostics/categories", headers=superuser_headers, json={"department_id": 1, "code": "CAT_CARD_CT", "name": "Cardiac Imaging"}).json()
    ct_svc = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": cat["id"], "code": "RAD_CARD_CT", "name": "Coronary CT Angiogram", "service_type": "RADIOLOGY", "base_price_paise": 800000, "is_order_set": False},
    ).json()

    prov = client.post("/v1/api/insurance/providers", headers=superuser_headers, json={"code": "INS_STAR_GOLD", "name": "Star Health Allied", "provider_type": "INSURER"}).json()
    plan = client.post(
        "/v1/api/insurance/plans",
        headers=superuser_headers,
        json={"provider_id": prov["id"], "code": "PLAN_STAR_GOLD_01", "name": "Star Gold Cashless Plan", "default_copay_percentage": 10, "apply_tariff_automatically": True},
    ).json()

    # Configure negotiated Plan Tariff: ₹5,500 (550,000 paise) instead of ₹8,000 rack rate
    t_res = client.post(
        f"/v1/api/insurance/plans/{plan['id']}/tariffs",
        headers=superuser_headers,
        json={"service_id": ct_svc["id"], "tariff_price_paise": 550000, "is_covered": True},
    )
    assert t_res.status_code == 201

    p, enc = _setup_patient_and_encounter(client, doctor_headers, encounter_type="OPD")
    now = datetime.now(timezone.utc)
    client.post(
        "/v1/api/insurance/policies",
        headers=billing_headers,
        json={
            "patient_id": p["id"],
            "provider_id": prov["id"],
            "plan_id": plan["id"],
            "policy_number": "STAR-GOLD-5500",
            "member_id": "MEM-SG1",
            "sum_insured_paise": 50000000,
            "valid_from": (now - timedelta(days=10)).isoformat(),
            "valid_till": (now + timedelta(days=200)).isoformat(),
        },
    )

    # Doctor places diagnostic order -> auto-bills negotiated Plan Tariff (550,000 paise) instead of Rack Rate (800,000 paise)!
    client.post("/v1/api/diagnostics/orders", headers=doctor_headers, json={"patient_id": p["id"], "ordered_service_ids": [ct_svc["id"]]})
    inv = client.get(f"/v1/api/billing/invoices?patient_id={p['id']}", headers=billing_headers).json()["items"][0]
    assert inv["total_amount_paise"] == 550000
    assert inv["line_items"][0]["unit_price_paise"] == 550000


def test_11_claim_adjudication_enforces_excluded_services_and_room_rent_capping(
    client, superuser_headers, doctor_headers, billing_headers
):
    """
    Scenario 11: Line-by-Line Claim Adjudication:
    - Plan has 0% co-pay, but caps daily room rent at ₹3,000/day (300,000 paise) and marks Cosmetic Kit (₹1,500 / 150,000 paise) as is_covered=False.
    - Patient stays 1 day in a ₹5,000/day Deluxe Bed (500,000 paise) + receives Cosmetic Kit (150,000 paise) -> Total Invoice = ₹6,500 (650,000 paise).
    - Claim Adjudication:
      * ₹2,000 room rent excess + ₹1,500 excluded service = ₹3,500 (350,000 paise) pushed to patient_copay_paise!
      * Insurer only pays the capped ₹3,000 (300,000 paise)!
    """
    cat = client.post("/v1/api/diagnostics/categories", headers=superuser_headers, json={"department_id": 1, "code": "CAT_EXCL", "name": "Consumables"}).json()
    excl_svc = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": cat["id"], "code": "SVC_COSM_KIT", "name": "Non-Medical Admission Kit", "service_type": "PROCEDURE", "base_price_paise": 150000, "is_order_set": False},
    ).json()

    prov = client.post("/v1/api/insurance/providers", headers=superuser_headers, json={"code": "TPA_PARAMOUNT", "name": "Paramount TPA", "provider_type": "TPA"}).json()
    plan = client.post(
        "/v1/api/insurance/plans",
        headers=superuser_headers,
        json={
            "provider_id": prov["id"],
            "code": "PLAN_PARA_CAP",
            "name": "Paramount Standard Room Cap Plan",
            "default_copay_percentage": 0,
            "max_room_rent_daily_paise": 300000,  # ₹3,000/day room rent cap
            "apply_tariff_automatically": True,
        },
    ).json()

    # Mark Non-Medical Kit as excluded (is_covered=False)
    client.post(
        f"/v1/api/insurance/plans/{plan['id']}/tariffs",
        headers=superuser_headers,
        json={"service_id": excl_svc["id"], "tariff_price_paise": 150000, "is_covered": False},
    )

    # Setup Deluxe Bed @ ₹5,000/day (500,000 paise)
    f_id = client.post("/v1/api/ip/floors", headers=superuser_headers, json={"floor_number": 10, "name": "Tenth Floor"}).json()["floor_id"]
    w_id = client.post("/v1/api/ip/wards", headers=superuser_headers, json={"floor_id": f_id, "department_id": 1, "code": "W-DLX-CAP", "name": "Deluxe Ward", "ward_type": "PRIVATE"}).json()["id"]
    r_id = client.post("/v1/api/ip/rooms", headers=superuser_headers, json={"ward_id": w_id, "room_number": "1001", "room_type": "DELUXE", "bed_capacity": 1}).json()["room_id"]
    bed_id = client.post("/v1/api/ip/beds", headers=superuser_headers, json={"room_id": r_id, "bed_number": "1001-DLX", "bed_type": "MOTORIZED", "base_daily_rate_paise": 500000}).json()["bed_id"]

    p, enc = _setup_patient_and_encounter(client, doctor_headers, encounter_type="IPD")
    now = datetime.now(timezone.utc)
    pol = client.post(
        "/v1/api/insurance/policies",
        headers=billing_headers,
        json={
            "patient_id": p["id"],
            "provider_id": prov["id"],
            "plan_id": plan["id"],
            "policy_number": "PARA-CAP-001",
            "member_id": "MEM-PC1",
            "sum_insured_paise": 50000000,
            "valid_from": (now - timedelta(days=10)).isoformat(),
            "valid_till": (now + timedelta(days=200)).isoformat(),
        },
    ).json()

    adm = client.post("/v1/api/adt/admissions", headers=doctor_headers, json={"encounter_id": enc["id"], "admission_type": "ROUTINE", "admitting_doctor_id": 2, "assigned_bed_id": bed_id}).json()
    client.post(f"/v1/api/adt/admissions/{adm['id']}/discharge", headers=superuser_headers, json={"discharge_disposition": "ROUTINE_DISCHARGE", "discharge_summary": "Discharged"})

    inv = client.get(f"/v1/api/billing/invoices?patient_id={p['id']}", headers=billing_headers).json()["items"][0]
    client.post(f"/v1/api/billing/invoices/{inv['id']}/items", headers=billing_headers, json={"service_id": excl_svc["id"], "description": "Admission Kit", "quantity": 1, "unit_price_paise": 150000})
    client.patch(f"/v1/api/billing/invoices/{inv['id']}/finalize", headers=billing_headers)

    clm_res = client.post("/v1/api/insurance/claims", headers=billing_headers, json={"invoice_id": inv["id"], "policy_id": pol["id"]})
    assert clm_res.status_code == 201
    clm = clm_res.json()
    assert clm["claimed_amount_paise"] == 650000
    assert clm["insurer_payable_paise"] == 300000  # Only ₹3,000 capped room rent is payable by insurer
    assert clm["patient_copay_paise"] == 350000    # ₹2,000 room excess + ₹1,500 excluded kit paid by patient


def test_12_compare_multiple_insurance_plans_out_of_pocket_estimator(client, superuser_headers, billing_headers):
    """Scenario 12: POST /api/insurance/plans/compare-estimate compares Gold vs Silver plans side-by-side."""
    cat = client.post("/v1/api/diagnostics/categories", headers=superuser_headers, json={"department_id": 1, "code": "CAT_CMP", "name": "Surgery Compare"}).json()
    surg = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": cat["id"], "code": "SURG_KNEE", "name": "Total Knee Replacement", "service_type": "PROCEDURE", "base_price_paise": 20000000, "is_order_set": False},
    ).json()

    prov = client.post("/v1/api/insurance/providers", headers=superuser_headers, json={"code": "INS_ICICI", "name": "ICICI Lombard", "provider_type": "INSURER"}).json()
    gold_plan = client.post(
        "/v1/api/insurance/plans",
        headers=superuser_headers,
        json={"provider_id": prov["id"], "code": "ICICI_GOLD", "name": "ICICI Gold (0% Copay, No Room Cap)", "default_copay_percentage": 0, "max_room_rent_daily_paise": None},
    ).json()
    silver_plan = client.post(
        "/v1/api/insurance/plans",
        headers=superuser_headers,
        json={"provider_id": prov["id"], "code": "ICICI_SILVER", "name": "ICICI Silver (20% Copay, ₹2k Room Cap)", "default_copay_percentage": 20, "max_room_rent_daily_paise": 200000},
    ).json()

    # Gold Plan negotiated tariff for Knee Replacement = ₹1,60,000 (16,000,000 paise)
    client.post(f"/v1/api/insurance/plans/{gold_plan['id']}/tariffs", headers=superuser_headers, json={"service_id": surg["id"], "tariff_price_paise": 16000000, "is_covered": True})

    cmp_res = client.post(
        "/v1/api/insurance/plans/compare-estimate",
        headers=billing_headers,
        json={
            "plan_ids": [gold_plan["id"], silver_plan["id"]],
            "service_ids": [surg["id"]],
            "bed_daily_rate_paise": 400000,  # ₹4,000/day room
            "stay_days": 3,
        },
    )
    assert cmp_res.status_code == 200
    items = cmp_res.json()
    assert len(items) == 2

    gold_est = next(i for i in items if i["plan_code"] == "ICICI_GOLD")
    silver_est = next(i for i in items if i["plan_code"] == "ICICI_SILVER")

    # Gold: ₹40,000 MoU savings, ₹0 patient out-of-pocket!
    assert gold_est["mou_tariff_savings_paise"] == 4000000
    assert gold_est["estimated_patient_out_of_pocket_paise"] == 0

    # Silver: ₹6,000 room rent excess (₹2,000/day x 3 days) + 20% co-pay on eligible amount!
    assert silver_est["room_rent_excess_paise"] == 600000
    assert silver_est["estimated_patient_out_of_pocket_paise"] > gold_est["estimated_patient_out_of_pocket_paise"]

def test_13_collect_advance_deposit_apply_to_invoice_and_refund_unused_balance(
    client, superuser_headers, doctor_headers, billing_headers
):
    """
    Scenario 13: Full IPD Advance Deposit & Refund Lifecycle:
    1. Collect ₹50,000 (5,000,000 paise) advance deposit from patient.
    2. Finalize an invoice of ₹32,000 (3,200,000 paise).
    3. Apply advance deposit to invoice -> auto-pays ₹32,000 via ADVANCE_ADJUSTMENT (Invoice -> PAID, Deposit remaining -> ₹18,000).
    4. Standard cashier is blocked (403) from issuing a refund voucher; Manager refunds remaining ₹18,000 -> Deposit status becomes REFUNDED!
    """
    cat = client.post("/v1/api/diagnostics/categories", headers=superuser_headers, json={"department_id": 1, "code": "CAT_DEP", "name": "Deposit Cat"}).json()
    svc = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": cat["id"], "code": "SVC_SURG_32K", "name": "Laparoscopic Appendectomy", "service_type": "PROCEDURE", "base_price_paise": 3200000, "is_order_set": False},
    ).json()

    p, enc = _setup_patient_and_encounter(client, doctor_headers, encounter_type="IPD")

    # 1. Collect ₹50,000 (5,000,000 paise) Advance Deposit
    dep_res = client.post(
        "/v1/api/billing/deposits",
        headers=billing_headers,
        json={"patient_id": p["id"], "encounter_id": enc["id"], "amount_paise": 5000000, "payment_method": "UPI", "transaction_reference": "UPI-ADV-50K"},
    )
    assert dep_res.status_code == 201
    dep = dep_res.json()
    assert dep["remaining_balance_paise"] == 5000000
    assert dep["status"] == "ACTIVE"

    # 2. Create & finalize ₹32,000 (3,200,000 paise) Invoice
    inv = client.post("/v1/api/billing/invoices", headers=billing_headers, json={"patient_id": p["id"], "encounter_id": enc["id"]}).json()
    client.post(f"/v1/api/billing/invoices/{inv['id']}/items", headers=billing_headers, json={"service_id": svc["id"], "description": "Appendectomy", "quantity": 1, "unit_price_paise": 3200000})
    client.patch(f"/v1/api/billing/invoices/{inv['id']}/finalize", headers=billing_headers)

    # 3. Apply Advance Deposit to Invoice -> draws down 3,200,000 paise automatically
    adj_res = client.post(
        f"/v1/api/billing/invoices/{inv['id']}/apply-deposit",
        headers=billing_headers,
        json={"deposit_id": dep["id"]},
    )
    assert adj_res.status_code == 201
    assert adj_res.json()["amount_paid_paise"] == 3200000
    assert adj_res.json()["payment_method"] == "ADVANCE_ADJUSTMENT"

    # Verify Invoice is PAID and Deposit has 1,800,000 paise (₹18,000) remaining
    inv_paid = client.get(f"/v1/api/billing/invoices/{inv['id']}", headers=billing_headers).json()
    assert inv_paid["status"] == "PAID"
    dep_after = client.get(f"/v1/api/billing/patients/{p['id']}/deposits", headers=billing_headers).json()[0]
    assert dep_after["remaining_balance_paise"] == 1800000
    assert dep_after["status"] == "ACTIVE"

    # 4. Standard billing clerk cannot issue cash/bank refunds (403)
    assert client.post(
        f"/v1/api/billing/deposits/{dep['id']}/refund",
        headers=billing_headers,
        json={"reason": "Unauthorized refund"},
    ).status_code == 403

    # 5. Authorized manager issues RefundVoucher for the remaining ₹18,000 (1,800,000 paise)
    rfnd_res = client.post(
        f"/v1/api/billing/deposits/{dep['id']}/refund",
        headers=superuser_headers,
        json={"refund_method": "BANK_TRANSFER", "transaction_reference": "NEFT-RFND-18K", "reason": "Excess IPD advance refunded upon discharge"},
    )
    assert rfnd_res.status_code == 201
    assert rfnd_res.json()["refund_amount_paise"] == 1800000

    dep_final = client.get(f"/v1/api/billing/patients/{p['id']}/deposits", headers=billing_headers).json()[0]
    assert dep_final["remaining_balance_paise"] == 0
    assert dep_final["status"] == "REFUNDED"


def test_14_block_cross_patient_deposit_application_and_exhausted_deposit_reuse(
    client, superuser_headers, doctor_headers, billing_headers
):
    """Scenario 14: Applying Patient A's deposit to Patient B's invoice returns 400; reusing an EXHAUSTED deposit returns 400."""
    cat = client.post("/v1/api/diagnostics/categories", headers=superuser_headers, json={"department_id": 1, "code": "CAT_DEP_G", "name": "Guard Cat"}).json()
    svc = client.post("/v1/api/diagnostics/catalog", headers=superuser_headers, json={"category_id": cat["id"], "code": "SVC_G_1K", "name": "X-Ray", "service_type": "RADIOLOGY", "base_price_paise": 100000, "is_order_set": False}).json()

    p_a, _ = _setup_patient_and_encounter(client, doctor_headers, encounter_type="OPD")
    p_b, _ = _setup_patient_and_encounter(client, doctor_headers, encounter_type="OPD")

    dep_a = client.post("/v1/api/billing/deposits", headers=billing_headers, json={"patient_id": p_a["id"], "amount_paise": 100000, "payment_method": "CASH"}).json()

    inv_b = client.post("/v1/api/billing/invoices", headers=billing_headers, json={"patient_id": p_b["id"]}).json()
    client.post(f"/v1/api/billing/invoices/{inv_b['id']}/items", headers=billing_headers, json={"service_id": svc["id"], "description": "X-Ray", "quantity": 1, "unit_price_paise": 100000})
    client.patch(f"/v1/api/billing/invoices/{inv_b['id']}/finalize", headers=billing_headers)

    # Cross-patient deposit application blocked with 400
    assert client.post(
        f"/v1/api/billing/invoices/{inv_b['id']}/apply-deposit",
        headers=billing_headers,
        json={"deposit_id": dep_a["id"]},
    ).status_code == 400