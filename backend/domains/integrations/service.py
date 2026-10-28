import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.domains.clinical.services import evaluate_lab_delta_check
from backend.domains.diagnostics.models import (
    DiagnosticOrder,
    DiagnosticTask,
    OrderStatusEnum,
    TaskStatusEnum,
)
from backend.domains.integrations.models import (
    EquipmentTestMapping,
    HL7IntegrationLog,
)
from backend.domains.patients.models import Patient

logger = logging.getLogger("avinya.hl7_service")

# MLLP Framing Bytes
VT = b"\x0b"
FS_CR = b"\x1c\x0d"
MAX_MLLP_MESSAGE_BYTES = 1024 * 1024

CRITICAL_PANIC_FLAGS = {"LL", "HH", "PANIC", "CRITICAL", "AA", "A*", "C"}


def parse_hl7_segments_ordered(raw_msg: str) -> list[list[str]]:
    """Splits raw HL7 v2 message into sequential segment lines preserving ordering."""
    segments: list[list[str]] = []
    lines = raw_msg.strip().replace("\r\n", "\r").replace("\n", "\r").split("\r")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        segments.append(line.split("|"))
    return segments


def parse_hl7_message(raw_msg: str) -> dict[str, list[list[str]]]:
    """Splits raw HL7 v2 message into segments and pipe-delimited fields."""
    segments: dict[str, list[list[str]]] = {}
    lines = raw_msg.strip().replace("\r\n", "\r").replace("\n", "\r").split("\r")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        fields = line.split("|")
        seg_name = fields[0]
        segments.setdefault(seg_name, []).append(fields)
    return segments


def redact_hl7_security(raw_msg: str) -> str:
    """Redacts MSH-8 / secret token from HL7 message for safe audit logging."""
    segments = []
    lines = raw_msg.strip().replace("\r\n", "\r").replace("\n", "\r").split("\r")
    for line in lines:
        if line.startswith("MSH|"):
            fields = line.split("|")
            if len(fields) > 7:
                fields[7] = "[REDACTED]"
            line = "|".join(fields)
        segments.append(line)
    return "\r".join(segments)


def build_hl7_ack_str(msg_control_id: str, success: bool, error_msg: str = "") -> str:
    """Builds standard HL7 ACK message string."""
    ack_code = "AA" if success else "AE"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    control_id = re.sub(r"[\x00-\x1f\x7f|]", "", msg_control_id or "UNKNOWN")[:80]
    safe_error = re.sub(r"[\x00-\x1f\x7f|]", " ", error_msg or "")[:120]
    return f"MSH|^~\\&|AVINYA_LIS|SERVER|||{ts}||ACK||P|2.3\rMSA|{ack_code}|{control_id}|{safe_error}\r"


def build_hl7_ack(msg_control_id: str, success: bool, error_msg: str = "") -> bytes:
    """Constructs the MLLP-framed HL7 Acknowledgement message expected by the equipment."""
    ack_str = build_hl7_ack_str(msg_control_id, success, error_msg)
    return VT + ack_str.encode("utf-8") + FS_CR


def _extract_task_id(identifier_str: str) -> Optional[int]:
    """Extracts numeric task ID from string tokens like '42', 'TASK-42', '42^RBC'."""
    if not identifier_str:
        return None
    token = identifier_str.split("^")[0].strip()
    if token.isdigit():
        return int(token)
    match = re.search(r"TASK-(\d+)", token, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def process_hl7_oru_r01_in_session(
    db: Session,
    raw_msg: str,
    equipment_id: Optional[int] = None,
) -> Tuple[str, bool, str, List[Dict[str, Any]]]:
    """
    Parses and files an unsolicited ORU^R01 laboratory observation result into
    the current tenant database session.

    Performs:
    1. Patient MRN / ID verification.
    2. Dynamic task matching via OBR-2 (Placer Order), OBR-3 (Filler Order),
       OBX-3 (Analyte code / task prefix), or EquipmentTestMapping table.
    3. Immutable verified/cancelled task guard (rejects modification of verified results).
    4. Panic / critical flag detection (OBX-8: LL, HH, PANIC, CRITICAL, AA).
    5. Automatic LIS Delta Check calculation against previous patient results.
    6. Multi-analyte structured formatting and order completion updating.
    7. Integration audit logging in hl7_integration_logs.
    """
    try:
        parsed = parse_hl7_message(raw_msg)
        msh = parsed.get("MSH", [])
        if not msh or len(msh[0]) <= 9:
            return "UNKNOWN", False, "Invalid HL7 MSH segment", []

        msg_control_id = msh[0][9] if len(msh[0]) > 9 else "UNKNOWN"

        # 1. Patient verification from PID-3 (N-40: strict match on MRN, reject fallback to internal ID)
        pid = parsed.get("PID", [])
        patient_identifier = (
            pid[0][3].split("^")[0].strip()
            if pid and len(pid[0]) > 3
            else ""
        )
        if not patient_identifier:
            return msg_control_id, False, "Missing PID patient identifier", []

        # Find patient by MRN strictly (N-40: prevent assigning results to unrelated internal ID)
        patient_stmt = select(Patient).where(Patient.mrn == patient_identifier)
        patient = db.execute(patient_stmt).scalar_one_or_none()

        if not patient:
            return msg_control_id, False, "No eligible result tasks were found", []

        # 2. Extract sequential segments and group OBX under preceding OBR (N-39)
        ordered_segments = parse_hl7_segments_ordered(raw_msg)
        obr_groups: List[Dict[str, Any]] = []
        current_group: Optional[Dict[str, Any]] = None

        for seg in ordered_segments:
            seg_name = seg[0]
            if seg_name == "OBR":
                current_group = {"obr": seg, "obx_list": []}
                obr_groups.append(current_group)
            elif seg_name == "OBX":
                if current_group is None:
                    current_group = {"obr": None, "obx_list": []}
                    obr_groups.append(current_group)
                current_group["obx_list"].append(seg)

        total_obx_count = sum(len(g["obx_list"]) for g in obr_groups)
        if total_obx_count == 0:
            return msg_control_id, False, "No OBX result segments found in HL7 message", []

        # Map active tasks for this patient ordered FIFO by created_at (N-41)
        active_tasks_stmt = (
            select(DiagnosticTask)
            .join(DiagnosticOrder, DiagnosticOrder.id == DiagnosticTask.order_id)
            .where(DiagnosticOrder.patient_id == patient.id)
            .order_by(DiagnosticTask.created_at.asc(), DiagnosticTask.id.asc())
            .options(joinedload(DiagnosticTask.order), joinedload(DiagnosticTask.service))
        )
        patient_tasks = db.execute(active_tasks_stmt).scalars().all()
        tasks_by_id = {t.id: t for t in patient_tasks}

        # N-41: Filter service code lookup strictly to unresulted pending tasks (FIFO order)
        # Prevents repeat tests from binding to older tasks that already have results entered
        unresulted_tasks_by_service_code: Dict[str, List[DiagnosticTask]] = {}
        for t in patient_tasks:
            if (
                t.service
                and t.service.code
                and t.status in {TaskStatusEnum.PENDING, TaskStatusEnum.SAMPLE_COLLECTED, TaskStatusEnum.IN_PROGRESS}
            ):
                unresulted_tasks_by_service_code.setdefault(t.service.code.upper(), []).append(t)

        # Check for equipment test mappings if equipment_id provided
        mapping_dict = {}
        if equipment_id:
            mappings = db.execute(
                select(EquipmentTestMapping).where(
                    EquipmentTestMapping.equipment_id == equipment_id,
                    EquipmentTestMapping.is_active.is_(True),
                )
            ).scalars().all()
            for m in mappings:
                mapping_dict[m.analyzer_test_code.upper()] = m.service_id

        # Target tasks to update: task_id -> list of OBX dicts
        task_observations: Dict[int, List[Dict[str, Any]]] = {}

        for group in obr_groups:
            obr = group["obr"]
            obx_list = group["obx_list"]
            if not obx_list:
                continue

            obr_task: Optional[DiagnosticTask] = None
            if obr:
                placer_num = obr[2] if len(obr) > 2 else ""
                filler_num = obr[3] if len(obr) > 3 else ""
                obr_task_id = _extract_task_id(placer_num) or _extract_task_id(filler_num)
                if obr_task_id and obr_task_id in tasks_by_id:
                    obr_task = tasks_by_id[obr_task_id]
                elif len(obr) > 4:
                    obr_code = obr[4].split("^")[0].strip().upper()
                    if obr_code in mapping_dict:
                        srv_id = mapping_dict[obr_code]
                        for t in patient_tasks:
                            if t.service_id == srv_id and t.status in {TaskStatusEnum.PENDING, TaskStatusEnum.SAMPLE_COLLECTED, TaskStatusEnum.IN_PROGRESS}:
                                obr_task = t
                                break
                    elif obr_code in unresulted_tasks_by_service_code:
                        candidates = unresulted_tasks_by_service_code[obr_code]
                        if len(candidates) > 1:
                            return msg_control_id, False, f"Ambiguous repeat test: multiple pending tasks exist for service '{obr_code}' without order control number (OBR-2/OBR-3 required)", []
                        obr_task = candidates[0]

            for obx in obx_list:
                if len(obx) <= 5:
                    continue
                set_id = obx[1] if len(obx) > 1 else "1"
                value_type = obx[2] if len(obx) > 2 else "NM"
                obs_ident = obx[3] if len(obx) > 3 else ""
                obs_value = obx[5] if len(obx) > 5 else ""
                units = obx[6] if len(obx) > 6 else ""
                ref_range = obx[7] if len(obx) > 7 else ""
                abnormal_flag = obx[8].strip().upper() if len(obx) > 8 and obx[8] else ""
                obs_status = obx[11].strip().upper() if len(obx) > 11 and obx[11] else "F"

                parts = obs_ident.split("^")
                test_code = parts[0].strip()
                test_name = parts[1].strip() if len(parts) > 1 else test_code

                # Identify target task for this observation
                matched_task = None
                task_id_from_obx = _extract_task_id(test_code)
                if task_id_from_obx and task_id_from_obx in tasks_by_id:
                    matched_task = tasks_by_id[task_id_from_obx]
                elif obr_task is not None:
                    matched_task = obr_task
                elif test_code.upper() in mapping_dict:
                    srv_id = mapping_dict[test_code.upper()]
                    for t in patient_tasks:
                        if t.service_id == srv_id and t.status in {TaskStatusEnum.PENDING, TaskStatusEnum.SAMPLE_COLLECTED, TaskStatusEnum.IN_PROGRESS}:
                            matched_task = t
                            break
                elif test_code.upper() in unresulted_tasks_by_service_code:
                    candidates = unresulted_tasks_by_service_code[test_code.upper()]
                    if len(candidates) > 1:
                        return msg_control_id, False, f"Ambiguous repeat test: multiple pending tasks exist for service '{test_code}' without order control number (OBR-2/OBR-3 required)", []
                    matched_task = candidates[0]

                if matched_task:
                    obs_dict = {
                        "set_id": set_id,
                        "value_type": value_type,
                        "test_code": test_code,
                        "test_name": test_name,
                        "value": obs_value,
                        "units": units,
                        "reference_range": ref_range,
                        "abnormal_flag": abnormal_flag,
                        "status": obs_status,
                    }
                    task_observations.setdefault(matched_task.id, []).append(obs_dict)

        if not task_observations:
            return msg_control_id, False, "No eligible result tasks were found", []

        # 3. Guard against verified / cancelled tasks
        for task_id in task_observations:
            task = tasks_by_id[task_id]
            if task.status in {TaskStatusEnum.VERIFIED, TaskStatusEnum.CANCELLED}:
                db.rollback()
                return msg_control_id, False, "Result task is not eligible for update", []

        # 4. Apply observations, panic flags, and LIS delta check
        updated_results = []
        parsed_order_ids = []

        for task_id, obs_items in task_observations.items():
            task = tasks_by_id[task_id]
            parsed_order_ids.append(str(task.id))

            has_panic = any(
                item["abnormal_flag"] in CRITICAL_PANIC_FLAGS for item in obs_items
            )
            if has_panic:
                task.is_critical_panic = True

            # Format result value
            prior_result = task.result_value
            is_amended = any(itm.get("status") in {"C", "A", "CORRECTED", "AMENDED"} for itm in obs_items)
            is_unobtainable = any(itm.get("status") in {"X", "CANNOT_OBTAIN", "REJECTED"} for itm in obs_items)
            is_preliminary = any(itm.get("status") in {"P", "PRELIMINARY"} for itm in obs_items)

            if is_unobtainable:
                task.result_value = None
                task.status = TaskStatusEnum.SAMPLE_REJECTED
                task.rejection_reason = "Specimen unobtainable / rejected by analyzer (OBX-11=X)"
                task.result_notes = f"{task.result_notes or ''} [UNOBTAINABLE: Specimen could not be analyzed (OBX-11=X). Re-collection required.]".strip()

                # N-69: Create visible persistent re-collection task in database so phlebotomy & lab worklist sees it
                recollection_task = DiagnosticTask(
                    order_id=task.order_id,
                    service_id=task.service_id,
                    status=TaskStatusEnum.PENDING,
                    result_notes=f"Re-collection required: analyzer rejected previous specimen (task #{task.id}) as unobtainable (OBX-11=X).",
                )
                db.add(recollection_task)

                from backend.core.database import get_redis
                try:
                    redis_cli = get_redis()
                    if redis_cli:
                        redis_cli.publish(
                            "lab_specimen_alerts",
                            json.dumps({
                                "event": "LAB_SPECIMEN_UNOBTAINABLE",
                                "order_id": task.order_id,
                                "task_id": task.id,
                                "recollection_task_id": None,
                                "patient_id": task.order.patient_id,
                                "service_code": task.service.code if task.service else "UNKNOWN",
                                "service_name": task.service.name if task.service else "Laboratory Test",
                                "message": "Specimen unobtainable (OBX-11=X). Re-collection required.",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                        )
                except Exception:
                    pass
            elif len(obs_items) == 1:
                single_val = obs_items[0]["value"]
                task.result_value = single_val
                if obs_items[0]["units"]:
                    task.result_notes = f"Units: {obs_items[0]['units']}"
                if obs_items[0]["reference_range"]:
                    task.result_notes = f"{task.result_notes or ''} | Ref: {obs_items[0]['reference_range']}".strip(" |")
                primary_numeric_str = single_val
            else:
                summary_lines = []
                primary_numeric_str = None
                for itm in obs_items:
                    line = f"{itm['test_name']}: {itm['value']} {itm['units']}".strip()
                    if itm["abnormal_flag"]:
                        line += f" [{itm['abnormal_flag']}]"
                    summary_lines.append(line)
                    if primary_numeric_str is None and re.match(r"^-?\d+(?:\.\d+)?$", itm["value"]):
                        primary_numeric_str = itm["value"]
                task.result_value = "\n".join(summary_lines)
                task.result_notes = json.dumps(obs_items)

            # N-42: If amended or replacing an existing result, append structured history
            if not is_unobtainable and (is_amended or prior_result) and prior_result and prior_result != task.result_value:
                amendment_log = f"[AMENDED {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} - Prior Result: '{prior_result}']"
                task.result_notes = f"{task.result_notes or ''} {amendment_log}".strip()

            # Evaluate LIS Delta Check if numeric (skip on unobtainable)
            if not is_unobtainable and primary_numeric_str:
                try:
                    delta_eval = evaluate_lab_delta_check(
                        session=db,
                        patient_id=patient.id,
                        service_id=task.service_id,
                        current_value_str=primary_numeric_str,
                        current_task_id=task.id,
                    )
                    task.delta_check_flag = delta_eval.get("delta_flag")
                    task.previous_result_value = delta_eval.get("previous_value")
                    task.delta_percentage = delta_eval.get("percentage_delta")
                    if delta_eval.get("delta_flag") == "DELTA_EXCEEDED":
                        detail_msg = delta_eval.get("detail", "")
                        task.result_notes = f"{task.result_notes or ''} [{detail_msg}]".strip()
                except Exception:
                    logger.debug("LIS delta check evaluation skipped or failed for task %s", task.id)

            if is_unobtainable:
                pass  # already handled above
            elif is_preliminary:
                task.status = TaskStatusEnum.IN_PROGRESS
                task.result_notes = f"{task.result_notes or ''} [PRELIMINARY: Result is preliminary and pending laboratory verification (OBX-11=P)]".strip()
            else:
                task.status = TaskStatusEnum.RESULT_ENTERED

            # Update parent order status (N-66: An unobtainable result must NOT mark order COMPLETED)
            order_tasks = task.order.tasks
            if all(t.status == TaskStatusEnum.VERIFIED for t in order_tasks):
                task.order.status = OrderStatusEnum.COMPLETED
            elif all(t.status in [TaskStatusEnum.CANCELLED, TaskStatusEnum.SAMPLE_REJECTED] for t in order_tasks):
                task.order.status = OrderStatusEnum.ACTIVE  # Remains OPEN / ACTIVE for recollection
            elif any(t.status in [TaskStatusEnum.RESULT_ENTERED, TaskStatusEnum.VERIFIED] for t in order_tasks):
                task.order.status = OrderStatusEnum.PARTIAL
            elif any(t.status in [TaskStatusEnum.IN_PROGRESS, TaskStatusEnum.PENDING, TaskStatusEnum.SAMPLE_COLLECTED, TaskStatusEnum.SAMPLE_REJECTED] for t in order_tasks):
                task.order.status = OrderStatusEnum.ACTIVE

            updated_results.append({
                "task_id": task.id,
                "service_code": task.service.code if task.service else None,
                "status": task.status.value,
                "is_critical_panic": task.is_critical_panic,
                "delta_check_flag": task.delta_check_flag,
                "result_value": task.result_value,
            })

        # 5. Record integration audit log
        db.add(
            HL7IntegrationLog(
                equipment_id=equipment_id,
                message_control_id=msg_control_id,
                raw_payload=redact_hl7_security(raw_msg),
                parsed_order_id=",".join(parsed_order_ids),
                status="SUCCESS",
            )
        )
        db.commit()

        return msg_control_id, True, "", updated_results

    except Exception:
        logger.exception("HL7 ORU^R01 processing failed.")
        db.rollback()
        # N-48: Return sanitized, generic HL7 error message without internal system details
        return "UNKNOWN", False, "HL7 processing failed: Internal laboratory interface error.", []
