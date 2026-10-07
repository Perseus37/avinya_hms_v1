import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from control_models import TenantRegistry
from database import ControlSessionLocal, tenant_engine_pool
from models import HL7IntegrationLog, DiagnosticTask, TaskStatusEnum

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("avinya.hl7_listener")

# MLLP Framing Bytes
VT = b'\x0b'
FS_CR = b'\x1c\x0d'

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

def process_and_route_result(raw_msg: str):
    """Routes the HL7 result to the correct tenant database based on MSH-4."""
    try:
        parsed = parse_hl7_message(raw_msg)
        
        # MSH-4 contains the Sending Facility (Our Branch Code)
        # MSH-10 contains the Message Control ID
        msh = parsed["MSH"][0]
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
            # OBX-3 is the Test Code, OBX-5 is the Result Value
            for obx in parsed.get("OBX", []):
                # Assuming OBR-2 or OBX-3 links to our diagnostic_tasks.id
                task_id_str = obx[3].split("^")[0] 
                result_val = obx[5]
                
                if task_id_str.isdigit():
                    task = t_db.get(DiagnosticTask, int(task_id_str))
                    if task:
                        task.result_value = result_val
                        task.status = TaskStatusEnum.RESULT_ENTERED
            
            # Log the payload in the hospital's local audit trail
            t_db.add(HL7IntegrationLog(
                message_control_id=msg_control_id,
                raw_payload=raw_msg,
                status="SUCCESS"
            ))
            t_db.commit()
            
        return msg_control_id, True, ""

    except Exception as e:
        logger.error(f"HL7 Processing Error: {str(e)}")
        return "UNKNOWN", False, str(e)


def build_hl7_ack(msg_control_id: str, success: bool, error_msg: str) -> bytes:
    """Constructs the HL7 Acknowledgement message expected by the machine."""
    ack_code = "AA" if success else "AE"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    ack_msg = f"MSH|^~\\&|AVINYA_LIS|SERVER|||{ts}||ACK||P|2.3\rMSA|{ack_code}|{msg_control_id}|{error_msg}\r"
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
            buffer += chunk
            
            # Look for the Minimal Lower Layer Protocol (MLLP) framing boundaries
            while VT in buffer and FS_CR in buffer:
                start_idx = buffer.find(VT)
                end_idx = buffer.find(FS_CR)
                
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    # Extract the raw HL7 payload (strip VT and FS_CR)
                    hl7_bytes = buffer[start_idx + 1:end_idx]
                    buffer = buffer[end_idx + 2:]
                    
                    raw_msg = hl7_bytes.decode('utf-8', errors='ignore')
                    logger.info(f"Received HL7 Payload: {raw_msg[:50]}...")
                    
                    # Process and route
                    msg_control_id, success, err = process_and_route_result(raw_msg)
                    
                    # Send Acknowledgement back to the machine
                    ack_bytes = build_hl7_ack(msg_control_id, success, err)
                    writer.write(ack_bytes)
                    await writer.drain()
    except Exception as e:
        logger.error(f"MLLP Stream Error from {addr}: {str(e)}")
    finally:
        logger.info(f"Closing LIS connection from {addr}")
        writer.close()
        await writer.wait_closed()


async def start_hl7_server(host="0.0.0.0", port=2575):
    server = await asyncio.start_server(handle_mllp_client, host, port)
    addr = server.sockets[0].getsockname()
    logger.info(f"Avinya HL7/MLLP Equipment Listener running on {addr}")

    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(start_hl7_server())