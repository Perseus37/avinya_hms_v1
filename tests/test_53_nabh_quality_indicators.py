import pytest
from datetime import datetime, timezone


def _create_patient(client, headers, name="Quality Audit Patient"):
    res = client.post(
        "/v1/api/patients",
        headers=headers,
        json={
            "full_name": name,
            "date_of_birth": "1985-05-20T00:00:00Z",
            "gender": "MALE",
            "blood_group": "O+",
            "phone": "+919876543210",
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_quality_incident_reporting_and_capa_lifecycle(client, superuser_headers, doctor_headers):
    """
    Scenario 1: Clinical team reports an HAI (CAUTI) incident, conducts Root Cause Analysis (RCA),
    assigns a CAPA plan, and tracks through to resolution with timestamped closure.
    """
    patient = _create_patient(client, superuser_headers, "CAUTI Patient")

    # 1. Report CAUTI incident
    rep_res = client.post(
        "/v1/api/quality/incidents",
        headers=doctor_headers,
        json={
            "incident_type": "CAUTI",
            "severity": "MODERATE",
            "patient_id": patient["id"],
            "device_days": 12,
            "description": "Patient developed fever and symptomatic bacteriuria after 12 days on indwelling urinary catheter.",
        },
    )
    assert rep_res.status_code == 201, rep_res.text
    incident = rep_res.json()
    assert incident["incident_code"].startswith("QI-")
    assert incident["status"] == "REPORTED"
    assert incident["device_days"] == 12
    incident_id = incident["id"]

    # 2. Update with Root Cause Analysis (RCA) and assign CAPA
    patch_res1 = client.patch(
        f"/v1/api/quality/incidents/{incident_id}",
        headers=doctor_headers,
        json={
            "status": "CAPA_ASSIGNED",
            "root_cause_analysis": "Catheter duration exceeded 7-day protocol without documented re-evaluation checklist.",
            "capa_plan": "Mandatory electronic daily reminder on ward census and nursing bundle audits.",
        },
    )
    assert patch_res1.status_code == 200, patch_res1.text
    updated1 = patch_res1.json()
    assert updated1["status"] == "CAPA_ASSIGNED"
    assert "daily reminder" in updated1["capa_plan"]

    # 3. Resolve incident
    patch_res2 = client.patch(
        f"/v1/api/quality/incidents/{incident_id}",
        headers=doctor_headers,
        json={"status": "RESOLVED"},
    )
    assert patch_res2.status_code == 200, patch_res2.text
    updated2 = patch_res2.json()
    assert updated2["status"] == "RESOLVED"
    assert updated2["resolved_at"] is not None

    # 4. Fetch incident details
    get_res = client.get(f"/v1/api/quality/incidents/{incident_id}", headers=doctor_headers)
    assert get_res.status_code == 200
    assert get_res.json()["incident_code"] == incident["incident_code"]


def test_medication_error_and_near_miss_reporting(client, doctor_headers):
    """
    Scenario 2: Nurse reports a near-miss medication administration event
    detected prior to infusion.
    """
    res = client.post(
        "/v1/api/quality/incidents",
        headers=doctor_headers,
        json={
            "incident_type": "MEDICATION_ERROR",
            "severity": "NEAR_MISS",
            "description": "Potassium chloride vial placed in regular electrolyte drawer; intercepted before reconstitution.",
            "root_cause_analysis": "Pharmacy night stock replenishment misplacement.",
            "capa_plan": "Dedicated high-alert medication locked bin in ward pharmacy cabinet.",
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["incident_type"] == "MEDICATION_ERROR"
    assert data["severity"] == "NEAR_MISS"
    assert data["status"] == "REPORTED"


def test_inpatient_fall_and_critical_care_incidents(client, doctor_headers):
    """
    Scenario 3: Inpatient fall and unplanned return to ICU events are logged
    for hospital accreditation safety tracking.
    """
    # 1. Inpatient Fall
    fall_res = client.post(
        "/v1/api/quality/incidents",
        headers=doctor_headers,
        json={
            "incident_type": "PATIENT_FALL",
            "severity": "MINOR",
            "description": "Patient slipped on wet bathroom floor during unassisted ambulation; no fracture sustained.",
        },
    )
    assert fall_res.status_code == 201, fall_res.text

    # 2. Unplanned ICU return
    icu_res = client.post(
        "/v1/api/quality/incidents",
        headers=doctor_headers,
        json={
            "incident_type": "UNPLANNED_ICU_READMISSION",
            "severity": "MAJOR",
            "description": "Post-CABG patient transferred to step-down ward developed acute respiratory distress and was re-intubated.",
        },
    )
    assert icu_res.status_code == 201, icu_res.text

    # 3. Unplanned OT return
    ot_res = client.post(
        "/v1/api/quality/incidents",
        headers=doctor_headers,
        json={
            "incident_type": "UNPLANNED_OT_RETURN",
            "severity": "MAJOR",
            "description": "Patient returned to operating theatre within 24h for surgical drainage of expanding hematoma.",
        },
    )
    assert ot_res.status_code == 201, ot_res.text

    # List incidents with filter by severity=MAJOR
    major_list_res = client.get(
        "/v1/api/quality/incidents?severity=MAJOR",
        headers=doctor_headers,
    )
    assert major_list_res.status_code == 200
    majors = major_list_res.json()
    assert len(majors) >= 2
    assert all(m["severity"] == "MAJOR" for m in majors)


def test_nabh_jci_quality_indicators_summary_dashboard(client, doctor_headers):
    """
    Scenario 4: Aggregates hospital-wide clinical quality accreditation indicators
    (CAUTI, CLABSI, VAP, SSI, Falls, Med Errors, Unplanned ICU/OT returns)
    and verifies benchmarking against NABH 5th Ed / JCI 7th Ed target thresholds.
    """
    res = client.get("/v1/api/quality/indicators/summary", headers=doctor_headers)
    assert res.status_code == 200, res.text
    dashboard = res.json()

    assert "hospital_inpatient_bed_days" in dashboard
    assert "total_surgeries_performed" in dashboard
    assert "total_medication_doses_logged" in dashboard
    assert "total_incidents_recorded" in dashboard

    indicators = dashboard["indicators"]
    assert len(indicators) >= 8

    indicator_map = {ind["code"]: ind for ind in indicators}
    expected_codes = [
        "CAUTI_RATE",
        "CLABSI_RATE",
        "VAP_RATE",
        "SSI_RATE",
        "FALL_RATE",
        "MED_ERROR_RATE",
        "UNPLANNED_ICU_RETURN_RATE",
        "UNPLANNED_OT_RETURN_RATE",
    ]
    for code in expected_codes:
        assert code in indicator_map, f"Missing indicator {code}"
        ind = indicator_map[code]
        assert "rate" in ind
        assert "target_threshold" in ind
        assert "warning_threshold" in ind
        assert ind["compliance_status"] in ("COMPLIANT", "WARNING", "NON_COMPLIANT")
        assert ind["unit"] in ("PER_1000_DEVICE_DAYS", "PERCENT", "PER_1000_BED_DAYS", "PER_1000_DOSES")


def test_quality_benchmarks_management(client, superuser_headers, doctor_headers):
    """
    Scenario 5: Lists standard accreditation benchmarks and registers a custom
    hospital quality indicator benchmark.
    """
    # 1. List benchmarks
    list_res = client.get("/v1/api/quality/benchmarks", headers=doctor_headers)
    assert list_res.status_code == 200, list_res.text
    benchmarks = list_res.json()
    assert len(benchmarks) >= 8
    assert any(b["indicator_code"] == "CAUTI_RATE" for b in benchmarks)

    # 2. Create custom benchmark
    create_res = client.post(
        "/v1/api/quality/benchmarks",
        headers=superuser_headers,
        json={
            "indicator_code": "HAPU_BEDSORE_RATE",
            "name": "Hospital-Acquired Pressure Ulcers Rate",
            "standard": "NABH_5TH_ED",
            "unit": "PERCENT",
            "target_threshold": 1.0,
            "warning_threshold": 2.0,
            "description": "Percentage of inpatients who develop stage II or higher pressure injury.",
        },
    )
    assert create_res.status_code == 201, create_res.text
    new_b = create_res.json()
    assert new_b["indicator_code"] == "HAPU_BEDSORE_RATE"
    assert new_b["target_threshold"] == 1.0

    # 3. Duplicate creation rejected with 409
    dup_res = client.post(
        "/v1/api/quality/benchmarks",
        headers=superuser_headers,
        json={
            "indicator_code": "HAPU_BEDSORE_RATE",
            "name": "Hospital-Acquired Pressure Ulcers Rate Duplicate",
            "unit": "PERCENT",
            "target_threshold": 1.0,
            "warning_threshold": 2.0,
        },
    )
    assert dup_res.status_code == 409
