import asyncio
import json
import logging
from typing import Optional
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status, Depends
from jose import JWTError, jwt
from config import settings
from control_models import TenantRegistry
from database import ControlSessionLocal, get_redis

from security.envelope import decrypt_tenant_secret

logger = logging.getLogger("avinya.websocket")
ws_router = APIRouter(tags=["Real-Time WebSockets & Hospital Alerts"])


def _authenticate_ws(token: Optional[str], tenant_slug: str) -> bool:
    """Verifies that the JWT token is valid for the specified tenant."""
    if not token:
        return False
    with ControlSessionLocal() as control_db:
        tenant = control_db.query(TenantRegistry).filter(
            TenantRegistry.subdomain_slug == tenant_slug.strip().lower(),
            TenantRegistry.is_active == True,
        ).first()
        if not tenant:
            return False

        try:
            payload = jwt.decode(token, decrypt_tenant_secret(tenant.jwt_secret_key), algorithms=[settings.ALGORITHM])
            if payload.get("tenant_slug") != tenant.subdomain_slug:
                return False
            return True
        except JWTError:
            return False


@ws_router.websocket("/v1/ws/{stream}")
async def websocket_event_stream(
    websocket: WebSocket,
    stream: str,
    token: Optional[str] = Query(default=None),
    tenant_slug: str = Query(default="default"),
    redis_client=Depends(get_redis),
):
    """
    Real-time streaming endpoint for hospital clients and waiting-room TV displays.
    Channels: 'panic_labs', 'er_trauma', 'waiting_room', 'housekeeping', 'all'
    """
    # Public waiting room displays can connect without staff credentials
    if stream != "waiting_room":
        if not _authenticate_ws(token, tenant_slug):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    await websocket.accept()
    pubsub = redis_client.pubsub()

    # r = get_redis()
    # pubsub = r.pubsub()

    channel_pattern = (
        f"tenant:{tenant_slug.lower()}:*"
        if stream == "all"
        else f"tenant:{tenant_slug.lower()}:{stream.lower()}"
    )

    if stream == "all":
        pubsub.psubscribe(channel_pattern)
    else:
        pubsub.subscribe(channel_pattern)

    async def _send_pump():
        try:
            while True:
                message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("data"):
                    raw_data = message["data"]
                    if isinstance(raw_data, bytes):
                        raw_data = raw_data.decode("utf-8")
                    await websocket.send_text(raw_data)
                await asyncio.sleep(0.05)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception as exc:
            logger.debug("WebSocket client disconnected: %s", str(exc))

    async def _receive_pump():
        try:
            while True:
                data = await websocket.receive_text()
                # Heartbeat reply
                if data == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass

    pump_task = asyncio.create_task(_send_pump())
    recv_task = asyncio.create_task(_receive_pump())

    try:
        done, pending = await asyncio.wait(
            [pump_task, recv_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    finally:
        try:
            pubsub.close()
        except Exception:
            pass