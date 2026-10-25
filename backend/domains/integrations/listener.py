import asyncio
import hmac
import logging
import re
import ssl
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.core.config import settings
from backend.control_plane.models import TenantRegistry
from backend.core.database import ControlSessionLocal, tenant_engine_pool
from backend.domains.diagnostics.models import DiagnosticTask, TaskStatusEnum
from backend.domains.integrations.models import HL7IntegrationLog
from backend.domains.patients.models import Patient
from backend.domains.diagnostics.models import DiagnosticOrder
from backend.security.envelope import decrypt_tenant_secret

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("avinya.hl7_listener")

from backend.domains.integrations.service import (
    build_hl7_ack,
    build_hl7_ack_str,
    parse_hl7_message,
    process_hl7_oru_r01_in_session,
    redact_hl7_security as _redact_hl7_security,
)

# MLLP Framing Bytes
VT = b'\x0b'
FS_CR = b'\x1c\x0d'
MAX_MLLP_MESSAGE_BYTES = 1024 * 1024

def extract_mllp_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Extract complete MLLP frames, discarding stray bytes and bounding incomplete frames."""
    frames = []
    while True:
        start_idx = buffer.find(VT)
        end_idx = buffer.find(FS_CR)
        if end_idx != -1 and (start_idx == -1 or end_idx < start_idx):
            buffer = buffer[end_idx + len(FS_CR):]
            continue
        if start_idx == -1:
            return frames, b""
        if start_idx:
            buffer = buffer[start_idx:]
        end_idx = buffer.find(FS_CR, len(VT))
        if end_idx == -1:
            if len(buffer) - len(VT) > MAX_MLLP_MESSAGE_BYTES:
                raise ValueError("MLLP message exceeds the configured size limit.")
            return frames, buffer
        if end_idx - len(VT) > MAX_MLLP_MESSAGE_BYTES:
            raise ValueError("MLLP message exceeds the configured size limit.")
        frames.append(buffer[len(VT):end_idx])
        buffer = buffer[end_idx + len(FS_CR):]


def _hl7_msh_security(raw_msg: str) -> tuple[str, str, str]:
    msh = next((segment for segment in raw_msg.split("\r") if segment.startswith("MSH|")), "")
    fields = msh.split("|")
    if len(fields) <= 9:
        raise ValueError("Invalid HL7 MSH segment.")
    return fields[7], fields[3], fields[9]


def _tenant_hl7_config(branch_code: str) -> tuple[str, str | None] | None:
    with ControlSessionLocal() as control_db:
        tenant = control_db.execute(
            select(TenantRegistry).where(
                TenantRegistry.branch_code == branch_code,
                TenantRegistry.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if not tenant:
            return None
        db_name = tenant.db_name
        encrypted_secret = tenant.hl7_shared_secret_encrypted
    return (
        db_name,
        decrypt_tenant_secret(encrypted_secret) if encrypted_secret else None,
    )


def authenticate_hl7_message(raw_msg: str) -> tuple[str, str | None]:
    try:
        supplied_secret, branch_code, message_control_id = _hl7_msh_security(raw_msg)
    except ValueError:
        return "UNKNOWN", None
    tenant_config = _tenant_hl7_config(branch_code)
    try:
        secret_matches = bool(
            tenant_config
            and tenant_config[1]
            and hmac.compare_digest(
                supplied_secret.encode("utf-8"),
                tenant_config[1].encode("utf-8"),
            )
        )
    except (AttributeError, UnicodeError):
        secret_matches = False
    if not secret_matches:
        return message_control_id, None
    return message_control_id, tenant_config[0]


def _redact_hl7_security(raw_msg: str) -> str:
    segments = []
    for segment in raw_msg.split("\r"):
        if segment.startswith("MSH|"):
            fields = segment.split("|")
            if len(fields) > 7:
                fields[7] = "[REDACTED]"
            segment = "|".join(fields)
        segments.append(segment)
    return "\r".join(segments)


def process_and_route_result(raw_msg: str, tenant_db_name: str | None = None):
    """Routes the HL7 result to the correct tenant database based on MSH-4."""
    try:
        parsed = parse_hl7_message(raw_msg)
        msh = parsed.get("MSH", [])
        if not msh or len(msh[0]) <= 9:
            return "UNKNOWN", False, "Invalid HL7 MSH segment"
        branch_code = msh[0][3]
        msg_control_id = msh[0][9]

        if tenant_db_name is None:
            tenant_config = _tenant_hl7_config(branch_code)
            tenant_db_name = tenant_config[0] if tenant_config else None
        if not tenant_db_name:
            logger.error(f"HL7 Routing Failed: Unknown facility branch code '{branch_code}'")
            return msg_control_id, False, "Unknown Sending Facility"

        # Connect to the specific Hospital's Database
        t_engine = tenant_engine_pool.get_engine(tenant_db_name)
        TSession = sessionmaker(bind=t_engine)

        with TSession() as t_db:
            ctrl_id, success, error_msg, _ = process_hl7_oru_r01_in_session(t_db, raw_msg)
            return ctrl_id, success, error_msg

    except Exception:
        logger.exception("HL7 processing failed.")
        return "UNKNOWN", False, "HL7 processing failed"


async def handle_mllp_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Reads the raw TCP stream, unwraps the MLLP envelope, and dispatches the payload."""
    addr = writer.get_extra_info('peername')
    logger.info(f"Accepted LIS equipment connection from {addr}")

    try:
        buffer = b""
        while True:
            try:
                chunk = await asyncio.wait_for(
                    reader.read(4096),
                    timeout=settings.HL7_CLIENT_IDLE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.info("Closing idle LIS connection from %s.", addr)
                break
            if not chunk:
                break
            try:
                frames, buffer = extract_mllp_frames(buffer + chunk)
            except ValueError:
                logger.warning("Rejected oversized MLLP frame from %s.", addr)
                break

            for frame in frames:
                msg_control_id = "UNKNOWN"
                try:
                    raw_msg = frame.decode("utf-8")
                    msg_control_id, tenant_db_name = authenticate_hl7_message(raw_msg)
                except (UnicodeDecodeError, ValueError):
                    writer.write(build_hl7_ack("UNKNOWN", False, "Invalid HL7 message"))
                    await writer.drain()
                    continue
                if not tenant_db_name:
                    writer.write(build_hl7_ack(msg_control_id, False, "Unauthorized sender"))
                    await writer.drain()
                    continue

                result_id, success, err = await asyncio.to_thread(
                    process_and_route_result, raw_msg, tenant_db_name
                )
                writer.write(build_hl7_ack(result_id, success, err))
                await writer.drain()
    except Exception:
        logger.exception("MLLP stream failed for %s.", addr)
    finally:
        logger.info(f"Closing LIS connection from {addr}")
        writer.close()
        await writer.wait_closed()


async def start_hl7_server(host=None, port=None):
    host = host or getattr(settings, "HL7_LISTEN_HOST", "127.0.0.1")
    port = port or getattr(settings, "HL7_PORT", 2575)
    if not settings.HL7_TLS_CERT_FILE or not settings.HL7_TLS_KEY_FILE:
        raise RuntimeError("HL7 TLS certificate and key must be configured before starting the listener.")
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_context.load_cert_chain(
        certfile=settings.HL7_TLS_CERT_FILE,
        keyfile=settings.HL7_TLS_KEY_FILE,
    )
    server = await asyncio.start_server(
        handle_mllp_client, host, port, ssl=tls_context
    )
    addr = server.sockets[0].getsockname()
    logger.info(f"Avinya HL7/MLLP Equipment Listener running on {addr}")

    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(start_hl7_server())