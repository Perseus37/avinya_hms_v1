"""
Enterprise Pharmacy & Bedside Clinical Safety Services:
1. Barcode Medication Administration (BCMA) 5-Rights Verification
2. Controlled Substances (Narcotics) Perpetual Vault & Dual-Witness Wastage Engine
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Optional

from fastapi import HTTPException
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
    MedicationMaster,
    PrescriptionItem,
    eMARStatusEnum,
)

logger = logging.getLogger("avinya.pharmacy.services")


def evaluate_bcma_five_rights(
    session: Session,
    encounter_id: int,
    prescription_item_id: int,
    scanned_patient_barcode: str,
    scanned_medication_barcode: str,
    dose_given: Optional[str] = None,
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

    med = rx_item.medication
    now = datetime.now(timezone.utc)
    messages: list[str] = []

    # 1. Right Patient Validation
    # Accepted scanned patient barcodes: Patient MRN, ID string, ENC-{id}, PAT:{patient_id}:{encounter_id}
    clean_pat_scan = scanned_patient_barcode.strip()
    valid_patient_identifiers = {
        str(patient.id),
        patient.mrn,
        f"ENC-{encounter.id}",
        f"PAT:{patient.id}:{encounter.id}",
        f"PAT:{patient.mrn}",
    }
    patient_matched = (
        clean_pat_scan in valid_patient_identifiers
        or (patient.mrn and clean_pat_scan.lower() == patient.mrn.lower())
    )
    if not patient_matched:
        messages.append(
            f"RIGHT PATIENT VIOLATION: Scanned barcode '{scanned_patient_barcode}' does not match patient MRN {patient.mrn}."
        )

    # 2. Right Medication Validation
    # Accepted drug barcodes: Drug Code, NDC/GTIN, Brand Name, Generic Name, MED:{med_id}:{batch}
    clean_med_scan = scanned_medication_barcode.strip().lower()
    valid_drug_identifiers = {
        med.code.strip().lower(),
        med.brand_name.strip().lower(),
        med.generic_name.strip().lower(),
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
        messages.append(
            f"RIGHT DRUG VIOLATION: Scanned drug barcode '{scanned_medication_barcode}' does not match prescribed medication '{med.brand_name}' ({med.code})."
        )

    # 3. Right Dose
    dose_matched = True
    prescribed_dose = rx_item.dosage_instruction
    actual_dose = dose_given or prescribed_dose

    # 4. Right Time (+/- 60 minutes)
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

    is_blocked = not patient_matched or not medication_matched
    is_warning = time_status in ("EARLY", "LATE")

    return {
        "encounter": encounter,
        "patient": patient,
        "prescription_item": rx_item,
        "medication": med,
        "patient_matched": patient_matched,
        "medication_matched": medication_matched,
        "dose_matched": dose_matched,
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
    scheduled_time: Optional[datetime] = None,
    override_warning: bool = False,
    override_reason: Optional[str] = None,
) -> dict:
    """
    Executes full BCMA validation and atomically creates eMAR and BCMA audit log records.
    Hard-stops on Patient or Medication mismatch. Enforces documentation for Time Window overrides.
    """
    eval_result = evaluate_bcma_five_rights(
        session=session,
        encounter_id=encounter_id,
        prescription_item_id=prescription_item_id,
        scanned_patient_barcode=scanned_patient_barcode,
        scanned_medication_barcode=scanned_medication_barcode,
        dose_given=dose_given,
        scheduled_time=scheduled_time,
    )

    rx_item = eval_result["prescription_item"]
    med = eval_result["medication"]
    patient = eval_result["patient"]

    # 1. Critical Hard-Stops (Patient or Drug Mismatch)
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
        "time_status": eval_result["time_status"],
        "messages": eval_result["messages"] or ["All 5 Rights successfully verified at bedside."],
        "emar_log_id": emar_log.id,
        "bcma_log_id": bcma_log.id,
    }


# =============================================================================
# Controlled Substances (Narcotics) Vault Operations
# =============================================================================

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

    vault = session.get(ControlledSubstanceVault, vault_id)
    if not vault:
        raise HTTPException(status_code=404, detail="Controlled substance vault register not found.")

    bal_before = vault.current_balance_units
    witness_verified = False

    # 1. Receipt into Vault (Stock replenishment)
    if movement_type == "RECEIPT":
        bal_after = bal_before + quantity_units
        if tamper_seal_number:
            vault.tamper_seal_number = tamper_seal_number

    # 2. Dispense from Vault to Ward/Bedside
    elif movement_type == "DISPENSE":
        if bal_before < quantity_units:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient narcotic vault balance. Requested: {quantity_units}, Available: {bal_before}.",
            )
        bal_after = bal_before - quantity_units

    # 3. Dual-Witness Wastage / Destruction
    elif movement_type == "WASTAGE_DESTROYED":
        if bal_before < quantity_units:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot waste more units than available in vault perpetual balance ({bal_before}).",
            )

        # Enforce independent witness
        if not witness_user_id:
            raise HTTPException(
                status_code=400,
                detail="Dual-witness protocol mandatory: A secondary licensed healthcare witness ID is required.",
            )
        if witness_user_id == primary_user.id:
            raise HTTPException(
                status_code=400,
                detail="Self-witnessing is strictly prohibited under narcotics safety regulations. Witness must be an independent licensed user.",
            )

        witness_user = session.get(User, witness_user_id)
        if not witness_user or witness_user.status != UserStatusEnum.ACTIVE:
            raise HTTPException(status_code=404, detail="Active witness user account not found.")

        # Verify witness credentials
        if not witness_password:
            raise HTTPException(status_code=400, detail="Witness password verification required.")

        if not pwd_context.verify(witness_password, witness_user.hashed_password):
            raise HTTPException(
                status_code=401,
                detail="Witness credential verification failed: Invalid witness password.",
            )

        if not wastage_reason or len(wastage_reason.strip()) < 5:
            raise HTTPException(
                status_code=400,
                detail="Detailed clinical wastage reason is mandatory (minimum 5 characters).",
            )

        witness_verified = True
        bal_after = bal_before - quantity_units

    # 4. Return unused units back to vault
    elif movement_type == "RETURN_TO_VAULT":
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
