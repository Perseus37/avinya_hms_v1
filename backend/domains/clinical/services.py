"""Clinical safety checks for CPOE order validation.

This module provides:
1. Allergy hard-stop verification against active patient allergies and cross-reactive drug classes.
2. Drug-Drug Interaction (DDI) detection (Contraindicated Hard-Stops, Critical warnings, and Significant interactions).
3. Duplicate therapy checking across new orders and active prescriptions.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any, Optional

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


def _match_drug_to_class(drug_name: Optional[str], drug_class: frozenset[str]) -> bool:
    if not drug_name:
        return False
    name_clean = drug_name.strip().lower()
    if not name_clean:
        return False
    for member in drug_class:
        if name_clean == member:
            return True
        if re.search(r"\b" + re.escape(member) + r"\b", name_clean):
            return True
        if re.search(r"\b" + re.escape(name_clean) + r"\b", member):
            return True
    return False


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


# -----------------------------------------------------------------------------
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

    from backend.domains.encounters.models import PatientEncounter

    # Query active prescriptions for this patient across encounters
    stmt = (
        select(MedicationMaster)
        .join(PrescriptionItem, PrescriptionItem.medication_id == MedicationMaster.id)
        .join(Prescription, Prescription.id == PrescriptionItem.prescription_id)
        .join(PatientEncounter, PatientEncounter.id == Prescription.encounter_id)
        .where(
            PatientEncounter.patient_id == patient_id,
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


# -----------------------------------------------------------------------------
# Enterprise Drug-Disease Contraindication Rules & Evaluator
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class DiseaseContraindicationRule:
    drug_keywords: frozenset[str]
    icd10_prefixes: tuple[str, ...]
    disease_keywords: tuple[str, ...]
    severity: str  # "CRITICAL_HARD_STOP", "CRITICAL"
    condition_name: str
    clinical_risk: str


DRUG_DISEASE_RULES: list[DiseaseContraindicationRule] = [
    DiseaseContraindicationRule(
        drug_keywords=frozenset({
            "propranolol", "atenolol", "timolol", "metoprolol", "carvedilol", "labetalol", "bisoprolol", "nadolol"
        }),
        icd10_prefixes=("J45", "J46"),
        disease_keywords=("asthma", "bronchospasm", "reactive airway"),
        severity="CRITICAL_HARD_STOP",
        condition_name="Bronchial Asthma",
        clinical_risk="Beta-adrenergic blockade precipitates severe bronchospasm, refractory status asthmaticus, and respiratory arrest.",
    ),
    DiseaseContraindicationRule(
        drug_keywords=frozenset({
            "aspirin", "ibuprofen", "diclofenac", "naproxen", "ketorolac", "indomethacin", "piroxicam", "aceclofenac"
        }),
        icd10_prefixes=("K25", "K26", "K27", "K28"),
        disease_keywords=("peptic ulcer", "gastric ulcer", "duodenal ulcer", "gi bleed"),
        severity="CRITICAL",
        condition_name="Active Peptic Ulcer Disease",
        clinical_risk="Inhibition of mucosal prostaglandins increases risk of fatal gastrointestinal perforation and acute upper GI hemorrhage.",
    ),
    DiseaseContraindicationRule(
        drug_keywords=frozenset({
            "metformin", "glycomet"
        }),
        icd10_prefixes=("N18.4", "N18.5", "N18.6", "N18.9", "N19"),
        disease_keywords=("chronic kidney disease", "ckd stage 4", "ckd stage 5", "renal failure", "end stage renal"),
        severity="CRITICAL",
        condition_name="Severe Renal Impairment (CKD Stage 4/5)",
        clinical_risk="Impaired drug clearance causes drug accumulation leading to severe metformin-associated lactic acidosis (MALA), with ~50% mortality.",
    ),
    DiseaseContraindicationRule(
        drug_keywords=frozenset({
            "enalapril", "lisinopril", "ramipril", "captopril", "losartan", "telmisartan", "valsartan", "olmesartan"
        }),
        icd10_prefixes=("O00", "O01", "O02", "O03", "O04", "O05", "O06", "O07", "O08", "O09", "O10", "O20", "O30", "Z33"),
        disease_keywords=("pregnancy", "pregnant", "gestation"),
        severity="CRITICAL_HARD_STOP",
        condition_name="Pregnancy",
        clinical_risk="Renin-angiotensin system blockade causes severe teratogenic fetopathy, oligohydramnios, neonatal renal failure, and fetal death.",
    ),
]


def check_drug_disease_contraindications(
    session: Session,
    patient_id: int,
    medication_generics: list[str],
    override_contraindication: bool = False,
    override_justification: Optional[str] = None,
) -> list[dict]:
    """Evaluates proposed medications against patient's active diagnoses from EncounterDiagnosis."""
    from backend.domains.clinical.models import EncounterDiagnosis
    from backend.domains.encounters.models import PatientEncounter
    from backend.domains.patients.models import DiagnosisMaster

    if not medication_generics:
        return []

    diag_stmt = (
        select(DiagnosisMaster.icd10_code, DiagnosisMaster.short_description)
        .join(EncounterDiagnosis, EncounterDiagnosis.diagnosis_id == DiagnosisMaster.id)
        .join(PatientEncounter, PatientEncounter.id == EncounterDiagnosis.encounter_id)
        .where(PatientEncounter.patient_id == patient_id)
    )
    active_diagnoses = session.execute(diag_stmt).all()
    if not active_diagnoses:
        return []

    normalized_drugs = {m.strip().lower() for m in medication_generics if m and len(m.strip()) >= 3}
    findings = []

    for rule in DRUG_DISEASE_RULES:
        # Check drug match
        matched_drug = None
        for nd in normalized_drugs:
            if any(k == nd or re.search(r"\b" + re.escape(k) + r"\b", nd) for k in rule.drug_keywords):
                matched_drug = nd
                break

        if not matched_drug:
            continue

        # Check disease match
        matched_dx = None
        for code, short_desc in active_diagnoses:
            c_upper = (code or "").strip().upper()
            n_lower = (short_desc or "").strip().lower()
            if any(c_upper.startswith(p) for p in rule.icd10_prefixes) or any(dk in n_lower for dk in rule.disease_keywords):
                matched_dx = f"{code} ({short_desc})"
                break

        if matched_dx:
            findings.append({
                "drug": matched_drug,
                "disease": matched_dx,
                "severity": rule.severity,
                "condition_name": rule.condition_name,
                "clinical_risk": rule.clinical_risk,
            })

    if not findings:
        return []

    hard_stops = [f for f in findings if f["severity"] == "CRITICAL_HARD_STOP"]
    if hard_stops:
        hs = hard_stops[0]
        raise HTTPException(
            status_code=409,
            detail=(
                f"CLINICAL CONTRAINDICATION HARD-STOP: Drug '{hs['drug']}' is absolutely contraindicated in {hs['condition_name']} (Diagnosed: {hs['disease']}). "
                f"Risk: {hs['clinical_risk']} Order blocked."
            ),
        )

    criticals = [f for f in findings if f["severity"] == "CRITICAL"]
    if criticals:
        if not override_contraindication or not override_justification or len(override_justification.strip()) < 10:
            crit = criticals[0]
            raise HTTPException(
                status_code=409,
                detail=(
                    f"CLINICAL CONTRAINDICATION WARNING: Drug '{crit['drug']}' in patient with {crit['condition_name']} ({crit['disease']}). "
                    f"Risk: {crit['clinical_risk']} "
                    f"To proceed, provide override_contraindication=True and contraindication_override_justification (min 10 characters)."
                ),
            )
        logger.warning(
            "Drug-Disease contraindication override accepted for patient %d: %s (Justification: %s)",
            patient_id,
            criticals,
            override_justification,
        )

    return findings


# -----------------------------------------------------------------------------
# Clinical Early Warning Score (NEWS2 / qSOFA) Engine
# -----------------------------------------------------------------------------

def calculate_news2_score(
    respiratory_rate: Optional[int] = None,
    spo2_percent: Optional[int] = None,
    bp_systolic: Optional[int] = None,
    pulse_bpm: Optional[int] = None,
    consciousness: Optional[str] = "ALERT",
    temperature_f: Optional[float] = None,
    on_supplemental_oxygen: bool = False,
    spo2_scale: int = 1,
) -> dict:
    """
    Computes Royal College of Physicians National Early Warning Score 2 (NEWS2).
    Evaluates physiological parameters, classifies aggregated clinical risk (LOW, MEDIUM, HIGH),
    and determines whether an immediate Rapid Response Team (RRT) alert must be triggered.
    Supports Scale 1 (standard) and Scale 2 (hypercapnic respiratory failure / COPD). (N-47)
    """
    # 0. Check that at least one measured physiological vital parameter is provided
    measured_vitals = [respiratory_rate, spo2_percent, bp_systolic, pulse_bpm, temperature_f]
    if all(v is None for v in measured_vitals):
        raise ValueError("At least one measured physiological vital parameter must be provided to calculate NEWS2.")

    # Physiological plausibility bounds checking (N-47)
    if respiratory_rate is not None and not (0 <= respiratory_rate <= 100):
        raise ValueError(f"Respiratory rate ({respiratory_rate}) out of plausible physiological range (0-100).")
    if spo2_percent is not None and not (0 <= spo2_percent <= 100):
        raise ValueError(f"SpO2 ({spo2_percent}%) out of plausible physiological range (0-100).")
    if bp_systolic is not None and not (20 <= bp_systolic <= 300):
        raise ValueError(f"Systolic blood pressure ({bp_systolic} mmHg) out of plausible physiological range (20-300).")
    if pulse_bpm is not None and not (10 <= pulse_bpm <= 300):
        raise ValueError(f"Pulse ({pulse_bpm} bpm) out of plausible physiological range (10-300).")
    if temperature_f is not None:
        if temperature_f > 45.0:  # Fahrenheit
            if not (60.0 <= temperature_f <= 120.0):
                raise ValueError(f"Temperature ({temperature_f}°F) out of plausible physiological range (60-120°F).")
        else:  # Celsius
            if not (15.0 <= temperature_f <= 50.0):
                raise ValueError(f"Temperature ({temperature_f}°C) out of plausible physiological range (15-50°C).")
    if spo2_scale not in (1, 2):
        raise ValueError(f"spo2_scale must be 1 or 2, got {spo2_scale}.")

    total_score = 0
    sub_scores = {}
    any_parameter_is_3 = False

    # Core parameters completeness evaluation
    core_parameters = {
        "respiratory_rate": respiratory_rate,
        "spo2_percent": spo2_percent,
        "bp_systolic": bp_systolic,
        "pulse_bpm": pulse_bpm,
        "consciousness": consciousness,
        "temperature": temperature_f,
    }
    missing_parameters = [param for param, val in core_parameters.items() if val is None]
    is_complete_set = len(missing_parameters) == 0

    # 1. Respiration Rate (breaths/min)
    if respiratory_rate is not None:
        if respiratory_rate <= 8:
            pts = 3
        elif 9 <= respiratory_rate <= 11:
            pts = 1
        elif 12 <= respiratory_rate <= 20:
            pts = 0
        elif 21 <= respiratory_rate <= 24:
            pts = 2
        else:  # >= 25
            pts = 3
        sub_scores["respiratory_rate"] = pts
        total_score += pts
        if pts == 3:
            any_parameter_is_3 = True

    # 2. SpO2 Scale 1 or Scale 2 (%) (N-47)
    if spo2_percent is not None:
        if spo2_scale == 2:
            # Scale 2: Hypercapnic respiratory failure (target 88-92%)
            if spo2_percent <= 83:
                pts = 3
            elif 84 <= spo2_percent <= 85:
                pts = 2
            elif 86 <= spo2_percent <= 87:
                pts = 1
            elif 88 <= spo2_percent <= 92:
                pts = 0
            elif 93 <= spo2_percent <= 94:
                pts = 1 if on_supplemental_oxygen else 0
            elif 95 <= spo2_percent <= 96:
                pts = 2 if on_supplemental_oxygen else 0
            else:  # >= 97
                pts = 3 if on_supplemental_oxygen else 0
        else:
            # Scale 1: Standard (target >= 96%)
            if spo2_percent <= 91:
                pts = 3
            elif 92 <= spo2_percent <= 93:
                pts = 2
            elif 94 <= spo2_percent <= 95:
                pts = 1
            else:  # >= 96
                pts = 0
        sub_scores["spo2"] = pts
        total_score += pts
        if pts == 3:
            any_parameter_is_3 = True

    # 3. Supplemental Oxygen
    o2_pts = 2 if on_supplemental_oxygen else 0
    sub_scores["supplemental_oxygen"] = o2_pts
    total_score += o2_pts

    # 4. Systolic Blood Pressure (mmHg)
    if bp_systolic is not None:
        if bp_systolic <= 90:
            pts = 3
        elif 91 <= bp_systolic <= 100:
            pts = 2
        elif 101 <= bp_systolic <= 110:
            pts = 1
        elif 111 <= bp_systolic <= 219:
            pts = 0
        else:  # >= 220
            pts = 3
        sub_scores["systolic_bp"] = pts
        total_score += pts
        if pts == 3:
            any_parameter_is_3 = True

    # 5. Pulse (Heart Rate, bpm)
    if pulse_bpm is not None:
        if pulse_bpm <= 40:
            pts = 3
        elif 41 <= pulse_bpm <= 50:
            pts = 1
        elif 51 <= pulse_bpm <= 90:
            pts = 0
        elif 91 <= pulse_bpm <= 110:
            pts = 1
        elif 111 <= pulse_bpm <= 130:
            pts = 2
        else:  # >= 131
            pts = 3
        sub_scores["pulse"] = pts
        total_score += pts
        if pts == 3:
            any_parameter_is_3 = True

    # 6. Consciousness (ACVPU Scale: Alert, Confusion, Voice, Pain, Unresponsive)
    c_str = (consciousness or "ALERT").strip().upper()
    if c_str in {"ALERT", "A"}:
        pts = 0
    else:
        pts = 3
    sub_scores["consciousness"] = pts
    total_score += pts
    if pts == 3:
        any_parameter_is_3 = True

    # 7. Temperature (°F / °C normalized)
    if temperature_f is not None:
        temp_c = (temperature_f - 32.0) * (5.0 / 9.0) if temperature_f > 45.0 else float(temperature_f)
        if temp_c <= 35.0:
            pts = 3
        elif 35.1 <= temp_c <= 36.0:
            pts = 1
        elif 36.1 <= temp_c <= 38.0:
            pts = 0
        elif 38.1 <= temp_c <= 39.0:
            pts = 1
        else:  # >= 39.1
            pts = 2
        sub_scores["temperature"] = pts
        total_score += pts
        if pts == 3:
            any_parameter_is_3 = True

    # Aggregated Risk Classification
    if total_score >= 7:
        risk_level = "HIGH"
        recommendation = "EMERGENCY: Immediate continuous vital monitoring. Mobilize Rapid Response Team (RRT) and transfer to ICU/HDU."
        rrt_alert = True
    elif total_score >= 5 or any_parameter_is_3:
        risk_level = "MEDIUM"
        recommendation = "URGENT: Urgent clinical review by attending physician within 30 minutes. Increase monitoring frequency to hourly."
        rrt_alert = True
    else:
        risk_level = "LOW"
        if not is_complete_set:
            recommendation = (
                f"INCOMPLETE_DATA: Mandatory parameters missing ({', '.join(missing_parameters)}). "
                f"Obtain complete vital set before concluding low risk; routine monitoring only if verified."
            )
        else:
            recommendation = "ROUTINE: Continue standard ward vital sign monitoring every 4 to 12 hours as clinically appropriate."
        rrt_alert = False

    # qSOFA score (quick Sepsis-related Organ Failure Assessment): SBP <= 100 (+1), RR >= 22 (+1), GCS < 15 / Altered mentation (+1)
    qsofa = 0
    if bp_systolic is not None and bp_systolic <= 100:
        qsofa += 1
    if respiratory_rate is not None and respiratory_rate >= 22:
        qsofa += 1
    if c_str not in {"ALERT", "A"}:
        qsofa += 1

    return {
        "news2_score": total_score,
        "risk_level": risk_level,
        "qsofa_score": qsofa,
        "is_rrt_alert_triggered": rrt_alert,
        "is_complete_set": is_complete_set,
        "missing_parameters": missing_parameters,
        "sub_scores": sub_scores,
        "clinical_recommendation": recommendation,
    }


# -----------------------------------------------------------------------------
# Diagnostic Laboratory Delta Checking Evaluator
# -----------------------------------------------------------------------------

def evaluate_lab_delta_check(
    session: Session,
    patient_id: int,
    service_id: int,
    current_value_str: str,
    current_task_id: Optional[int] = None,
) -> dict:
    """
    Evaluates incoming numerical laboratory result against patient's previous result.
    Flags acute implausible shifts (e.g. hemolyzed specimens, IV fluid contamination, or acute collapse).
    """
    from datetime import datetime, timedelta, timezone
    import re
    from backend.domains.diagnostics.models import (
        DiagnosticOrder,
        DiagnosticTask,
        LabDeltaCheckConfig,
    )

    config = session.execute(
        select(LabDeltaCheckConfig).where(LabDeltaCheckConfig.service_id == service_id)
    ).scalar_one_or_none()
    if not config:
        return {"delta_flag": "NORMAL", "detail": "No delta check rule configured."}

    num_match = re.search(r"[-+]?\d*\.?\d+", current_value_str)
    if not num_match:
        return {"delta_flag": "NORMAL", "detail": "Non-numeric current result."}
    curr_val = float(num_match.group(0))

    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=config.lookback_hours)
    prev_task_stmt = (
        select(DiagnosticTask)
        .join(DiagnosticOrder, DiagnosticOrder.id == DiagnosticTask.order_id)
        .where(
            DiagnosticOrder.patient_id == patient_id,
            DiagnosticTask.service_id == service_id,
            DiagnosticTask.result_value.isnot(None),
            DiagnosticTask.created_at >= cutoff_time,
        )
    )
    if current_task_id:
        prev_task_stmt = prev_task_stmt.where(DiagnosticTask.id != current_task_id)

    prev_task_stmt = prev_task_stmt.order_by(DiagnosticTask.created_at.desc(), DiagnosticTask.id.desc()).limit(1)
    prev_task = session.execute(prev_task_stmt).scalar_one_or_none()
    if not prev_task or not prev_task.result_value:
        return {"delta_flag": "NO_PRIOR_VALUE", "detail": "Baseline result established."}

    prev_num_match = re.search(r"[-+]?\d*\.?\d+", prev_task.result_value)
    if not prev_num_match:
        return {"delta_flag": "NORMAL", "detail": "Non-numeric previous result."}
    prev_val = float(prev_num_match.group(0))

    abs_delta = abs(curr_val - prev_val)
    pct_delta = (abs_delta / prev_val * 100.0) if prev_val != 0 else 0.0

    is_exceeded = False
    reasons = []
    if config.max_absolute_delta is not None and abs_delta > config.max_absolute_delta:
        is_exceeded = True
        reasons.append(f"Absolute delta {abs_delta:.2f} exceeds threshold {config.max_absolute_delta:.2f}")
    if config.max_percentage_delta is not None and pct_delta > config.max_percentage_delta:
        is_exceeded = True
        reasons.append(f"Percentage shift {pct_delta:.1f}% exceeds threshold {config.max_percentage_delta:.1f}%")

    if is_exceeded:
        return {
            "delta_flag": "DELTA_EXCEEDED",
            "severity": config.severity,
            "previous_value": str(prev_val),
            "current_value": str(curr_val),
            "absolute_delta": abs_delta,
            "percentage_delta": pct_delta,
            "detail": f"LIS DELTA CHECK ALERT: {', '.join(reasons)}. Pathologist review required before release.",
        }
    return {
        "delta_flag": "NORMAL",
        "previous_value": str(prev_val),
        "current_value": str(curr_val),
        "absolute_delta": abs_delta,
        "percentage_delta": pct_delta,
        "detail": "Within acceptable historical limits.",
    }

