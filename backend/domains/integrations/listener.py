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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("avinya.hl7_listener")

# MLLP Framing Bytes
VT = b'\x0b'
FS_CR = b'\x1c\x0d'
MAX_MLLP_MESSAGE_BYTES = 1024 * 1024

def parse_hl7_message(raw_msg: str) -> dict:
    """Basic parser to extract the Sending Facility (Tenant), Patient, and Result segments."""
    segments = raw_msg.strip().split("\r")
    parsed = {"MSH": [], "PID": [], "OBR": [], "OBX": []}
    
    for segment in segments:
        fields = segment.split("|")
        seg_type = fields[0]
        if seg_type in parsed:
            parsed[seg_type].append(fields)
    return parsed

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


def _hl7_msh_security(raw_msg: str) -> tuple[str, str]:
    msh = next((segment for segment in raw_msg.split("\r") if segment.startswith("MSH|")), "")
    fields = msh.split("|")
    if len(fields) <= 9:
        raise ValueError("Invalid HL7 MSH segment.")
    return fields[7], fields[9]


def process_and_route_result(raw_msg: str):
    """Routes the HL7 result to the correct tenant database based on MSH-4."""
    try:
        parsed = parse_hl7_message(raw_msg)
        
        # MSH-4 contains the Sending Facility (Our Branch Code)
        # MSH-10 contains the Message Control ID
        msh = parsed["MSH"][0]
        if len(msh) <= 9:
            return "UNKNOWN", False, "Invalid HL7 MSH segment"
        branch_code = msh[3]
        msg_control_id = msh[9]
        
        # Look up the Tenant Database based on the transmitting machine's Branch Code
        with ControlSessionLocal() as control_db:
            tenant = control_db.execute(
                select(TenantRegistry).where(TenantRegistry.branch_code == branch_code)
            ).scalar_one_or_none()
            
        if not tenant:
            logger.error(f"HL7 Routing Failed: Unknown facility branch code '{branch_code}'")
            return msg_control_id, False, "Unknown Sending Facility"

        # Connect to the specific Hospital's Database
        t_engine = tenant_engine_pool.get_engine(tenant.db_name)
        TSession = sessionmaker(bind=t_engine)
        
        with TSession() as t_db:
            updated_count = 0
            # OBX-3 is the Test Code, OBX-5 is the Result Value
            for obx in parsed.get("OBX", []):
                if len(obx) <= 5:
                    continue
                # Assuming OBR-2 or OBX-3 links to our diagnostic_tasks.id
                task_id_str = obx[3].split("^")[0] 
                result_val = obx[5]
                
                if task_id_str.isdigit():
                    task = t_db.get(DiagnosticTask, int(task_id_str))
                    if task:
                        if task.status in {TaskStatusEnum.VERIFIED, TaskStatusEnum.CANCELLED}:
                            t_db.rollback()
                            return msg_control_id, False, "Result task is not eligible for update"
                        task.result_value = result_val
                        task.status = TaskStatusEnum.RESULT_ENTERED
                        updated_count += 1

            if not updated_count:
                return msg_control_id, False, "No eligible result tasks were found"
            
            # Log the payload in the hospital's local audit trail
            t_db.add(HL7IntegrationLog(
                message_control_id=msg_control_id,
                raw_payload=raw_msg,
                status="SUCCESS"
            ))
            t_db.commit()
            
        return msg_control_id, True, ""

    except Exception:
        logger.exception("HL7 processing failed.")
        return "UNKNOWN", False, "HL7 processing failed"


def build_hl7_ack(msg_control_id: str, success: bool, error_msg: str) -> bytes:
    """Constructs the HL7 Acknowledgement message expected by the machine."""
    ack_code = "AA" if success else "AE"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    control_id = re.sub(r"[\x00-\x1f\x7f|]", "", msg_control_id)[:80]
    safe_error = re.sub(r"[\x00-\x1f\x7f|]", " ", error_msg)[:120]
    ack_msg = f"MSH|^~\\&|AVINYA_LIS|SERVER|||{ts}||ACK||P|2.3\rMSA|{ack_code}|{control_id}|{safe_error}\r"
    return VT + ack_msg.encode('utf-8') + FS_CR


async def handle_mllp_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Reads the raw TCP stream, unwraps the MLLP envelope, and dispatches the payload."""
    addr = writer.get_extra_info('peername')
    logger.info(f"Accepted LIS equipment connection from {addr}")

    try:
        buffer = b""
        while True:
            chunk = await reader.read(4096)
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
                    supplied_secret, msg_control_id = _hl7_msh_security(raw_msg)
                except (UnicodeDecodeError, ValueError):
                    writer.write(build_hl7_ack("UNKNOWN", False, "Invalid HL7 message"))
                    await writer.drain()
                    continue
                configured_secret = settings.HL7_SHARED_SECRET
                if not configured_secret or not hmac.compare_digest(
                    supplied_secret, configured_secret
                ):
                    writer.write(build_hl7_ack(msg_control_id, False, "Unauthorized sender"))
                    await writer.drain()
                    continue

                result_id, success, err = await asyncio.to_thread(
                    process_and_route_result, raw_msg
                )
                writer.write(build_hl7_ack(result_id, success, err))
                await writer.drain()
    except Exception:
        logger.exception("MLLP stream failed for %s.", addr)
    finally:
        logger.info(f"Closing LIS connection from {addr}")
        writer.close()
        await writer.wait_closed()


async def start_hl7_server(host="0.0.0.0", port=2575):
    if not settings.HL7_SHARED_SECRET:
        raise RuntimeError("HL7_SHARED_SECRET must be configured before starting the listener.")
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