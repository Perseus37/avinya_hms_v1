from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from database import get_db, get_redis
from dependencies import get_current_user, RequirePermission
from schemas import AllergyCheckRequest, TaxonomyResponse, AllergyMasterCreate
from services.taxonomy_cache import get_department_taxonomy, invalidate_department_taxonomy_cache
from services.cpoe import check_allergy_hard_stop
from models import AllergyMaster

router = APIRouter(prefix="/api/clinical", tags=["Clinical Core & EMR"])

@router.post("/allergies", status_code=status.HTTP_201_CREATED)
def create_allergy_master(payload: AllergyMasterCreate, db: Session = Depends(get_db), redis=Depends(get_redis), _: bool = Depends(RequirePermission("manage_clinical_mdm"))):
    allergy = AllergyMaster(
        department_id=payload.department_id,
        code=payload.code,
        name=payload.name,
        category=payload.category,
        default_severity=payload.default_severity,
        is_active=True
    )
    db.add(allergy)
    db.commit()
    invalidate_department_taxonomy_cache(redis, "allergies", payload.department_id)
    return {"status": "success", "message": "Allergy master created and cache busted."}

@router.get("/taxonomy/{taxonomy}/{department_id}", response_model=TaxonomyResponse)
def fetch_taxonomy(taxonomy: str, department_id: int, db: Session = Depends(get_db), redis=Depends(get_redis)):
    items = get_department_taxonomy(redis, db, taxonomy, department_id)
    return {"items": items}

@router.post("/orders/check-allergy")
def verify_prescription_safety(payload: AllergyCheckRequest, db: Session = Depends(get_db), _=Depends(get_current_user)):
    check_allergy_hard_stop(db, payload.patient_id, payload.allergen_keywords)
    return {"status": "safe", "message": "CPOE verification passed: No clinical allergy conflicts detected."}