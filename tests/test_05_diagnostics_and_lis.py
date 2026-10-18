from sqlalchemy.orm import Session

from backend.domains.diagnostics.models import DiagnosticTask
from backend.domains.integrations import listener


def _setup_cbc_bundle(client, superuser_headers):
    """Helper to create Lab Category, 2 atomic tests (RBC, WBC), and 1 Order Set package (CBC)."""
    cat = client.post(
        "/v1/api/diagnostics/categories",
        headers=superuser_headers,
        json={"department_id": 1, "code": "CAT_HEMA", "name": "Hematology"},
    ).json()

    rbc = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": cat["id"], "code": "LAB_RBC", "name": "RBC Count", "service_type": "LAB", "base_price_paise": 20000, "is_order_set": False},
    ).json()

    wbc = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": cat["id"], "code": "LAB_WBC", "name": "WBC Count", "service_type": "LAB", "base_price_paise": 20000, "is_order_set": False},
    ).json()

    cbc_pkg = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": cat["id"], "code": "PKG_CBC", "name": "Complete Blood Count Panel", "service_type": "PACKAGE", "base_price_paise": 35000, "is_order_set": True},
    ).json()

    return {"cat": cat, "rbc": rbc, "wbc": wbc, "cbc_pkg": cbc_pkg}


def test_01_create_diagnostic_category_and_catalog_items(client, superuser_headers, doctor_headers):
    """Scenario 1: Create Service Category and Service Catalog items and list them."""
    items = _setup_cbc_bundle(client, superuser_headers)
    cat_list = client.get("/v1/api/diagnostics/categories", headers=doctor_headers)
    assert cat_list.status_code == 200
    assert cat_list.json()["total"] == 1
    assert items["cbc_pkg"]["is_order_set"] is True


def test_02_bundle_mapping_self_reference_and_duplicate_guards(client, superuser_headers):
    """Scenario 2: Self-referencing bundle returns 400; duplicate child mapping returns 409."""
    items = _setup_cbc_bundle(client, superuser_headers)
    pkg_id = items["cbc_pkg"]["id"]
    rbc_id = items["rbc"]["id"]

    # Self-reference guard (400)
    assert client.post(
        "/v1/api/diagnostics/catalog/bundle",
        headers=superuser_headers,
        json={"parent_service_id": pkg_id, "child_service_id": pkg_id, "quantity": 1},
    ).status_code == 400

    # First mapping succeeds (201)
    assert client.post(
        "/v1/api/diagnostics/catalog/bundle",
        headers=superuser_headers,
        json={"parent_service_id": pkg_id, "child_service_id": rbc_id, "quantity": 1},
    ).status_code == 201

    # Duplicate mapping rejected (409)
    assert client.post(
        "/v1/api/diagnostics/catalog/bundle",
        headers=superuser_headers,
        json={"parent_service_id": pkg_id, "child_service_id": rbc_id, "quantity": 1},
    ).status_code == 409


def test_03_circular_bundle_dependency_detection_in_explosion_engine(client, superuser_headers, doctor_headers):
    """Scenario 3: Circular package mapping (Pkg A -> Pkg B -> Pkg A) is blocked with 400."""
    cat = client.post("/v1/api/diagnostics/categories", headers=superuser_headers, json={"department_id": 1, "code": "CAT_CIRC", "name": "Circular Test"}).json()
    pkg_a = client.post("/v1/api/diagnostics/catalog", headers=superuser_headers, json={"category_id": cat["id"], "code": "PKG_A", "name": "Pkg A", "service_type": "PACKAGE", "base_price_paise": 1000, "is_order_set": True}).json()
    pkg_b = client.post("/v1/api/diagnostics/catalog", headers=superuser_headers, json={"category_id": cat["id"], "code": "PKG_B", "name": "Pkg B", "service_type": "PACKAGE", "base_price_paise": 1000, "is_order_set": True}).json()

    client.post("/v1/api/diagnostics/catalog/bundle", headers=superuser_headers, json={"parent_service_id": pkg_a["id"], "child_service_id": pkg_b["id"], "quantity": 1})
    client.post("/v1/api/diagnostics/catalog/bundle", headers=superuser_headers, json={"parent_service_id": pkg_b["id"], "child_service_id": pkg_a["id"], "quantity": 1})

    p = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "Circ P", "date_of_birth": "1990-01-01T00:00:00Z", "gender": "MALE"}).json()
    order_res = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": p["id"], "ordered_service_ids": [pkg_a["id"]]},
    )
    assert order_res.status_code == 400
    assert "circular" in order_res.json()["message"].lower()


def test_04_order_explosion_engine_resolves_package_into_atomic_tasks(client, superuser_headers, doctor_headers):
    """Scenario 4: Ordering PKG_CBC explodes into 2 atomic DiagnosticTask tickets (LAB_RBC and LAB_WBC)."""
    items = _setup_cbc_bundle(client, superuser_headers)
    client.post("/v1/api/diagnostics/catalog/bundle", headers=superuser_headers, json={"parent_service_id": items["cbc_pkg"]["id"], "child_service_id": items["rbc"]["id"], "quantity": 1})
    client.post("/v1/api/diagnostics/catalog/bundle", headers=superuser_headers, json={"parent_service_id": items["cbc_pkg"]["id"], "child_service_id": items["wbc"]["id"], "quantity": 1})

    p = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "CBC P", "date_of_birth": "1990-01-01T00:00:00Z", "gender": "MALE"}).json()
    order = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": p["id"], "clinical_notes": "Rule out anemia", "ordered_service_ids": [items["cbc_pkg"]["id"]]},
    )
    assert order.status_code == 201
    tasks = order.json()["tasks"]
    assert len(tasks) == 2
    assert {t["service_id"] for t in tasks} == {items["rbc"]["id"], items["wbc"]["id"]}


def test_05_lab_result_entry_transitions_order_to_partial(client, superuser_headers, doctor_headers):
    """Scenario 5: Entering result on 1 of 2 tasks moves task to RESULT_ENTERED and parent order to PARTIAL."""
    items = _setup_cbc_bundle(client, superuser_headers)
    client.post("/v1/api/diagnostics/catalog/bundle", headers=superuser_headers, json={"parent_service_id": items["cbc_pkg"]["id"], "child_service_id": items["rbc"]["id"], "quantity": 1})
    client.post("/v1/api/diagnostics/catalog/bundle", headers=superuser_headers, json={"parent_service_id": items["cbc_pkg"]["id"], "child_service_id": items["wbc"]["id"], "quantity": 1})

    p = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "Partial P", "date_of_birth": "1990-01-01T00:00:00Z", "gender": "MALE"}).json()
    order = client.post("/v1/api/diagnostics/orders", headers=doctor_headers, json={"patient_id": p["id"], "ordered_service_ids": [items["cbc_pkg"]["id"]]}).json()
    t1_id = order["tasks"][0]["id"]

    res = client.patch(
        f"/v1/api/diagnostics/tasks/{t1_id}/result",
        headers=doctor_headers,
        json={"result_value": "4.8 million/mcL", "result_notes": "Normal RBC", "status": "RESULT_ENTERED"},
    )
    assert res.status_code == 200

    updated_order = client.get(f"/v1/api/diagnostics/orders/{order['id']}", headers=doctor_headers).json()
    assert updated_order["status"] == "PARTIAL"


def test_06_user_without_verify_permission_cannot_verify_task(client, superuser_headers, doctor_headers):
    """Scenario 6: ROLE_DOCTOR has diagnostics:write but NOT diagnostics:verify; attempting VERIFIED returns 403."""
    items = _setup_cbc_bundle(client, superuser_headers)
    p = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "Perm P", "date_of_birth": "1990-01-01T00:00:00Z", "gender": "MALE"}).json()
    order = client.post("/v1/api/diagnostics/orders", headers=doctor_headers, json={"patient_id": p["id"], "ordered_service_ids": [items["rbc"]["id"]]}).json()
    task_id = order["tasks"][0]["id"]

    # Enter result as superuser first
    client.patch(
        f"/v1/api/diagnostics/tasks/{task_id}/result",
        headers=superuser_headers,
        json={"result_value": "5.0", "status": "RESULT_ENTERED"},
    )

    # Doctor (lacks 'diagnostics:verify') tries to verify -> 403
    verify_attempt = client.patch(
        f"/v1/api/diagnostics/tasks/{task_id}/result",
        headers=doctor_headers,
        json={"result_value": "5.0", "status": "VERIFIED"},
    )
    assert verify_attempt.status_code == 403


def test_07_separation_of_duties_prevents_self_verification(client, superuser_headers):
    """Scenario 7: A non-superuser lab pathologist holding both write and verify cannot verify their own test (403)."""
    # Create a custom role with BOTH diagnostics:write and diagnostics:verify
    role = client.post("/v1/api/auth/roles", headers=superuser_headers, json={"name": "Pathologist", "description": "Lab write+verify"}).json()
    perms = client.get("/v1/api/auth/permissions", headers=superuser_headers).json()
    diag_ids = [p["id"] for p in perms if p["code"] in ("diagnostics:read", "diagnostics:write", "diagnostics:verify", "patients:write")]
    client.post(f"/v1/api/auth/roles/{role['id']}/permissions", headers=superuser_headers, json=diag_ids)

    client.post(
        "/v1/api/auth/users/doctor",
        headers=superuser_headers,
        json={"login_id": "PATH01", "email": "path1@default.com", "password": "StaffPass123!", "full_name": "Pathologist One", "gender": "FEMALE", "role_ids": [role["id"]]},
    )
    token = client.post("/v1/api/auth/login", headers={"X-Hospital-Subdomain": "default"}, json={"email": "path1@default.com", "password": "StaffPass123!"}).json()["access_token"]
    path1_headers = {"Authorization": f"Bearer {token}", "X-Hospital-Subdomain": "default"}

    items = _setup_cbc_bundle(client, superuser_headers)
    p = client.post("/v1/api/patients", headers=path1_headers, json={"full_name": "SoD Patient", "date_of_birth": "1990-01-01T00:00:00Z", "gender": "MALE"}).json()
    order = client.post("/v1/api/diagnostics/orders", headers=path1_headers, json={"patient_id": p["id"], "ordered_service_ids": [items["rbc"]["id"]]}).json()
    task_id = order["tasks"][0]["id"]

    # Pathologist 1 enters the result
    client.patch(f"/v1/api/diagnostics/tasks/{task_id}/result", headers=path1_headers, json={"result_value": "4.9", "status": "RESULT_ENTERED"})

    # Pathologist 1 attempts to verify their OWN result -> 403 Separation of duties violation
    self_verify = client.patch(f"/v1/api/diagnostics/tasks/{task_id}/result", headers=path1_headers, json={"result_value": "4.9", "status": "VERIFIED"})
    assert self_verify.status_code == 403
    assert "separation of duties" in self_verify.json()["message"].lower()


def test_08_second_verifier_completes_task_and_auto_completes_order(client, superuser_headers, doctor_headers):
    """Scenario 8: When a different authorized verifier verifies all tasks, DiagnosticOrder becomes COMPLETED."""
    items = _setup_cbc_bundle(client, superuser_headers)
    p = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "Complete P", "date_of_birth": "1990-01-01T00:00:00Z", "gender": "MALE"}).json()
    order = client.post("/v1/api/diagnostics/orders", headers=doctor_headers, json={"patient_id": p["id"], "ordered_service_ids": [items["rbc"]["id"]]}).json()
    task_id = order["tasks"][0]["id"]

    # Doctor enters result
    client.patch(f"/v1/api/diagnostics/tasks/{task_id}/result", headers=doctor_headers, json={"result_value": "5.1", "status": "RESULT_ENTERED"})

    # Superuser (different user) verifies result
    ver_res = client.patch(f"/v1/api/diagnostics/tasks/{task_id}/result", headers=superuser_headers, json={"result_value": "5.1", "status": "VERIFIED"})
    assert ver_res.status_code == 200

    final_order = client.get(f"/v1/api/diagnostics/orders/{order['id']}", headers=doctor_headers).json()
    assert final_order["status"] == "COMPLETED"


def test_mllp_parser_discards_stray_end_marker_and_extracts_frames():
    frame = b"MSH|message"
    frames, remainder = listener.extract_mllp_frames(
        listener.FS_CR
        + listener.VT
        + frame
        + listener.FS_CR
        + listener.VT
        + b"partial"
    )

    assert frames == [frame]
    assert remainder == listener.VT + b"partial"


def test_mllp_parser_rejects_oversized_incomplete_frame():
    import pytest

    payload = listener.VT + (b"x" * (listener.MAX_MLLP_MESSAGE_BYTES + 1))
    with pytest.raises(ValueError, match="size limit"):
        listener.extract_mllp_frames(payload)


def test_hl7_result_cannot_overwrite_verified_diagnostic_task(
    client, superuser_headers, doctor_headers, multi_tenant_env, monkeypatch
):
    items = _setup_cbc_bundle(client, superuser_headers)
    patient = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Verified LIS Patient",
            "date_of_birth": "1990-01-01T00:00:00Z",
            "gender": "MALE",
        },
    ).json()
    order = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient["id"], "ordered_service_ids": [items["rbc"]["id"]]},
    ).json()
    task_id = order["tasks"][0]["id"]
    assert client.patch(
        f"/v1/api/diagnostics/tasks/{task_id}/result",
        headers=doctor_headers,
        json={"result_value": "5.1", "status": "RESULT_ENTERED"},
    ).status_code == 200
    assert client.patch(
        f"/v1/api/diagnostics/tasks/{task_id}/result",
        headers=superuser_headers,
        json={"result_value": "5.1", "status": "VERIFIED"},
    ).status_code == 200

    monkeypatch.setattr(
        listener, "ControlSessionLocal", multi_tenant_env["control_session_factory"]
    )
    raw_message = (
        "MSH|^~\\&|DEVICE|HQ-HYD-01|AVINYA|HOSP|20261018||ORU^R01|CTL-VERIFIED|P|2.3\r"
        f"OBX|1|NM|{task_id}^RBC||999|mg/dL\r"
    )
    control_id, success, message = listener.process_and_route_result(raw_message)

    assert control_id == "CTL-VERIFIED"
    assert success is False
    assert "not eligible" in message
    with Session(multi_tenant_env["tenant_engines"]["hms_default"]) as db:
        task = db.get(DiagnosticTask, task_id)
        assert task.status.value == "VERIFIED"
        assert task.result_value == "5.1"