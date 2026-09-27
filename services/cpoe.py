"""Clinical safety checks for CPOE order validation.

This module prevents unsafe medication or treatment ordering when the patient has
an active allergy match in their recorded clinical history.
"""

from sqlalchemy.orm import Session
from sqlalchemy import select
from models import PatientAllergy, AllergyMaster
from fastapi import HTTPException


def check_allergy_hard_stop(session: Session, patient_id: int, allergen_keywords: list[str]):
    """Queries PatientAllergies and blocks CPOE order if a keyword matches."""
    stmt = (
        select(AllergyMaster.name)
        .join(PatientAllergy, PatientAllergy.allergy_id == AllergyMaster.id)
        .where(PatientAllergy.patient_id == patient_id)
        .where(PatientAllergy.is_active == True)
        .where(AllergyMaster.name.in_(allergen_keywords))
    )
    conflicts = session.execute(stmt).scalars().all()
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail=f"CPOE SAFETY HARD STOP: Patient has active recorded allergy to {', '.join(conflicts)}",
        )
    return True