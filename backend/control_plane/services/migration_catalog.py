"""Discover tenant ORM models that can be targeted by the migration studio."""

from importlib import import_module
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, Integer, JSON, Numeric, String

from backend.core.database import Base


_DOMAIN_MODELS_PATH = Path(__file__).resolve().parents[2] / "domains"
_SYSTEM_FIELDS = {"created_at", "updated_at", "created_by_id", "updated_by_id"}
PROTECTED_FIELDS = {"api_key_prefix", "api_key_hash"}


def get_migration_models() -> dict[str, type[Any]]:
    for domain_dir in _DOMAIN_MODELS_PATH.iterdir():
        if domain_dir.is_dir() and (domain_dir / "models.py").is_file():
            import_module(f"backend.domains.{domain_dir.name}.models")

    models = {}
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        if table.schema is not None:
            continue
        models[table.name.upper()] = mapper.class_
    return dict(sorted(models.items()))


def _type_name(column_type: Any) -> str:
    if isinstance(column_type, Enum):
        return "enum"
    if isinstance(column_type, Boolean):
        return "boolean"
    if isinstance(column_type, DateTime):
        return "datetime"
    if isinstance(column_type, Date):
        return "date"
    if isinstance(column_type, Integer):
        return "integer"
    if isinstance(column_type, (Float, Numeric)):
        return "number"
    if isinstance(column_type, JSON):
        return "json"
    if isinstance(column_type, String):
        return "string"
    return "string"


def _single_column_unique_fields(model: type[Any]) -> set[str]:
    unique_fields = {
        column.name for column in model.__table__.columns if column.unique
    }
    for constraint in model.__table__.constraints:
        if getattr(constraint, "unique", False) and len(constraint.columns) == 1:
            unique_fields.add(next(iter(constraint.columns)).name)
    for index in model.__table__.indexes:
        if index.unique and len(index.columns) == 1:
            unique_fields.add(next(iter(index.columns)).name)
    return unique_fields


def get_migration_schema_catalog() -> dict[str, list[dict[str, Any]]]:
    catalog = {}
    for entity, model in get_migration_models().items():
        fields = []
        unique_fields = _single_column_unique_fields(model)
        for column in model.__table__.columns:
            if (
                column.primary_key
                or column.name in _SYSTEM_FIELDS
                or column.name in PROTECTED_FIELDS
            ):
                continue
            if model.__tablename__ == "invoices" and column.name == "patient_id":
                continue
            fields.append(
                {
                    "id": column.name,
                    "label": (
                        "Password (hashed during import)"
                        if column.name == "hashed_password"
                        else column.name.replace("_", " ").title()
                    ),
                    "type": _type_name(column.type),
                    "unique": column.name in unique_fields,
                    "required": not column.nullable
                    and column.default is None
                    and column.server_default is None
                    and not (
                        model.__tablename__ == "patients"
                        and column.name == "mrn"
                    ),
                }
            )
        if model.__tablename__ == "invoices":
            fields.append(
                {
                    "id": "patient_mrn",
                    "label": "Patient MRN",
                    "type": "string",
                    "required": True,
                }
            )
        catalog[entity] = fields
    return catalog
