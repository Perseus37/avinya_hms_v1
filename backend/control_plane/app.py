from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI
from config import settings
from control_models import ControlBase
from control_plane.fleet_ops_router import router as fleet_router
from control_plane.provisioning_router import router as provision_router
from control_plane.ui_console import router as ui_router
from database import control_engine, ensure_postgres_db_exists

logger = logging.getLogger("avinya.control_plane")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_postgres_db_exists(settings.CONTROL_PLANE_DB_NAME)
    ControlBase.metadata.create_all(bind=control_engine)
    logger.info("Avinya Nexus Control Plane initialized.")
    yield


onboarding_app = FastAPI(
    title="Avinya Nexus // Multi-Tenant Control Plane",
    version="4.0.0",
    lifespan=lifespan,
)


@onboarding_app.get("/health", tags=["Control Plane Health"])
def control_plane_health():
    return {"status": "healthy", "service": "avinya-control-plane", "version": "4.0.0"}


onboarding_app.include_router(provision_router)
onboarding_app.include_router(fleet_router)
onboarding_app.include_router(ui_router)