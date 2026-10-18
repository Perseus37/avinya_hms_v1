import asyncio
import json
import logging
from typing import Optional
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status, Depends
import jwt
from sqlalchemy.orm import Session, sessionmaker
from backend.core.config import settings
from backend.control_plane.models import TenantRegistry
from backend.core.database import ControlSessionLocal, get_redis, tenant_engine_pool
from backend.domains.identity.models import User, UserStatusEnum
from backend.core.dependencies import user_has_permission

from backend.security.envelope import decrypt_tenant_secret

logger = logging.getLogger("avinya.websocket")
ws_router = APIRouter(tags=["Real-Time WebSockets & Hospital Alerts"])

STREAM_PERMISSIONS = {
    "panic_labs": ("diagnostics:read",),
    "er_trauma": ("adt:read", "opd:read"),
    "housekeeping": ("adt:read", "wards:read"),
}


def _authenticate_ws(token: Optional[str], tenant_slug: str, stream: str) -> bool:
    """Verifies the JWT, active account, tenant, and requested event-stream access."""
    if not token:
        return False
    normalized_slug = tenant_slug.strip().lower()
    with ControlSessionLocal() as control_db:
        tenant = control_db.query(TenantRegistry).filter(
            TenantRegistry.subdomain_slug == normalized_slug,
            TenantRegistry.is_active == True,
        ).first()
        if not tenant:
            return False

        try:
            payload = jwt.decode(
                token,
                decrypt_tenant_secret(tenant.jwt_secret_key),
                algorithms=[settings.ALGORITHM],
            )
            if payload.get("tenant_slug") != tenant.subdomain_slug:
                return False
        except jwt.InvalidTokenError:
            return False

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.isdecimal():
        return False

    tenant_engine = tenant_engine_pool.get_engine(tenant.db_name)
    with Session(bind=tenant_engine) as tenant_db:
        user = tenant_db.get(User, int(subject))
        if not user or user.status != UserStatusEnum.ACTIVE:
            return False
        if stream == "all":
            return user.is_superuser
        return user_has_permission(
            tenant_db, user, *STREAM_PERMISSIONS.get(stream, ())
        )


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
    normalized_stream = stream.strip().lower()
    if normalized_stream not in {*STREAM_PERMISSIONS, "all", "waiting_room"}:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Waiting-room displays intentionally expose only the public call-up feed.
    if normalized_stream != "waiting_room" and not _authenticate_ws(
        token, tenant_slug, normalized_stream
    ):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    pubsub = redis_client.pubsub()

    channel_pattern = (
        f"tenant:{tenant_slug.strip().lower()}:*"
        if normalized_stream == "all"
        else f"tenant:{tenant_slug.strip().lower()}:{normalized_stream}"
    )

    if normalized_stream == "all":
        pubsub.psubscribe(channel_pattern)
    else:
        pubsub.subscribe(channel_pattern)

    async def _send_pump():
        try:
            while True:
                # PERF-003: Use timeout=0.0 for non-blocking read, yielding control back to the event loop
                message = pubsub.get_message(ignore_subscribe_messages=True, timeout=0.0)
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