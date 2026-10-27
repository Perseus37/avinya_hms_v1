"""
Enterprise Pharmacy & Bedside Clinical Safety Services:
1. Barcode Medication Administration (BCMA) 5-Rights Verification
2. Controlled Substances (Narcotics) Perpetual Vault & Dual-Witness Wastage Engine
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import re
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domains.encounters.models import PatientEncounter
from backend.domains.identity.models import User, UserStatusEnum
from backend.domains.identity.passwords import pwd_context
from backend.domains.patients.models import Patient
from backend.domains.pharmacy.models import (
    BCMAScanAuditLog,
    ControlledSubstanceMovementLog,
    ControlledSubstanceVault,
    MedicationAdministrationLog,
    PrescriptionItem,
    PrescriptionStatusEnum,
    eMARStatusEnum,
)

logger = logging.getLogger("avinya.pharmacy.services")


# -----------------------------------------------------------------------------
# BCMA Dose and Route Tokens & Vocabularies
# -----------------------------------------------------------------------------

VALID_DOSE_UNITS = {
    "mg", "mcg", "ug", "g", "gram", "grams", "kg",
    "ml", "l", "liter", "liters", "litre", "litres",
    "iu", "unit", "units",
    "meq", "mmol",
    "tablet", "tablets", "tab", "tabs",
    "capsule", "capsules", "cap", "caps",
    "drop", "drops", "gtt",
    "puff", "puffs",
    "patch", "patches",
    "ampoule", "ampoules", "amp",
    "vial", "vials",
    "suppository", "suppositories",
    "spray", "sprays",
    "mg/ml", "mcg/ml", "iu/ml", "%",
}

MASS_VOLUME_DOSE_UNITS = {
    "mg", "mcg", "ug", "g", "gram", "grams", "kg",
    "ml", "l", "liter", "liters", "litre", "litres",
    "iu", "unit", "units",
    "meq", "mmol",
    "mg/ml", "mcg/ml", "iu/ml", "%",
}

NON_DOSE_WORDS = {
    "day", "days", "d", "daily", "times", "time", "hr", "hrs", "hour", "hours",
    "week", "weeks", "wk", "wks", "month", "months", "mo", "min", "minute", "minutes",
    "dose", "doses", "od", "bd", "bid", "tid", "qid", "prn", "stat", "sos", "x", "every",
}

ROUTE_ALIASES = {
    "oral": "ORAL",
    "po": "ORAL",
    "by mouth": "ORAL",
    "mouth": "ORAL",
    "per os": "ORAL",
    "tablet": "ORAL",
    "capsule": "ORAL",
    "syrup": "ORAL",
    "iv": "IV",
    "intravenous": "IV",
    "iv push": "IV",
    "iv piggyback": "IV",
    "iv infusion": "IV",
    "im": "IM",
    "intramuscular": "IM",
    "sc": "SC",
    "sq": "SC",
    "subcut": "SC",
    "subcutaneous": "SC",
    "topical": "TOPICAL",
    "transdermal": "TOPICAL",
    "inhalation": "INHALATION",
    "inhaled": "INHALATION",
    "nebulized": "INHALATION",
    "nebulizer": "INHALATION",
    "rectal": "RECTAL",
    "pr": "RECTAL",
    "suppository": "RECTAL",
    "sublingual": "SUBLINGUAL",
    "sl": "SUBLINGUAL",
    "ophthalmic": "OPHTHALMIC",
    "eye": "OPHTHALMIC",
    "otic": "OTIC",
    "ear": "OTIC",
    "nasal": "NASAL",
}

UNIT_NORMALIZATION = {
    "kg": (1000000.0, "mg"),
    "g": (1000.0, "mg"),
    "gram": (1000.0, "mg"),
    "grams": (1000.0, "mg"),
    "mg": (1.0, "mg"),
    "mcg": (0.001, "mg"),
    "ug": (0.001, "mg"),
    "l": (1000.0, "ml"),
    "liter": (1000.0, "ml"),
    "liters": (1000.0, "ml"),
    "litre": (1000.0, "ml"),
    "litres": (1000.0, "ml"),
    "ml": (1.0, "ml"),
    "tab": (1.0, "tablet"),
    "tabs": (1.0, "tablet"),
    "tablet": (1.0, "tablet"),
    "tablets": (1.0, "tablet"),
    "cap": (1.0, "capsule"),
    "caps": (1.0, "capsule"),
    "capsule": (1.0, "capsule"),
    "capsules": (1.0, "capsule"),
    "amp": (1.0, "ampoule"),
    "ampoule": (1.0, "ampoule"),
    "ampoules": (1.0, "ampoule"),
    "vial": (1.0, "vial"),
    "vials": (1.0, "vial"),
    "iu": (1.0, "iu"),
    "unit": (1.0, "iu"),
    "units": (1.0, "iu"),
}


def _extract_dose_tokens(text: Optional[str]) -> List[Tuple[float, str]]:
    """
    Extracts structured (quantity, unit) pairs from dosage text.
    Handles '125 mg', '250mg IV push', '1 tablet twice daily', '50mcg/ml', '0.5 ml', '25mg'.
    Filters out duration/frequency tokens ('5 days', '3 times').
    """
    if not text:
        return []
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*([a-zA-Z]+(?:/[a-zA-Z]+)?)?", str(text))
    tokens = []
    for num_str, unit in matches:
        try:
            val = float(num_str)
            raw_unit = unit.lower().strip() if unit else ""
            clean_unit = raw_unit.rstrip("s") if len(raw_unit) > 2 and raw_unit.endswith("s") else raw_unit
            if clean_unit in NON_DOSE_WORDS:
                continue
            tokens.append((val, clean_unit))
        except ValueError:
            pass
    return tokens


def _extract_route(text: Optional[str]) -> Optional[str]:
    """Extracts standardized administration route from text (e.g. 'IV', 'ORAL', 'IM', 'SC')."""
    if not text:
        return None
    lower = text.lower()
    for phrase in ["by mouth", "iv push", "iv piggyback", "iv infusion", "per os"]:
        if phrase in lower:
            return ROUTE_ALIASES[phrase]
    words = re.findall(r"\b[a-zA-Z]+\b", lower)
    for w in words:
        if w in ROUTE_ALIASES:
            return ROUTE_ALIASES[w]
    return None


def _is_dose_match(given_text: str, target_text: Optional[str]) -> bool:
    """
    Compares administered dose against target instruction or drug strength.
    Uses numeric quantity and unit normalization to prevent false substring positives,
    rejects blank doses, and prevents bare integers without units from matching measured doses.
    """
    if not given_text or not str(given_text).strip():
        return False
    if not target_text or not str(target_text).strip():
        return False

    given_tokens = _extract_dose_tokens(given_text)
    target_tokens = _extract_dose_tokens(target_text)

    if given_tokens and target_tokens:
        for g_val, g_unit in given_tokens:
            for t_val, t_unit in target_tokens:
                # If one has a unit and the other does not, they cannot match (bare number cannot match measured dose)
                if bool(g_unit) != bool(t_unit):
                    continue

                if not g_unit and not t_unit:
                    if abs(g_val - t_val) < 1e-6:
                        return True
                    continue

                # Both have units: normalize
                g_factor, g_norm_unit = UNIT_NORMALIZATION.get(g_unit, (1.0, g_unit))
                t_factor, t_norm_unit = UNIT_NORMALIZATION.get(t_unit, (1.0, t_unit))

                if g_norm_unit == t_norm_unit:
                    if abs((g_val * g_factor) - (t_val * t_factor)) < 1e-6:
                        return True
        return False

    norm_given = re.sub(r"\s+", "", str(given_text)).strip().lower()
    norm_target = re.sub(r"\s+", "", str(target_text)).strip().lower()
    if norm_given and norm_given == norm_target:
        return True

    clean_given = str(given_text).strip().lower()
    if not clean_given:
        return False
    pattern = r"\b" + re.escape(clean_given) + r"\b"
    return bool(re.search(pattern, str(target_text).lower()))


def evaluate_bcma_five_rights(
    session: Session,
    encounter_id: int,
    prescription_item_id: int,
    scanned_patient_barcode: str,
    scanned_medication_barcode: str,
    dose_given: Optional[str] = None,
    route: Optional[str] = None,
    scheduled_time: Optional[datetime] = None,
) -> dict:
    """
    Evaluates the 5-Rights of Barcode Medication Administration (BCMA):
    1. Right Patient (Wristband scan vs Encounter Patient)
    2. Right Drug (Unit-dose packaging / NDC / Hospital Code vs Prescription)
    3. Right Dose (Prescribed dose matching)
    4. Right Route (Order route validation)
    5. Right Time (+/- 60 minute administration window or PRN)
    """
    encounter = session.get(PatientEncounter, encounter_id)
    if not encounter:
        raise HTTPException(status_code=404, detail=f"Encounter {encounter_id} not found.")

    patient = session.get(Patient, encounter.patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient record not found.")

    rx_item = session.get(PrescriptionItem, prescription_item_id)
    if not rx_item:
        raise HTTPException(status_code=404, detail=f"Prescription item {prescription_item_id} not found.")

    if rx_item.prescription.encounter_id != encounter_id:
        raise HTTPException(
            status_code=400,
            detail=f"Prescription item {prescription_item_id} belongs to a different encounter.",
        )

    # N-43: Validate prescription is ACTIVE before bedside administration
    if rx_item.prescription.status != PrescriptionStatusEnum.ACTIVE:
        raise HTTPException(
            status_code=409,
            detail=f"BCMA CRITICAL HARD-STOP: Prescription {rx_item.prescription_id} is in status '{rx_item.prescription.status.value}'. Only ACTIVE prescriptions can be administered.",
        )

    med = rx_item.medication
    now = datetime.now(timezone.utc)
    messages: list[str] = []

    # 1. Right Patient Validation
    # Reject bare patient database ID (e.g., "1") to require machine-readable wristband MRN
    clean_pat_scan = scanned_patient_barcode.strip()
    valid_patient_identifiers = {
        patient.mrn,
        f"ENC-{encounter.id}",
        f"PAT:{patient.id}:{encounter.id}",
        f"PAT:{patient.mrn}",
    }
    if clean_pat_scan.isdigit() and clean_pat_scan not in valid_patient_identifiers:
        patient_matched = False
        messages.append(
            f"RIGHT PATIENT VIOLATION: Scanned barcode '{scanned_patient_barcode}' is a bare database ID. Machine-readable wristband MRN barcode is required."
        )
    else:
        patient_matched = (
            clean_pat_scan in valid_patient_identifiers
            or (patient.mrn and clean_pat_scan.lower() == patient.mrn.lower())
        )
        if not patient_matched:
            messages.append(
                f"RIGHT PATIENT VIOLATION: Scanned barcode '{scanned_patient_barcode}' does not match patient MRN {patient.mrn}."
            )

    # 2. Right Medication Validation (N-43 / N-62: reject raw brand name and generic name, require NDC, barcode, code, or med:<id>)
    clean_med_scan = scanned_medication_barcode.strip().lower()
    valid_drug_identifiers = {
        med.code.strip().lower(),
        f"med:{med.id}",
        f"med:{med.code.strip().lower()}",
    }
    if med.ndc_or_barcode:
        valid_drug_identifiers.add(med.ndc_or_barcode.strip().lower())

    medication_matched = False
    if clean_med_scan in valid_drug_identifiers:
        medication_matched = True
    elif clean_med_scan.startswith("med:"):
        parts = clean_med_scan.split(":")
        if len(parts) >= 2 and parts[1] in (str(med.id), med.code.strip().lower()):
            medication_matched = True

    if not medication_matched:
        if med.brand_name and clean_med_scan == med.brand_name.strip().lower():
            messages.append(
                f"RIGHT DRUG VIOLATION: Scanned value '{scanned_medication_barcode}' is a medication brand name. Machine-readable barcode or drug code is required."
            )
        else:
            messages.append(
                f"RIGHT DRUG VIOLATION: Scanned drug barcode '{scanned_medication_barcode}' does not match prescribed medication '{med.brand_name}' ({med.code})."
            )

    # 3. Right Dose (N-43 / N-62: parse and validate dose; dose is mandatory for administration)
    prescribed_dose = rx_item.dosage_instruction
    actual_dose = dose_given or prescribed_dose
    dose_matched = True
    if dose_given is None or not str(dose_given).strip():
        dose_matched = False
        messages.append("RIGHT DOSE VIOLATION: Administered dose is mandatory and cannot be omitted or blank.")
    else:
        prescribed_tokens = _extract_dose_tokens(prescribed_dose)
        has_explicit_mass_volume = any(u in MASS_VOLUME_DOSE_UNITS for _, u in prescribed_tokens)

        matched_prescription = _is_dose_match(dose_given, prescribed_dose)
        if has_explicit_mass_volume:
            if not matched_prescription:
                dose_matched = False
                messages.append(
                    f"RIGHT DOSE VIOLATION: Administered dose '{dose_given}' does not match prescribed instruction '{prescribed_dose}'."
                )
        else:
            matched_strength = _is_dose_match(dose_given, med.strength)
            if not matched_prescription and not matched_strength:
                dose_matched = False
                messages.append(
                    f"RIGHT DOSE VIOLATION: Administered dose '{dose_given}' does not match prescribed instruction '{prescribed_dose}' or drug strength '{med.strength}'."
                )

    # 4. Right Route (N-62 / N-65: Route is mandatory and validated against prescription / form)
    prescribed_route = _extract_route(prescribed_dose)
    is_injectable = bool(med.form and med.form.upper() in {"INJECTION", "INFUSION", "INJ"})
    is_oral = bool(med.form and med.form.upper() in {"TABLET", "CAPSULE", "SYRUP", "TAB", "CAP"})

    if not prescribed_route:
        if is_oral:
            prescribed_route = "ORAL"

    administered_route = _extract_route(route) or _extract_route(dose_given)

    route_matched = True
    if administered_route is None:
        route_matched = False
        messages.append("RIGHT ROUTE VIOLATION: Administered route is mandatory and cannot be omitted.")
    elif prescribed_route:
        if prescribed_route != administered_route:
            route_matched = False
            messages.append(
                f"RIGHT ROUTE VIOLATION: Administered route '{administered_route}' does not match prescribed route '{prescribed_route}'."
            )
    elif is_injectable:
        # N-65: Injectable formulations without explicit route in order can accept any valid parenteral route (IV, IM, SC)
        if administered_route not in {"IV", "IM", "SC", "INTRATHECAL"}:
            route_matched = False
            messages.append(
                f"RIGHT ROUTE VIOLATION: Injectable formulation requires parenteral route (IV/IM/SC), got '{administered_route}'."
            )

    # 5. Right Time (+/- 60 minutes)
    time_status = "ON_TIME"
    if scheduled_time:
        if scheduled_time.tzinfo is None:
            sched_utc = scheduled_time.replace(tzinfo=timezone.utc)
        else:
            sched_utc = scheduled_time.astimezone(timezone.utc)

        delta_minutes = (now - sched_utc).total_seconds() / 60.0
        if delta_minutes < -60.0:
            time_status = "EARLY"
            messages.append(
                f"RIGHT TIME WARNING: Dose scheduled for {sched_utc.strftime('%H:%M UTC')} is being given {abs(int(delta_minutes))} minutes early."
            )
        elif delta_minutes > 60.0:
            time_status = "LATE"
            messages.append(
                f"RIGHT TIME WARNING: Dose scheduled for {sched_utc.strftime('%H:%M UTC')} is {int(delta_minutes)} minutes overdue."
            )
    else:
        time_status = "PRN"

    is_blocked = not patient_matched or not medication_matched or not dose_matched or not route_matched
    is_warning = time_status in ("EARLY", "LATE")

    return {
        "encounter": encounter,
        "patient": patient,
        "prescription_item": rx_item,
        "medication": med,
        "patient_matched": patient_matched,
        "medication_matched": medication_matched,
        "dose_matched": dose_matched,
        "route_matched": route_matched,
        "time_status": time_status,
        "is_blocked": is_blocked,
        "is_warning": is_warning,
        "messages": messages,
        "actual_dose": actual_dose,
    }


def execute_bcma_administration(
    session: Session,
    encounter_id: int,
    prescription_item_id: int,
    administered_by_user: User,
    scanned_patient_barcode: str,
    scanned_medication_barcode: str,
    dose_given: Optional[str] = None,
    route: Optional[str] = None,
    scheduled_time: Optional[datetime] = None,
    override_warning: bool = False,
    override_reason: Optional[str] = None,
) -> dict:
    """
    Executes full BCMA validation and atomically creates eMAR and BCMA audit log records.
    Hard-stops on Patient, Medication, Dose, or Route mismatch. Enforces documentation for Time Window overrides.
    """
    eval_result = evaluate_bcma_five_rights(
        session=session,
        encounter_id=encounter_id,
        prescription_item_id=prescription_item_id,
        scanned_patient_barcode=scanned_patient_barcode,
        scanned_medication_barcode=scanned_medication_barcode,
        dose_given=dose_given,
        route=route,
        scheduled_time=scheduled_time,
    )

    rx_item = eval_result["prescription_item"]
    med = eval_result["medication"]
    patient = eval_result["patient"]

    # 1. Critical Hard-Stops (Patient, Drug, Dose, or Route Mismatch)
    if eval_result["is_blocked"]:
        audit = BCMAScanAuditLog(
            encounter_id=encounter_id,
            prescription_item_id=prescription_item_id,
            administered_by_id=administered_by_user.id,
            scanned_patient_barcode=scanned_patient_barcode,
            scanned_medication_barcode=scanned_medication_barcode,
            patient_matched=eval_result["patient_matched"],
            medication_matched=eval_result["medication_matched"],
            dose_matched=eval_result["dose_matched"],
            route_matched=eval_result["route_matched"],
            time_status=eval_result["time_status"],
            verification_status="BLOCKED_MISMATCH",
            override_reason=override_reason,
            emar_log_id=None,
        )
        session.add(audit)
        session.commit()
        error_detail = " | ".join(eval_result["messages"])
        raise HTTPException(
            status_code=409,
            detail=f"BCMA CRITICAL HARD-STOP: Bedside 5-Rights Verification Failed! {error_detail}. Dose administration prohibited.",
        )

    # 2. Time Window Warnings (Early / Late)
    verification_status = "VERIFIED"
    if eval_result["is_warning"]:
        if not override_warning or not override_reason or len(override_reason.strip()) < 5:
            audit = BCMAScanAuditLog(
                encounter_id=encounter_id,
                prescription_item_id=prescription_item_id,
                administered_by_id=administered_by_user.id,
                scanned_patient_barcode=scanned_patient_barcode,
                scanned_medication_barcode=scanned_medication_barcode,
                patient_matched=eval_result["patient_matched"],
                medication_matched=eval_result["medication_matched"],
                dose_matched=eval_result["dose_matched"],
                route_matched=eval_result["route_matched"],
                time_status=eval_result["time_status"],
                verification_status="WARNING",
                override_reason=None,
                emar_log_id=None,
            )
            session.add(audit)
            session.commit()
            warning_detail = " | ".join(eval_result["messages"])
            raise HTTPException(
                status_code=409,
                detail=f"BCMA TIME WINDOW WARNING: {warning_detail}. To proceed, set override_warning=True and provide clinical override_reason (min 5 chars).",
            )
        verification_status = "WARNING_OVERRIDDEN"

    # 3. Pre-administration allergy check
    from backend.domains.clinical.services import check_allergy_hard_stop
    check_allergy_hard_stop(session, patient.id, [med.brand_name, med.generic_name, med.code])

    # N-43: Prevent duplicate bedside administration within scheduled interval
    dup_query = select(MedicationAdministrationLog).where(
        MedicationAdministrationLog.prescription_item_id == prescription_item_id,
        MedicationAdministrationLog.status == eMARStatusEnum.ADMINISTERED,
    )
    if scheduled_time:
        dup_query = dup_query.where(MedicationAdministrationLog.scheduled_time == scheduled_time)
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
        dup_query = dup_query.where(MedicationAdministrationLog.administered_at >= cutoff)

    existing_admin = session.execute(dup_query).scalars().first()
    if existing_admin:
        raise HTTPException(
            status_code=409,
            detail=f"BCMA CRITICAL HARD-STOP: Duplicate administration detected! Dose already administered at {existing_admin.administered_at.isoformat()}.",
        )

    # 4. Create eMAR Administration Record
    emar_log = MedicationAdministrationLog(
        encounter_id=encounter_id,
        prescription_item_id=prescription_item_id,
        administered_by_id=administered_by_user.id,
        scheduled_time=scheduled_time,
        status=eMARStatusEnum.ADMINISTERED,
        dose_given=eval_result["actual_dose"],
        notes=f"BCMA Verified Bedside Administration. Status: {verification_status}" + (f" (Override Reason: {override_reason})" if override_reason else ""),
    )
    session.add(emar_log)
    session.flush()

    # 5. Create BCMA Audit Record
    bcma_log = BCMAScanAuditLog(
        encounter_id=encounter_id,
        prescription_item_id=prescription_item_id,
        administered_by_id=administered_by_user.id,
        scanned_patient_barcode=scanned_patient_barcode,
        scanned_medication_barcode=scanned_medication_barcode,
        patient_matched=True,
        medication_matched=True,
        dose_matched=True,
        route_matched=True,
        time_status=eval_result["time_status"],
        verification_status=verification_status,
        override_reason=override_reason,
        emar_log_id=emar_log.id,
    )
    session.add(bcma_log)

    # 6. Increment dispensed / administered quantity count if below requested
    if rx_item.dispensed_quantity < rx_item.requested_quantity:
        rx_item.dispensed_quantity += 1

    session.commit()
    session.refresh(emar_log)
    session.refresh(bcma_log)

    return {
        "is_verified": True,
        "verification_status": verification_status,
        "patient_matched": True,
        "medication_matched": True,
        "dose_matched": True,
        "route_matched": True,
        "time_status": eval_result["time_status"],
        "messages": eval_result["messages"] or ["All 5 Rights successfully verified at bedside."],
        "emar_log_id": emar_log.id,
        "bcma_log_id": bcma_log.id,
    }


# =============================================================================
# Controlled Substances (Narcotics) Vault Operations
# =============================================================================

def _verify_witness_credentials(
    session: Session,
    primary_user: User,
    witness_user_id: int,
    witness_password: str,
) -> bool:
    """Verifies independent secondary healthcare witness credentials with rate limiting."""
    if witness_user_id == primary_user.id:
        raise HTTPException(
            status_code=400,
            detail="Self-witnessing is strictly prohibited under narcotics safety regulations. Witness must be an independent licensed user.",
        )

    witness_user = session.get(User, witness_user_id)
    if not witness_user or witness_user.status != UserStatusEnum.ACTIVE:
        raise HTTPException(status_code=404, detail="Active witness user account not found.")

    if not witness_password:
        raise HTTPException(status_code=400, detail="Witness password verification required.")

    from backend.core.database import get_redis
    try:
        redis_cli = get_redis()
    except Exception:
        redis_cli = None
    fail_key = f"rl:narcotics_witness_fail:{witness_user.id}"
    if redis_cli:
        try:
            failed_cnt = redis_cli.get(fail_key)
            if failed_cnt and int(failed_cnt) >= 5:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many failed witness password attempts. Account locked temporarily.",
                )
        except HTTPException:
            raise
        except Exception:
            pass

    if not pwd_context.verify(witness_password, witness_user.hashed_password):
        if redis_cli:
            try:
                p = redis_cli.pipeline()
                p.incr(fail_key)
                p.expire(fail_key, 300)
                p.execute()
            except Exception:
                pass
        raise HTTPException(
            status_code=401,
            detail="Witness credential verification failed: Invalid witness password.",
        )

    if redis_cli:
        try:
            redis_cli.delete(fail_key)
        except Exception:
            pass

    return True


def process_controlled_substance_movement(
    session: Session,
    vault_id: int,
    movement_type: str,
    quantity_units: int,
    primary_user: User,
    witness_user_id: Optional[int] = None,
    witness_password: Optional[str] = None,
    encounter_id: Optional[int] = None,
    prescription_item_id: Optional[int] = None,
    wastage_reason: Optional[str] = None,
    disposal_method: Optional[str] = None,
    batch_number: Optional[str] = None,
    tamper_seal_number: Optional[str] = None,
    notes: Optional[str] = None,
) -> ControlledSubstanceMovementLog:
    """
    Executes a perpetual inventory update for controlled narcotics.
    Enforces independent dual-witness authentication with credential verification for all wastage/destruction.
    """
    if quantity_units <= 0:
        raise HTTPException(status_code=400, detail="Transaction quantity must be strictly greater than 0.")

    # N-44: Row lock vault row to prevent race conditions on concurrent dispatches
    stmt = select(ControlledSubstanceVault).where(ControlledSubstanceVault.id == vault_id).with_for_update()
    vault = session.execute(stmt).scalar_one_or_none()
    if not vault:
        raise HTTPException(status_code=404, detail="Controlled substance vault register not found.")

    bal_before = vault.current_balance_units
    witness_verified = False

    # 1. Receipt into Vault (Stock replenishment)
    if movement_type == "RECEIPT":
        bal_after = bal_before + quantity_units
        if tamper_seal_number:
            vault.tamper_seal_number = tamper_seal_number
        if witness_user_id:
            _verify_witness_credentials(session, primary_user, witness_user_id, witness_password)
            witness_verified = True

    # 2. Dispense from Vault to Ward/Bedside
    elif movement_type == "DISPENSE":
        if bal_before < quantity_units:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient narcotic vault balance. Requested: {quantity_units}, Available: {bal_before}.",
            )
        # N-44: Validate destination / clinical link
        if not encounter_id and not prescription_item_id and not (notes and notes.strip()):
            raise HTTPException(
                status_code=400,
                detail="Dispense destination required: encounter_id, prescription_item_id, or destination notes must be provided.",
            )
        # N-44: Validate encounter and prescription item existence when provided
        if encounter_id is not None:
            enc = session.get(PatientEncounter, encounter_id)
            if not enc:
                raise HTTPException(status_code=404, detail=f"Encounter {encounter_id} not found.")
        if prescription_item_id is not None:
            rx_i = session.get(PrescriptionItem, prescription_item_id)
            if not rx_i:
                raise HTTPException(status_code=404, detail=f"Prescription item {prescription_item_id} not found.")
        if witness_user_id:
            _verify_witness_credentials(session, primary_user, witness_user_id, witness_password)
            witness_verified = True
        bal_after = bal_before - quantity_units

    # 3. Dual-Witness Wastage / Destruction
    elif movement_type == "WASTAGE_DESTROYED":
        if bal_before < quantity_units:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot waste more units than available in vault perpetual balance ({bal_before}).",
            )

        # Enforce independent witness with verified credentials
        if not witness_user_id:
            raise HTTPException(
                status_code=400,
                detail="Dual-witness protocol mandatory: A secondary licensed healthcare witness ID is required.",
            )
        _verify_witness_credentials(session, primary_user, witness_user_id, witness_password)

        if not wastage_reason or len(wastage_reason.strip()) < 5:
            raise HTTPException(
                status_code=400,
                detail="Detailed clinical wastage reason is mandatory (minimum 5 characters).",
            )

        witness_verified = True
        bal_after = bal_before - quantity_units

    # 4. Return unused units back to vault
    elif movement_type == "RETURN_TO_VAULT":
        if encounter_id is not None:
            enc = session.get(PatientEncounter, encounter_id)
            if not enc:
                raise HTTPException(status_code=404, detail=f"Encounter {encounter_id} not found.")
        if prescription_item_id is not None:
            rx_i = session.get(PrescriptionItem, prescription_item_id)
            if not rx_i:
                raise HTTPException(status_code=404, detail=f"Prescription item {prescription_item_id} not found.")
        if witness_user_id:
            _verify_witness_credentials(session, primary_user, witness_user_id, witness_password)
            witness_verified = True
        bal_after = bal_before + quantity_units

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported movement type: {movement_type}")

    # Update perpetual balance
    vault.current_balance_units = bal_after

    movement_log = ControlledSubstanceMovementLog(
        vault_id=vault.id,
        movement_type=movement_type,
        quantity_units=quantity_units,
        balance_before=bal_before,
        balance_after=bal_after,
        encounter_id=encounter_id,
        prescription_item_id=prescription_item_id,
        primary_user_id=primary_user.id,
        witness_user_id=witness_user_id,
        witness_verified=witness_verified,
        wastage_reason=wastage_reason,
        disposal_method=disposal_method,
        batch_number=batch_number,
        notes=notes,
    )
    session.add(movement_log)
    session.commit()
    session.refresh(movement_log)
    session.refresh(vault)

    logger.info(
        "Narcotics movement %s recorded for Vault %d: %d units. Balance: %d -> %d (Witnessed: %s)",
        movement_type,
        vault.id,
        quantity_units,
        bal_before,
        bal_after,
        witness_verified,
    )
    return movement_log
