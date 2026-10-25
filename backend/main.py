from contextlib import asynccontextmanager
from backend.security.envelope import _get_master_key
import contextvars
import logging
import re
import secrets
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST
from backend.core.metrics import (
    HTTP_REQUESTS_TOTAL,
    HTTP_REQUEST_DURATION_SECONDS,
    get_prometheus_metrics_bytes,
    normalize_metric_path,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db, get_redis
from backend.domains.encounters import adt_router, opd_router
from backend.domains.identity import auth_router
from backend.domains.billing import router as billing_router
from backend.domains.clinical import (
    router as clinical_router,
    nursing_er_router,
    vital_registries_router,
)
from backend.domains.diagnostics import router as diagnostic_router
from backend.domains.federation import router as federation_router
from backend.domains.fhir import router as fhir_router
from backend.domains.patients import router as patient_router
from backend.domains.patients.break_glass_router import router as break_glass_router
from backend.domains.pharmacy import router as pharmacy_router
from backend.domains.facility import router as ward_router
from backend.domains.reports import router as reports_router
from backend.domains.insurance import router as insurance_router
from backend.domains.surgery import router as ot_router
from backend.domains.bloodbank import router as blood_pacs_router
from backend.domains.analytics import router as analytics_router
from backend.domains.policies import router as tenant_admin_router
from backend.domains.finance import router as finance_ap_router
from backend.domains.integrations import router as equipment_router
from backend.domains.realtime import router as websocket_router
from backend.core.dependencies import RequireLicensedModule
from backend.domains.procurement import router as procurement_router
from backend.domains.service_ops import router as service_ops_router
from backend.domains.privacy import router as privacy_router
from backend.domains.workforce import router as workforce_router
from backend.domains.audit import router as audit_router

# -----------------------------------------------------------------------------
# M1 FIX: Context-Safe Request ID Logging Across All Libraries
# -----------------------------------------------------------------------------
request_id_ctx_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="SYSTEM")
SAFE_REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9\-_]{1,64}$")


class RequestIDFilter(logging.Filter):
    """Injects the current context's request_id into EVERY log record globally."""
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx_var.get("SYSTEM")
        return True


log_handler = logging.StreamHandler()
log_handler.addFilter(RequestIDFilter())
log_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(levelname)s | [ReqID: %(request_id)s] | %(name)s | %(message)s")
)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers = [log_handler]

logger = logging.getLogger("avinya.api")


# -----------------------------------------------------------------------------
# H11 FIX: Unified Exception Handlers (Registered on Parent and Mounted v1 App)
# -----------------------------------------------------------------------------
def register_exception_handlers(target_app: FastAPI) -> None:
    @target_app.exception_handler(HTTPException)
    async def custom_http_exception_handler(request: Request, exc: HTTPException):
        req_id = request_id_ctx_var.get("UNKNOWN")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": True,
                "code": f"ERR_{exc.status_code}",
                "message": exc.detail,
                "request_id": req_id,
            },
        )

    @target_app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        req_id = request_id_ctx_var.get("UNKNOWN")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": True,
                "code": "ERR_VALIDATION_FAILED",
                "message": "Payload validation failed. Check 'details' for specific field errors.",
                "details": jsonable_encoder(
                    exc.errors(), custom_encoder={Exception: str}
                ),
                "request_id": req_id,
            },
        )

    @target_app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError):
        req_id = request_id_ctx_var.get("UNKNOWN")
        orig_msg = str(exc.orig).lower() if exc.orig else str(exc).lower()
        logger.warning("Database IntegrityError intercepted: %s", orig_msg)

        if "unique" in orig_msg or "duplicate key" in orig_msg:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "error": True,
                    "code": "ERR_CONFLICT",
                    "message": "A record with the provided unique identifier already exists.",
                    "request_id": req_id,
                },
            )
        if "foreign key" in orig_msg:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error": True,
                    "code": "ERR_INVALID_REFERENCE",
                    "message": "One or more referenced entity IDs do not exist or cannot be removed while in use.",
                    "request_id": req_id,
                },
            )
        if "check constraint" in orig_msg or "violates check" in orig_msg:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error": True,
                    "code": "ERR_CONSTRAINT_VIOLATION",
                    "message": "Operation violates database domain or numeric constraints.",
                    "request_id": req_id,
                },
            )

        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": True,
                "code": "ERR_DATABASE_INTEGRITY",
                "message": "Database integrity constraint violated.",
                "request_id": req_id,
            },
        )

    @target_app.exception_handler(OperationalError)
    async def operational_error_handler(request: Request, exc: OperationalError):
        req_id = request_id_ctx_var.get("UNKNOWN")
        orig_msg = str(exc.orig).lower() if exc.orig else str(exc).lower()
        logger.error("Database OperationalError intercepted: %s", orig_msg)

        if "could not obtain lock" in orig_msg or "lock_not_available" in orig_msg:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "error": True,
                    "code": "ERR_RESOURCE_LOCKED",
                    "message": "Resource is currently locked by another concurrent transaction. Please retry.",
                    "request_id": req_id,
                },
            )

        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": True,
                "code": "ERR_DB_UNAVAILABLE",
                "message": "Database operational error occurred.",
                "request_id": req_id,
            },
        )

    @target_app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        req_id = request_id_ctx_var.get("UNKNOWN")
        logger.exception("Unhandled server exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": True,
                "code": "ERR_INTERNAL_SERVER",
                "message": "An unexpected internal server error occurred.",
                "request_id": req_id,
            },
        )


# -----------------------------------------------------------------------------
# Application Assembly
# -----------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # N-10: Eagerly validate cryptographic master key and salt on application startup
    _get_master_key()
    yield


is_prod_docs_disabled = (
    str(settings.ENVIRONMENT).lower() in {"production", "prod"}
    and not getattr(settings, "ENABLE_PRODUCTION_API_DOCS", False)
)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Enterprise-grade, ACID-compliant Hospital Management System Backend.",
    lifespan=lifespan,
    docs_url=None if is_prod_docs_disabled else "/docs",
    redoc_url=None if is_prod_docs_disabled else "/redoc",
    openapi_url=None if is_prod_docs_disabled else "/openapi.json",
)

v1_app = FastAPI(
    title=f"{settings.PROJECT_NAME} - V1",
    version="1.0.0",
    docs_url=None if is_prod_docs_disabled else "/docs",
    redoc_url=None if is_prod_docs_disabled else "/redoc",
    openapi_url=None if is_prod_docs_disabled else "/openapi.json",
)

register_exception_handlers(app)
register_exception_handlers(v1_app)

cors_origins = [
    origin.strip()
    for origin in settings.CORS_ORIGINS.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Request-ID",
        "Idempotency-Key",
        "X-Federation-API-Key",
        "X-Hospital-Subdomain",
        "X-Master-Setup-Key",
        "X-Control-Plane-Secret",
    ],
)


@app.middleware("http")
async def request_correlation_middleware(request: Request, call_next):
    client_req_id = request.headers.get("X-Request-ID", "")
    if client_req_id and SAFE_REQUEST_ID_PATTERN.match(client_req_id):
        req_id = client_req_id
    else:
        req_id = str(uuid.uuid4())

    token = request_id_ctx_var.set(req_id)
    request.state.request_id = req_id
    start_time = time.time()

    logger.info("Started %s %s", request.method, request.url.path)
    try:
        response = await call_next(request)
        latency_sec = time.time() - start_time
        process_time = latency_sec * 1000
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Process-Time-Ms"] = f"{process_time:.2f}"
        logger.info("Completed %s %s - Status: %s in %.2fms", request.method, request.url.path, response.status_code, process_time)

        try:
            norm_path = normalize_metric_path(request.url.path)
            HTTP_REQUESTS_TOTAL.labels(
                method=request.method,
                endpoint=norm_path,
                status=str(response.status_code),
            ).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(
                method=request.method,
                endpoint=norm_path,
            ).observe(latency_sec)
        except Exception:
            pass

        return response
    finally:
        request_id_ctx_var.reset(token)


# Bind all V1 routers on both v1_app (/v1/api/...) and root app (/api/...) with modular license guards
for target in (v1_app, app):
    # Core identity, MPI, print engine, websockets, and admin policies are always enabled for every tenant
    target.include_router(auth_router.router)
    target.include_router(patient_router.router)
    target.include_router(break_glass_router)
    target.include_router(fhir_router.router)
    target.include_router(clinical_router.router)
    target.include_router(federation_router.router)
    target.include_router(reports_router.router)
    target.include_router(websocket_router.ws_router)
    target.include_router(tenant_admin_router.router)
    target.include_router(audit_router.router)

    # Modularly licensed clinical, surgical, diagnostic & financial domains
    target.include_router(opd_router.router, dependencies=[Depends(RequireLicensedModule("opd"))])
    target.include_router(ward_router.router, dependencies=[Depends(RequireLicensedModule("ipd_adt"))])
    target.include_router(adt_router.router, dependencies=[Depends(RequireLicensedModule("ipd_adt"))])
    target.include_router(vital_registries_router.router, dependencies=[Depends(RequireLicensedModule("ipd_adt"))])
    target.include_router(pharmacy_router.router, dependencies=[Depends(RequireLicensedModule("pharmacy"))])
    target.include_router(diagnostic_router.router, dependencies=[Depends(RequireLicensedModule("diagnostics"))])
    target.include_router(billing_router.router, dependencies=[Depends(RequireLicensedModule("billing"))])
    target.include_router(insurance_router.router, dependencies=[Depends(RequireLicensedModule("insurance_tpa"))])
    target.include_router(ot_router.router, dependencies=[Depends(RequireLicensedModule("ot_surgery"))])
    target.include_router(blood_pacs_router.router, dependencies=[Depends(RequireLicensedModule("blood_bank"))])
    target.include_router(nursing_er_router.router, dependencies=[Depends(RequireLicensedModule("er_mlc"))])
    target.include_router(analytics_router.router, dependencies=[Depends(RequireLicensedModule("executive_bi"))])
    target.include_router(finance_ap_router.router, dependencies=[Depends(RequireLicensedModule("executive_bi"))])
    target.include_router(equipment_router.router, dependencies=[Depends(RequireLicensedModule("diagnostics"))])
    target.include_router(procurement_router.router, dependencies=[Depends(RequireLicensedModule("procurement"))])
    target.include_router(
        service_ops_router.ambulance_router,
        dependencies=[Depends(RequireLicensedModule("ambulance"))],
    )
    target.include_router(
        service_ops_router.dietary_router,
        dependencies=[Depends(RequireLicensedModule("dietary"))],
    )
    target.include_router(
        service_ops_router.cssd_router,
        dependencies=[Depends(RequireLicensedModule("cssd"))],
    )
    target.include_router(
        service_ops_router.mortuary_router,
        dependencies=[Depends(RequireLicensedModule("mortuary"))],
    )
    target.include_router(service_ops_router.bmw_router)
    target.include_router(privacy_router)
    target.include_router(
        workforce_router,
        dependencies=[Depends(RequireLicensedModule("workforce"))],
    )
    
app.mount("/v1", v1_app)


@app.get("/", include_in_schema=False)
def api_home():
    """Give direct tenant URLs a useful local/prod landing page."""
    return RedirectResponse(url="/docs")

# -----------------------------------------------------------------------------
# M11 FIX: Liveness & Deep Readiness Health Probes
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# M-13: Prometheus Telemetry and Metrics Exposition
# -----------------------------------------------------------------------------
@app.get("/metrics", include_in_schema=False)
@v1_app.get("/metrics", include_in_schema=False)
def prometheus_metrics(request: Request):
    """Exposes application and runtime metrics in Prometheus text format."""
    required_token = settings.METRICS_SCRAPE_TOKEN
    if required_token:
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()
        elif request.headers.get("X-Metrics-Token"):
            token = request.headers.get("X-Metrics-Token", "").strip()

        if not token or not secrets.compare_digest(token, required_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: valid metrics scrape token required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    elif str(settings.ENVIRONMENT).lower() in {"production", "prod"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: METRICS_SCRAPE_TOKEN must be configured in production environments.",
        )
    return Response(content=get_prometheus_metrics_bytes(), media_type=CONTENT_TYPE_LATEST)

@app.get("/health", tags=["System Health"])
def liveness_check():
    """Lightweight liveness probe for container orchestrators."""
    return {"success": True, "status": "healthy", "version": settings.VERSION}


@app.get("/health/ready", tags=["System Health"])
def readiness_check(db: Session = Depends(get_db), redis_client=Depends(get_redis)):
    """Deep readiness probe verifying PostgreSQL and Redis connectivity."""
    db_ok = False
    redis_ok = False

    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:
        logger.error("Readiness check failed on PostgreSQL: %s", str(e))

    try:
        if redis_client and redis_client.ping():
            redis_ok = True
    except Exception as e:
        logger.warning("Readiness check warning on Redis: %s", str(e))

    if not db_ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connectivity check failed.",
        )

    return {
        "success": True,
        "status": "ready",
        "database": "connected" if db_ok else "unreachable",
        "redis_cache": "connected" if redis_ok else "degraded",
    }