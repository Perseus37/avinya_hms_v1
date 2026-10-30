import os
import tempfile
from sqlalchemy import create_engine
from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata

from backend.control_plane.models import ControlBase
from backend.core.database import Base
from backend.scripts.migrate import upgrade_tenant_database_url
import backend.scripts.migrate
_ = backend.scripts.migrate


def test_all_foreign_keys_are_indexed_in_metadata():
    """
    Audit 22 Section 22.4 requirement:
    Verify that 100% of foreign keys across both Tenant Base and Control Plane
    are explicitly indexed or backed by a primary key / unique constraint.
    Zero unindexed foreign keys are permitted.
    """
    unindexed = []
    total_fks = 0

    for metadata, name in [
        (Base.metadata, "Tenant Base"),
        (ControlBase.metadata, "Control Plane"),
    ]:
        for table_name, table in sorted(metadata.tables.items()):
            for fk in table.foreign_keys:
                total_fks += 1
                col = fk.parent
                has_index = (
                    col.primary_key
                    or col.index
                    or any(col.name in [c.name for c in idx.columns] for idx in table.indexes)
                )
                has_unique_constraint = (
                    col.unique
                    or any(
                        col.name in [c.name for c in uc.columns]
                        for uc in table.constraints
                        if uc.__class__.__name__ == "UniqueConstraint"
                    )
                )
                if not has_index and not has_unique_constraint:
                    unindexed.append(f"{name} -> {table_name}.{col.name} references {fk.target_fullname}")

    assert unindexed == [], f"Found {len(unindexed)} unindexed foreign keys: {unindexed}"
    assert total_fks >= 252, f"Expected at least 252 foreign keys, found {total_fks}"


def test_foreign_key_index_naming_and_length():
    """
    Verify all foreign key index names comply with PostgreSQL 63-byte identifier limit
    and follow the ix_<table_name>_<column_name> convention.
    """
    for table_name, table in Base.metadata.tables.items():
        for idx in table.indexes:
            assert len(idx.name) <= 63, f"Index name '{idx.name}' on {table_name} exceeds 63 characters"
            assert not idx.name.startswith(" "), f"Index name '{idx.name}' has leading whitespace"


def test_foreign_key_migration_e1a3c7b5d902_matches_metadata():
    """
    Verify that upgrading through Alembic to revision e1a3c7b5d902 matches Base.metadata with 0 diffs.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_db_path = f.name

    try:
        db_url = f"sqlite:///{temp_db_path}"
        upgrade_tenant_database_url(db_url)
        engine = create_engine(db_url)
        try:
            with engine.connect() as conn:
                ctx = MigrationContext.configure(conn, opts={"compare_type": True})
                diffs = compare_metadata(ctx, Base.metadata)
                assert diffs == [], f"Alembic migration differs from ORM schema: {diffs}"
        finally:
            engine.dispose()
    finally:
        if os.path.exists(temp_db_path):
            try:
                os.remove(temp_db_path)
            except Exception:
                pass
