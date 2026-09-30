import logging
import uuid
import time
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from routers import (
    auth_router, ward_router, clinical_router, patient_router, 
    diagnostic_router, adt_router, billing_router, opd_router, pharmacy_router
)

# -----------------------------------------------------------------------------
# Structured Observability
# -----------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [ReqID: %(request_id)s] %(name)s %(message)s")
logger = logging.getLogger("avinya.api")

class RequestTracingMiddleware(BaseHTTPMiddleware):
    """Injects a unique Correlation ID into every request for distributed tracing."""
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        # Temporarily inject into request state for logging context
        request.state.request_id = request_id
        
        start_time = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        
        # Log the completed request with the correlation ID
        logger.info(
            "HTTP %s %s completed in %sms with status %s",
            request.method,
            request.url.path,
            duration_ms,
            response.status_code,
            extra={"request_id": request_id}
        )
        
        # Return the ID to the client so frontend teams can report it in bug tickets
        response.headers["X-Request-ID"] = request_id
        return response

# -----------------------------------------------------------------------------
# Domain Composition & Versioning
# -----------------------------------------------------------------------------
app = FastAPI(title="Avinya Enterprise HMS API", version="1.0.0")

# Mount Middleware
app.add_middleware(RequestTracingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://portal.avinyahms.com", "http://localhost:3000"], # Strictly define origins
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

# Exception Envelopes
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": {"code": exc.status_code, "message": exc.detail, "path": request.url.path}}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"success": False, "error": {"code": 422, "message": "Request validation failed", "details": exc.errors()}}
    )

# API Versioning Prefix
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

@app.get("/health", tags=["System"])
def health_check():
    return {"success": True, "status": "operational", "system": "Avinya HMS Core Backend"}