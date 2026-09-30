import logging
import uuid
import time
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from routers.v1 import (
    ward_router
)
from routers.v1 import adt_router, auth_router, billing_router, clinical_router, diagnostic_router, opd_router, patient_router, pharmacy_router

# -----------------------------------------------------------------------------
# Structured Observability & Logging Context Filter
# -----------------------------------------------------------------------------
class RequestIdFilter(logging.Filter):
    """Safely ensures request_id always exists in the log record to prevent KeyErrors."""
    def filter(self, record):
        if not hasattr(record, "request_id"):
            record.request_id = "SYSTEM"
        return True

# Initialize logging base config
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] [ReqID: %(request_id)s] %(name)s %(message)s"
)

logger = logging.getLogger("avinya.api")
# Attach the safety filter to your app's logger
logger.addFilter(RequestIdFilter())

# -----------------------------------------------------------------------------
# Domain Composition & Versioning
# -----------------------------------------------------------------------------
app = FastAPI(title="Avinya Enterprise HMS API", version="1.0.0")

# API Versioning Sub-App
api_v1 = FastAPI()

api_v1.include_router(auth_router.router)
api_v1.include_router(ward_router.router)
api_v1.include_router(clinical_router.router)
api_v1.include_router(patient_router.router)
api_v1.include_router(diagnostic_router.router)
api_v1.include_router(adt_router.router)
api_v1.include_router(billing_router.router)
api_v1.include_router(opd_router.router)
api_v1.include_router(pharmacy_router.router)

# Mount the v1 router to the main app
app.mount("/v1", api_v1)

# -----------------------------------------------------------------------------
# Middleware Configuration
# -----------------------------------------------------------------------------
# Replaced BaseHTTPMiddleware with a pure ASGI middleware function style 
# to ensure compatibility with sub-mounted apps and prevent exception-handler bypasses.
@app.middleware("http")
async def request_tracing_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    
    start_time = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    
    logger.info(
        "HTTP %s %s completed in %sms with status %s",
        request.method,
        request.url.path,
        duration_ms,
        response.status_code,
        extra={"request_id": request_id}
    )
    
    response.headers["X-Request-ID"] = request_id
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"], 
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

# -----------------------------------------------------------------------------
# Global Exception Envelopes (Applied to both Main App and Sub-App)
# -----------------------------------------------------------------------------
def register_exception_handlers(application: FastAPI):
    @application.exception_handler(StarletteHTTPException)
    async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "error": {"code": exc.status_code, "message": exc.detail, "path": request.url.path}}
        )

    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"success": False, "error": {"code": 422, "message": "Request validation failed", "details": exc.errors()}}
        )

# Register handlers on both layers because FastAPI mounts isolate exception contexts
register_exception_handlers(app)
register_exception_handlers(api_v1)

# -----------------------------------------------------------------------------
# Base Core Endpoints
# -----------------------------------------------------------------------------
@app.get("/health", tags=["System"])
def health_check():
    return {"success": True, "status": "operational", "system": "Avinya HMS Core Backend"}
