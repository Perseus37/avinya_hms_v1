"""Application entry point for the Avinya HMS backend.

This file acts as the composition root for the healthcare system. The API is
organized by business domains rather than by action type, so identity,
operations, patient, and clinical logic remain easy to navigate.
"""

from fastapi import FastAPI

from routers import auth_router, ward_router, clinical_router, patient_router

# -----------------------------------------------------------------------------
# Domain composition
# -----------------------------------------------------------------------------
app = FastAPI(title="Avinya Enterprise HMS API", version="1.0.0")

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
    return {"status": "operational", "system": "Avinya HMS Core Backend"}