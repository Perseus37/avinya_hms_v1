"""
Tests for Enterprise Track 1:
- CPOE Order Sets (Protocol Bundles)
- Clinical Decision Support: Drug-Disease Contraindications
- Early Warning Scoring: NEWS2 & qSOFA with RRT Escalation
- LIS Delta Checking: Acute Patient Result Shift Detection
"""

import pytest


def _create_patient(client, headers, name="Track One Patient", dob="1985-04-12T00:00:00Z", gender="MALE"):
    res = client.post(
        "/v1/api/patients",
        headers=headers,
        json={
            "full_name": name,
            "date_of_birth": dob,
            "gender": gender,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _create_encounter(client, headers, patient_id):
    res = client.post(
        "/v1/api/adt/encounters",
        headers=headers,
        json={
            "patient_id": patient_id,
            "encounter_type": "IPD",
            "primary_doctor_id": 2,
            "department_id": 1,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _create_medication(client, headers, code, brand, generic):
    res = client.post(
        "/v1/api/pharmacy/medications",
        headers=headers,
        json={
            "code": code,
            "brand_name": brand,
            "generic_name": generic,
            "form": "TABLET",
            "strength": "500mg",
        },
    )
    assert res.status_code in (200, 201), res.text
    return res.json()


def _create_service(client, headers, code, name, service_type="LAB"):
    cat_res = client.post(
        "/v1/api/diagnostics/categories",
        headers=headers,
        json={"department_id": 1, "code": f"CAT_{code[:8]}", "name": f"Category {name[:20]}"},
    )
    if cat_res.status_code == 201:
        category_id = cat_res.json()["id"]
    else:
        # Fallback to category 1
        category_id = 1

    res = client.post(
        "/v1/api/diagnostics/catalog",
        headers=headers,
        json={
            "category_id": category_id,
            "code": code,
            "name": name,
            "service_type": service_type,
            "base_price_paise": 50000,
            "is_order_set": False,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


# -----------------------------------------------------------------------------
# 1. CPOE Order Sets Tests
# -----------------------------------------------------------------------------

def test_order_set_master_creation_and_retrieval(client, superuser_headers, doctor_headers, pharmacist_headers):
    """Verifies that clinical order set protocol templates can be authored and fetched."""
    med = _create_medication(client, pharmacist_headers, "MED_AMOX_01", "Amoxil", "Amoxicillin")
    lab = _create_service(client, superuser_headers, "SRV_CBC_01", "Complete Blood Count")

    payload = {
        "code": "PROTOCOL_SEPSIS_01",
        "title": "Severe Sepsis Resuscitation Bundle",
        "specialty": "Critical Care",
        "clinical_indication": "Suspected or confirmed sepsis with hemodynamic compromise",
        "items": [
            {
                "item_type": "MEDICATION",
                "medication_id": med["id"],
                "dosage_instruction": "1g IV stat",
                "quantity": 1,
                "priority": "STAT",
                "clinical_instructions": "Administer within 1 hour of triage",
            },
            {
                "item_type": "DIAGNOSTIC_LAB",
                "service_id": lab["id"],
                "priority": "STAT",
                "clinical_instructions": "Draw before first antibiotic dose",
            },
        ],
    }

    res = client.post("/v1/api/clinical/order-sets", headers=superuser_headers, json=payload)
    assert res.status_code == 201, res.text
    created = res.json()
    assert created["code"] == "PROTOCOL_SEPSIS_01"
    assert len(created["items"]) == 2

    # Fetch list
    list_res = client.get("/v1/api/clinical/order-sets?specialty=Critical", headers=doctor_headers)
    assert list_res.status_code == 200
    sets = list_res.json()
    assert any(s["code"] == "PROTOCOL_SEPSIS_01" for s in sets)

    # Fetch detail
    get_res = client.get(f"/v1/api/clinical/order-sets/{created['id']}", headers=doctor_headers)
    assert get_res.status_code == 200
    assert get_res.json()["title"] == "Severe Sepsis Resuscitation Bundle"


def test_apply_order_set_to_encounter_creates_prescriptions_and_tasks(
    client, superuser_headers, doctor_headers, pharmacist_headers
):
    """Verifies atomic creation of prescriptions and diagnostic orders when an order set is applied."""
    p = _create_patient(client, doctor_headers, name="Sepsis Patient A")
    enc = _create_encounter(client, doctor_headers, p["id"])

    med = _create_medication(client, pharmacist_headers, "MED_CEFT_01", "Rocephin", "Ceftriaxone")
    lab = _create_service(client, superuser_headers, "SRV_LACT_01", "Serum Lactate")

    os_res = client.post(
        "/v1/api/clinical/order-sets",
        headers=superuser_headers,
        json={
            "code": "PROTOCOL_SEPSIS_HOUR1",
            "title": "Hour-1 Sepsis Bundle",
            "items": [
                {"item_type": "MEDICATION", "medication_id": med["id"], "dosage_instruction": "2g IV stat", "quantity": 1},
                {"item_type": "DIAGNOSTIC_LAB", "service_id": lab["id"], "priority": "STAT"},
            ],
        },
    )
    order_set_id = os_res.json()["id"]

    apply_res = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/apply-order-set",
        headers=doctor_headers,
        json={"order_set_id": order_set_id, "clinical_justification": "Septic shock protocol"},
    )
    assert apply_res.status_code == 201, apply_res.text
    apply_data = apply_res.json()
    assert apply_data["status"] == "success"
    assert apply_data["prescription_id"] is not None
    assert apply_data["diagnostic_order_id"] is not None
    assert apply_data["medication_count"] == 1
    assert apply_data["investigation_count"] == 1

    # Verify diagnostic order has 1 task in PENDING status
    tasks_res = client.get(f"/v1/api/diagnostics/orders/{apply_data['diagnostic_order_id']}/tasks", headers=superuser_headers)
    assert tasks_res.status_code == 200
    assert tasks_res.json()["total"] == 1


# -----------------------------------------------------------------------------
# 2. Drug-Disease Contraindication Tests
# -----------------------------------------------------------------------------

def test_drug_disease_contraindication_hard_stop(client, superuser_headers, doctor_headers, pharmacist_headers):
    """
    Beta-blockers (e.g. Propranolol) in an Asthma patient (ICD-10 J45) triggers a clinical contraindication hard stop.
    """
    p = _create_patient(client, doctor_headers, name="Asthmatic Patient")
    enc = _create_encounter(client, doctor_headers, p["id"])

    # Create ICD-10 diagnosis in master catalog
    dx_master = client.post(
        "/v1/api/clinical/diagnoses",
        headers=superuser_headers,
        json={
            "department_id": 1,
            "icd10_code": "J45.909",
            "short_description": "Asthma",
            "full_description": "Unspecified asthma, uncomplicated",
        },
    ).json()

    # Record asthma diagnosis for encounter
    diag_res = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/diagnoses",
        headers=doctor_headers,
        json={
            "diagnosis_id": dx_master["id"],
            "diagnosis_type": "PRIMARY",
            "clinical_remarks": "Known chronic bronchial asthma",
        },
    )
    assert diag_res.status_code == 201, diag_res.text

    # Medication: Propranolol
    prop = _create_medication(client, pharmacist_headers, "MED_PROP_01", "Inderal", "Propranolol")

    # Create Order Set with Propranolol
    os_res = client.post(
        "/v1/api/clinical/order-sets",
        headers=superuser_headers,
        json={
            "code": "PROTOCOL_MIGRAINE_01",
            "title": "Migraine Prophylaxis Bundle",
            "items": [
                {"item_type": "MEDICATION", "medication_id": prop["id"], "dosage_instruction": "40mg BD", "quantity": 30},
            ],
        },
    )
    order_set_id = os_res.json()["id"]

    # 1. Applying without override triggers 409 Contraindication Hard-Stop
    apply_blocked = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/apply-order-set",
        headers=doctor_headers,
        json={"order_set_id": order_set_id},
    )
    assert apply_blocked.status_code == 409
    err1 = apply_blocked.json().get("message") or apply_blocked.json().get("detail", "")
    assert "CLINICAL CONTRAINDICATION HARD-STOP" in err1
    assert "Bronchial Asthma" in err1

    # 2. Absolute contraindications cannot be overridden even if override is attempted
    apply_override_blocked = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/apply-order-set",
        headers=doctor_headers,
        json={
            "order_set_id": order_set_id,
            "override_contraindication": True,
            "contraindication_override_justification": "Doctor override attempted",
        },
    )
    assert apply_override_blocked.status_code == 409
    err2 = apply_override_blocked.json().get("message") or apply_override_blocked.json().get("detail", "")
    assert "CLINICAL CONTRAINDICATION HARD-STOP" in err2
    assert "Order blocked" in err2

    # 3. Warning-level contraindication (NSAID in Peptic Ulcer) can be overridden with documented clinical justification
    dx_ulcer = client.post(
        "/v1/api/clinical/diagnoses",
        headers=superuser_headers,
        json={
            "department_id": 1,
            "icd10_code": "K25.0",
            "short_description": "Gastric Ulcer",
            "full_description": "Acute gastric ulcer with hemorrhage",
        },
    ).json()

    p_ulcer = _create_patient(client, doctor_headers, name="Ulcer Patient")
    enc_ulcer = _create_encounter(client, doctor_headers, p_ulcer["id"])
    client.post(
        f"/v1/api/clinical/encounters/{enc_ulcer['id']}/diagnoses",
        headers=doctor_headers,
        json={"diagnosis_id": dx_ulcer["id"], "diagnosis_type": "PRIMARY"},
    )

    ibup = _create_medication(client, pharmacist_headers, "MED_IBUP_01", "Brufen", "Ibuprofen")
    os_ulcer = client.post(
        "/v1/api/clinical/order-sets",
        headers=superuser_headers,
        json={
            "code": "PROTOCOL_ANALGESIC_01",
            "title": "Analgesic Bundle",
            "items": [
                {"item_type": "MEDICATION", "medication_id": ibup["id"], "dosage_instruction": "400mg TDS", "quantity": 10},
            ],
        },
    ).json()

    # Blocked without override
    res_blocked_warning = client.post(
        f"/v1/api/clinical/encounters/{enc_ulcer['id']}/apply-order-set",
        headers=doctor_headers,
        json={"order_set_id": os_ulcer["id"]},
    )
    assert res_blocked_warning.status_code == 409
    err_warn = res_blocked_warning.json().get("message") or res_blocked_warning.json().get("detail", "")
    assert "CLINICAL CONTRAINDICATION WARNING" in err_warn

    # Allowed with documented justification
    res_allowed_warning = client.post(
        f"/v1/api/clinical/encounters/{enc_ulcer['id']}/apply-order-set",
        headers=doctor_headers,
        json={
            "order_set_id": os_ulcer["id"],
            "override_contraindication": True,
            "contraindication_override_justification": "Gastroenterology approved with high-dose PPI co-prescription",
        },
    )
    assert res_allowed_warning.status_code == 201, res_allowed_warning.text


# -----------------------------------------------------------------------------
# 3. Early Warning Scoring (NEWS2 & qSOFA) Tests
# -----------------------------------------------------------------------------

def test_news2_scoring_normal_baseline(client, doctor_headers):
    """Normal baseline physiological vitals return NEWS2 = 0, LOW risk, no RRT alert."""
    p = _create_patient(client, doctor_headers, name="Stable Ward Patient")
    enc = _create_encounter(client, doctor_headers, p["id"])

    payload = {
        "respiratory_rate": 16,
        "spo2_percent": 98,
        "bp_systolic": 120,
        "pulse_bpm": 72,
        "consciousness": "ALERT",
        "temperature_f": 98.6,
        "on_supplemental_oxygen": False,
    }

    res = client.post(f"/v1/api/clinical/encounters/{enc['id']}/vitals/news2", headers=doctor_headers, json=payload)
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["news2_score"] == 0
    assert data["risk_level"] == "LOW"
    assert data["qsofa_score"] == 0
    assert data["is_rrt_alert_triggered"] is False
    assert "ward vital sign monitoring" in data["clinical_recommendation"].lower()


def test_news2_scoring_critical_deterioration_triggers_rrt(client, doctor_headers):
    """
    Critically deteriorating vitals (tachypnea, hypoxia on O2, hypotension, tachycardia, altered mental state)
    trigger NEWS2 >= 7 with High Risk and immediate Rapid Response Team (RRT) alert.
    """
    p = _create_patient(client, doctor_headers, name="Deteriorating Patient")
    enc = _create_encounter(client, doctor_headers, p["id"])

    payload = {
        "respiratory_rate": 28,          # score 3 (>=25)
        "spo2_percent": 88,              # score 3 (<=91)
        "on_supplemental_oxygen": True,  # score 2
        "bp_systolic": 85,               # score 3 (<=90)
        "pulse_bpm": 135,                # score 3 (>=131)
        "consciousness": "CVPU",         # score 3 (altered)
        "temperature_f": 103.5,          # score 2 (>=39.1C / 102.4F)
    }

    res = client.post(f"/v1/api/clinical/encounters/{enc['id']}/vitals/news2", headers=doctor_headers, json=payload)
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["news2_score"] >= 15
    assert data["risk_level"] == "HIGH"
    assert data["qsofa_score"] == 3  # RR >= 22 (1), SBP <= 100 (1), Altered mental state (1)
    assert data["is_rrt_alert_triggered"] is True
    assert "rapid response team" in data["clinical_recommendation"].lower()

    # Verify query historical scores
    history_res = client.get(f"/v1/api/clinical/encounters/{enc['id']}/early-warning-scores", headers=doctor_headers)
    assert history_res.status_code == 200
    scores = history_res.json()
    assert len(scores) >= 1
    assert scores[0]["is_rrt_alert_triggered"] is True


def test_news2_scale_2_and_physiological_bounds_validation(client, doctor_headers):
    """N-47: Physiological bounds checking, empty vital rejection, and SpO2 Scale 2."""
    from backend.domains.clinical.services import calculate_news2_score

    # 1. All empty vitals must raise ValueError
    with pytest.raises(ValueError, match="At least one measured physiological vital parameter"):
        calculate_news2_score()

    # 2. Implausible physiological bounds must raise ValueError
    with pytest.raises(ValueError, match="Respiratory rate.*out of plausible"):
        calculate_news2_score(respiratory_rate=120)

    with pytest.raises(ValueError, match="SpO2.*out of plausible"):
        calculate_news2_score(spo2_percent=105)

    with pytest.raises(ValueError, match="Systolic blood pressure.*out of plausible"):
        calculate_news2_score(bp_systolic=15)

    with pytest.raises(ValueError, match="Pulse.*out of plausible"):
        calculate_news2_score(pulse_bpm=5)

    with pytest.raises(ValueError, match="Temperature.*out of plausible"):
        calculate_news2_score(temperature_f=130.0)

    # 3. SpO2 Scale 1 vs Scale 2 for COPD / Hypercapnic Respiratory Failure
    # At 90% SpO2 on room air:
    # Scale 1 considers this hypoxic (pts = 3)
    res_scale1 = calculate_news2_score(spo2_percent=90, spo2_scale=1, respiratory_rate=16)
    assert res_scale1["sub_scores"]["spo2"] == 3

    # Scale 2 considers 88-92% the target normal range (pts = 0)
    res_scale2 = calculate_news2_score(spo2_percent=90, spo2_scale=2, respiratory_rate=16)
    assert res_scale2["sub_scores"]["spo2"] == 0

    # 4. API endpoint rejects out-of-bounds vitals with 422
    p = _create_patient(client, doctor_headers, name="Bounds Test Patient")
    enc = _create_encounter(client, doctor_headers, p["id"])

    res_invalid = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/vitals/news2",
        headers=doctor_headers,
        json={"respiratory_rate": 200, "spo2_percent": 98},
    )
    assert res_invalid.status_code == 422
    assert "out of plausible physiological range" in res_invalid.json()["message"]

    # 5. API endpoint with Scale 2 succeeds
    res_copd = client.post(
        f"/v1/api/clinical/encounters/{enc['id']}/vitals/news2",
        headers=doctor_headers,
        json={"respiratory_rate": 16, "spo2_percent": 90, "spo2_scale": 2, "bp_systolic": 120, "pulse_bpm": 75},
    )
    assert res_copd.status_code == 201
    assert res_copd.json()["news2_score"] == 0



# -----------------------------------------------------------------------------
# 4. LIS Delta Checking Tests
# -----------------------------------------------------------------------------

def test_lis_delta_checking_detects_acute_shift(client, superuser_headers, doctor_headers):
    """
    When repeat laboratory tests show an acute shift exceeding configured thresholds,
    the task is automatically flagged with DELTA_EXCEEDED and alerts pathologist for review.
    """
    # 1. Create Potassium service
    potassium = _create_service(client, superuser_headers, "SRV_K_01", "Serum Potassium")

    # 2. Configure Delta check threshold (max absolute delta = 1.5 mmol/L, max percentage delta = 30%)
    cfg_res = client.post(
        "/v1/api/diagnostics/delta-configs",
        headers=superuser_headers,
        json={
            "service_id": potassium["id"],
            "parameter_name": "Potassium",
            "max_absolute_delta": 1.5,
            "max_percentage_delta": 30.0,
            "lookback_hours": 24,
            "severity": "CRITICAL",
        },
    )
    assert cfg_res.status_code == 201, cfg_res.text

    p = _create_patient(client, doctor_headers, name="Dialysis Patient")

    # 3. Create First Order & Task (Baseline: 4.0 mmol/L)
    res_order1 = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={
            "patient_id": p["id"],
            "clinical_notes": "Morning metabolic panel",
            "ordered_service_ids": [potassium["id"]],
        },
    )
    assert res_order1.status_code == 201, res_order1.text
    order1 = res_order1.json()
    task1_id = order1["tasks"][0]["id"]

    res1 = client.patch(
        f"/v1/api/diagnostics/tasks/{task1_id}/result",
        headers=superuser_headers,
        json={"status": "RESULT_ENTERED", "result_value": "4.0 mmol/L"},
    )
    assert res1.status_code == 200, res1.text
    task1_data = res1.json()
    assert task1_data["delta_check_flag"] in ("NO_PRIOR_VALUE", "NORMAL")

    # 4. Create Second Order & Task (Acute shift: 6.8 mmol/L -> Delta = +2.8, 70% shift)
    res_order2 = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={
            "patient_id": p["id"],
            "clinical_notes": "Post-procedure repeat panel",
            "ordered_service_ids": [potassium["id"]],
        },
    )
    assert res_order2.status_code == 201, res_order2.text
    order2 = res_order2.json()
    task2_id = order2["tasks"][0]["id"]

    res2 = client.patch(
        f"/v1/api/diagnostics/tasks/{task2_id}/result",
        headers=superuser_headers,
        json={"status": "RESULT_ENTERED", "result_value": "6.8 mmol/L"},
    )
    assert res2.status_code == 200, res2.text
    task2_data = res2.json()
    assert task2_data["delta_check_flag"] == "DELTA_EXCEEDED"
    assert task2_data["previous_result_value"] == "4.0"
    assert task2_data["delta_percentage"] == pytest.approx(70.0, 0.1)
    assert "LIS DELTA CHECK ALERT" in task2_data["result_notes"]
