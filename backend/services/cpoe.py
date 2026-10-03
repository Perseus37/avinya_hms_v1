"""Clinical safety checks for CPOE order validation.

This module prevents unsafe medication or treatment ordering when the patient
has an active allergy match in their recorded clinical history.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from models import AllergyMaster, AllergySeverityEnum, PatientAllergy

CROSS_REACTIVITY_GROUPS: list[set[str]] = [
    {"penicillin", "amoxicillin", "ampicillin", "piperacillin", "augmentin", "amoxil"},
    {"sulfa", "sulfonamide", "sulfamethoxazole", "trimethoprim-sulfamethoxazole", "bactrim", "septra"},
    {"nsaid", "aspirin", "ibuprofen", "naproxen", "diclofenac", "ketorolac", "celecoxib"},
    {"cephalosporin", "ceftriaxone", "cephalexin", "cefuroxime", "cefixime", "cefazolin"},
]


def _expand_keywords(raw_keywords: list[str]) -> set[str]:
    """Normalizes keywords to lowercase and expands known cross-reactive drug classes."""
    normalized = {kw.strip().lower() for kw in raw_keywords if kw and kw.strip()}
    expanded = set(normalized)
    for token in list(normalized):
        for group in CROSS_REACTIVITY_GROUPS:
            if any(member in token or token in member for member in group):
                expanded.update(group)
    return expanded


def check_allergy_hard_stop(session: Session, patient_id: int, allergen_keywords: list[str]) -> bool:
    """
    C4 FIX: Performs a case-insensitive, substring, and drug-class cross-reactivity
    check against a patient's active SEVERE or ANAPHYLACTIC allergies.
    """
    expanded_keywords = _expand_keywords(allergen_keywords)
    if not expanded_keywords:
        return True

    stmt = (
        select(PatientAllergy, AllergyMaster)
        .join(AllergyMaster, PatientAllergy.allergy_id == AllergyMaster.id)
        .where(
            PatientAllergy.patient_id == patient_id,
            PatientAllergy.is_active == True,
            PatientAllergy.severity.in_([AllergySeverityEnum.SEVERE, AllergySeverityEnum.ANAPHYLACTIC]),
        )
    )
    charted_allergies = session.execute(stmt).all()

    for patient_allergy, allergy_master in charted_allergies:
        charted_name = allergy_master.name.strip().lower()
        for kw in expanded_keywords:
            if kw in charted_name or charted_name in kw:
                reaction_text = patient_allergy.reaction_description or "Severe adverse reaction"
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"CLINICAL HARD-STOP: Patient has an active {patient_allergy.severity.value} "
                        f"allergy to '{allergy_master.name}' (Reaction: {reaction_text}). "
                        f"Order blocked."
                    ),
                )
    return True