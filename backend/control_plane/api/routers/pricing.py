from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.control_plane.services.auth import verify_control_plane_secret
from backend.control_plane.services.migration_catalog import get_migration_schema_catalog
from backend.control_plane.services.pricing import (
    get_blueprint_catalog,
    get_bundle_catalog,
    get_module_catalog,
)
from backend.core.database import get_control_db

router = APIRouter(prefix="/api", tags=["Control Plane Pricing & Catalogs"])


@router.get("/pricing/catalog", dependencies=[Depends(verify_control_plane_secret)])
def get_commercial_pricing_catalog(
    db: Session = Depends(get_control_db),
) -> Dict[str, Any]:
    """Expose commercial catalogs and migration schemas to the control-plane UI."""
    modules = [{"code": code, **details} for code, details in get_module_catalog(db).items()]
    bundles = [{"code": code, **details} for code, details in get_bundle_catalog(db).items()]
    return {
        "modules": modules,
        "bundles": bundles,
        "migrationSchemas": get_migration_schema_catalog(),
    }


@router.get("/blueprints", dependencies=[Depends(verify_control_plane_secret)])
def get_spatial_blueprint_catalog(
    db: Session = Depends(get_control_db),
) -> Dict[str, Any]:
    """Expose hospital spatial templates used by the provisioning studio."""
    blueprints = [
        {"key": key, **details} for key, details in get_blueprint_catalog(db).items()
    ]
    return {"blueprints": blueprints}
