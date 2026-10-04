import json
import pytest
from starlette.websockets import WebSocketDisconnect
import services.event_bus as event_bus
from services.event_bus import publish_tenant_event


def test_01_websocket_waiting_room_public_connection_and_heartbeat(client):
    """Scenario 1: Waiting room display connects without auth and handles ping/pong."""
    with client.websocket_connect("/v1/ws/waiting_room?tenant_slug=default") as ws:
        ws.send_text("ping")
        data = json.loads(ws.receive_text())
        assert data["type"] == "pong"


def test_02_websocket_authenticated_streams_reject_unauthorized_tokens(client):
    """Scenario 2: Protected streams (panic_labs, er_trauma) close unauthorized connections with code 1008."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/v1/ws/panic_labs?token=invalid_token&tenant_slug=default") as ws:
            ws.receive_text()


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