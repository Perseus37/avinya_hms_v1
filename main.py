from fastapi import FastAPI
from routers import auth_router, ward_router, clinical_router, patient_router

app = FastAPI(title="Avinya Enterprise HMS API", version="1.0.0")

app.include_router(auth_router.router)
app.include_router(ward_router.router)
app.include_router(clinical_router.router)
app.include_router(patient_router.router)

@app.get("/health", tags=["System"])
def health_check():
    return {"status": "operational", "system": "Avinya HMS Core Backend"}