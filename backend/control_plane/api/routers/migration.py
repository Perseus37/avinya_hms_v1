import csv
import io
import json
import logging
import secrets
import tempfile
import os
from typing import Any, Dict, List, Optional
from datetime import date, datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, BackgroundTasks
from sqlalchemy import Boolean, Date, DateTime, Enum, Float, Integer, JSON, Numeric, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.control_plane.models import ControlPlaneAuditLog, TenantRegistry
from backend.control_plane.services.migration_catalog import (
    PROTECTED_FIELDS,
    get_migration_models,
)
from backend.core.database import ControlSessionLocal, get_control_db, tenant_engine_pool
from backend.control_plane.services.auth import _verify_vendor_key
from backend.core.config import settings
from backend.domains.billing.models import Invoice
from backend.domains.identity.models import User
from backend.domains.identity.passwords import pwd_context
from backend.domains.patients.models import Patient

logger = logging.getLogger("avinya.migration")
router = APIRouter(prefix="/api/migration", tags=["Control Plane Visual ETL Studio"])

ENTITY_MODEL_MAP = get_migration_models()
SYSTEM_FIELDS = {"created_at", "updated_at", "created_by_id", "updated_by_id"}
ETL_UNARY_OPERATIONS = {
    "trim", "uppercase", "lowercase", "map", "replace", "conditional",
    "null_if", "substring", "split_part", "to_number", "add", "subtract",
    "multiply", "divide", "parse_date",
}
ETL_MULTI_INPUT_OPERATIONS = {"combine", "coalesce"}
ETL_DUPLICATE_POLICIES = {"skip", "update", "reject", "allow"}


def _single_column_unique_fields(entity_model: Any) -> set[str]:
    fields = {column.name for column in entity_model.__table__.columns if column.unique}
    for constraint in entity_model.__table__.constraints:
        if getattr(constraint, "unique", False) and len(constraint.columns) == 1:
            fields.add(next(iter(constraint.columns)).name)
    for index in entity_model.__table__.indexes:
        if index.unique and len(index.columns) == 1:
            fields.add(next(iter(index.columns)).name)
    return fields


def _validate_duplicate_settings(
    entity_model: Any, policy: str, field: Optional[str], mapping: Optional[dict] = None
) -> None:
    if policy not in ETL_DUPLICATE_POLICIES:
        raise ValueError(f"Unsupported duplicate policy: {policy}")
    if policy == "allow":
        return
    if field not in _single_column_unique_fields(entity_model):
        raise ValueError("Choose a mapped, single-column unique field for duplicate detection.")
    if mapping is not None and field not in mapping:
        raise ValueError("The duplicate key field must be mapped from the CSV.")

def _validate_mapping(mapping_config: Any, entity_model: Any) -> None:
    if not isinstance(mapping_config, dict):
        raise ValueError("Node mapping must be a JSON object.")

    allowed_fields = {
        column.name
        for column in entity_model.__table__.columns
        if not column.primary_key
        and column.name not in SYSTEM_FIELDS
        and column.name not in PROTECTED_FIELDS
    }
    if entity_model == Invoice:
        allowed_fields.add("patient_mrn")

    invalid_fields = set(mapping_config) - allowed_fields
    if invalid_fields:
        raise ValueError(
            "Unsupported destination field(s): "
            + ", ".join(sorted(invalid_fields))
        )
    blocked_required_fields = [
        column.name
        for column in entity_model.__table__.columns
        if column.name in PROTECTED_FIELDS
        and not column.nullable
        and column.default is None
        and column.server_default is None
    ]
    if blocked_required_fields:
        raise ValueError(
            "This table requires protected fields and cannot be imported through "
            "the migration studio."
        )
    for destination, rules in mapping_config.items():
        if not isinstance(rules, dict):
            raise ValueError(f"Mapping rules for '{destination}' must be an object.")
        if "expression" in rules:
            _validate_expression(rules["expression"])
        source_column = rules.get("source_column")
        if source_column is not None and not isinstance(source_column, str):
            raise ValueError(f"Source column for '{destination}' must be a string.")
        transform_map = rules.get("transform_map")
        if transform_map is not None and not isinstance(transform_map, dict):
            raise ValueError(f"Transform map for '{destination}' must be an object.")
    mapped_fields = set(mapping_config)
    for column in entity_model.__table__.columns:
        if (
            column.nullable
            or column.primary_key
            or column.name in SYSTEM_FIELDS
            or column.default is not None
            or column.server_default is not None
            or (entity_model == Patient and column.name == "mrn")
        ):
            continue
        if entity_model == Invoice and column.name == "patient_id":
            if "patient_mrn" in mapped_fields:
                continue
        if column.name not in mapped_fields:
            raise ValueError(f"Required destination field '{column.name}' is not mapped.")


def _validate_expression(expression: Any, depth: int = 0) -> None:
    if depth > settings.MIGRATION_MAX_EXPRESSION_DEPTH or not isinstance(expression, dict):
        raise ValueError(
            "ETL expressions must be objects with a maximum depth of "
            f"{settings.MIGRATION_MAX_EXPRESSION_DEPTH}."
        )
    expression_type = expression.get("type")
    if expression_type == "source":
        if not isinstance(expression.get("column"), str) or not expression["column"]:
            raise ValueError("Source expressions require a column name.")
        return
    if expression_type == "constant":
        if not isinstance(expression.get("value"), (str, int, float, bool, type(None))):
            raise ValueError("Constant values must be scalar.")
        return
    if expression_type == "combine":
        inputs = expression.get("inputs")
        if not isinstance(inputs, list) or len(inputs) < 2 or len(inputs) > settings.MIGRATION_MAX_INPUTS:
            raise ValueError(
                f"Combine blocks require between 2 and {settings.MIGRATION_MAX_INPUTS} inputs."
            )
        if not isinstance(expression.get("separator", " "), str):
            raise ValueError("Combine separators must be text.")
        for item in inputs:
            _validate_expression(item, depth + 1)
        return
    if expression_type == "coalesce":
        inputs = expression.get("inputs")
        if not isinstance(inputs, list) or len(inputs) < 2 or len(inputs) > settings.MIGRATION_MAX_INPUTS:
            raise ValueError(
                f"Coalesce blocks require between 2 and {settings.MIGRATION_MAX_INPUTS} inputs."
            )
        for item in inputs:
            _validate_expression(item, depth + 1)
        return
    if expression_type == "transform":
        operation = expression.get("operation")
        if not isinstance(operation, str) or operation not in ETL_UNARY_OPERATIONS:
            raise ValueError(f"Unsupported transformation operation: {operation}")
        _validate_expression(expression.get("input"), depth + 1)
        if operation == "map" and not isinstance(expression.get("map", {}), dict):
            raise ValueError("Value maps must be JSON objects.")
        if operation in {"add", "subtract", "multiply", "divide"}:
            operand = expression.get("operand")
            if not isinstance(operand, (int, float, str)) or isinstance(operand, bool):
                raise ValueError("Numeric transformations require a numeric operand.")
            try:
                Decimal(str(operand))
            except Exception as exc:
                raise ValueError("Numeric transformation operand is invalid.") from exc
        if operation == "substring":
            if not isinstance(expression.get("start", 0), int):
                raise ValueError("Substring start must be an integer.")
            if expression.get("length") is not None and not isinstance(expression["length"], int):
                raise ValueError("Substring length must be an integer.")
        if operation == "split_part":
            if not isinstance(expression.get("delimiter"), str) or not expression["delimiter"]:
                raise ValueError("Split operation requires a non-empty delimiter.")
            if (
                not isinstance(expression.get("part_index"), int)
                or isinstance(expression.get("part_index"), bool)
                or expression["part_index"] < 0
            ):
                raise ValueError("Split operation requires a non-negative integer part index.")
        if operation == "parse_date":
            if not isinstance(expression.get("input_format"), str) or not expression["input_format"]:
                raise ValueError("Date parsing requires an input format.")
            if not isinstance(expression.get("output_format", "%Y-%m-%d"), str):
                raise ValueError("Date output format must be text.")
        if operation == "conditional":
            operator = expression.get("operator")
            if not isinstance(operator, str) or operator not in {
                "equals", "not_equals", "contains", "is_empty", "not_empty"
            }:
                raise ValueError("Unsupported conditional operator.")
        for key in ("oldValue", "newValue", "compareValue", "whenTrue", "whenFalse"):
            if key in expression and not isinstance(expression[key], str):
                raise ValueError(f"Conditional and replacement values must be text ({key}).")
        return
    raise ValueError(f"Unsupported ETL expression type: {expression_type}")


def _evaluate_expression(expression: dict, row: dict) -> Any:
    expression_type = expression["type"]
    if expression_type == "source":
        return row.get(expression["column"])
    if expression_type == "constant":
        return expression.get("value")
    if expression_type == "combine":
        values = [_evaluate_expression(item, row) for item in expression["inputs"]]
        return expression.get("separator", " ").join(
            str(value) for value in values if value not in (None, "")
        )
    if expression_type == "coalesce":
        for item in expression["inputs"]:
            value = _evaluate_expression(item, row)
            if value not in (None, ""):
                return value
        return None

    value = _evaluate_expression(expression["input"], row)
    if value is None:
        return None
    operation = expression["operation"]
    if operation == "trim":
        return str(value).strip()
    if operation == "uppercase":
        return str(value).upper()
    if operation == "lowercase":
        return str(value).lower()
    if operation == "map":
        mapping = expression.get("map", {})
        normalized = str(value).strip().upper()
        return mapping.get(normalized, mapping.get(str(value), value))
    if operation == "replace":
        old_value = expression.get("oldValue", "")
        return str(value).replace(old_value, expression.get("newValue", ""))
    if operation == "conditional":
        normalized = str(value).strip().casefold()
        compare_value = expression.get("compareValue", "").casefold()
        operator = expression["operator"]
        matches = {
            "equals": normalized == compare_value,
            "not_equals": normalized != compare_value,
            "contains": compare_value in normalized,
            "is_empty": not normalized,
            "not_empty": bool(normalized),
        }[operator]
        return expression.get("whenTrue", "") if matches else expression.get("whenFalse", "")
    if operation == "null_if":
        return None if str(value).strip().casefold() == str(expression.get("compareValue", "")).strip().casefold() else value
    if operation == "substring":
        start = expression.get("start", 0)
        length = expression.get("length")
        return str(value)[start:] if length is None else str(value)[start : start + length]
    if operation == "split_part":
        parts = str(value).split(expression["delimiter"])
        index = expression["part_index"]
        return parts[index] if -len(parts) <= index < len(parts) else None
    if operation == "to_number":
        number = Decimal(str(value).strip())
        return number if number.is_finite() else None
    if operation in {"add", "subtract", "multiply", "divide"}:
        number = Decimal(str(value).strip())
        operand = Decimal(str(expression["operand"]))
        if operation == "add":
            return number + operand
        if operation == "subtract":
            return number - operand
        if operation == "multiply":
            return number * operand
        if operand == 0:
            raise ValueError("Division by zero in ETL transformation.")
        return number / operand
    if operation == "parse_date":
        parsed = datetime.strptime(str(value).strip(), expression["input_format"])
        return parsed.strftime(expression.get("output_format", "%Y-%m-%d"))
    raise ValueError(f"Unsupported transformation operation: {operation}")


def _apply_dynamic_mapping(
    row: dict, mapping_config: dict, entity_model: Any = None
) -> dict:
    """Transforms a legacy CSV row strictly based on the React Flow UI configuration."""
    if entity_model is not None:
        _validate_mapping(mapping_config, entity_model)
    allowed_fields = (
        {
            column.name
            for column in entity_model.__table__.columns
            if not column.primary_key
            and column.name not in SYSTEM_FIELDS
            and column.name not in PROTECTED_FIELDS
        }
        if entity_model is not None
        else None
    )
    if entity_model == Invoice:
        allowed_fields.add("patient_mrn")
    if allowed_fields is not None:
        invalid_fields = set(mapping_config) - allowed_fields
        if invalid_fields:
            raise ValueError(
                "Unsupported destination field(s): "
                + ", ".join(sorted(invalid_fields))
            )

    clean_data = {}
    columns = (
        {column.name: column for column in entity_model.__table__.columns}
        if entity_model is not None
        else {}
    )
    for db_field, rules in mapping_config.items():
        if "expression" in rules:
            raw_value = _evaluate_expression(rules["expression"], row)
        else:
            source_col = rules.get("source_column")
            raw_value = row.get(source_col) if source_col else None

        if "transform_map" in rules and raw_value is not None:
            raw_val_str = str(raw_value).strip().upper()
            transform_map = rules["transform_map"]
            if isinstance(transform_map, dict):
                raw_value = transform_map.get(raw_val_str, raw_value)

        if raw_value in (None, "") and "fallback" in rules:
            raw_value = rules["fallback"]
        if raw_value is not None and db_field in columns:
            raw_value = _coerce_migration_value(raw_value, columns[db_field].type)
        clean_data[db_field] = raw_value

    if entity_model == User and clean_data.get("hashed_password"):
        clean_data["hashed_password"] = pwd_context.hash(
            str(clean_data["hashed_password"])
        )

    if not clean_data.get("mrn") and (
        "mrn" in mapping_config or entity_model == Patient
    ):
        clean_data["mrn"] = f"MRN-{secrets.token_hex(6).upper()}"

    if entity_model is not None:
        for column in entity_model.__table__.columns:
            if (
                column.nullable
                or column.primary_key
                or column.name in SYSTEM_FIELDS
                or column.default is not None
                or column.server_default is not None
                or (entity_model == Patient and column.name == "mrn")
            ):
                continue
            if entity_model == Invoice and column.name == "patient_id":
                if clean_data.get("patient_mrn"):
                    continue
            if clean_data.get(column.name) in (None, ""):
                raise ValueError(f"Required destination field '{column.name}' has no value.")

    if allowed_fields is None or "is_active" in allowed_fields:
        clean_data.setdefault("is_active", True)
    if allowed_fields is None or "allow_network_sharing" in allowed_fields:
        clean_data.setdefault("allow_network_sharing", True)
    return clean_data


def _coerce_migration_value(value: Any, column_type: Any) -> Any:
    if value == "":
        return None
    if isinstance(column_type, Enum):
        enum_class = column_type.enum_class
        if enum_class is None:
            return value
        normalized_value = str(value).strip().upper()
        legacy_aliases = {"F": "FEMALE", "M": "MALE"}
        normalized_value = legacy_aliases.get(normalized_value, normalized_value)
        for member in enum_class:
            if normalized_value in {
                member.name.upper(),
                str(member.value).upper(),
            }:
                return member
        raise ValueError(f"Unsupported {enum_class.__name__} value: {value}")
    if isinstance(column_type, Boolean):
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
        raise ValueError(f"Invalid boolean value: {value}")
    if isinstance(column_type, DateTime):
        return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if isinstance(column_type, Date):
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    if isinstance(column_type, Integer):
        return int(value)
    if isinstance(column_type, (Float, Numeric)):
        return Decimal(str(value)) if isinstance(column_type, Numeric) else float(value)
    if isinstance(column_type, JSON) and isinstance(value, str):
        return json.loads(value)
    if isinstance(column_type, String):
        return str(value)
    return value

@router.post("/{slug}/analyze-csv")
async def analyze_legacy_csv(
    slug: str, master_setup_key: str = Form(...), file: UploadFile = File(...), control_db: Session = Depends(get_control_db),
):
    _verify_vendor_key(master_setup_key)
    tenant = control_db.execute(select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug)).scalar_one_or_none()
    if not tenant: raise HTTPException(status_code=404, detail="Tenant not found.")

    content = await file.read(settings.MIGRATION_MAX_UPLOAD_BYTES + 1)
    if len(content) > settings.MIGRATION_MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="CSV exceeds the configured upload limit.")
    stream = io.StringIO(content.decode("utf-8-sig", errors="ignore"))
    try:
        headers = next(csv.reader(stream))
    except StopIteration:
        raise HTTPException(status_code=400, detail="CSV file is empty.")
    return {"status": "success", "subdomain_slug": slug, "source_columns": [h.strip() for h in headers if h.strip()]}

@router.post("/{slug}/preview-mapping")
async def preview_visual_node_mapping(
    slug: str,
    master_setup_key: str = Form(...),
    entity_type: str = Form(...),
    node_mapping_json: str = Form(...),
    duplicate_policy: str = Form("skip"),
    duplicate_field: Optional[str] = Form(None),
    file: UploadFile = File(...),
    control_db: Session = Depends(get_control_db),
):
    _verify_vendor_key(master_setup_key)
    try:
        mapping = json.loads(node_mapping_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid node mapping JSON.") from exc
    entity_model = ENTITY_MODEL_MAP.get(entity_type.upper())
    if entity_model is None:
        raise HTTPException(status_code=400, detail=f"Unsupported entity target: {entity_type}")
    if not isinstance(mapping, dict):
        raise HTTPException(status_code=400, detail="Node mapping must be a JSON object.")
    try:
        _validate_mapping(mapping, entity_model)
        _validate_duplicate_settings(entity_model, duplicate_policy, duplicate_field, mapping)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    content = await file.read(settings.MIGRATION_MAX_UPLOAD_BYTES + 1)
    if len(content) > settings.MIGRATION_MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="CSV exceeds the configured upload limit.")
    stream = io.StringIO(content.decode("utf-8-sig", errors="ignore"))
    reader = csv.DictReader(stream)
    previews = []
    errors = []
    duplicate_count = 0
    seen_values = set()
    existing_values = set()
    duplicate_values = set()
    if duplicate_policy != "allow" and duplicate_field:
        target_engine = tenant_engine_pool.get_engine(tenant.db_name)
        with Session(bind=target_engine) as tenant_db:
            existing_values = set(
                tenant_db.scalars(
                    select(getattr(entity_model, duplicate_field))
                    .where(getattr(entity_model, duplicate_field).is_not(None))
                ).all()
            )

    for row_idx, row in enumerate(reader, start=2):
        try:
            transformed_data = _apply_dynamic_mapping(row, mapping, entity_model)
            value = transformed_data.get(duplicate_field) if duplicate_field else None
            if duplicate_policy != "allow" and value in (None, ""):
                raise ValueError(
                    f"Duplicate key field '{duplicate_field}' has no value."
                )
            is_duplicate = (
                duplicate_policy != "allow"
                and value not in (None, "")
                and (value in existing_values or value in seen_values)
            )
            if is_duplicate:
                duplicate_count += 1
                duplicate_values.add(value)
            if value not in (None, ""):
                seen_values.add(value)
            if len(previews) < settings.MIGRATION_PREVIEW_ROWS:
                previews.append(
                    {"row": row_idx, "data": transformed_data, "duplicate": is_duplicate}
                )
        except Exception as e:
            if len(errors) < settings.MIGRATION_PREVIEW_ERROR_LIMIT:
                errors.append({"row": row_idx, "error": str(e)})

    return {
        "status": "success",
        "previews": previews,
        "errors": errors,
        "duplicate_policy": duplicate_policy,
        "duplicate_field": duplicate_field,
        "duplicate_count": duplicate_count,
        "distinct_duplicate_values": len(duplicate_values),
    }


def _write_migration_audit(action: str, slug: str, details: str) -> None:
    try:
        with ControlSessionLocal() as control_db:
            control_db.add(
                ControlPlaneAuditLog(
                    action=action,
                    target_slug=slug,
                    actor="VENDOR_CONSOLE",
                    details=details[:2000],
                )
            )
            control_db.commit()
    except Exception:
        logger.exception("Could not persist migration outcome for tenant %s", slug)


def _execute_bulk_migration_background(
    tenant_db_name: str,
    tenant_slug: str,
    entity_model: Any,
    mapping: dict,
    temp_filepath: str,
    duplicate_policy: str,
    duplicate_field: Optional[str],
):
    target_engine = tenant_engine_pool.get_engine(tenant_db_name)
    TSession = sessionmaker(bind=target_engine, autocommit=False, autoflush=False)
    counts = {"inserted": 0, "updated": 0, "skipped": 0, "rejected": 0, "failed": 0}
    errors = []

    try:
        with open(temp_filepath, 'r', encoding='utf-8-sig', errors='ignore') as f:
            reader = csv.DictReader(f)
            with TSession() as target_db:
                for row_idx, row in enumerate(reader, start=2):
                    try:
                        clean_data = _apply_dynamic_mapping(row, mapping, entity_model)

                        for key, val in clean_data.items():
                            if val and isinstance(val, str) and ("date" in key or "dob" in key):
                                clean_data[key] = datetime.fromisoformat(val)

                        if entity_model == Invoice and "patient_mrn" in clean_data:
                            mrn_val = clean_data.pop("patient_mrn")
                            pid = target_db.execute(
                                select(Patient.id).where(Patient.mrn == mrn_val)
                            ).scalar_one_or_none()
                            if not pid:
                                raise ValueError(f"Foreign Key Error: Patient MRN '{mrn_val}' does not exist.")
                            clean_data["patient_id"] = pid

                        match = None
                        if duplicate_policy != "allow":
                            duplicate_value = clean_data.get(duplicate_field)
                            if duplicate_value in (None, ""):
                                raise ValueError(
                                    f"Duplicate key field '{duplicate_field}' has no value."
                                )
                            match = target_db.execute(
                                select(entity_model).where(
                                    getattr(entity_model, duplicate_field) == duplicate_value
                                )
                            ).scalar_one_or_none()

                        if match is not None and duplicate_policy in {"skip", "reject"}:
                            counts["skipped" if duplicate_policy == "skip" else "rejected"] += 1
                            continue

                        try:
                            with target_db.begin_nested():
                                if match is not None and duplicate_policy == "update":
                                    for key, value in clean_data.items():
                                        setattr(match, key, value)
                                    if hasattr(match, "updated_at"):
                                        match.updated_at = datetime.now(timezone.utc)
                                    target_db.flush()
                                    counts["updated"] += 1
                                else:
                                    target_db.add(entity_model(**clean_data))
                                    target_db.flush()
                                    counts["inserted"] += 1
                        except IntegrityError as exc:
                            counts["failed"] += 1
                            if len(errors) < 20:
                                errors.append(f"row {row_idx}: unique or foreign-key constraint failed")
                            logger.warning(
                                "Migration row %s constraint failure for %s (%s)",
                                row_idx,
                                tenant_slug,
                                type(exc).__name__,
                            )
                    except Exception as exc:
                        counts["failed"] += 1
                        if len(errors) < 20:
                            errors.append(f"row {row_idx}: {str(exc)[:300]}")
                        logger.exception("Migration row %s failed for tenant %s", row_idx, tenant_slug)
                target_db.commit()
        _write_migration_audit(
            "ETL_VISUAL_MIGRATION_COMPLETED",
            tenant_slug,
            f"{entity_model.__tablename__}: {json.dumps(counts)}; sample_errors={json.dumps(errors)}",
        )
    except Exception as exc:
        logger.exception("Migration job failed for tenant %s", tenant_slug)
        _write_migration_audit(
            "ETL_VISUAL_MIGRATION_FAILED",
            tenant_slug,
            (
                f"{entity_model.__tablename__} migration failed ({type(exc).__name__}); "
                f"counts={json.dumps(counts)}; see service logs."
            ),
        )
    finally:
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)

@router.post("/{slug}/execute-node-mapping")
async def execute_visual_node_migration(
    slug: str,
    background_tasks: BackgroundTasks,
    master_setup_key: str = Form(...),
    entity_type: str = Form(...),
    node_mapping_json: str = Form(...),
    duplicate_policy: str = Form("skip"),
    duplicate_field: Optional[str] = Form(None),
    file: UploadFile = File(...),
    control_db: Session = Depends(get_control_db),
):
    _verify_vendor_key(master_setup_key)
    tenant = control_db.execute(select(TenantRegistry).where(TenantRegistry.subdomain_slug == slug)).scalar_one_or_none()
    if not tenant: raise HTTPException(status_code=404, detail="Tenant not found.")

    target_model = ENTITY_MODEL_MAP.get(entity_type.upper())
    if not target_model: raise HTTPException(status_code=400, detail=f"Unsupported entity target: {entity_type}")

    try:
        mapping = json.loads(node_mapping_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid node mapping JSON.") from exc
    try:
        _validate_mapping(mapping, target_model)
        _validate_duplicate_settings(target_model, duplicate_policy, duplicate_field, mapping)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    try:
        total_bytes = 0
        while chunk := await file.read(1024 * 1024):
            total_bytes += len(chunk)
            if total_bytes > settings.MIGRATION_MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413, detail="CSV exceeds the configured upload limit."
                )
            temp_file.write(chunk)
        temp_file.close()
    except Exception:
        temp_file.close()
        os.unlink(temp_file.name)
        raise

    background_tasks.add_task(
        _execute_bulk_migration_background,
        tenant.db_name,
        tenant.subdomain_slug,
        target_model,
        mapping,
        temp_file.name,
        duplicate_policy,
        duplicate_field,
    )

    control_db.add(
        ControlPlaneAuditLog(
            action="ETL_VISUAL_MIGRATION_STARTED",
            target_slug=slug,
            details=(
                f"Background migration initiated for {entity_type}; "
                f"duplicate_policy={duplicate_policy}; duplicate_field={duplicate_field or 'n/a'}."
            ),
        )
    )
    control_db.commit()

    return {
        "status": "processing",
        "message": (
            f"Queued {entity_type} migration into {tenant.db_name}; "
            f"duplicates will be handled with '{duplicate_policy}'. "
            "See the Forensic Audit Ledger for final row counts."
        ),
    }