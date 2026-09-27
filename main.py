"""Application entry point for the Avinya HMS backend.

This file acts as the composition root for the healthcare system. The API is
organized by business domains rather than by action type, so identity,
operations, patient, and clinical logic remain easy to navigate.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from routers import auth_router, ward_router, clinical_router, patient_router

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


@app.get("/health", tags=["System"])
def health_check():
    """Simple readiness endpoint for monitoring and local smoke tests."""
    return {"success": True, "status": "operational", "system": "Avinya HMS Core Backend"}