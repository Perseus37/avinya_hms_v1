from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.domains.patients.models import BreakGlassGrant, Patient


def _patient(db: Session) -> Patient:
    patient = Patient(
        mrn="BG-001",
        full_name="Emergency Patient",
        date_of_birth=datetime(1980, 1, 1, tzinfo=timezone.utc),
        gender="OTHER",
        is_active=True,
    )
    db.add(patient)
    db.commit()
    return patient


def test_break_glass_scope_expiry_alert_and_review(
    client,
    multi_tenant_env,
    doctor_headers,
    auditor_headers,
    superuser_headers,
    fake_redis,
):
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        patient = _patient(db)
        patient_id = patient.id

    grant_response = client.post(
        f"/v1/api/break-glass/patients/{patient_id}/grants",
        headers=doctor_headers,
        json={"reason": "Unconscious patient needs urgent identity and allergy check"},
    )
    assert grant_response.status_code == 201, grant_response.text
    grant = grant_response.json()
    assert grant["status"] == "ACTIVE"
    assert "break_glass" in next(iter(fake_redis.store))
    assert client.post(
        f"/v1/api/break-glass/patients/{patient_id}/grants",
        headers=auditor_headers,
        json={"reason": "Emergency access requested without clinical permission"},
    ).status_code == 403

    patient_data = client.get(
        f"/v1/api/break-glass/grants/{grant['grant_id']}/patient",
        headers=doctor_headers,
    )
    assert patient_data.status_code == 200, patient_data.text
    assert patient_data.json()["patient"]["mrn"] == "BG-001"

    assert client.get(
        f"/v1/api/break-glass/grants/{grant['grant_id']}/patient",
        headers=auditor_headers,
    ).status_code == 403
    assert client.get(
        "/v1/api/break-glass/reviews/pending", headers=auditor_headers
    ).status_code == 403
    assert client.get(
        "/v1/api/break-glass/reviews/pending", headers=superuser_headers
    ).json()["items"][0]["grant_id"] == grant["grant_id"]

    self_review = client.post(
        f"/v1/api/break-glass/reviews/{grant['grant_id']}",
        headers=doctor_headers,
        json={"outcome": "JUSTIFIED", "notes": "Clinically justified emergency access"},
    )
    assert self_review.status_code == 403
    reviewed = client.post(
        f"/v1/api/break-glass/reviews/{grant['grant_id']}",
        headers=superuser_headers,
        json={"outcome": "JUSTIFIED", "notes": "Reviewed against emergency record"},
    )
    assert reviewed.status_code == 200, reviewed.text
    assert client.post(
        f"/v1/api/break-glass/reviews/{grant['grant_id']}",
        headers=superuser_headers,
        json={"outcome": "JUSTIFIED", "notes": "Duplicate review"},
    ).status_code == 409

    with Session(engine) as db:
        stored = db.get(BreakGlassGrant, grant["grant_id"])
        stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert client.get(
        f"/v1/api/break-glass/grants/{grant['grant_id']}/patient",
        headers=doctor_headers,
    ).status_code == 403


def test_break_glass_limit_and_notification_outage_do_not_block_access(
    client, multi_tenant_env, doctor_headers, fake_redis, monkeypatch
):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "BREAK_GLASS_MAX_REQUESTS_PER_DAY", 1)
    monkeypatch.setattr(
        fake_redis,
        "publish",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Redis unavailable")),
    )
    engine = multi_tenant_env["tenant_engines"]["hms_default"]
    with Session(engine) as db:
        patient_id = _patient(db).id

    payload = {"reason": "Urgent allergy review during emergency care"}
    response = client.post(
        f"/v1/api/break-glass/patients/{patient_id}/grants",
        headers=doctor_headers,
        json=payload,
    )
    assert response.status_code == 201, response.text
    assert client.post(
        f"/v1/api/break-glass/patients/{patient_id}/grants",
        headers=doctor_headers,
        json=payload,
    ).status_code == 429
    with Session(engine) as db:
        assert db.query(BreakGlassGrant).count() == 1


def test_break_glass_alert_stream_is_restricted_to_iam_read_users(
    client, multi_tenant_env, monkeypatch, doctor_headers
):
    from backend.domains.realtime import router as realtime_router

    monkeypatch.setattr(
        realtime_router,
        "ControlSessionLocal",
        multi_tenant_env["control_session_factory"],
    )
    monkeypatch.setattr(
        realtime_router.tenant_engine_pool,
        "get_engine",
        lambda db_name: multi_tenant_env["tenant_engines"][db_name],
    )
    login = client.post(
        "/v1/api/auth/login",
        headers={"X-Hospital-Subdomain": "default"},
        json={"email": "admin@default.com", "password": "AdminPassword123!"},
    )
    assert login.status_code == 200
    assert realtime_router._authenticate_ws(
        login.json()["access_token"], "default", "break_glass"
    )
    assert not realtime_router._authenticate_ws(
        doctor_headers["Authorization"].split(" ", 1)[1],
        "default",
        "break_glass",
    )
