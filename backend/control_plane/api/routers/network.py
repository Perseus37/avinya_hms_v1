from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.core.config import settings
from backend.core.database import get_control_db
from backend.control_plane.models import TenantRegistry

# FIX: Import authorization barrier
from backend.control_plane.services.auth import verify_control_plane_secret

router = APIRouter(prefix="/api/fleet", tags=["Control Plane Networking"])

# FIX: Protect endpoint from external topology mapping
@router.get("/traefik-config", include_in_schema=False, dependencies=[Depends(verify_control_plane_secret)])
def generate_dynamic_traefik_routing(control_db: Session = Depends(get_control_db)):
    """
    Traefik HTTP Provider endpoint. Traefik polls this internally to dynamically
    route custom white-labeled domains and provision Let's Encrypt SSL certificates
    without requiring Docker container restarts.
    """
    tenants = control_db.execute(
        select(TenantRegistry).where(
            TenantRegistry.is_active == True,
            TenantRegistry.custom_domain.is_not(None)
        )
    ).scalars().all()

    routers = {}
    services = {}

    for t in tenants:
        router_name = f"router-{t.subdomain_slug}-custom"

        routers[router_name] = {
            "rule": f"Host(`{t.custom_domain}`)",
            "service": "hms-api-service",
            "tls": {
                "certResolver": "le-ssl"
            }
        }

    services["hms-api-service"] = {
        "loadBalancer": {
            "servers": [{"url": f"{settings.API_INTERNAL_URL}/"}]
        }
    }

    return {
        "http": {
            "routers": routers,
            "services": services
        }
    }