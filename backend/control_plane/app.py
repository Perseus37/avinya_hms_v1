from contextlib import asynccontextmanager
import logging
from pathlib import Path
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from control_models import ControlBase
from control_plane.fleet_ops_router import router as fleet_router
from control_plane.provisioning_router import router as provision_router
from database import control_engine, ensure_postgres_db_exists
from control_plane.migration_router import router as migration_router
logger = logging.getLogger("avinya.control_plane")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_postgres_db_exists(settings.CONTROL_PLANE_DB_NAME)
    ControlBase.metadata.create_all(bind=control_engine)
    logger.info("Avinya Nexus Control Plane v4.5 (React SPA Engine) initialized.")
    yield


onboarding_app = FastAPI(
    title="Avinya Nexus // Multi-Tenant Control Plane API",
    version="4.5.0",
    lifespan=lifespan,
)

onboarding_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Process-Time-Ms"],
)


@onboarding_app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.2f}"
    return response


@onboarding_app.get("/health", tags=["Control Plane Health"])
def control_plane_health():
    return {"status": "healthy", "service": "avinya-control-plane", "version": "4.5.0"}


onboarding_app.include_router(provision_router)
onboarding_app.include_router(fleet_router)
onboarding_app.include_router(migration_router)

_CANDIDATE_DISTS = [
    Path("/app/onboarding-ui/dist"),
    Path(__file__).resolve().parents[2] / "onboarding-ui" / "dist",
    Path(__file__).resolve().parents[1] / "onboarding-ui" / "dist",
]
_SPA_DIST = next((p for p in _CANDIDATE_DISTS if p.exists()), None)

if _SPA_DIST and (_SPA_DIST / "index.html").exists():
    if (_SPA_DIST / "assets").exists():
        onboarding_app.mount("/assets", StaticFiles(directory=str(_SPA_DIST / "assets")), name="assets")

    @onboarding_app.get("/{full_path:path}", include_in_schema=False)
    def serve_react_spa(full_path: str):
        return FileResponse(str(_SPA_DIST / "index.html"))