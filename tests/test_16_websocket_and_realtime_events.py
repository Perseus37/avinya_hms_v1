import json
import pytest
from starlette.websockets import WebSocketDisconnect
import backend.core.event_bus as event_bus
from backend.core.event_bus import publish_tenant_event


def test_01_websocket_waiting_room_public_connection_and_heartbeat(client):
    """Scenario 1: Waiting room display connects without auth and handles ping/pong."""
    with client.websocket_connect("/v1/ws/waiting_room?tenant_slug=default") as ws:
        ws.send_text("ping")
        data = json.loads(ws.receive_text())
        assert data["type"] == "pong"


def test_02_websocket_authenticated_streams_reject_unauthorized_tokens(client):
    """Scenario 2: Protected streams (panic_labs, er_trauma) close unauthorized connections with code 1008."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/v1/ws/panic_labs?tenant_slug=default",
            subprotocols=["bearer", "invalid_token"],
        ) as ws:
            ws.receive_text()


def test_websocket_authorizes_requested_stream_and_rejects_wildcard_for_staff(
    client, multi_tenant_env, monkeypatch
):
    from backend.domains.realtime import router as realtime_router

    monkeypatch.setattr(
        realtime_router, "ControlSessionLocal", multi_tenant_env["control_session_factory"]
    )
    monkeypatch.setattr(
        realtime_router.tenant_engine_pool,
        "get_engine",
        lambda db_name: multi_tenant_env["tenant_engines"][db_name],
    )
    doctor_login = client.post(
        "/v1/api/auth/login",
        headers={"X-Hospital-Subdomain": "default"},
        json={"email": "doctor@default.com", "password": "StaffPass123!"},
    )
    assert doctor_login.status_code == 200
    doctor_token = doctor_login.json()["access_token"]

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/v1/ws/panic_labs?token={doctor_token}&tenant_slug=default"
        ):
            pass

    with client.websocket_connect(
        "/v1/ws/panic_labs?tenant_slug=default",
        subprotocols=["bearer", doctor_token],
    ) as ws:
        ws.send_text("ping")
        assert json.loads(ws.receive_text())["type"] == "pong"

    billing_login = client.post(
        "/v1/api/auth/login",
        headers={"X-Hospital-Subdomain": "default"},
        json={"email": "billing@default.com", "password": "StaffPass123!"},
    )
    assert billing_login.status_code == 200
    billing_token = billing_login.json()["access_token"]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/v1/ws/panic_labs?tenant_slug=default",
            subprotocols=["bearer", billing_token],
        ):
            pass

    for stream in ("all", "unknown_stream"):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/v1/ws/{stream}?tenant_slug=default",
                subprotocols=["bearer", doctor_token],
            ):
                pass


def test_websocket_rejects_tenant_mfa_challenge_tokens(
    client, multi_tenant_env, monkeypatch
):
    from backend.core.config import settings
    from backend.domains.realtime import router as realtime_router

    monkeypatch.setattr(settings, "MFA_REQUIRED_FOR_PRIVILEGED", True)
    monkeypatch.setattr(
        realtime_router, "ControlSessionLocal", multi_tenant_env["control_session_factory"]
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
    challenge_token = login.json()["mfa_token"]

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/v1/ws/panic_labs?tenant_slug=default",
            subprotocols=["bearer", challenge_token],
        ):
            pass


def test_websocket_rejects_missing_users_even_with_valid_jwt(
    client, multi_tenant_env, monkeypatch
):
    import jwt
    from datetime import datetime, timedelta, timezone
    from backend.core.config import settings
    from backend.control_plane.models import TenantRegistry
    from backend.security.envelope import decrypt_tenant_secret
    from backend.domains.realtime import router as realtime_router

    monkeypatch.setattr(
        realtime_router, "ControlSessionLocal", multi_tenant_env["control_session_factory"]
    )
    monkeypatch.setattr(
        realtime_router.tenant_engine_pool,
        "get_engine",
        lambda db_name: multi_tenant_env["tenant_engines"][db_name],
    )

    with multi_tenant_env["control_session_factory"]() as control_db:
        tenant = control_db.query(TenantRegistry).filter_by(subdomain_slug="default").one()
        signing_key = decrypt_tenant_secret(tenant.jwt_secret_key)
    token = jwt.encode(
        {
            "sub": "9999999",
            "tenant_slug": "default",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        signing_key,
        algorithm=settings.ALGORITHM,
    )
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/v1/ws/panic_labs?tenant_slug=default",
            subprotocols=["bearer", token],
        ):
            pass


def test_03_event_bus_publishes_tenant_namespaced_messages(monkeypatch, fake_redis):
    """Scenario 3: publish_tenant_event prefixes the channel with tenant_slug and serializes event data."""
    monkeypatch.setattr(event_bus, "get_redis", lambda: fake_redis)
    publish_tenant_event(
        tenant_slug="apollo",
        stream="panic_labs",
        event_type="CRITICAL_PANIC_VALUE_ALERT",
        payload={"task_id": 99, "result_value": "7.1 mEq/L"},
    )
    channel_key = "pubsub:tenant:apollo:panic_labs"
    assert channel_key in fake_redis.store
    published_msg = json.loads(fake_redis.store[channel_key])
    assert published_msg["event_type"] == "CRITICAL_PANIC_VALUE_ALERT"
    assert published_msg["tenant_slug"] == "apollo"
    assert published_msg["payload"]["result_value"] == "7.1 mEq/L"