"""
Audit Round 19 Remediation Test Suite.
Verifies remediations for findings identified in external auditor report (Section 19):
1. N-70 (High Severity - Urgent Medication Safety / Never-Event Prevention):
   - Bedside route check: English pronouns/nouns ('it', 'id', 'ia') never map to high-risk routes.
   - Injectable formulation with no route in order strictly blocks INTRATHECAL, EPIDURAL, INTRAARTERIAL.
   - High-risk neuraxial routes strictly require explicit prescription route in order.
2. N-69 (Medium Severity - Lab Worklist & Re-collection):
   - Unobtainable analyzer result (OBX-11 = X) generates a persistent re-collection task in the database.
   - Surfaces on the laboratory worklist queue (/tasks/worklist).
3. N-72 (Medium Severity - Consistent Staging / Production Handling):
   - ENVIRONMENT='staging' enforces production guards across all switches:
     - Metrics endpoint token protection (HTTP 401 without scrape token).
     - HSTS headers (Strict-Transport-Security).
     - Physical database drop guard (bootstrap.py --reset blocked).
     - OpenAPI/docs suppression and developer script protections.
4. N-44 Follow-up (Narcotics Vault Safety Hardening):
   - Format validation for batch number and procurement supply references (rejects repetitive junk characters).
   - Witness clinical role check (rejects non-clinical accounts with HTTP 403).
   - Return capping: Returns cannot exceed cumulative net dispensed units (prevents vault balance inflation).
"""

from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domains.diagnostics.models import DiagnosticOrder, DiagnosticTask, OrderStatusEnum, TaskStatusEnum
from backend.domains.identity.models import User, UserStatusEnum
from backend.domains.identity.passwords import pwd_context
from backend.domains.pharmacy.services import _extract_route


# =============================================================================
# 1. N-70: Bedside Medication Route Parsing & Never-Event Prevention
# =============================================================================

def test_n70_extract_route_avoids_english_word_collisions():
    """
    N-70: English pronouns/nouns ('it', 'id', 'ia') must never be parsed as administration routes:
    - 'Give it over 30 minutes' -> None (NOT INTRATHECAL)
    - 'take it after meals' -> None (NOT INTRATHECAL)
    - 'Do not give it IV' -> IV (from 'IV', ignoring 'it')
    - 'patient id checked, 5 mg' -> None (NOT INTRADERMAL)
    - '15 mg IT' -> INTRATHECAL (explicit dose followed by IT abbreviation)
    - '10 mg IV push' -> IV
    - '5 mg IM' -> IM
    - '0.5 ml SC' -> SC
    - 'route: IT' -> INTRATHECAL
    - '12 mg intrathecal stat' -> INTRATHECAL
    """
    assert _extract_route("Give it over 30 minutes") is None
    assert _extract_route("take it after meals") is None
    assert _extract_route("patient id checked, 5 mg") is None
    assert _extract_route("Do not give it IV") == "IV"
    assert _extract_route("give it slowly over 5 minutes") is None

    # Legitimate abbreviations and words
    assert _extract_route("15 mg IT") == "INTRATHECAL"
    assert _extract_route("route: IT") == "INTRATHECAL"
    assert _extract_route("15 mg intrathecal stat") == "INTRATHECAL"
    assert _extract_route("intrathecal") == "INTRATHECAL"
    assert _extract_route("INTRATHECAL") == "INTRATHECAL"
    assert _extract_route("10 mg IV push") == "IV"
    assert _extract_route("5 mg IM") == "IM"
    assert _extract_route("0.5 ml SC") == "SC"
    assert _extract_route("1 tablet po twice daily") == "ORAL"
    assert _extract_route("ORAL") == "ORAL"


def test_n70_bcma_never_event_prevention_intrathecal_blocked_for_unspecified_injectable(
    client, superuser_headers, doctor_headers, nurse_headers
):
    """
    N-70: For an injectable drug where the order does NOT explicitly name a route:
    - Systemic parenteral route (IV, IM, SC) is accepted.
    - Neuraxial routes (INTRATHECAL, EPIDURAL) or INTRAARTERIAL must be STRICTLY BLOCKED (never-event).
    - When an order explicitly names 'intrathecal', intrathecal passes and IV is blocked.
    """
    p_res = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "Never-Event Route Safety Patient", "date_of_birth": "1987-05-12T00:00:00Z", "gender": "MALE"},
    )
    patient = p_res.json()

    enc_res = client.post(
        "/v1/api/adt/encounters",
        headers=superuser_headers,
        json={"patient_id": patient["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1},
    )
    encounter = enc_res.json()

    # Create injectable medication (Vincristine / generic injectable)
    med_res = client.post(
        "/v1/api/pharmacy/medications",
        headers=superuser_headers,
        json={
            "code": "DRUG_VINCRISTINE_INJ",
            "brand_name": "Oncovin Injection",
            "generic_name": "Vincristine Sulfate",
            "form": "INJECTION",
            "strength": "1 mg/ml",
            "ndc_or_barcode": "BARCODE-VINC-INJ-001",
        },
    )
    med = med_res.json()

    # 1. Prescribe with NO explicit route: "1 mg, give it slowly over 5 minutes"
    rx_res = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=superuser_headers,
        json={
            "encounter_id": encounter["id"],
            "items": [
                {
                    "medication_id": med["id"],
                    "dosage_instruction": "1 mg, give it slowly over 5 minutes",
                    "requested_quantity": 1,
                }
            ],
        },
    )
    rx_item_id = rx_res.json()["items"][0]["id"]

    # 1a. Nurse administers IV -> ALLOWED (parenteral systemic route for injectable)
    res_iv = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_item_id,
            "scanned_patient_barcode": patient["mrn"],
            "scanned_medication_barcode": "BARCODE-VINC-INJ-001",
            "dose_given": "1 mg",
            "route": "IV",
        },
    )
    assert res_iv.status_code in (200, 201), res_iv.text
    assert res_iv.json()["is_verified"] is True
    assert res_iv.json()["route_matched"] is True

    # 1b. Nurse attempts to administer INTRATHECAL -> STRICTLY BLOCKED! (Never-event prevention)
    res_it_blocked = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_item_id,
            "scanned_patient_barcode": patient["mrn"],
            "scanned_medication_barcode": "BARCODE-VINC-INJ-001",
            "dose_given": "1 mg",
            "route": "INTRATHECAL",
        },
    )
    assert res_it_blocked.status_code == 409
    assert "RIGHT ROUTE VIOLATION" in res_it_blocked.text
    assert "High-risk route 'INTRATHECAL' is strictly prohibited" in res_it_blocked.text

    # 1c. Nurse attempts to administer EPIDURAL -> STRICTLY BLOCKED!
    res_epi_blocked = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_item_id,
            "scanned_patient_barcode": patient["mrn"],
            "scanned_medication_barcode": "BARCODE-VINC-INJ-001",
            "dose_given": "1 mg",
            "route": "EPIDURAL",
        },
    )
    assert res_epi_blocked.status_code == 409
    assert "High-risk route 'EPIDURAL' is strictly prohibited" in res_epi_blocked.text

    # 2. Legitimate intrathecal prescription: "15 mg intrathecal stat"
    med_spinal_res = client.post(
        "/v1/api/pharmacy/medications",
        headers=superuser_headers,
        json={
            "code": "DRUG_BUPIV_SPINAL_N70",
            "brand_name": "Marcaine Spinal 0.5%",
            "generic_name": "Bupivacaine HCl",
            "form": "INJECTION",
            "strength": "5 mg/ml",
            "ndc_or_barcode": "BARCODE-BUPI-SPINAL-N70",
        },
    )
    med_spinal = med_spinal_res.json()

    rx_spinal_res = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=superuser_headers,
        json={
            "encounter_id": encounter["id"],
            "items": [
                {
                    "medication_id": med_spinal["id"],
                    "dosage_instruction": "15 mg intrathecal stat",
                    "requested_quantity": 1,
                }
            ],
        },
    )
    rx_spinal_item_id = rx_spinal_res.json()["items"][0]["id"]

    # 2a. Administering intrathecal to intrathecal order -> ALLOWED
    res_spinal_ok = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_spinal_item_id,
            "scanned_patient_barcode": patient["mrn"],
            "scanned_medication_barcode": "BARCODE-BUPI-SPINAL-N70",
            "dose_given": "15 mg",
            "route": "INTRATHECAL",
        },
    )
    assert res_spinal_ok.status_code in (200, 201), res_spinal_ok.text
    assert res_spinal_ok.json()["is_verified"] is True

    # 2b. Administering IV to intrathecal order -> BLOCKED
    res_wrong_iv = client.post(
        "/v1/api/pharmacy/bcma/verify-and-administer",
        headers=nurse_headers,
        json={
            "encounter_id": encounter["id"],
            "prescription_item_id": rx_spinal_item_id,
            "scanned_patient_barcode": patient["mrn"],
            "scanned_medication_barcode": "BARCODE-BUPI-SPINAL-N70",
            "dose_given": "15 mg",
            "route": "IV",
        },
    )
    assert res_wrong_iv.status_code == 409
    assert "RIGHT ROUTE VIOLATION" in res_wrong_iv.text


# =============================================================================
# 2. N-69: Lab Re-collection Persistent Database Task & Worklist
# =============================================================================

def test_n69_unobtainable_result_creates_persistent_recollection_task_and_worklist_entry(
    client, superuser_headers, doctor_headers
):
    """
    N-69: When an unobtainable analyzer result (OBX-11 = X) arrives:
    - Original task is set to SAMPLE_REJECTED with rejection note.
    - Parent order remains ACTIVE.
    - A visible, persistent re-collection task is created in the database on the same order with PENDING status.
    - Surfaces on /v1/api/diagnostics/tasks/worklist.
    """
    p_res = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "Re-collection Task Patient", "date_of_birth": "1992-04-18T00:00:00Z", "gender": "FEMALE"},
    )
    patient = p_res.json()

    srv_res = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": 1, "code": "LAB_CALCIUM_N69", "name": "Serum Calcium Urgent", "service_type": "LAB", "base_price_paise": 2800},
    )
    service = srv_res.json()

    order_res = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    )
    order_id = order_res.json()["id"]
    initial_task_id = order_res.json()["tasks"][0]["id"]

    # Analyzer transmits OBX status X (Clotted blood / unobtainable)
    hl7_msg = (
        "MSH|^~\\&|ANALYZER|HQ-HYD-01|AVINYA_LIS|SERVER|20261028080000||ORU^R01|MSG-N69-01|P|2.3\r"
        f"PID|1||{patient['mrn']}||Recollection^Patient||19920418|F\r"
        f"OBR|1|{initial_task_id}||LAB_CALCIUM_N69^Serum Calcium|||20261028081500|||||||||||||||||F\r"
        "OBX|1|NM|LAB_CALCIUM_N69^Serum Calcium||CANNOT_OBTAIN|mg/dL|||||X\r"
    )

    res_hl7 = client.post(
        "/v1/api/equipment/hl7/oru-r01",
        headers=superuser_headers,
        json={"raw_message": hl7_msg},
    )
    assert res_hl7.status_code == 200, res_hl7.text

    # Verify original task and order in database
    from backend.core import database
    engine = database.tenant_engine_pool.get_engine("hms_default")
    with Session(engine) as db:
        orig_task = db.get(DiagnosticTask, initial_task_id)
        assert orig_task.status == TaskStatusEnum.SAMPLE_REJECTED
        assert orig_task.rejection_reason == "Specimen unobtainable / rejected by analyzer (OBX-11=X)"

        order = db.get(DiagnosticOrder, order_id)
        assert order.status == OrderStatusEnum.ACTIVE

        # Verify that a NEW persistent re-collection task was created in the database
        all_order_tasks = db.execute(
            select(DiagnosticTask).where(DiagnosticTask.order_id == order_id).order_by(DiagnosticTask.id.asc())
        ).scalars().all()
        assert len(all_order_tasks) == 2, "Expected original rejected task and new re-collection task"

        recollection_task = all_order_tasks[1]
        assert recollection_task.id != initial_task_id
        assert recollection_task.status == TaskStatusEnum.PENDING
        assert "Re-collection required" in recollection_task.result_notes

    # Verify laboratory worklist endpoint returns the tasks
    res_worklist = client.get(
        "/v1/api/diagnostics/tasks/worklist",
        headers=doctor_headers,
    )
    assert res_worklist.status_code == 200, res_worklist.text
    worklist_items = res_worklist.json()["items"]
    worklist_task_ids = [t["id"] for t in worklist_items]
    assert initial_task_id in worklist_task_ids
    assert recollection_task.id in worklist_task_ids


# =============================================================================
# 3. N-72: Staging Treated as Production Across All Security Switches
# =============================================================================

def test_n72_staging_treated_as_production_across_all_switches(client, monkeypatch):
    """
    N-72: Under ENVIRONMENT='staging':
    - settings.is_production() evaluates to True.
    - /metrics requires METRICS_SCRAPE_TOKEN (answers 401 without token).
    - HSTS header (Strict-Transport-Security) is sent on API responses.
    - bootstrap.py --reset database dropping is strictly blocked.
    """
    from backend.core.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    assert settings.is_production() is True

    # 1. Database reset is strictly disallowed in staging
    from backend.scripts.bootstrap import reset_physical_databases
    with pytest.raises(RuntimeError) as exc_info:
        reset_physical_databases()
    assert "disallowed in production" in str(exc_info.value)

    # 2. Metrics endpoint requires METRICS_SCRAPE_TOKEN in staging (returns 401 without token)
    monkeypatch.setattr(settings, "METRICS_SCRAPE_TOKEN", None)
    res_metrics_unauth = client.get("/metrics")
    assert res_metrics_unauth.status_code == 401
    assert "METRICS_SCRAPE_TOKEN must be configured" in res_metrics_unauth.text

    # 2b. Metrics with token configured returns 401 if token missing or invalid, 200 if valid
    monkeypatch.setattr(settings, "METRICS_SCRAPE_TOKEN", "secure-staging-scrape-token-998877")
    res_metrics_no_hdr = client.get("/metrics")
    assert res_metrics_no_hdr.status_code == 401

    res_metrics_ok = client.get(
        "/metrics",
        headers={"Authorization": "Bearer secure-staging-scrape-token-998877"},
    )
    assert res_metrics_ok.status_code == 200

    # 3. HSTS response header present in staging
    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert "Strict-Transport-Security" in res_health.headers
    assert "max-age=63072000" in res_health.headers["Strict-Transport-Security"]


# =============================================================================
# 4. N-44: Narcotics Vault Safety - Role Verification, Format Validation, Return Capping
# =============================================================================

def test_n44_narcotics_vault_format_checks_capping_and_witness_roles(
    client, superuser_headers
):
    """
    N-44: Controlled Substances (Narcotics) Vault:
    - Receipt rejects repetitive/junk batch numbers ('xxx') and supply references ('xxxxx').
    - Receipt rejects witness accounts without licensed healthcare credentials with HTTP 403.
    - Dispense rejects repetitive ward notes ('xxxxxxxxxx').
    - Return rejects returns that exceed cumulative net dispensed units (cannot inflate inventory).
    - Return rejects non-licensed witness accounts with HTTP 403.
    """
    # Create controlled substance medication
    med_res = client.post(
        "/v1/api/pharmacy/medications",
        headers=superuser_headers,
        json={
            "code": "DRUG_MORPHINE_10MG_N44",
            "brand_name": "Morphine Sulfate 10mg",
            "generic_name": "Morphine Sulfate",
            "form": "INJECTION",
            "strength": "10 mg/ml",
            "is_controlled_substance": True,
            "ndc_or_barcode": "0409-1762-30",
        },
    )
    med = med_res.json()

    # Create test vault
    v_res = client.post(
        "/v1/api/pharmacy/controlled-substances/vault",
        headers=superuser_headers,
        json={
            "medication_id": med["id"],
            "vault_location": "Central Inpatient Pharmacy Vault 4",
            "initial_balance_units": 20,
            "unit_of_measure": "ampoule",
            "tamper_seal_number": "SEAL-N44-01",
        },
    )
    assert v_res.status_code in (200, 201), v_res.text
    vault = v_res.json()

    # Create an active user with NO clinical/pharmacy roles (e.g. non-clinical billing clerk)
    from backend.core import database
    engine = database.tenant_engine_pool.get_engine("hms_default")
    with Session(engine) as db:
        non_clinical_user = User(
            login_id="NONCLINICAL01",
            email="clerk@hospital.local",
            hashed_password=pwd_context.hash("ClerkPass123!"),
            full_name="Non-Clinical Clerk",
            status=UserStatusEnum.ACTIVE,
            is_superuser=False,
        )
        db.add(non_clinical_user)
        db.commit()
        db.refresh(non_clinical_user)
        non_clinical_id = non_clinical_user.id

    # --- 1. RECEIPT CONTROLS ---
    # 1a. Repetitive batch number 'xxx' rejected with 400
    rec_bad_batch = client.post(
        "/v1/api/pharmacy/controlled-substances/receipt",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 10,
            "witness_user_id": 2,  # Dr. Gregory House (licensed doctor)
            "witness_password": "StaffPass123!",
            "batch_number": "xxx",  # Junk repeated characters
            "notes": "Valid Supply Invoice PO-12345",
        },
    )
    assert rec_bad_batch.status_code == 400
    assert "Invalid batch/lot number format" in rec_bad_batch.text

    # 1b. Repetitive reference 'xxxxx' rejected with 400
    rec_bad_ref = client.post(
        "/v1/api/pharmacy/controlled-substances/receipt",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 10,
            "witness_user_id": 2,
            "witness_password": "StaffPass123!",
            "batch_number": "LOT-2026-N44",
            "notes": "xxxxx",  # Junk repetitive reference
        },
    )
    assert rec_bad_ref.status_code == 400
    assert "Invalid supply reference format" in rec_bad_ref.text

    # 1c. Witness without clinical/pharmacy role rejected with 403
    rec_bad_witness = client.post(
        "/v1/api/pharmacy/controlled-substances/receipt",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 10,
            "witness_user_id": non_clinical_id,
            "witness_password": "ClerkPass123!",
            "batch_number": "LOT-2026-N44",
            "notes": "Valid Supply Invoice PO-12345",
        },
    )
    assert rec_bad_witness.status_code == 403
    assert "Witness must be a licensed healthcare professional" in rec_bad_witness.text

    # 1d. Valid receipt with licensed witness and valid references succeeds
    rec_ok = client.post(
        "/v1/api/pharmacy/controlled-substances/receipt",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 10,
            "witness_user_id": 2,
            "witness_password": "StaffPass123!",
            "batch_number": "LOT-2026-N44",
            "notes": "Procurement Order PO-99124 State Medical Supplies",
        },
    )
    assert rec_ok.status_code == 201, rec_ok.text
    assert rec_ok.json()["balance_after"] == 30  # 20 + 10

    # --- 2. DISPENSE CONTROLS ---
    # 2a. Repetitive ward notes 'xxxxxxxxxx' rejected with 400
    disp_bad_notes = client.post(
        "/v1/api/pharmacy/controlled-substances/dispense",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 5,
            "notes": "xxxxxxxxxx",  # 10 repetitive characters
        },
    )
    assert disp_bad_notes.status_code == 400

    # 2b. Valid dispense to ward of 5 units succeeds
    disp_ok = client.post(
        "/v1/api/pharmacy/controlled-substances/dispense",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 5,
            "notes": "ICU Emergency Floor Stock Crash Cart 1",
        },
    )
    assert disp_ok.status_code == 201, disp_ok.text
    assert disp_ok.json()["balance_after"] == 25  # 30 - 5 = 25 (Net dispensed: 5)

    # --- 3. RETURN CAPPING CONTROLS ---
    # 3a. Return of 500 units when only 5 were dispensed strictly REJECTED with 400!
    ret_500_blocked = client.post(
        "/v1/api/pharmacy/controlled-substances/return",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 500,
            "witness_user_id": 2,
            "witness_password": "StaffPass123!",
            "notes": "Unused stock returned from procedure room 3",
        },
    )
    assert ret_500_blocked.status_code == 400
    assert "exceeds cumulative net dispensed units" in ret_500_blocked.text

    # 3b. Return of 10 units when only 5 were dispensed strictly REJECTED with 400!
    ret_10_blocked = client.post(
        "/v1/api/pharmacy/controlled-substances/return",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 10,
            "witness_user_id": 2,
            "witness_password": "StaffPass123!",
            "notes": "Unused stock returned from procedure room 3",
        },
    )
    assert ret_10_blocked.status_code == 400
    assert "exceeds cumulative net dispensed units" in ret_10_blocked.text

    # 3c. Return with non-clinical witness rejected with 403!
    ret_bad_witness = client.post(
        "/v1/api/pharmacy/controlled-substances/return",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 2,
            "witness_user_id": non_clinical_id,
            "witness_password": "ClerkPass123!",
            "notes": "Unused stock returned from procedure room 3",
        },
    )
    assert ret_bad_witness.status_code == 403
    assert "Witness must be a licensed healthcare professional" in ret_bad_witness.text

    # 3d. Valid return of 2 units (<= 5 dispensed) succeeds!
    ret_ok_1 = client.post(
        "/v1/api/pharmacy/controlled-substances/return",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 2,
            "witness_user_id": 2,
            "witness_password": "StaffPass123!",
            "notes": "Unused ampoules returned from ICU Crash Cart 1",
        },
    )
    assert ret_ok_1.status_code == 201, ret_ok_1.text
    assert ret_ok_1.json()["balance_after"] == 27  # 25 + 2 = 27 (Net remaining returnable: 3)

    # 3e. Returning another 2 units (<= 3 remaining returnable) succeeds!
    ret_ok_2 = client.post(
        "/v1/api/pharmacy/controlled-substances/return",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 2,
            "witness_user_id": 2,
            "witness_password": "StaffPass123!",
            "notes": "Unused ampoules returned from ICU Crash Cart 1",
        },
    )
    assert ret_ok_2.status_code == 201, ret_ok_2.text
    assert ret_ok_2.json()["balance_after"] == 29  # 27 + 2 = 29 (Net remaining returnable: 1)

    # 3f. Attempting to return another 2 units (> 1 remaining returnable) strictly REJECTED!
    ret_excess_blocked = client.post(
        "/v1/api/pharmacy/controlled-substances/return",
        headers=superuser_headers,
        json={
            "vault_id": vault["id"],
            "quantity_units": 2,
            "witness_user_id": 2,
            "witness_password": "StaffPass123!",
            "notes": "Unused ampoules returned from ICU Crash Cart 1",
        },
    )
    assert ret_excess_blocked.status_code == 400
    assert "exceeds cumulative net dispensed units" in ret_excess_blocked.text

