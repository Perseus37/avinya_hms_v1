"""
Audit Round 20 Remediation Test Suite.
Verifies remediations for findings identified in external auditor report (Section 20):
1. N-73 (Medium Severity - Laboratory Re-collection Task Deduplication & Auto-Cancellation):
   - Analyzer device retries of unobtainable results (OBX-11 = X) do NOT create duplicate re-collection tasks.
   - When a valid final result arrives (even on an initially rejected task), any pending re-collection task is automatically cancelled.
   - Subsequent results without an order number are not blocked by stale duplicate pending tasks.
   - Order status transitions correctly to COMPLETED once all ordered services are verified.
2. N-44 Hardening (Narcotics Witness Authorization):
   - Pharmacy read-only users cannot witness controlled narcotics receipts or returns.
   - Only clinical roles (doctor, nurse, pharmacist) or write permissions (pharmacy:write) can act as secondary witness.
3. ABDM Production Guard:
   - Simulated/mocked ABHA generation is strictly blocked in production environments.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core import database
from backend.core.config import settings
from backend.domains.diagnostics.models import (
    DiagnosticOrder,
    DiagnosticTask,
    OrderStatusEnum,
    TaskStatusEnum,
)
from backend.domains.identity.models import Permission, Role, User, UserStatusEnum
from backend.domains.identity.passwords import pwd_context
from backend.domains.pharmacy.models import (
    ControlledSubstanceVault,
    MedicationFormEnum,
    MedicationMaster,
)


# =============================================================================
# 1. N-73: Analyzer Re-collection Deduplication & Auto-Cancellation
# =============================================================================

def test_n73_analyzer_unobtainable_retry_does_not_duplicate_recollection_tasks(
    client, superuser_headers, doctor_headers
):
    """
    N-73 (1): An analyzer 'unobtainable' result (OBX-11 = X) creates a single pending re-collection task.
    When the device retries and sends the same message again, it must NOT create a duplicate task.
    """
    # 1. Create Patient & Diagnostic Order
    p_res = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "N73 Deduplication Patient", "date_of_birth": "1994-06-12T00:00:00Z", "gender": "MALE"},
    )
    patient = p_res.json()

    srv_res = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": 1, "code": "LAB_POTASSIUM_N73", "name": "Serum Potassium N73", "service_type": "LAB", "base_price_paise": 2400},
    )
    service = srv_res.json()

    order_res = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    )
    order_id = order_res.json()["id"]
    initial_task_id = order_res.json()["tasks"][0]["id"]

    hl7_msg = (
        "MSH|^~\\&|ANALYZER|HQ-HYD-01|AVINYA_LIS|SERVER|20261028080000||ORU^R01|MSG-N73-01|P|2.3\r"
        f"PID|1||{patient['mrn']}||N73^Patient||19940612|M\r"
        f"OBR|1|{initial_task_id}||LAB_POTASSIUM_N73^Serum Potassium|||20261028081500|||||||||||||||||F\r"
        "OBX|1|NM|LAB_POTASSIUM_N73^Serum Potassium||CANNOT_OBTAIN|mEq/L|||||X\r"
    )

    # 2. First transmission of OBX-11=X
    res1 = client.post("/v1/api/equipment/hl7/oru-r01", headers=superuser_headers, json={"raw_message": hl7_msg})
    assert res1.status_code == 200, res1.text

    engine = database.tenant_engine_pool.get_engine("hms_default")
    with Session(engine) as db:
        tasks_after_first = db.execute(
            select(DiagnosticTask).where(DiagnosticTask.order_id == order_id).order_by(DiagnosticTask.id.asc())
        ).scalars().all()
        assert len(tasks_after_first) == 2, "Expected 2 tasks: 1 rejected + 1 pending re-collection"
        assert tasks_after_first[0].status == TaskStatusEnum.SAMPLE_REJECTED
        assert tasks_after_first[1].status == TaskStatusEnum.PENDING
        assert "[ORIGINAL_TASK:" in tasks_after_first[1].result_notes

    # 3. Device retry: Analyzer re-sends the exact same OBX-11=X message
    res2 = client.post("/v1/api/equipment/hl7/oru-r01", headers=superuser_headers, json={"raw_message": hl7_msg})
    assert res2.status_code == 200, res2.text

    with Session(engine) as db:
        tasks_after_retry = db.execute(
            select(DiagnosticTask).where(DiagnosticTask.order_id == order_id).order_by(DiagnosticTask.id.asc())
        ).scalars().all()
        # N-73: MUST remain 2 tasks, NOT 3!
        assert len(tasks_after_retry) == 2, f"Device retry duplicated tasks! Expected 2, found {len(tasks_after_retry)}"


def test_n73_late_final_result_auto_cancels_recollection_and_completes_order(
    client, superuser_headers, doctor_headers
):
    """
    N-73 (2 & 3):
    - When a final result arrives for an order that has a pending re-collection task:
      The pending re-collection task is automatically CANCELLED.
    - Subsequent messages with no order number are not rejected as ambiguous.
    - When the result is verified, the order status transitions to COMPLETED.
    """
    # 1. Create Patient & Order
    p_res = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "N73 AutoCancel Patient", "date_of_birth": "1988-02-14T00:00:00Z", "gender": "FEMALE"},
    )
    patient = p_res.json()

    srv_res = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": 1, "code": "LAB_CALCIUM_N73", "name": "Serum Calcium N73", "service_type": "LAB", "base_price_paise": 2600},
    )
    service = srv_res.json()

    order_res = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [service["id"]]},
    )
    order_id = order_res.json()["id"]
    initial_task_id = order_res.json()["tasks"][0]["id"]

    # 2. Reject initial sample with OBX-11=X
    hl7_reject = (
        "MSH|^~\\&|ANALYZER|HQ-HYD-01|AVINYA_LIS|SERVER|20261028080000||ORU^R01|MSG-N73-REJ|P|2.3\r"
        f"PID|1||{patient['mrn']}||AutoCancel^Patient||19880214|F\r"
        f"OBR|1|{initial_task_id}||LAB_CALCIUM_N73^Serum Calcium|||20261028081500|||||||||||||||||F\r"
        "OBX|1|NM|LAB_CALCIUM_N73^Serum Calcium||CANNOT_OBTAIN|mg/dL|||||X\r"
    )
    res_rej = client.post("/v1/api/equipment/hl7/oru-r01", headers=superuser_headers, json={"raw_message": hl7_reject})
    assert res_rej.status_code == 200

    engine = database.tenant_engine_pool.get_engine("hms_default")
    with Session(engine) as db:
        tasks = db.execute(
            select(DiagnosticTask).where(DiagnosticTask.order_id == order_id).order_by(DiagnosticTask.id.asc())
        ).scalars().all()
        assert len(tasks) == 2
        recollection_task_id = tasks[1].id
        assert tasks[1].status == TaskStatusEnum.PENDING

    # 3. Late final result arrives carrying the original task ID (e.g. 11.5 mg/dL, OBX-11=F)
    hl7_final = (
        "MSH|^~\\&|ANALYZER|HQ-HYD-01|AVINYA_LIS|SERVER|20261028083000||ORU^R01|MSG-N73-FINAL|P|2.3\r"
        f"PID|1||{patient['mrn']}||AutoCancel^Patient||19880214|F\r"
        f"OBR|1|{initial_task_id}||LAB_CALCIUM_N73^Serum Calcium|||20261028083000|||||||||||||||||F\r"
        "OBX|1|NM|LAB_CALCIUM_N73^Serum Calcium||11.5|mg/dL|8.5-10.2|H|||F\r"
    )
    res_final = client.post("/v1/api/equipment/hl7/oru-r01", headers=superuser_headers, json={"raw_message": hl7_final})
    assert res_final.status_code == 200, res_final.text

    with Session(engine) as db:
        orig = db.get(DiagnosticTask, initial_task_id)
        assert orig.status == TaskStatusEnum.RESULT_ENTERED
        assert orig.result_value == "11.5"

        recoll = db.get(DiagnosticTask, recollection_task_id)
        # N-73: Re-collection task must be auto-cancelled!
        assert recoll.status == TaskStatusEnum.CANCELLED, f"Expected CANCELLED, got {recoll.status}"
        assert "AUTO-CANCELLED" in recoll.result_notes

        order = db.get(DiagnosticOrder, order_id)
        assert order.status == OrderStatusEnum.PARTIAL  # Result entered, awaiting verification

    # 4. Verify that a subsequent HL7 result with NO order number is NOT rejected as ambiguous
    hl7_no_order_id = (
        "MSH|^~\\&|ANALYZER|HQ-HYD-01|AVINYA_LIS|SERVER|20261028084500||ORU^R01|MSG-N73-NOID|P|2.3\r"
        f"PID|1||{patient['mrn']}||AutoCancel^Patient||19880214|F\r"
        "OBR|1|||LAB_CALCIUM_N73^Serum Calcium|||20261028084500|||||||||||||||||F\r"
        "OBX|1|NM|LAB_CALCIUM_N73^Serum Calcium||11.8|mg/dL|8.5-10.2|H|||F\r"
    )
    res_no_id = client.post("/v1/api/equipment/hl7/oru-r01", headers=superuser_headers, json={"raw_message": hl7_no_order_id})
    # Since re-collection task was cancelled, there are 0 pending tasks -> should NOT report "Ambiguous repeat test"
    assert "Ambiguous repeat test" not in res_no_id.text

    # 5. Doctor/Lab verifies the task -> Order transitions to COMPLETED
    res_verify = client.patch(
        f"/v1/api/diagnostics/tasks/{initial_task_id}/result",
        headers=superuser_headers,
        json={"status": "VERIFIED"},
    )
    assert res_verify.status_code == 200, res_verify.text

    with Session(engine) as db:
        order = db.get(DiagnosticOrder, order_id)
        assert order.status == OrderStatusEnum.COMPLETED, f"Order status should be COMPLETED, got {order.status}"


# =============================================================================
# 2. N-44: Narcotics Witness Role Hardening (Read-Only Insufficient)
# =============================================================================

def test_n44_narcotics_witness_requires_write_or_clinical_role(
    client, superuser_headers, monkeypatch
):
    """
    N-44 Hardening: Witnessing narcotic vault transactions requires write permission
    (pharmacy:write, adt:write, opd:write) or a licensed clinical role.
    A user with only pharmacy:read is rejected with HTTP 403.
    """
    engine = database.tenant_engine_pool.get_engine("hms_default")
    with Session(engine) as db:
        # Create vault medication
        med = db.execute(
            select(MedicationMaster).where(MedicationMaster.brand_name == "Fentanyl Citrate N44")
        ).scalar_one_or_none()
        if not med:
            med = MedicationMaster(
                code="MED_FENTANYL_N44",
                brand_name="Fentanyl Citrate N44",
                generic_name="Fentanyl",
                form=MedicationFormEnum.INJECTION,
                strength="50 mcg/mL",
                is_controlled_substance=True,
                low_stock_threshold_units=5,
            )
            db.add(med)
            db.flush()

        vault = db.execute(
            select(ControlledSubstanceVault).where(ControlledSubstanceVault.medication_id == med.id)
        ).scalar_one_or_none()
        if not vault:
            vault = ControlledSubstanceVault(
                medication_id=med.id,
                vault_location="ICU Satellite Safe N44",
                current_balance_units=20,
            )
            db.add(vault)
            db.flush()
        vault_id = vault.id

        # Create witness with ONLY pharmacy:read permission (no write, no clinical role)
        read_only_user = db.execute(
            select(User).where(User.email == "witness.readonly@hospital.org")
        ).scalar_one_or_none()
        if not read_only_user:
            read_only_user = User(
                login_id="witness_readonly",
                email="witness.readonly@hospital.org",
                full_name="Read Only Auditor",
                hashed_password=pwd_context.hash("AuditorPass123!"),
                status=UserStatusEnum.ACTIVE,
                is_superuser=False,
            )
            db.add(read_only_user)
            db.flush()
            p_read = db.execute(select(Permission).where(Permission.code == "pharmacy:read")).scalar_one_or_none()
            auditor_role = Role(code="ROLE_PHM_AUDITOR_N44", name="Pharmacy Auditor N44", description="Read-only pharmacy auditor")
            db.add(auditor_role)
            db.flush()
            if p_read:
                auditor_role.permissions.append(p_read)
            read_only_user.roles.append(auditor_role)
            db.commit()
        read_only_witness_id = read_only_user.id

    # Attempt to witness receipt with read-only user -> MUST FAIL with 403
    res_ro = client.post(
        "/v1/api/pharmacy/controlled-substances/receipt",
        headers=superuser_headers,
        json={
            "vault_id": vault_id,
            "quantity_units": 10,
            "notes": "Procurement invoice reference #N44-RECEIPT",
            "batch_number": "BATCH-N44-01",
            "witness_user_id": read_only_witness_id,
            "witness_password": "AuditorPass123!",
        },
    )
    assert res_ro.status_code == 403, f"Expected 403 for read-only witness, got {res_ro.status_code}"
    assert "Witness must be a licensed healthcare professional" in res_ro.text

    # Now create a licensed clinical pharmacist witness
    with Session(engine) as db:
        clinical_user = db.execute(
            select(User).where(User.email == "witness.pharmacist@hospital.org")
        ).scalar_one_or_none()
        if not clinical_user:
            clinical_user = User(
                login_id="witness_pharmacist",
                email="witness.pharmacist@hospital.org",
                full_name="Registered Pharmacist Witness",
                hashed_password=pwd_context.hash("PharmWitness123!"),
                status=UserStatusEnum.ACTIVE,
                is_superuser=False,
            )
            db.add(clinical_user)
            db.flush()
            p_write = db.execute(select(Permission).where(Permission.code == "pharmacy:write")).scalar_one_or_none()
            pharm_role = db.execute(select(Role).where(Role.code == "ROLE_PHARMACIST")).scalar_one_or_none()
            if not pharm_role:
                pharm_role = Role(code="ROLE_PHARMACIST", name="pharmacist", description="Licensed Pharmacist")
                db.add(pharm_role)
                db.flush()
            if p_write and p_write not in pharm_role.permissions:
                pharm_role.permissions.append(p_write)
            if pharm_role not in clinical_user.roles:
                clinical_user.roles.append(pharm_role)
            db.commit()
        clinical_witness_id = clinical_user.id

    # Attempt to witness receipt with clinical pharmacist -> MUST SUCCEED with 201
    res_ok = client.post(
        "/v1/api/pharmacy/controlled-substances/receipt",
        headers=superuser_headers,
        json={
            "vault_id": vault_id,
            "quantity_units": 10,
            "notes": "Valid dual-witness receipt documentation",
            "batch_number": "BATCH-N44-02",
            "witness_user_id": clinical_witness_id,
            "witness_password": "PharmWitness123!",
        },
    )
    assert res_ok.status_code == 201, f"Expected 201 for valid witness, got {res_ok.status_code}: {res_ok.text}"


# =============================================================================
# 3. ABDM Production Guard
# =============================================================================

def test_abdm_simulated_gateway_blocked_in_production(client, superuser_headers, monkeypatch):
    """
    ABDM Safety Guard (N-45, N-46):
    When running in production, simulated ABHA generation is strictly refused with HTTP 503.
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    res = client.post(
        "/api/abdm/m1/generate-abha",
        headers=superuser_headers,
        json={
            "aadhaar_or_mobile": "9876543210",
            "full_name": "Prod Patient",
            "date_of_birth": "1990-01-01T00:00:00Z",
            "gender": "MALE",
        },
    )
    assert res.status_code == 503, f"Expected 503 in production, got {res.status_code}"
    assert "Simulated ABHA" in res.text or "gateway" in res.text.lower()
