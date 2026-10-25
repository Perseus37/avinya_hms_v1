import pytest
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domains.diagnostics.models import (
    DiagnosticOrder,
    DiagnosticTask,
    LabDeltaCheckConfig,
    OrderStatusEnum,
    ServiceCatalog,
    TaskStatusEnum,
)
from backend.domains.identity.models import User
from backend.domains.integrations.models import (
    EquipmentMaster,
    EquipmentTestMapping,
    ProtocolEnum,
)
from backend.domains.patients.models import Patient


def _create_patient(client, headers, name="HL7 Analyzer Patient"):
    res = client.post(
        "/v1/api/patients",
        headers=headers,
        json={
            "full_name": name,
            "date_of_birth": "1988-06-15T00:00:00Z",
            "gender": "FEMALE",
            "blood_group": "B+",
            "phone": "+919876501234",
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def _create_lab_service(client, headers, code, name, base_price_paise=10000):
    res = client.post(
        "/v1/api/diagnostics/catalog",
        headers=headers,
        json={
            "category_id": 1,
            "code": code,
            "name": name,
            "service_type": "LAB",
            "base_price_paise": base_price_paise,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_hl7_oru_r01_multi_analyte_parsing_and_filing(client, superuser_headers, doctor_headers):
    """
    Scenario 1: Unsolicited ORU^R01 message containing multiple analyte OBX segments
    is ingested via REST gateway, updating the diagnostic task with formatted observations.
    """
    patient = _create_patient(client, superuser_headers, "Hemogram Patient")
    service = _create_lab_service(client, superuser_headers, "CBC_PANEL", "Complete Hemogram Panel")

    # Order the test
    order_res = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    )
    assert order_res.status_code == 201, order_res.text
    order_data = order_res.json()
    order_id = order_data["id"]
    task_id = order_data["tasks"][0]["id"]

    # Construct unsolicited HL7 ORU^R01 message from hematology analyzer
    hl7_msg = (
        "MSH|^~\\&|SYSMEX_XN|HQ-HYD-01|AVINYA_LIS|SERVER|20261025220000||ORU^R01|MSG-HEMO-001|P|2.3\r"
        f"PID|1||{patient['mrn']}||Hemogram^Patient||19880615|F\r"
        f"OBR|1|{task_id}||CBC_PANEL^Complete Hemogram Panel|||20261025215000|||||||||||||||||F\r"
        "OBX|1|NM|WBC^White Blood Cell Count||7.8|10*3/uL|4.0-11.0|N|||F\r"
        "OBX|2|NM|RBC^Red Blood Cell Count||4.5|10*6/uL|4.0-5.5|N|||F\r"
        "OBX|3|NM|HGB^Hemoglobin||13.5|g/dL|12.0-16.0|N|||F\r"
        "OBX|4|NM|PLT^Platelet Count||250|10*3/uL|150-450|N|||F\r"
    )

    ingest_res = client.post(
        "/v1/api/equipment/hl7/oru-r01",
        headers=superuser_headers,
        json={"raw_message": hl7_msg},
    )
    assert ingest_res.status_code == 200, ingest_res.text
    data = ingest_res.json()
    assert data["status"] == "PROCESSED"
    assert data["message_control_id"] == "MSG-HEMO-001"
    assert "MSA|AA|MSG-HEMO-001" in data["ack_hl7"]
    assert data["matched_tasks_count"] == 1
    assert data["panic_alerts_count"] == 0

    # Verify task state in HMS
    task_res = client.get(f"/v1/api/diagnostics/orders/{order_id}/tasks", headers=doctor_headers)
    assert task_res.status_code == 200
    task_data = task_res.json()["items"][0]
    assert task_data["status"] == "RESULT_ENTERED"
    assert "Hemoglobin: 13.5 g/dL" in task_data["result_value"]
    assert "White Blood Cell Count: 7.8 10*3/uL" in task_data["result_value"]


def test_hl7_panic_critical_flag_detection(client, superuser_headers, doctor_headers):
    """
    Scenario 2: Observation with critical/panic abnormal flags (e.g. HH or LL)
    automatically trips is_critical_panic = True and is counted in panic_alerts_count.
    """
    patient = _create_patient(client, superuser_headers, "Panic Flag Patient")
    service = _create_lab_service(client, superuser_headers, "SERUM_K", "Serum Potassium")

    order_res = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    )
    order_data = order_res.json()
    order_id = order_data["id"]
    task_id = order_data["tasks"][0]["id"]

    # Critical high potassium of 7.4 mEq/L with HH flag
    hl7_msg = (
        "MSH|^~\\&|ROCHE_COBAS|HQ-HYD-01|AVINYA_LIS|SERVER|20261025220500||ORU^R01|MSG-PANIC-002|P|2.3\r"
        f"PID|1||{patient['mrn']}||Panic^Patient||19880615|F\r"
        f"OBR|1|TASK-{task_id}||SERUM_K^Serum Potassium|||20261025220000|||||||||||||||||F\r"
        "OBX|1|NM|K^Potassium||7.4|mEq/L|3.5-5.0|HH|||F\r"
    )

    ingest_res = client.post(
        "/v1/api/equipment/hl7/oru-r01",
        headers=superuser_headers,
        json={"raw_message": hl7_msg},
    )
    assert ingest_res.status_code == 200, ingest_res.text
    data = ingest_res.json()
    assert data["status"] == "PROCESSED"
    assert data["panic_alerts_count"] == 1
    assert data["tasks"][0]["is_critical_panic"] is True

    # Verify task directly
    task_res = client.get(f"/v1/api/diagnostics/orders/{order_id}/tasks", headers=doctor_headers)
    assert task_res.json()["items"][0]["is_critical_panic"] is True


def test_hl7_delta_check_evaluation_integration(client, superuser_headers, doctor_headers):
    """
    Scenario 3: Ingesting an HL7 result triggers the automated LIS Delta Check engine,
    detecting acute variance against the patient's historical baseline.
    """
    patient = _create_patient(client, superuser_headers, "Delta Check HL7 Patient")
    service = _create_lab_service(client, superuser_headers, "DELTA_CREAT", "Serum Creatinine")

    # 1. Configure LIS Delta Check threshold (max 30% jump)
    delta_cfg_res = client.post(
        "/v1/api/diagnostics/delta-configs",
        headers=superuser_headers,
        json={
            "service_id": service["id"],
            "parameter_name": "Serum Creatinine",
            "max_percentage_delta": 30.0,
            "lookback_hours": 72,
            "severity": "CRITICAL",
        },
    )
    assert delta_cfg_res.status_code == 201, delta_cfg_res.text

    # 2. Historical result: 1.0 mg/dL (Verified)
    hist_order = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    ).json()
    hist_task_id = hist_order["tasks"][0]["id"]
    client.patch(
        f"/v1/api/diagnostics/tasks/{hist_task_id}/result",
        headers=doctor_headers,
        json={"result_value": "1.0", "status": "RESULT_ENTERED"},
    )
    client.patch(
        f"/v1/api/diagnostics/tasks/{hist_task_id}/result",
        headers=superuser_headers,
        json={"result_value": "1.0", "status": "VERIFIED"},
    )

    # 3. New order for current encounter
    new_order = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    ).json()
    new_order_id = new_order["id"]
    new_task_id = new_order["tasks"][0]["id"]

    # 4. Analyzer delivers 2.2 mg/dL (120% increase)
    hl7_msg = (
        "MSH|^~\\&|COBAS_6000|HQ-HYD-01|AVINYA_LIS|SERVER|20261025221000||ORU^R01|MSG-DELTA-003|P|2.3\r"
        f"PID|1||{patient['mrn']}||Delta^Patient||19880615|F\r"
        f"OBR|1|{new_task_id}||DELTA_CREAT^Creatinine|||20261025220500|||||||||||||||||F\r"
        "OBX|1|NM|CREAT^Creatinine||2.2|mg/dL|0.6-1.2|H|||F\r"
    )

    ingest_res = client.post(
        "/v1/api/equipment/hl7/oru-r01",
        headers=superuser_headers,
        json={"raw_message": hl7_msg},
    )
    assert ingest_res.status_code == 200, ingest_res.text
    data = ingest_res.json()
    assert data["tasks"][0]["delta_check_flag"] == "DELTA_EXCEEDED"

    task_res = client.get(f"/v1/api/diagnostics/orders/{new_order_id}/tasks", headers=doctor_headers)
    task_data = task_res.json()["items"][0]
    assert task_data["delta_check_flag"] == "DELTA_EXCEEDED"
    assert task_data["previous_result_value"] == "1.0"
    assert task_data["delta_percentage"] == pytest.approx(120.0, 0.1)


def test_hl7_equipment_test_mapping_resolution(client, superuser_headers, doctor_headers):
    """
    Scenario 4: Analyzer sends equipment-specific test mnemonic (e.g. 'GLUC-HEX')
    which is dynamically mapped to the HMS ServiceCatalog item via EquipmentTestMapping.
    """
    patient = _create_patient(client, superuser_headers, "Mapping Patient")
    service = _create_lab_service(client, superuser_headers, "FBS_GLU", "Fasting Blood Sugar")

    # 1. Register laboratory equipment
    eq_res = client.post(
        "/v1/api/equipment/",
        headers=superuser_headers,
        json={
            "department_id": 1,
            "name": "Beckman AU5800 Chemistry Analyzer",
            "manufacturer": "Beckman Coulter",
            "device_model": "AU5800",
            "ip_address": "192.168.10.45",
            "protocol": "HL7_V2",
            "is_bidirectional": True,
        },
    )
    assert eq_res.status_code == 201, eq_res.text
    equipment_id = eq_res.json()["id"]

    # 2. Register mapping from machine code 'GLUC-AU' -> service
    map_res = client.post(
        "/v1/api/equipment/mappings",
        headers=superuser_headers,
        json={
            "equipment_id": equipment_id,
            "analyzer_test_code": "GLUC-AU",
            "analyzer_test_name": "Glucose Hexokinase",
            "service_id": service["id"],
            "units": "mg/dL",
        },
    )
    assert map_res.status_code == 201, map_res.text

    # List mappings
    list_map_res = client.get("/v1/api/equipment/mappings", headers=superuser_headers)
    assert list_map_res.status_code == 200
    assert any(m["analyzer_test_code"] == "GLUC-AU" for m in list_map_res.json())

    # 3. Order test
    order_res = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    )
    task_id = order_res.json()["tasks"][0]["id"]

    # 4. Ingest result using machine analyte code without explicit task id in OBR
    hl7_msg = (
        "MSH|^~\\&|AU5800|HQ-HYD-01|AVINYA_LIS|SERVER|20261025221500||ORU^R01|MSG-MAP-004|P|2.3\r"
        f"PID|1||{patient['mrn']}||Mapping^Patient||19880615|F\r"
        "OBR|1|||GLUC-AU^Glucose|||20261025221000|||||||||||||||||F\r"
        "OBX|1|NM|GLUC-AU^Glucose||105|mg/dL|70-99|H|||F\r"
    )

    ingest_res = client.post(
        "/v1/api/equipment/hl7/oru-r01",
        headers=superuser_headers,
        json={"raw_message": hl7_msg, "equipment_id": equipment_id},
    )
    assert ingest_res.status_code == 200, ingest_res.text
    data = ingest_res.json()
    assert data["status"] == "PROCESSED"
    assert data["matched_tasks_count"] == 1
    assert data["tasks"][0]["task_id"] == task_id


def test_hl7_machine_authentication_via_secret_header(client, superuser_headers, doctor_headers):
    """
    Scenario 5: Equipment communicates without user token, authenticating solely
    via X-HL7-Secret header against tenant's encrypted secret.
    """
    # 1. Rotate secret to get fresh token
    rot_res = client.post("/v1/api/equipment/hl7-secret/rotate", headers=superuser_headers)
    assert rot_res.status_code == 200
    secret = rot_res.json()["secret"]

    patient = _create_patient(client, superuser_headers, "Secret Auth Patient")
    service = _create_lab_service(client, superuser_headers, "URIC_ACID", "Serum Uric Acid")

    order_res = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    )
    task_id = order_res.json()["tasks"][0]["id"]

    hl7_msg = (
        "MSH|^~\\&|ANALYZER|HQ-HYD-01|AVINYA_LIS|SERVER|20261025222000||ORU^R01|MSG-AUTH-005|P|2.3\r"
        f"PID|1||{patient['mrn']}||Auth^Patient||19880615|F\r"
        f"OBR|1|{task_id}||URIC_ACID^Uric Acid|||20261025221500|||||||||||||||||F\r"
        "OBX|1|NM|URIC^Uric Acid||6.2|mg/dL|3.5-7.2|N|||F\r"
    )

    # Calling with valid X-HL7-Secret header and NO Authorization header
    machine_headers = {
        "X-HL7-Secret": secret,
        "X-Hospital-Subdomain": "default",
    }
    ingest_res = client.post(
        "/v1/api/equipment/hl7/oru-r01",
        headers=machine_headers,
        json={"raw_message": hl7_msg},
    )
    assert ingest_res.status_code == 200, ingest_res.text
    assert ingest_res.json()["status"] == "PROCESSED"

    # Calling with bad secret returns 401
    bad_headers = {
        "X-HL7-Secret": "invalid-secret-key-12345",
        "X-Hospital-Subdomain": "default",
    }
    bad_res = client.post(
        "/v1/api/equipment/hl7/oru-r01",
        headers=bad_headers,
        json={"raw_message": hl7_msg},
    )
    assert bad_res.status_code == 401


def test_hl7_immutable_verified_task_guard(client, superuser_headers, doctor_headers):
    """
    Scenario 6: Verified diagnostic task is immutable and cannot be overwritten by
    subsequent unsolicited analyzer transmissions.
    """
    patient = _create_patient(client, superuser_headers, "Guard Patient")
    service = _create_lab_service(client, superuser_headers, "LIPID_CHOL", "Total Cholesterol")

    order_res = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    )
    order_data = order_res.json()
    order_id = order_data["id"]
    task_id = order_data["tasks"][0]["id"]

    # Mark as VERIFIED
    client.patch(
        f"/v1/api/diagnostics/tasks/{task_id}/result",
        headers=doctor_headers,
        json={"result_value": "180", "status": "RESULT_ENTERED"},
    )
    client.patch(
        f"/v1/api/diagnostics/tasks/{task_id}/result",
        headers=superuser_headers,
        json={"result_value": "180", "status": "VERIFIED"},
    )

    # Attempt to overwrite via HL7
    hl7_msg = (
        "MSH|^~\\&|ANALYZER|HQ-HYD-01|AVINYA_LIS|SERVER|20261025222500||ORU^R01|MSG-GUARD-006|P|2.3\r"
        f"PID|1||{patient['mrn']}||Guard^Patient||19880615|F\r"
        f"OBR|1|{task_id}||LIPID_CHOL^Cholesterol|||20261025222000|||||||||||||||||F\r"
        "OBX|1|NM|CHOL^Cholesterol||999|mg/dL|120-200|H|||F\r"
    )

    ingest_res = client.post(
        "/v1/api/equipment/hl7/oru-r01",
        headers=superuser_headers,
        json={"raw_message": hl7_msg},
    )
    assert ingest_res.status_code == 200
    data = ingest_res.json()
    assert data["status"] == "REJECTED"
    assert "not eligible for update" in data["error_details"]
    assert "MSA|AE|MSG-GUARD-006" in data["ack_hl7"]

    # Ensure value remains unchanged
    task_res = client.get(f"/v1/api/diagnostics/orders/{order_id}/tasks", headers=doctor_headers)
    task_data = task_res.json()["items"][0]
    assert task_data["result_value"] == "180"
    assert task_data["status"] == "VERIFIED"
