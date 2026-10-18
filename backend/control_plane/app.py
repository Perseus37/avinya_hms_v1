import os
import logging
import traceback
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# Router Imports
from backend.control_plane.api.routers.fleet_ops import router as fleet_ops_router
from backend.control_plane.api.routers.pricing import router as pricing_router
from backend.control_plane.api.routers.migration import router as migration_router
from backend.control_plane.api.routers.provisioning import router as provisioning_router
from backend.control_plane.api.routers.saas_billing import router as saas_billing_router
from backend.control_plane.api.routers.ui_console import router as ui_console_router
from backend.control_plane.api.routers.network import router as control_plane_network_router

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Database Imports
from backend.core.config import settings
from backend.core.database import ControlSessionLocal, tenant_engine_pool
from backend.control_plane.models import ControlPlaneAuditLog

# Configure strict JSON logging for the Control Plane
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger("avinya.control_plane")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Avinya Control Plane Booting: Initializing Connection Pools...")
    try:
        yield
    finally:
        logger.info(
            "Avinya Control Plane Shutting Down: Disposing Tenant Connection Pools..."
        )
        tenant_engine_pool.dispose_all()


app = FastAPI(
    title="Avinya Control Plane API",
    version="5.1.0",
    description="Multi-tenant hospital fleet orchestration and provisioning engine.",
    lifespan=lifespan,
)

# -------------------------------------------------------------------------
# CORS & MIDDLEWARE
# -------------------------------------------------------------------------

# Restrict CORS to specific frontend domains (e.g., localhost:3000, admin.avinya.com)
allowed_origins = [
    origin.strip()
    for origin in settings.CORS_ORIGINS.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

@app.middleware("http")
async def db_session_cleanup_middleware(request: Request, call_next):
    """
    Guarantees that database connections are returned to the pool after every request.
    Prevents PostgreSQL 'Too many clients' fatal crashes during high concurrency.
    """
    response = None
    try:
        response = await call_next(request)
    finally:
        # If any router attached a DB session to the request state, force close it
        if hasattr(request.state, "db") and request.state.db:
            request.state.db.close()
    return response

# -------------------------------------------------------------------------
# GLOBAL EXCEPTION HANDLERS
# -------------------------------------------------------------------------

@app.exception_handler(Exception)
async def secure_global_exception_handler(request: Request, exc: Exception):
    """
    Catches unhandled 500 errors to prevent Python stack traces and DB credentials
    from leaking to the browser DevTools. Logs the real error internally.
    """
    logger.error(f"CRITICAL UNHANDLED ERROR on {request.method} {request.url.path}: {str(exc)}")
    logger.debug(traceback.format_exc())
    path_parts = request.url.path.strip("/").split("/")
    target_slug = (
        path_parts[path_parts.index("tenants") + 1]
        if "tenants" in path_parts and len(path_parts) > path_parts.index("tenants") + 1
        else "CONTROL_PLANE"
    )
    try:
        with ControlSessionLocal() as audit_db:
            audit_db.add(
                ControlPlaneAuditLog(
                    action="API_ERROR",
                    target_slug=target_slug[:63],
                    actor="SYSTEM",
                    details=(
                        f"{request.method} {request.url.path} failed with "
                        f"{type(exc).__name__}; see service logs for diagnostic details."
                    ),
                )
            )
            audit_db.commit()
    except Exception:
        logger.exception("Failed to persist API error audit event")

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal Server Error",
            "message": "An unexpected system failure occurred. The event has been logged for forensics."
        }
    )

# -------------------------------------------------------------------------
# ROUTER REGISTRATION
# -------------------------------------------------------------------------

app.include_router(fleet_ops_router)
app.include_router(pricing_router)
app.include_router(migration_router)
app.include_router(provisioning_router)
app.include_router(saas_billing_router)
app.include_router(ui_console_router)
app.include_router(control_plane_network_router)

# -------------------------------------------------------------------------
# GLOBAL WEBHOOK RECEIVER (ASYNCHRONOUS)
# -------------------------------------------------------------------------

def process_webhook_payload(payload: dict):
    """Isolated worker function to process webhooks without blocking the API."""
    try:
        # Example: Syncing payment gateway confirmations or EMR federation pings
        event_type = payload.get("event")
        logger.info(f"Processing background webhook event: {event_type}")
        # Add your heavy DB processing here
    except Exception as e:
        logger.error(f"Failed to process webhook payload: {str(e)}")

@app.post("/api/webhooks/ingress", tags=["Webhooks"])
async def receive_global_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """
    Receives incoming webhooks and immediately hands them off to the background thread.
    Prevents external API rate-limiting or synchronous DB deadlocks on Uvicorn.
    """
    try:
        payload = await request.json()
        background_tasks.add_task(process_webhook_payload, payload)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"message": "Webhook received and queued for processing."}
        )
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Invalid JSON payload."}
        )
# -------------------------------------------------------------------------
# STATIC SPA MOUNTING
# -------------------------------------------------------------------------
SPA_DIST = "/app/onboarding-ui/dist"

if os.path.exists(SPA_DIST):
    app.mount("/assets", StaticFiles(directory=f"{SPA_DIST}/assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_react_spa(full_path: str):
        return FileResponse(f"{SPA_DIST}/index.html")