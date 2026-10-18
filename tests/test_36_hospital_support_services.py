from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.domains.patients.models import Patient


def _create_patient(db: Session) -> int:
    patient = Patient(
        mrn="SUPPORT-001",
        full_name="Support Services Patient",
        date_of_birth=datetime(1985, 6, 1, tzinfo=timezone.utc),
        gender="OTHER",
        is_active=True,
    )
    db.add(patient)
    db.commit()
    return patient.id


def test_ambulance_dispatch_is_exclusive_and_status_history_is_audited(
    client, multi_tenant_env, superuser_headers
):
    vehicle = client.post(
        "/v1/api/ambulance/vehicles",
        headers=superuser_headers,
        json={
            "registration_number": "TS09AB1234",
            "call_sign": "AMB-01",
            "vehicle_type": "ALS",
        },
    )
    assert vehicle.status_code == 201, vehicle.text
    vehicle_id = vehicle.json()["id"]
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        patient_id = _create_patient(db)

    payload = {
        "patient_id": patient_id,
        "vehicle_id": vehicle_id,
        "pickup_location": "Emergency department",
        "destination": "City cardiac center",
        "acuity": "EMERGENCY",
    }
    dispatch = client.post(
        "/v1/api/ambulance/dispatches",
        headers=superuser_headers,
        json=payload,
    )
    assert dispatch.status_code == 201, dispatch.text
    dispatch_id = dispatch.json()["id"]
    assert client.post(
        "/v1/api/ambulance/dispatches",
        headers=superuser_headers,
        json=payload,
    ).status_code == 409

    arrived = client.patch(
        f"/v1/api/ambulance/dispatches/{dispatch_id}/status",
        headers=superuser_headers,
        json={"status": "ARRIVED"},
    )
    assert arrived.status_code == 200
    completed = client.patch(
        f"/v1/api/ambulance/dispatches/{dispatch_id}/status",
        headers=superuser_headers,
        json={"status": "COMPLETED"},
    )
    assert completed.status_code == 200
    assert client.patch(
        f"/v1/api/ambulance/dispatches/{dispatch_id}/status",
        headers=superuser_headers,
        json={"status": "ARRIVED"},
    ).status_code == 409
    history = client.get(
        f"/v1/api/ambulance/dispatches/{dispatch_id}/history",
        headers=superuser_headers,
    )
    assert [event["to_status"] for event in history.json()["items"]] == [
        "DISPATCHED",
        "ARRIVED",
        "COMPLETED",
    ]


def test_dietary_orders_require_tenant_patient_and_valid_lifecycle(
    client, multi_tenant_env, superuser_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        patient_id = _create_patient(db)
    order = client.post(
        "/v1/api/dietary/orders",
        headers=superuser_headers,
        json={
            "patient_id": patient_id,
            "diet_type": "Diabetic low salt",
            "meal": "LUNCH",
            "scheduled_for": "2026-10-20T12:00:00+05:30",
            "allergy_notes": "No known food allergies",
        },
    )
    assert order.status_code == 201, order.text
    order_id = order.json()["id"]
    for next_status in ("PREPARED", "DELIVERED"):
        transition = client.patch(
            f"/v1/api/dietary/orders/{order_id}/status",
            headers=superuser_headers,
            json={"status": next_status},
        )
        assert transition.status_code == 200, transition.text
    assert client.patch(
        f"/v1/api/dietary/orders/{order_id}/status",
        headers=superuser_headers,
        json={"status": "ORDERED"},
    ).status_code == 409
    assert client.post(
        "/v1/api/dietary/orders",
        headers=superuser_headers,
        json={
            "patient_id": 999999,
            "diet_type": "Regular",
            "meal": "DINNER",
            "scheduled_for": "2026-10-20T18:00:00+05:30",
        },
    ).status_code == 404


def test_cssd_batch_requires_both_indicators_before_release(
    client, superuser_headers
):
    instrument_set = client.post(
        "/v1/api/cssd/instrument-sets",
        headers=superuser_headers,
        json={"code": "ORTHO-SET-01", "name": "Orthopedic surgery set"},
    )
    assert instrument_set.status_code == 201, instrument_set.text
    batch = client.post(
        "/v1/api/cssd/batches",
        headers=superuser_headers,
        json={
            "batch_number": "CSSD-20261020-001",
            "instrument_set_id": instrument_set.json()["id"],
            "sterilizer_id": "STEAM-02",
            "cycle_type": "STEAM",
        },
    )
    assert batch.status_code == 201, batch.text
    batch_id = batch.json()["id"]
    released = client.post(
        f"/v1/api/cssd/batches/{batch_id}/complete-cycle",
        headers=superuser_headers,
        json={
            "chemical_indicator_passed": True,
            "biological_indicator_passed": True,
            "notes": "Cycle record reviewed",
        },
    )
    assert released.status_code == 200
    assert released.json()["status"] == "RELEASED"
    assert client.post(
        f"/v1/api/cssd/batches/{batch_id}/complete-cycle",
        headers=superuser_headers,
        json={
            "chemical_indicator_passed": True,
            "biological_indicator_passed": True,
        },
    ).status_code == 409

    failed_batch = client.post(
        "/v1/api/cssd/batches",
        headers=superuser_headers,
        json={
            "batch_number": "CSSD-20261020-002",
            "instrument_set_id": instrument_set.json()["id"],
            "sterilizer_id": "STEAM-02",
            "cycle_type": "STEAM",
        },
    )
    quarantined = client.post(
        f"/v1/api/cssd/batches/{failed_batch.json()['id']}/complete-cycle",
        headers=superuser_headers,
        json={
            "chemical_indicator_passed": True,
            "biological_indicator_passed": False,
        },
    )
    assert quarantined.status_code == 200
    assert quarantined.json()["status"] == "QUARANTINED"


def test_mortuary_release_requires_identity_check_and_witness(
    client, superuser_headers
):
    case = client.post(
        "/v1/api/mortuary/cases",
        headers=superuser_headers,
        json={
            "case_reference": "MORT-20261020-001",
            "deceased_name": "Unidentified Person",
            "source_department": "Emergency",
            "custody_location": "Cold room A-02",
        },
    )
    assert case.status_code == 201, case.text
    case_id = case.json()["id"]
    release_path = f"/v1/api/mortuary/cases/{case_id}/release"
    assert client.post(
        release_path,
        headers=superuser_headers,
        json={
            "recipient_name": "Family representative",
            "identity_verified": False,
            "witness_name": "Duty nurse",
        },
    ).status_code == 422
    released = client.post(
        release_path,
        headers=superuser_headers,
        json={
            "recipient_name": "Family representative",
            "identity_verified": True,
            "witness_name": "Duty nurse",
        },
    )
    assert released.status_code == 200, released.text
    assert released.json()["status"] == "RELEASED"
    assert client.post(
        release_path,
        headers=superuser_headers,
        json={
            "recipient_name": "Another representative",
            "identity_verified": True,
            "witness_name": "Duty nurse",
        },
    ).status_code == 409
