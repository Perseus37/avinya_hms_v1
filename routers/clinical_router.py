from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db, get_redis
from dependencies import get_current_user
from schemas import AllergyCheckRequest, TaxonomyResponse
from services.taxonomy_cache import get_department_taxonomy
from services.cpoe import check_allergy_hard_stop

router = APIRouter(prefix="/api/clinical", tags=["Clinical"])

@router.get("/taxonomy/{taxonomy}/{department_id}", response_model=TaxonomyResponse)
def fetch_taxonomy(taxonomy: str, department_id: int, db: Session = Depends(get_db), redis=Depends(get_redis)):
    items = get_department_taxonomy(redis, db, taxonomy, department_id)
    return {"items": items}

@router.post("/orders/check-allergy")
def verify_prescription_safety(payload: AllergyCheckRequest, db: Session = Depends(get_db), _=Depends(get_current_user)):
    check_allergy_hard_stop(db, payload.patient_id, payload.allergen_keywords)
    return {"status": "safe", "message": "No drug-allergy interactions detected."}