from fastapi import FastAPI
from routers import auth_router, ward_router, clinical_router

app = FastAPI(title="Avinya HMS API", version="1.0.0")

app.include_router(auth_router.router)
app.include_router(ward_router.router)
app.include_router(clinical_router.router)

@app.get("/health")
def health_check():
    return {"status": "operational"}