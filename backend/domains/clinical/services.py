"""Clinical safety checks for CPOE order validation.

This module provides:
1. Allergy hard-stop verification against active patient allergies and cross-reactive drug classes.
2. Drug-Drug Interaction (DDI) detection (Contraindicated Hard-Stops, Critical warnings, and Significant interactions).
3. Duplicate therapy checking across new orders and active prescriptions.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Iterable, List, Optional, Set

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domains.patients.models import (
    AllergyMaster,
    AllergySeverityEnum,
    PatientAllergy,
)

logger = logging.getLogger("avinya.clinical.safety")

# -----------------------------------------------------------------------------
# Allergy Cross-Reactivity Groups
# -----------------------------------------------------------------------------
CROSS_REACTIVITY_GROUPS: list[set[str]] = [
    {"penicillin", "amoxicillin", "ampicillin", "piperacillin", "augmentin", "amoxil"},
    {"sulfa", "sulfonamide", "sulfamethoxazole", "trimethoprim-sulfamethoxazole", "bactrim", "septra"},
    {"nsaid", "aspirin", "ibuprofen", "naproxen", "diclofenac", "ketorolac", "celecoxib", "aceclofenac"},
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
    Performs case-insensitive, substring, and drug-class cross-reactivity
    checks against a patient's active SEVERE or ANAPHYLACTIC allergies.
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


# -----------------------------------------------------------------------------
# Drug-Drug Interaction (DDI) & Duplicate Therapy Rules
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class InteractionRule:
    class_a: frozenset[str]
    class_b: frozenset[str]
    severity: str  # "CRITICAL_HARD_STOP", "CRITICAL", "WARNING"
    description: str
    clinical_risk: str


CLINICAL_DRUG_CLASSES = {
    "NITRATES": frozenset({
        "nitroglycerin", "isosorbide dinitrate", "isosorbide mononitrate", "isosorbide", "sorbitrate", "nitrostat"
    }),
    "PDE5_INHIBITORS": frozenset({
        "sildenafil", "tadalafil", "vardenafil", "avanafil", "viagra", "cialis"
    }),
    "POTASSIUM_SPARING": frozenset({
        "spironolactone", "eplerenone", "triamterene", "amiloride", "aldactone"
    }),
    "POTASSIUM_SUPPLEMENTS": frozenset({
        "potassium chloride", "potassium citrate", "potklor", "k-cl"
    }),
    "ANTICOAGULANTS": frozenset({
        "warfarin", "heparin", "enoxaparin", "rivaroxaban", "apixaban", "dabigatran", "clexane", "coumadin"
    }),
    "NSAIDS": frozenset({
        "aspirin", "ibuprofen", "naproxen", "diclofenac", "ketorolac", "celecoxib", "aceclofenac", "piroxicam", "indomethacin"
    }),
    "METHOTREXATE": frozenset({
        "methotrexate", "trexall"
    }),
    "ACE_INHIBITORS_AND_ARBS": frozenset({
        "enalapril", "lisinopril", "ramipril", "captopril", "perindopril", "losartan", "telmisartan", "valsartan", "olmesartan"
    }),
    "MAO_INHIBITORS": frozenset({
        "linezolid", "selegiline", "phenelzine", "rasagiline", "moclobemide"
    }),
    "SEROTONERGIC_AGENTS": frozenset({
        "fluoxetine", "sertraline", "paroxetine", "escitalopram", "citalopram", "venlafaxine", "duloxetine", "tramadol"
    }),
    "CLOPIDOGREL": frozenset({
        "clopidogrel", "plavix"
    }),
    "OMEPRAZOLE_ESOMEPRAZOLE": frozenset({
        "omeprazole", "esomeprazole", "prilosec", "nexium"
    }),
    "PPIS": frozenset({
        "omeprazole", "esomeprazole", "pantoprazole", "rabeprazole", "lansoprazole"
    }),
    "STATINS": frozenset({
        "atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "pitavastatin"
    }),
}

INTERACTION_RULES: list[InteractionRule] = [
    # Absolute Contraindications (CRITICAL_HARD_STOP - Cannot be overridden)
    InteractionRule(
        class_a=CLINICAL_DRUG_CLASSES["NITRATES"],
        class_b=CLINICAL_DRUG_CLASSES["PDE5_INHIBITORS"],
        severity="CRITICAL_HARD_STOP",
        description="Nitrates combined with PDE5 Inhibitors",
        clinical_risk="Profound, refractory life-threatening hypotension and cardiovascular collapse. Absolute contraindication.",
    ),
    InteractionRule(
        class_a=CLINICAL_DRUG_CLASSES["POTASSIUM_SPARING"],
        class_b=CLINICAL_DRUG_CLASSES["POTASSIUM_SUPPLEMENTS"],
        severity="CRITICAL_HARD_STOP",
        description="Potassium-sparing diuretics combined with Potassium supplements",
        clinical_risk="Severe hyperkalemia with immediate risk of fatal cardiac arrhythmia and cardiac arrest. Absolute contraindication.",
    ),
    # Critical High-Risk Interactions (Requires explicit clinical override justification)
    InteractionRule(
        class_a=CLINICAL_DRUG_CLASSES["ANTICOAGULANTS"],
        class_b=CLINICAL_DRUG_CLASSES["NSAIDS"],
        severity="CRITICAL",
        description="Anticoagulants combined with NSAIDs / Antiplatelets",
        clinical_risk="Marked synergistic risk of severe gastrointestinal hemorrhage and systemic bleeding.",
    ),
    InteractionRule(
        class_a=CLINICAL_DRUG_CLASSES["METHOTREXATE"],
        class_b=CLINICAL_DRUG_CLASSES["NSAIDS"],
        severity="CRITICAL",
        description="Methotrexate combined with NSAIDs",
        clinical_risk="Decreased renal clearance of methotrexate leading to acute bone marrow suppression and nephrotoxicity.",
    ),
    InteractionRule(
        class_a=CLINICAL_DRUG_CLASSES["ACE_INHIBITORS_AND_ARBS"],
        class_b=CLINICAL_DRUG_CLASSES["POTASSIUM_SPARING"],
        severity="CRITICAL",
        description="ACE Inhibitors / ARBs combined with Potassium-sparing diuretics",
        clinical_risk="High risk of severe hyperkalemia. Requires close electrolyte and renal function monitoring.",
    ),
    InteractionRule(
        class_a=CLINICAL_DRUG_CLASSES["MAO_INHIBITORS"],
        class_b=CLINICAL_DRUG_CLASSES["SEROTONERGIC_AGENTS"],
        severity="CRITICAL",
        description="MAO Inhibitors (including Linezolid) combined with SSRIs / SNRIs / Serotonergic agents",
        clinical_risk="Life-threatening Serotonin Syndrome (hyperthermia, autonomic instability, neuromuscular rigidity).",
    ),
    # Significant Warnings
    InteractionRule(
        class_a=CLINICAL_DRUG_CLASSES["CLOPIDOGREL"],
        class_b=CLINICAL_DRUG_CLASSES["OMEPRAZOLE_ESOMEPRAZOLE"],
        severity="WARNING",
        description="Clopidogrel combined with Omeprazole / Esomeprazole",
        clinical_risk="CYP2C19 inhibition reduces clopidogrel active metabolite and antiplatelet efficacy. Consider pantoprazole instead.",
    ),
]

DUPLICATE_THERAPY_CLASSES = [
    ("NSAID Duplicate Therapy", CLINICAL_DRUG_CLASSES["NSAIDS"]),
    ("Proton Pump Inhibitor (PPI) Duplicate Therapy", CLINICAL_DRUG_CLASSES["PPIS"]),
    ("Statin Duplicate Therapy", CLINICAL_DRUG_CLASSES["STATINS"]),
    ("ACEI / ARB Duplicate Therapy", CLINICAL_DRUG_CLASSES["ACE_INHIBITORS_AND_ARBS"]),
]


def _match_drug_to_class(drug_name: str, drug_class: frozenset[str]) -> bool:
    name_clean = drug_name.strip().lower()
    return any(member in name_clean or name_clean in member for member in drug_class)


def evaluate_drug_interactions(
    medications: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Evaluates pairs among the provided medications (each with 'id', 'name', 'generic_name')
    and returns detected interactions and duplicate therapies.
    """
    findings: list[dict[str, Any]] = []

    # 1. Duplicate Therapy Check
    seen_ids = set()
    for med in medications:
        if med["id"] in seen_ids:
            findings.append({
                "type": "DUPLICATE_THERAPY",
                "severity": "CRITICAL",
                "drug_a": med["name"],
                "drug_b": med["name"],
                "description": f"Duplicate medication ordered: {med['name']}",
                "clinical_risk": "Redundant dosing and elevated toxic exposure.",
            })
        seen_ids.add(med["id"])

    # Duplicate class check
    for class_label, drug_set in DUPLICATE_THERAPY_CLASSES:
        matching_meds = [
            med for med in medications
            if _match_drug_to_class(med["name"], drug_set) or _match_drug_to_class(med.get("generic_name", ""), drug_set)
        ]
        if len(matching_meds) > 1:
            findings.append({
                "type": "DUPLICATE_THERAPY",
                "severity": "CRITICAL",
                "drug_a": matching_meds[0]["name"],
                "drug_b": matching_meds[1]["name"],
                "description": f"{class_label}: {matching_meds[0]['name']} and {matching_meds[1]['name']}",
                "clinical_risk": "Concurrent use of multiple agents within the same therapeutic drug class is not recommended.",
            })

    # 2. Drug-Drug Interactions Check (All pairs)
    n = len(medications)
    for i in range(n):
        for j in range(i + 1, n):
            med_a = medications[i]
            med_b = medications[j]
            names_a = [med_a["name"], med_a.get("generic_name", "")]
            names_b = [med_b["name"], med_b.get("generic_name", "")]

            for rule in INTERACTION_RULES:
                match_a_first = any(_match_drug_to_class(na, rule.class_a) for na in names_a) and any(_match_drug_to_class(nb, rule.class_b) for nb in names_b)
                match_b_first = any(_match_drug_to_class(nb, rule.class_a) for nb in names_b) and any(_match_drug_to_class(na, rule.class_b) for na in names_a)

                if match_a_first or match_b_first:
                    findings.append({
                        "type": "DRUG_DRUG_INTERACTION",
                        "severity": rule.severity,
                        "drug_a": med_a["name"],
                        "drug_b": med_b["name"],
                        "description": rule.description,
                        "clinical_risk": rule.clinical_risk,
                    })

    return findings


def check_drug_drug_interactions(
    session: Session,
    patient_id: int,
    candidate_medications: list[Any],
    *,
    encounter_id: Optional[int] = None,
    override_warning: bool = False,
    override_justification: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Performs comprehensive DDI and duplicate therapy validation across:
    1. Newly prescribed items.
    2. Active prescriptions currently on record for the patient.

    Enforces:
    - CRITICAL_HARD_STOP: Immediate 409 Conflict. Cannot be overridden.
    - CRITICAL / DUPLICATE: 409 Conflict unless override_warning=True and justification is provided.
    """
    from backend.domains.pharmacy.models import (
        MedicationMaster,
        Prescription,
        PrescriptionItem,
        PrescriptionStatusEnum,
    )

    all_med_records: list[dict[str, Any]] = [
        {
            "id": med.id,
            "name": med.brand_name,
            "generic_name": med.generic_name,
            "source": "CURRENT_ORDER",
        }
        for med in candidate_medications
    ]

    # Query active prescriptions for this patient
    stmt = (
        select(MedicationMaster)
        .join(PrescriptionItem, PrescriptionItem.medication_id == MedicationMaster.id)
        .join(Prescription, Prescription.id == PrescriptionItem.prescription_id)
        .where(
            Prescription.encounter_id == encounter_id if encounter_id else True,
            Prescription.status.in_([PrescriptionStatusEnum.ACTIVE, PrescriptionStatusEnum.PARTIAL]),
        )
    )
    existing_meds = session.execute(stmt).scalars().all()
    for emed in existing_meds:
        all_med_records.append({
            "id": emed.id,
            "name": emed.brand_name,
            "generic_name": emed.generic_name,
            "source": "ACTIVE_PRESCRIPTION",
        })

    findings = evaluate_drug_interactions(all_med_records)
    if not findings:
        return []

    # Check for hard stops
    hard_stops = [f for f in findings if f["severity"] == "CRITICAL_HARD_STOP"]
    if hard_stops:
        hs = hard_stops[0]
        raise HTTPException(
            status_code=409,
            detail=(
                f"CLINICAL HARD-STOP: {hs['description']} between '{hs['drug_a']}' and '{hs['drug_b']}'. "
                f"Risk: {hs['clinical_risk']} Order blocked."
            ),
        )

    # Check for critical warnings & duplicate therapy
    critical_findings = [f for f in findings if f["severity"] == "CRITICAL"]
    if critical_findings:
        if not override_warning or not override_justification or len(override_justification.strip()) < 10:
            crit = critical_findings[0]
            raise HTTPException(
                status_code=409,
                detail=(
                    f"CLINICAL INTERACTION WARNING: {crit['description']} between '{crit['drug_a']}' and '{crit['drug_b']}'. "
                    f"Risk: {crit['clinical_risk']} "
                    f"To proceed, provide override_ddi_warning=True and ddi_override_justification (min 10 characters)."
                ),
            )
        logger.warning(
            "Clinical DDI override accepted for patient %d: %s (Justification: %s)",
            patient_id,
            critical_findings,
            override_justification,
        )

    return findings
