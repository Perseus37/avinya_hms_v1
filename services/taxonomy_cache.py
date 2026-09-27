import json
from sqlalchemy.orm import Session
from sqlalchemy import select
from models import AllergyMaster, ChiefComplaintMaster, ComorbidityMaster

def get_department_taxonomy(redis_client, session: Session, taxonomy: str, department_id: int):
    cache_key = f"taxonomy:{taxonomy}:{department_id}"
    cached_data = redis_client.get(cache_key)
    
    if cached_data:
        return json.loads(cached_data)
        
    results = []
    if taxonomy == "allergies":
        stmt = select(AllergyMaster.id, AllergyMaster.code, AllergyMaster.name).where(AllergyMaster.department_id == department_id, AllergyMaster.is_active == True)
        results = [{"id": r.id, "code": r.code, "name": r.name} for r in session.execute(stmt)]
    elif taxonomy == "complaints":
        stmt = select(ChiefComplaintMaster.id, ChiefComplaintMaster.code, ChiefComplaintMaster.name).where(ChiefComplaintMaster.department_id == department_id, ChiefComplaintMaster.is_active == True)
        results = [{"id": r.id, "code": r.code, "name": r.name} for r in session.execute(stmt)]
        
    if results:
        redis_client.setex(cache_key, 3600, json.dumps(results))
    return results

def invalidate_department_taxonomy_cache(redis_client, taxonomy: str, department_id: int):
    cache_key = f"taxonomy:{taxonomy}:{department_id}"
    redis_client.delete(cache_key)