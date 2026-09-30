"""Application entry point for the Avinya HMS backend.

This file acts as the composition root for the healthcare system. The API is
organized by business domains rather than by action type, so identity,
operations, patient, and clinical logic remain easy to navigate.
"""

import logging
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from routers import auth_router, ward_router, clinical_router, patient_router, diagnostic_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("avinya.api")

# -----------------------------------------------------------------------------
# Domain composition
# -----------------------------------------------------------------------------
app = FastAPI(title="Avinya Enterprise HMS API", version="1.0.0")

# --- GLOBAL EXCEPTION HANDLERS (Standard Error Envelope) ---
# -----------------------------------------------------------------------------
# Global exception handlers ensure that all errors are returned in a consistent
# format. This is important for clients that need to parse error responses and
# -----------------------------------------------------------------------------
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": exc.status_code,
                "message": exc.detail,
                "path": request.url.path
            }
        },
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": {
                "code": 422,
                "message": "Request validation failed",
                "details": exc.errors(),
                "path": request.url.path
            }
        },
    )

# Identity and access domain
app.include_router(auth_router.router)

# Operations and facility domain
app.include_router(ward_router.router)

# Clinical and patient care domain
app.include_router(clinical_router.router)
app.include_router(patient_router.router)
app.include_router(diagnostic_router.router)

@app.middleware("http")
async def log_request_details(request: Request, call_next):
    """Log request metadata and latency for API observability."""
    start = perf_counter()
    response = await call_next(request)
    duration_ms = round((perf_counter() - start) * 1000, 2)
    logger.info(
        "HTTP %s %s completed in %sms with status %s",
        request.method,
        request.url.path,
        duration_ms,
        response.status_code,
    )
    return response


@app.get("/health", tags=["System"])
def health_check():
    """Simple readiness endpoint for monitoring and local smoke tests."""
    return {"success": True, "status": "operational", "system": "Avinya HMS Core Backend"}
