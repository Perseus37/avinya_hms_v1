import json
import logging
from sqlalchemy import select
from sqlalchemy.orm import Session
from models import (
    AllergyMaster,
    ChiefComplaintMaster,
    ComorbidityMaster,
    DiagnosisMaster,
    PhysicalExaminationMaster,
)

logger = logging.getLogger("avinya.cache")


def get_department_taxonomy(
    redis_client,
    session: Session,
    taxonomy: str,
    department_id: int,
    tenant_slug: str = "default",
):
    """Returns a tenant-namespaced department taxonomy payload from Redis or PostgreSQL."""
    cache_key = f"{tenant_slug}:taxonomy:{taxonomy}:{department_id}"

    try:
        if redis_client:
            cached_data = redis_client.get(cache_key)
            if cached_data:
                return json.loads(cached_data)
    except Exception as e:
        logger.warning("Redis read failed for %s: %s", cache_key, str(e))

    results = []
    if taxonomy == "allergies":
        stmt = select(AllergyMaster.id, AllergyMaster.code, AllergyMaster.name).where(
            AllergyMaster.department_id == department_id, AllergyMaster.is_active == True
        )
        results = [{"id": r.id, "code": r.code, "name": r.name} for r in session.execute(stmt)]
    elif taxonomy == "complaints":
        stmt = select(ChiefComplaintMaster.id, ChiefComplaintMaster.code, ChiefComplaintMaster.name).where(
            ChiefComplaintMaster.department_id == department_id, ChiefComplaintMaster.is_active == True
        )
        results = [{"id": r.id, "code": r.code, "name": r.name} for r in session.execute(stmt)]
    elif taxonomy == "comorbidities":
        stmt = select(
            ComorbidityMaster.id, ComorbidityMaster.code, ComorbidityMaster.name, ComorbidityMaster.icd10_code
        ).where(ComorbidityMaster.department_id == department_id, ComorbidityMaster.is_active == True)
        results = [{"id": r.id, "code": r.code, "name": r.name, "icd10_code": r.icd10_code} for r in session.execute(stmt)]
    elif taxonomy == "diagnoses":
        stmt = select(
            DiagnosisMaster.id, DiagnosisMaster.icd10_code, DiagnosisMaster.short_description, DiagnosisMaster.full_description
        ).where(DiagnosisMaster.department_id == department_id, DiagnosisMaster.is_active == True)
        results = [
            {"id": r.id, "icd10_code": r.icd10_code, "short_description": r.short_description, "full_description": r.full_description}
            for r in session.execute(stmt)
        ]
    elif taxonomy == "physical_exams":
        stmt = select(
            PhysicalExaminationMaster.id, PhysicalExaminationMaster.code, PhysicalExaminationMaster.name, PhysicalExaminationMaster.body_system
        ).where(PhysicalExaminationMaster.department_id == department_id, PhysicalExaminationMaster.is_active == True)
        results = [{"id": r.id, "code": r.code, "name": r.name, "body_system": r.body_system} for r in session.execute(stmt)]

    if results:
        try:
            if redis_client:
                redis_client.set(cache_key, json.dumps(results), ex=3600)
        except Exception as e:
            logger.warning("Redis write failed for %s: %s", cache_key, str(e))

    return results


def invalidate_department_taxonomy_cache(
    redis_client,
    taxonomy: str,
    department_id: int,
    tenant_slug: str = "default",
):
    """Busts only the requesting hospital tenant's taxonomy cache."""
    cache_key = f"{tenant_slug}:taxonomy:{taxonomy}:{department_id}"
    try:
        if redis_client:
            redis_client.delete(cache_key)
    except Exception as e:
        logger.warning("Redis invalidation failed for %s: %s", cache_key, str(e))