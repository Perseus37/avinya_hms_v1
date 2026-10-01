import json
import logging
from sqlalchemy.orm import Session
from sqlalchemy import select
from models import (
    AllergyMaster, ChiefComplaintMaster, ComorbidityMaster,
    DiagnosisMaster, PhysicalExaminationMaster
)

logger = logging.getLogger("avinya.cache")

def get_department_taxonomy(redis_client, session: Session, taxonomy: str, department_id: int):
    """Return a department taxonomy payload from Redis cache or the database safely."""
    cache_key = f"taxonomy:{taxonomy}:{department_id}"
    
    # H10 FIX: Best-effort cache read. Do not crash if Redis is unavailable.
    try:
        if redis_client:
            cached_data = redis_client.get(cache_key)
            if cached_data:
                return json.loads(cached_data)
    except Exception as e:
        logger.warning("Redis cache read failed for %s: %s", cache_key, str(e))

    results = []
    if taxonomy == "allergies":
        stmt = select(AllergyMaster.id, AllergyMaster.code, AllergyMaster.name).where(
            AllergyMaster.department_id == department_id,
            AllergyMaster.is_active == True
        )
        results = [{"id": r.id, "code": r.code, "name": r.name} for r in session.execute(stmt)]
    elif taxonomy == "complaints":
        stmt = select(ChiefComplaintMaster.id, ChiefComplaintMaster.code, ChiefComplaintMaster.name).where(
            ChiefComplaintMaster.department_id == department_id,
            ChiefComplaintMaster.is_active == True
        )
        results = [{"id": r.id, "code": r.code, "name": r.name} for r in session.execute(stmt)]
    elif taxonomy == "comorbidities":
        stmt = select(ComorbidityMaster.id, ComorbidityMaster.code, ComorbidityMaster.name, ComorbidityMaster.icd10_code).where(
            ComorbidityMaster.department_id == department_id,
            ComorbidityMaster.is_active == True
        )
        results = [{"id": r.id, "code": r.code, "name": r.name, "icd10_code": r.icd10_code} for r in session.execute(stmt)]
    elif taxonomy == "diagnoses":
        stmt = select(DiagnosisMaster.id, DiagnosisMaster.icd10_code, DiagnosisMaster.short_description, DiagnosisMaster.full_description).where(
            DiagnosisMaster.department_id == department_id,
            DiagnosisMaster.is_active == True
        )
        results = [{"id": r.id, "icd10_code": r.icd10_code, "short_description": r.short_description, "full_description": r.full_description} for r in session.execute(stmt)]
    elif taxonomy == "physical_exams":
        stmt = select(PhysicalExaminationMaster.id, PhysicalExaminationMaster.code, PhysicalExaminationMaster.name, PhysicalExaminationMaster.body_system).where(
            PhysicalExaminationMaster.department_id == department_id,
            PhysicalExaminationMaster.is_active == True
        )
        results = [{"id": r.id, "code": r.code, "name": r.name, "body_system": r.body_system} for r in session.execute(stmt)]

    # H10 FIX: Only cache if results actually exist, and swallow Redis write errors gracefully
    if results:
        try:
            if redis_client:
                redis_client.setex(cache_key, 3600, json.dumps(results))
        except Exception as e:
            logger.warning("Redis cache write failed for %s: %s", cache_key, str(e))

    return results

def invalidate_department_taxonomy_cache(redis_client, taxonomy: str, department_id: int):
    """Remove stale cached taxonomy data after master record changes."""
    cache_key = f"taxonomy:{taxonomy}:{department_id}"
    try:
        if redis_client:
            redis_client.delete(cache_key)
    except Exception as e:
        logger.warning("Redis cache invalidation failed for %s: %s", cache_key, str(e))