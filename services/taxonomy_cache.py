"""Department taxonomy caching helpers.

These utilities centralize the lookup patterns for department-specific master
records and cache them in Redis to reduce repeated database reads for common
clinical metadata such as diagnoses and allergy definitions.
"""

import json
from sqlalchemy.orm import Session
from sqlalchemy import select
from models import (
    AllergyMaster,
    ChiefComplaintMaster,
    ComorbidityMaster,
    DiagnosisMaster,
    PhysicalExaminationMaster,
)


def get_department_taxonomy(redis_client, session: Session, taxonomy: str, department_id: int):
    """Return a department taxonomy payload from Redis cache or the database."""
    cache_key = f"taxonomy:{taxonomy}:{department_id}"
    cached_data = redis_client.get(cache_key)

    if cached_data:
        return json.loads(cached_data)

    results = []
    if taxonomy == "allergies":
        stmt = select(AllergyMaster.id, AllergyMaster.code, AllergyMaster.name).where(
            AllergyMaster.department_id == department_id,
            AllergyMaster.is_active == True,
        )
        results = [{"id": r.id, "code": r.code, "name": r.name} for r in session.execute(stmt)]
    elif taxonomy == "complaints":
        stmt = select(ChiefComplaintMaster.id, ChiefComplaintMaster.code, ChiefComplaintMaster.name).where(
            ChiefComplaintMaster.department_id == department_id,
            ChiefComplaintMaster.is_active == True,
        )
        results = [{"id": r.id, "code": r.code, "name": r.name} for r in session.execute(stmt)]
    elif taxonomy == "comorbidities":
        stmt = select(ComorbidityMaster.id, ComorbidityMaster.code, ComorbidityMaster.name, ComorbidityMaster.icd10_code).where(
            ComorbidityMaster.department_id == department_id,
            ComorbidityMaster.is_active == True,
        )
        results = [{"id": r.id, "code": r.code, "name": r.name, "icd10_code": r.icd10_code} for r in session.execute(stmt)]
    elif taxonomy == "diagnoses":
        stmt = select(DiagnosisMaster.id, DiagnosisMaster.icd10_code, DiagnosisMaster.short_description, DiagnosisMaster.full_description).where(
            DiagnosisMaster.department_id == department_id,
            DiagnosisMaster.is_active == True,
        )
        results = [{"id": r.id, "icd10_code": r.icd10_code, "short_description": r.short_description, "full_description": r.full_description} for r in session.execute(stmt)]
    elif taxonomy == "physical_exams":
        stmt = select(PhysicalExaminationMaster.id, PhysicalExaminationMaster.code, PhysicalExaminationMaster.name, PhysicalExaminationMaster.body_system).where(
            PhysicalExaminationMaster.department_id == department_id,
            PhysicalExaminationMaster.is_active == True,
        )
        results = [{"id": r.id, "code": r.code, "name": r.name, "body_system": r.body_system} for r in session.execute(stmt)]

    if results:
        redis_client.setex(cache_key, 3600, json.dumps(results))
    return results


def invalidate_department_taxonomy_cache(redis_client, taxonomy: str, department_id: int):
    """Remove stale cached taxonomy data after master record changes."""
    cache_key = f"taxonomy:{taxonomy}:{department_id}"
    redis_client.delete(cache_key)