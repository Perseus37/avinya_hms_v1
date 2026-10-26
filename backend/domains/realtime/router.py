import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status, Depends
import jwt
from sqlalchemy.orm import Session, sessionmaker
from backend.core.config import settings
from backend.control_plane.models import TenantRegistry
from backend.core.database import ControlSessionLocal, extract_subdomain_slug, get_redis, tenant_engine_pool
from backend.domains.identity.models import AuthSession, User, UserStatusEnum
from backend.core.dependencies import user_has_permission

from backend.security.envelope import decrypt_tenant_secret

logger = logging.getLogger("avinya.websocket")
ws_router = APIRouter(tags=["Real-Time WebSockets & Hospital Alerts"])
_connection_limit = asyncio.Semaphore(settings.WS_MAX_CONNECTIONS)
_waiting_room_connection_limit = asyncio.Semaphore(
    settings.WS_MAX_WAITING_ROOM_CONNECTIONS
)

STREAM_PERMISSIONS = {
    "panic_labs": ("diagnostics:read",),
    "er_trauma": ("adt:read", "opd:read"),
    "housekeeping": ("adt:read", "wards:read"),
    "break_glass": ("iam:read",),
}


def _validate_tenant_slug(tenant_slug: str) -> bool:
    """F-11: Verifies that the requested tenant exists and is active in the control plane."""
    normalized_slug = tenant_slug.strip().lower()
    if normalized_slug == settings.DEFAULT_TENANT_SLUG:
        return True
    try:
        with ControlSessionLocal() as control_db:
            return bool(
                control_db.query(TenantRegistry)
                .filter(
                    TenantRegistry.subdomain_slug == normalized_slug,
                    TenantRegistry.is_active == True,
                )
                .first()
            )
    except Exception:
        return False

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
            if (
                payload.get("tenant_slug") != tenant.subdomain_slug
                or "purpose" in payload
            ):
                return False
        except jwt.InvalidTokenError:
            return False

    subject = payload.get("sub")
    token_id = payload.get("jti")
    if (
        not isinstance(subject, str)
        or not subject.isdecimal()
        or not isinstance(token_id, str)
        or not token_id
    ):
        return False

    tenant_engine = tenant_engine_pool.get_engine(tenant.db_name)
    with Session(bind=tenant_engine) as tenant_db:
        user = tenant_db.get(User, int(subject))
        if not user or user.status != UserStatusEnum.ACTIVE:
            return False
        auth_session = tenant_db.get(AuthSession, token_id)
        if (
            not auth_session
            or auth_session.user_id != user.id
            or auth_session.revoked_at is not None
        ):
            return False
        expires_at = auth_session.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
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

    connection_limit = (
        _waiting_room_connection_limit
        if normalized_stream == "waiting_room"
        else _connection_limit
    )
    if connection_limit.locked():
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
        return

    await connection_limit.acquire()
    pubsub = None
    try:
        # F-11: Derive tenant slug securely from request host/headers before falling back to query param
        derived_slug = extract_subdomain_slug(websocket)
        effective_slug = (
            derived_slug
            if (derived_slug and derived_slug != settings.DEFAULT_TENANT_SLUG)
            else (tenant_slug.strip().lower() if tenant_slug else settings.DEFAULT_TENANT_SLUG)
        )

        is_valid_tenant = await asyncio.to_thread(_validate_tenant_slug, effective_slug)
        if not is_valid_tenant:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        # Browser WebSocket clients pass credentials through the handshake
        # subprotocol header instead of a URL that can be recorded in logs.
        offered_protocols = websocket.scope.get("subprotocols", [])
        token = (
            offered_protocols[1]
            if len(offered_protocols) == 2 and offered_protocols[0] == "bearer"
            else None
        )
        if normalized_stream != "waiting_room":
            try:
                authorized = await asyncio.to_thread(
                    _authenticate_ws, token, effective_slug, normalized_stream
                )
            except Exception:
                logger.exception("WebSocket authentication failed.")
                await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
                return
            if not authorized:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

        await websocket.accept(
            subprotocol="bearer" if normalized_stream != "waiting_room" else None
        )
        pubsub = redis_client.pubsub()

        channel_pattern = (
            f"tenant:{effective_slug}:*"
            if normalized_stream == "all"
            else f"tenant:{effective_slug}:{normalized_stream}"
        )
        if normalized_stream == "all":
            pubsub.psubscribe(channel_pattern)
        else:
            pubsub.subscribe(channel_pattern)

        async def _send_pump():
            next_auth_check = asyncio.get_running_loop().time() + 30
            try:
                while True:
                    if normalized_stream != "waiting_room":
                        now = asyncio.get_running_loop().time()
                        if now >= next_auth_check:
                            authorized = await asyncio.to_thread(
                                _authenticate_ws,
                                token,
                                effective_slug,
                                normalized_stream,
                            )
                            if not authorized:
                                await websocket.close(
                                    code=status.WS_1008_POLICY_VIOLATION
                                )
                                return
                            next_auth_check = now + 30
                    # F-11: Offload synchronous Redis socket polling to avoid blocking the asyncio event loop
                    message = await asyncio.to_thread(
                        pubsub.get_message,
                        ignore_subscribe_messages=True,
                        timeout=0.0,
                    )
                    if message and message.get("data"):
                        raw_data = message["data"]
                        if isinstance(raw_data, bytes):
                            raw_data = raw_data.decode("utf-8")
                        await websocket.send_text(raw_data)
                    await asyncio.sleep(0.2)
            except WebSocketDisconnect:
                return
            except Exception as exc:
                logger.debug("WebSocket client disconnected: %s", str(exc))

        async def _receive_pump():
            try:
                while True:
                    data = await websocket.receive_text()
                    if data == "ping":
                        await websocket.send_text(json.dumps({"type": "pong"}))
            except WebSocketDisconnect:
                return

        pump_task = asyncio.create_task(_send_pump())
        recv_task = asyncio.create_task(_receive_pump())
        tasks = [pump_task, recv_task]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        try:
            if pubsub is not None:
                pubsub.close()
        finally:
            connection_limit.release()