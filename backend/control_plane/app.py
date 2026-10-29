from pathlib import Path
import os
import asyncio
import logging
import traceback
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import event

# Router Imports
from backend.control_plane.api.routers.fleet_ops import router as fleet_ops_router
from backend.control_plane.api.routers.pricing import router as pricing_router
from backend.control_plane.api.routers.migration import router as migration_router
from backend.control_plane.api.routers.provisioning import router as provisioning_router
from backend.control_plane.api.routers.saas_billing import router as saas_billing_router
from backend.control_plane.api.routers.ui_console import router as ui_console_router
from backend.control_plane.api.routers.network import router as control_plane_network_router
from backend.control_plane.services.auth import (
    authenticate_operator_token,
    control_plane_actor, control_plane_operator_role,
    create_operator_auth_router,
)

import urllib.parse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

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


@event.listens_for(ControlPlaneAuditLog, "before_insert")
def _attribute_operator_audit(mapper, connection, audit_log):
    actor = control_plane_actor.get()
    if actor:
        audit_log.actor = actor[:100]


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


is_prod_docs_disabled = (
    settings.is_production()
    and not getattr(settings, "ENABLE_PRODUCTION_API_DOCS", False)
)

app = FastAPI(
    title="Avinya Control Plane API",
    version="5.1.0",
    description="Multi-tenant hospital fleet orchestration and provisioning engine.",
    lifespan=lifespan,
    docs_url=None if is_prod_docs_disabled else "/docs",
    redoc_url=None if is_prod_docs_disabled else "/redoc",
    openapi_url=None if is_prod_docs_disabled else "/openapi.json",
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


@app.middleware("http")
async def operator_session_middleware(request: Request, call_next):
    """Validates named operator bearer sessions before protected dependencies run."""
    authorization = request.headers.get("Authorization", "")
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else None
    identity = (
        await asyncio.to_thread(authenticate_operator_token, token)
        if token
        else None
    )
    request.state.operator_token_present = token is not None
    request.state.operator_email = identity["email"] if identity else None
    request.state.operator_id = identity["id"] if identity else None
    request.state.operator_token_id = identity["token_id"] if identity else None
    request.state.operator_role = str(identity.get("role", "superadmin")) if identity else None
    actor_token = control_plane_actor.set(str(identity["email"])) if identity else None
    role_token = control_plane_operator_role.set(str(identity.get("role", "superadmin"))) if identity else None
    try:
        return await call_next(request)
    finally:
        if actor_token is not None:
            control_plane_actor.reset(actor_token)
        if role_token is not None:
            control_plane_operator_role.reset(role_token)

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
app.include_router(create_operator_auth_router())

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
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"message": "Invalid JSON payload."}
        )
# -------------------------------------------------------------------------
# STATIC SPA MOUNTING & CONFINED FILE RESOLUTION (N-75)
# -------------------------------------------------------------------------
LOCAL_SPA_DIST = Path(__file__).resolve().parent.parent.parent / "onboarding-ui" / "dist"

def get_spa_dist_path() -> Path:
    env_dir = os.getenv("SPA_DIST_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    if os.path.exists("/app/onboarding-ui/dist"):
        return Path("/app/onboarding-ui/dist").resolve()
    return LOCAL_SPA_DIST.resolve()

SPA_DIST = str(get_spa_dist_path())

spa_path = get_spa_dist_path()
assets_dir = spa_path / "assets"
if assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

@app.get("/{full_path:path}", include_in_schema=False)
async def serve_react_spa(full_path: str):
    base_dir = get_spa_dist_path()
    index_file = base_dir / "index.html"

    # URL-decode and clean leading slashes/backslashes
    unquoted = urllib.parse.unquote(full_path or "").strip()
    clean_rel = unquoted.lstrip("/\\")

    # Strict Path Traversal Defense (N-75):
    # Reject empty path, parent traversal '..', Windows drive colons, null bytes, or absolute roots
    if (
        not clean_rel
        or ".." in clean_rel
        or "\0" in clean_rel
        or ":" in clean_rel
        or clean_rel.startswith(("/", "\\"))
    ):
        if index_file.is_file():
            return FileResponse(index_file)
        return HTMLResponse("<!doctype html><html><body><h1>Avinya Onboarding Portal</h1></body></html>", status_code=200)

    try:
        target = (base_dir / clean_rel).resolve()
    except Exception:
        if index_file.is_file():
            return FileResponse(index_file)
        return HTMLResponse("<!doctype html><html><body><h1>Avinya Onboarding Portal</h1></body></html>", status_code=200)

    # Strictly enforce boundary confinement: target MUST be inside base_dir
    try:
        is_safe = target.is_relative_to(base_dir)
    except AttributeError:
        is_safe = str(target).startswith(str(base_dir))

    if not is_safe:
        # Path traversal attempt escaped base_dir! Return fallback index or 404
        if index_file.is_file():
            return FileResponse(index_file)
        return HTMLResponse("<!doctype html><html><body><h1>Avinya Onboarding Portal</h1></body></html>", status_code=200)

    if target.is_file():
        return FileResponse(target)

    # Client-side SPA route fallback
    if index_file.is_file():
        return FileResponse(index_file)
    return HTMLResponse("<!doctype html><html><body><h1>Avinya Onboarding Portal</h1></body></html>", status_code=200)