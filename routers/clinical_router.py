from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from database import get_db, get_redis
from dependencies import get_current_user, RequirePermission
from schemas import AllergyCheckRequest, TaxonomyResponse, AllergyCreate
from services.taxonomy_cache import get_department_taxonomy, invalidate_department_taxonomy_cache
from services.cpoe import check_allergy_hard_stop
from models import AllergyMaster

router = APIRouter(prefix="/api/clinical", tags=["Clinical Core & EMR"])

@router.post("/allergies", status_code=status.HTTP_201_CREATED)
def create_allergy_master(payload: AllergyCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("can_access_patients"))):
    allergy = AllergyMaster(name=payload.name, is_active=True)
    db.add(allergy)
    db.commit()
    
    # Invalidate cache so subsequent reads pull fresh data from DB
    invalidate_department_taxonomy_cache(redis, "allergies", payload.department_id)
    return {"status": "success", "message": "Allergy master updated and cache invalidated."}

@router.get("/taxonomy/{taxonomy}/{department_id}", response_model=TaxonomyResponse)
def fetch_taxonomy(taxonomy: str, department_id: int, db: Session = Depends(get_db), redis=Depends(get_redis)):
    items = get_department_taxonomy(redis, db, taxonomy, department_id)
    return {"items": items}

@router.post("/orders/check-allergy")
def verify_prescription_safety(payload: AllergyCheckRequest, db: Session = Depends(get_db), _=Depends(get_current_user)):
    check_allergy_hard_stop(db, payload.patient_id, payload.allergen_keywords)
    return {"status": "safe", "message": "No drug-allergy interactions detected."}