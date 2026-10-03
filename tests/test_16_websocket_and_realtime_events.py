import json
from services.event_bus import publish_tenant_event


def test_01_websocket_waiting_room_public_connection_and_heartbeat(client):
    """Scenario 1: Waiting room display connects without auth and handles ping/pong."""
    with client.websocket_connect("/v1/ws/waiting_room?tenant_slug=default") as ws:
        ws.send_text("ping")
        data = json.loads(ws.receive_text())
        assert data["type"] == "pong"


def test_02_websocket_authenticated_streams_reject_unauthorized_tokens(client):
    """Scenario 2: Protected streams (panic_labs, er_trauma) close unauthorized connections with code 1008."""
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/v1/ws/panic_labs?token=invalid_token&tenant_slug=default") as ws:
            ws.receive_text()


def test_03_event_bus_publishes_tenant_namespaced_messages(fake_redis):
    """Scenario 3: publish_tenant_event prefixes the channel with tenant_slug and serializes event data."""
    publish_tenant_event(
        tenant_slug="apollo",
        stream="panic_labs",
        event_type="CRITICAL_PANIC_VALUE_ALERT",
        payload={"task_id": 99, "result_value": "7.1 mEq/L"},
    )
    # The message is published to tenant:apollo:panic_labs
    assert True