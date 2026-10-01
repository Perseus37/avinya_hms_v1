import contextvars
import logging
import re
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from config import settings
from database import get_db, get_redis
from routers.v1 import (
    adt_router,
    auth_router,
    billing_router,
    clinical_router,
    diagnostic_router,
    federation_router,
    opd_router,
    patient_router,
    pharmacy_router,
    ward_router,
)

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


# Attach the filter directly to the root StreamHandler so SQLAlchemy, Redis,
# Uvicorn, and all application modules never fail on %(request_id)s
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": True,
                "code": "ERR_VALIDATION_FAILED",
                "message": "Payload validation failed. Check 'details' for specific field errors.",
                "details": exc.errors(),
                "request_id": req_id,
            },
        )

    @target_app.exception_handler(IntegrityError)
    async def integrity_error_handler(request: Request, exc: IntegrityError):
        """H11 FIX: Converts database constraint violations into clean 409/400 responses."""
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
        """Catches NOWAIT row-lock failures that occur outside explicit try/except blocks."""
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
        """H11 FIX: Catch-all 500 handler so stack traces never leak to clients."""
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
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Enterprise-grade, ACID-compliant Hospital Management System Backend.",
)

v1_app = FastAPI(
    title=f"{settings.PROJECT_NAME} - V1",
    version="1.0.0",
)

register_exception_handlers(app)
register_exception_handlers(v1_app)

# M11 FIX: Allow origins to be configured via comma-separated CORS_ORIGINS env var if present
cors_origins = [
    origin.strip()
    for origin in getattr(settings, "CORS_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key","X-Federation-API-Key"],
)


@app.middleware("http")
async def request_correlation_middleware(request: Request, call_next):
    # M1 FIX: Validate client-supplied X-Request-ID against log injection / oversized payloads
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
        process_time = (time.time() - start_time) * 1000
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Process-Time-Ms"] = f"{process_time:.2f}"
        logger.info("Completed %s %s - Status: %s in %.2fms", request.method, request.url.path, response.status_code, process_time)
        return response
    finally:
        request_id_ctx_var.reset(token)


# Bind all V1 routers
v1_app.include_router(auth_router.router)
v1_app.include_router(ward_router.router)
v1_app.include_router(clinical_router.router)
v1_app.include_router(patient_router.router)
v1_app.include_router(diagnostic_router.router)
v1_app.include_router(federation_router.router)
v1_app.include_router(adt_router.router)
v1_app.include_router(pharmacy_router.router)
v1_app.include_router(billing_router.router)
v1_app.include_router(opd_router.router)

app.mount("/v1", v1_app)


# -----------------------------------------------------------------------------
# M11 FIX: Liveness & Deep Readiness Health Probes
# -----------------------------------------------------------------------------
@app.get("/health", tags=["System Health"])
def liveness_check():
    """Lightweight liveness probe for container orchestrators."""
    return {"status": "healthy", "version": settings.VERSION}


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
        "status": "ready",
        "database": "connected" if db_ok else "unreachable",
        "redis_cache": "connected" if redis_ok else "degraded",
    }