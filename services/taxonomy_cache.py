import json
from sqlalchemy.orm import Session
from sqlalchemy import select
from models import AllergyMaster

def get_department_taxonomy(redis_client, session: Session, taxonomy: str, department_id: int):
    cache_key = f"taxonomy:{taxonomy}:{department_id}"
    cached_data = redis_client.get(cache_key)
    
    if cached_data:
        return json.loads(cached_data)
        
    if taxonomy == "allergies":
        stmt = select(AllergyMaster.id, AllergyMaster.name).where(AllergyMaster.is_active == True)
        results = [{"id": row.id, "name": row.name} for row in session.execute(stmt)]
        redis_client.setex(cache_key, 3600, json.dumps(results))
        return results
        
    return []

def invalidate_department_taxonomy_cache(redis_client, taxonomy: str, department_id: int):
    cache_key = f"taxonomy:{taxonomy}:{department_id}"
    redis_client.delete(cache_key)