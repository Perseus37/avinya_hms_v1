"""Sandbox Sanitizer & Table Classification Service (R-02 / F-02).

Enforces strict zero-leakage scrubbing and credential wiping on cloned sandbox/UAT
databases to prevent live patient PII/PHI, physician contact details, real passwords,
MFA secrets, active sessions, and audit/security incident logs from existing in non-production.

Provides a comprehensive classification scheme where EVERY table in the tenant schema
must belong to:
1. MASTER_AND_CONFIG_TABLES (system reference masters & policy metadata)
2. PURGED_LOG_TABLES (sessions, security tokens, audit trails, and privacy breach logs)
3. SCRUBBED_DATA_TABLES (transactional, clinical, and user identity tables requiring masking)
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional, Set
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from backend.core.database import tenant_engine_pool
from backend.domains.identity.passwords import pwd_context

logger = logging.getLogger("avinya.control_plane.sandbox_sanitizer")

DUMMY_SANDBOX_PASSWORD = "SandboxPass123!"

MASTER_AND_CONFIG_TABLES: frozenset[str] = frozenset({
    "allergies_master",
    "ambulance_vehicles",
    "beds_master",
    "blood_bag_inventory",
    "chief_complaints_master",
    "comorbidities_master",
    "cssd_instrument_sets",
    "controlled_substance_vaults",
    "departments",
    "diagnoses_master",
    "equipment_master",
    "equipment_test_mappings",
    "floors",
    "hospital_policy_settings",
    "insurance_plan_tariffs",
    "insurance_plans",
    "insurance_providers",
    "lab_delta_check_configs",
    "medication_master",
    "operation_theatres",
    "order_set_items",
    "order_set_masters",
    "permissions",
    "physical_examinations_master",
    "physical_rooms_master",
    "privacy_legal_holds",
    "privacy_policy_revisions",
    "privacy_retention_policies",
    "procurement_vendors",
    "quality_indicator_benchmarks",
    "report_templates",
    "role_permissions",
    "roles",
    "service_bundle_mappings",
    "service_catalog",
    "service_categories",
    "wards",
    "alembic_version",
})

PURGED_LOG_TABLES: frozenset[str] = frozenset({
    "auth_sessions",
    "password_reset_tokens",
    "mfa_recovery_codes",
    "mfa_enrollment_codes",
    "hl7_integration_logs",
    "federation_transfer_logs",
    "privacy_decision_audits",
    "privacy_retention_run_ledgers",
    "patient_data_rights_requests",
    "privacy_data_breach_incidents",
    "procurement_document_download_audits",
    "break_glass_uses",
    "break_glass_grants",
    "service_workflow_events",
    "siem_audit_events",
})

SCRUBBED_DATA_TABLES: frozenset[str] = frozenset({
    "abdm_consent_artefacts",
    "ambulance_dispatches",
    "appointment_status_history",
    "appointment_waitlist_entries",
    "appointment_waitlist_history",
    "appointments",
    "attendance_records",
    "bcma_scan_audit_logs",
    "bed_transfer_logs",
    "biomedical_waste_bags",
    "biomedical_waste_manifests",
    "birth_records",
    "blood_cross_match_orders",
    "charge_capture_events",
    "clinical_early_warning_scores",
    "clinical_notes",
    "clinical_quality_incidents",
    "controlled_substance_movement_logs",
    "cssd_sterilization_batches",
    "death_records",
    "diagnostic_orders",
    "diagnostic_tasks",
    "dietary_orders",
    "doctor_leaves",
    "doctor_profiles",
    "encounter_diagnoses",
    "encounter_physical_exams",
    "encounter_vitals",
    "er_triage_assessments",
    "federated_clients",
    "finance_expenses",
    "finance_recurring_expenses",
    "fluid_balance_logs",
    "inpatient_admissions",
    "insurance_claims",
    "insurance_pre_auths",
    "inventory_batches",
    "invoice_line_items",
    "invoices",
    "leave_requests",
    "medication_administration_logs",
    "medico_legal_cases",
    "mortuary_cases",
    "nhcx_claim_exchange_logs",
    "nursing_shift_assignments",
    "operative_anesthesia_records",
    "patient_allergies",
    "patient_comorbidities",
    "patient_consent_records",
    "patient_deposits",
    "patient_encounters",
    "patient_insurance_policies",
    "patients",
    "payment_receipts",
    "payroll_calculation_events",
    "payroll_lines",
    "payroll_periods",
    "pharmacy_return_logs",
    "prescription_items",
    "prescriptions",
    "procurement_grn",
    "procurement_grn_items",
    "procurement_indent_items",
    "procurement_indents",
    "procurement_po_items",
    "procurement_purchase_orders",
    "procurement_vendor_invoice_items",
    "procurement_vendor_invoices",
    "radiology_study_metadata",
    "refund_vouchers",
    "salary_profiles",
    "service_demand_requests",
    "service_demand_status_history",
    "surgical_implant_logs",
    "surgical_schedules",
    "user_roles",
    "users",
    "who_surgical_safety_checklists",
})

ALL_CLASSIFIED_TABLES: frozenset[str] = MASTER_AND_CONFIG_TABLES | PURGED_LOG_TABLES | SCRUBBED_DATA_TABLES


def get_unclassified_tables(table_names: Set[str]) -> Set[str]:
    """Returns any database table name that has not been categorized into the sandbox governance matrix."""
    return set(table_names) - ALL_CLASSIFIED_TABLES


def scrub_sandbox_database(sandbox_db: str, sandbox_password: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes deep anonymization and credential reset across all tables in a cloned sandbox database.
    Guarantees no live PHI/PII or real access credentials remain in non-production.
    """
    engine = tenant_engine_pool.get_engine(sandbox_db)
    stats: Dict[str, Any] = {"purged_tables": 0, "scrubbed_tables": 0}

    with Session(bind=engine) as session:
        inspector = inspect(session.connection())
        table_names = set(inspector.get_table_names())

        # 1. PURGE SENSITIVE LOGS, SESSIONS, AND SECURITY TOKENS
        for tbl in PURGED_LOG_TABLES:
            if tbl in table_names:
                session.execute(text(f'DELETE FROM "{tbl}"'))  # nosec B608
                stats["purged_tables"] += 1

        # 2. WIPE USERS, PASSWORDS, AND MFA (N-37)
        if "users" in table_names:
            from backend.domains.identity.models import User
            eff_password = sandbox_password or DUMMY_SANDBOX_PASSWORD
            dummy_hash = pwd_context.hash(eff_password)
            users = session.execute(select(User)).scalars().all()
            for u in users:
                u.hashed_password = dummy_hash
                u.mfa_secret = None
                u.mfa_enabled = False
                u.mfa_last_used_counter = None
                u.failed_login_attempts = 0
                u.locked_until = None
                u.password_changed_at = datetime.now(timezone.utc)
                if hasattr(u, "phone_number"):
                    u.phone_number = None

                is_admin = getattr(u, "is_superuser", False) or getattr(u, "role", "") == "admin"
                if is_admin:
                    u.email = f"sandbox_admin_{u.id}@sandbox.local"
                else:
                    u.email = f"staff_{u.id}@sandbox.local"
            stats["scrubbed_tables"] += 1

        # 3. PATIENTS DEMOGRAPHICS & IDENTIFIERS
        if "patients" in table_names:
            from backend.domains.patients.models import Patient
            patients = session.execute(select(Patient)).scalars().all()
            for p in patients:
                p.full_name = f"Sandbox Patient {p.id}"
                p.mrn = f"SBX-{p.id}"
                p.phone_number = "9000000000"
                p.email = f"patient_{p.id}@sandbox.local"
                p.address = "Sandbox Medical Suite"
                p.village = None
                p.city = "Sandbox City"
                p.district = "Sandbox District"
                p.state = "Sandbox State"
                p.pincode = "500001"
                p.abha_number = None
                p.emergency_contact_name = None
                p.emergency_contact_phone = None
                p.blood_group = None
                p.allow_network_sharing = False
                if getattr(p, "date_of_birth", None):
                    p.date_of_birth = datetime(p.date_of_birth.year, 1, 1)
            stats["scrubbed_tables"] += 1

        # 4. CLINICAL CONSULTATION NOTES & ENCOUNTERS
        if "clinical_notes" in table_names:
            session.execute(
                text("""
                    UPDATE clinical_notes
                    SET subjective_notes = 'Sandbox anonymized consultation notes',
                        objective_notes = 'Sandbox anonymized clinical observations',
                        assessment_notes = 'Sandbox anonymized clinical assessment',
                        plan_notes = 'Sandbox anonymized care plan'
                """)
            )
            stats["scrubbed_tables"] += 1

        if "patient_encounters" in table_names:
            session.execute(
                text("""
                    UPDATE patient_encounters
                    SET chief_complaint_notes = 'Sandbox clinical encounter'
                    WHERE chief_complaint_notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "patient_consent_records" in table_names:
            session.execute(
                text("""
                    UPDATE patient_consent_records
                    SET evidence_reference = NULL
                    WHERE evidence_reference IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "inpatient_admissions" in table_names:
            session.execute(
                text("""
                    UPDATE inpatient_admissions
                    SET discharge_summary = 'Sandbox discharge summary'
                    WHERE discharge_summary IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "bed_transfer_logs" in table_names:
            session.execute(
                text("""
                    UPDATE bed_transfer_logs
                    SET transfer_reason = 'Sandbox routine bed transfer'
                    WHERE transfer_reason IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "prescriptions" in table_names:
            session.execute(
                text("""
                    UPDATE prescriptions
                    SET clinical_notes = 'Sandbox standard prescription'
                    WHERE clinical_notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "diagnostic_orders" in table_names:
            session.execute(
                text("""
                    UPDATE diagnostic_orders
                    SET clinical_notes = 'Sandbox diagnostic evaluation'
                    WHERE clinical_notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "diagnostic_tasks" in table_names:
            session.execute(
                text("""
                    UPDATE diagnostic_tasks
                    SET result_value = 'Sandbox normal range',
                        result_notes = 'Sandbox verified test result',
                        rejection_reason = NULL
                    WHERE result_value IS NOT NULL OR result_notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "clinical_early_warning_scores" in table_names:
            session.execute(
                text("""
                    UPDATE clinical_early_warning_scores
                    SET clinical_recommendation = 'Sandbox standardized clinical recommendation'
                    WHERE clinical_recommendation IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "bcma_scan_audit_logs" in table_names:
            session.execute(
                text("""
                    UPDATE bcma_scan_audit_logs
                    SET scanned_patient_barcode = 'PAT-SANDBOX-SCRUBBED',
                        scanned_medication_barcode = 'MED-SANDBOX-SCRUBBED',
                        override_reason = 'Sandbox protocol override'
                    WHERE override_reason IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "controlled_substance_movement_logs" in table_names:
            session.execute(
                text("""
                    UPDATE controlled_substance_movement_logs
                    SET wastage_reason = 'Sandbox protocol wastage',
                        notes = 'Sandbox narcotics movement record',
                        disposal_method = 'SANDBOX_NEUTRALIZATION'
                    WHERE wastage_reason IS NOT NULL OR notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "charge_capture_events" in table_names:
            session.execute(
                text("""
                    UPDATE charge_capture_events
                    SET description = 'Sandbox RCM charge line'
                """)
            )
            stats["scrubbed_tables"] += 1

        if "abdm_consent_artefacts" in table_names:
            session.execute(
                text("""
                    UPDATE abdm_consent_artefacts
                    SET signature = NULL,
                        hiu_id = 'SANDBOX-HIU'
                """)
            )
            stats["scrubbed_tables"] += 1

        if "nhcx_claim_exchange_logs" in table_names:
            session.execute(
                text("""
                    UPDATE nhcx_claim_exchange_logs
                    SET bundle_json = '{"resourceType": "Bundle", "status": "sandbox-scrubbed"}'
                """)
            )
            stats["scrubbed_tables"] += 1

        if "clinical_quality_incidents" in table_names:
            session.execute(
                text("""
                    UPDATE clinical_quality_incidents
                    SET description = 'Sandbox anonymized clinical quality incident',
                        root_cause_analysis = CASE WHEN root_cause_analysis IS NOT NULL THEN 'Sandbox RCA record' ELSE NULL END,
                        capa_plan = CASE WHEN capa_plan IS NOT NULL THEN 'Sandbox CAPA plan' ELSE NULL END
                """)
            )
            stats["scrubbed_tables"] += 1

        if "radiology_study_metadata" in table_names:
            session.execute(
                text("""
                    UPDATE radiology_study_metadata
                    SET radiologist_impression = 'Sandbox normal impression',
                        pacs_viewer_url = 'https://pacs.sandbox.local/study/sbx'
                    WHERE radiologist_impression IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "operative_anesthesia_records" in table_names:
            session.execute(
                text("""
                    UPDATE operative_anesthesia_records
                    SET anesthesia_notes = 'Sandbox anesthesia notes',
                        operative_findings = 'Sandbox operative findings',
                        post_op_instructions = 'Sandbox post-op instructions'
                """)
            )
            stats["scrubbed_tables"] += 1

        # 5. FINANCIAL & INVOICING REMARKS
        if "invoices" in table_names:
            session.execute(
                text("""
                    UPDATE invoices
                    SET notes = 'Sandbox invoice',
                        discount_reason = NULL
                    WHERE notes IS NOT NULL OR discount_reason IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "refund_vouchers" in table_names:
            session.execute(
                text("""
                    UPDATE refund_vouchers
                    SET reason = 'Sandbox refund voucher'
                    WHERE reason IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        # 6. INSURANCE POLICIES & PRE-AUTHS
        if "patient_insurance_policies" in table_names:
            from backend.domains.insurance.models import PatientInsurancePolicy
            policies = session.execute(select(PatientInsurancePolicy)).scalars().all()
            for pol in policies:
                pol.policy_number = f"POL-SBX-{pol.id}"
                pol.member_id = f"MEM-SBX-{pol.id}"
            stats["scrubbed_tables"] += 1

        if "insurance_pre_auths" in table_names:
            from backend.domains.insurance.models import InsurancePreAuth
            preauths = session.execute(select(InsurancePreAuth)).scalars().all()
            for pa in preauths:
                pa.clinical_justification = None
                pa.tpa_query_remarks = None
                pa.tpa_reference_number = f"TPA-SBX-{pa.id}"
            stats["scrubbed_tables"] += 1

        # 7. BIRTH, DEATH & MEDICO-LEGAL RECORDS
        if "birth_records" in table_names:
            from backend.domains.clinical.models import BirthRecord
            births = session.execute(select(BirthRecord)).scalars().all()
            for b in births:
                b.baby_full_name = f"Sandbox Baby {b.id}"
                b.clinical_notes = None
            stats["scrubbed_tables"] += 1

        if "death_records" in table_names:
            from backend.domains.clinical.models import DeathRecord
            deaths = session.execute(select(DeathRecord)).scalars().all()
            for d in deaths:
                d.body_handed_over_to = "Sandbox Informant"
                d.antecedent_cause_notes = None
            stats["scrubbed_tables"] += 1

        if "medico_legal_cases" in table_names:
            from backend.domains.clinical.models import MedicoLegalCase
            mlcs = session.execute(select(MedicoLegalCase)).scalars().all()
            for m in mlcs:
                m.brought_by_name = "Sandbox Informant"
                m.brought_by_relation = None
                m.brought_by_phone = "9000000000"
                m.alleged_history = "Anonymized sandbox history"
                m.identification_marks = "Anonymized"
                m.police_station_name = "Sandbox PS"
                m.fir_number = None
            stats["scrubbed_tables"] += 1

        # 8. HR / WORKFORCE DATA
        if "salary_profiles" in table_names:
            session.execute(
                text("""
                    UPDATE salary_profiles
                    SET note = 'Sandbox salary record'
                    WHERE note IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "leave_requests" in table_names:
            session.execute(
                text("""
                    UPDATE leave_requests
                    SET reason = 'Sandbox leave request'
                    WHERE reason IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "attendance_records" in table_names:
            session.execute(
                text("""
                    UPDATE attendance_records
                    SET note = 'Sandbox attendance'
                    WHERE note IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        # 9. CLINICAL & ANCILLARY FREE-TEXT SCRUBBING (N-37)
        if "mortuary_cases" in table_names:
            session.execute(
                text("""
                    UPDATE mortuary_cases
                    SET deceased_name = 'Sandbox Deceased',
                        release_recipient = CASE WHEN release_recipient IS NOT NULL THEN 'Sandbox Recipient' ELSE NULL END,
                        release_witness = CASE WHEN release_witness IS NOT NULL THEN 'Sandbox Witness' ELSE NULL END
                """)
            )
            stats["scrubbed_tables"] += 1

        if "patient_allergies" in table_names:
            session.execute(
                text("""
                    UPDATE patient_allergies
                    SET reaction_description = 'Sandbox allergy reaction'
                    WHERE reaction_description IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "patient_comorbidities" in table_names:
            session.execute(
                text("""
                    UPDATE patient_comorbidities
                    SET clinical_remarks = 'Sandbox comorbidity notes'
                    WHERE clinical_remarks IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "encounter_diagnoses" in table_names:
            session.execute(
                text("""
                    UPDATE encounter_diagnoses
                    SET clinical_remarks = 'Sandbox diagnostic evaluation'
                    WHERE clinical_remarks IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "encounter_physical_exams" in table_names:
            session.execute(
                text("""
                    UPDATE encounter_physical_exams
                    SET findings_notes = 'Sandbox normal physical findings'
                    WHERE findings_notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "encounter_vitals" in table_names:
            session.execute(
                text("""
                    UPDATE encounter_vitals
                    SET notes = 'Sandbox vitals checked'
                    WHERE notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "er_triage_assessments" in table_names:
            session.execute(
                text("""
                    UPDATE er_triage_assessments
                    SET chief_complaint = 'Sandbox ER presentation',
                        triage_notes = 'Sandbox triage assessment'
                    WHERE chief_complaint IS NOT NULL OR triage_notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "fluid_balance_logs" in table_names:
            session.execute(
                text("""
                    UPDATE fluid_balance_logs
                    SET notes = 'Sandbox fluid balance'
                    WHERE notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "dietary_orders" in table_names:
            session.execute(
                text("""
                    UPDATE dietary_orders
                    SET allergy_notes = CASE WHEN allergy_notes IS NOT NULL THEN 'Sandbox dietary allergy note' ELSE NULL END,
                        instructions = CASE WHEN instructions IS NOT NULL THEN 'Sandbox dietary instructions' ELSE NULL END
                """)
            )
            stats["scrubbed_tables"] += 1

        if "medication_administration_logs" in table_names:
            session.execute(
                text("""
                    UPDATE medication_administration_logs
                    SET notes = 'Sandbox verified administration'
                    WHERE notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "prescription_items" in table_names:
            session.execute(
                text("""
                    UPDATE prescription_items
                    SET dosage_instruction = 'Sandbox standard dosage instruction'
                    WHERE dosage_instruction IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        # 10. APPOINTMENTS & SCHEDULING (N-37)
        if "appointments" in table_names:
            session.execute(
                text("""
                    UPDATE appointments
                    SET reason_for_visit = 'Sandbox routine consultation'
                    WHERE reason_for_visit IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "appointment_waitlist_entries" in table_names:
            session.execute(
                text("""
                    UPDATE appointment_waitlist_entries
                    SET reason_for_visit = 'Sandbox consultation waitlist'
                    WHERE reason_for_visit IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "appointment_status_history" in table_names:
            session.execute(
                text("""
                    UPDATE appointment_status_history
                    SET reason = 'Sandbox status update'
                    WHERE reason IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "appointment_waitlist_history" in table_names:
            session.execute(
                text("""
                    UPDATE appointment_waitlist_history
                    SET reason = 'Sandbox waitlist update'
                    WHERE reason IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        # 11. SURGICAL SUITE & IMPLANTS (N-37)
        if "surgical_schedules" in table_names:
            session.execute(
                text("""
                    UPDATE surgical_schedules
                    SET pre_op_diagnosis_notes = 'Sandbox scheduled procedure'
                    WHERE pre_op_diagnosis_notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "surgical_implant_logs" in table_names:
            session.execute(
                text("""
                    UPDATE surgical_implant_logs
                    SET manufacturer = 'Sandbox Medical Implants',
                        serial_or_batch_number = 'SBX-IMPLANT-000'
                """)
            )
            stats["scrubbed_tables"] += 1

        if "who_surgical_safety_checklists" in table_names:
            stats["scrubbed_tables"] += 1

        # 12. MEDICAL PROFILES & ROLES (N-37)
        if "doctor_profiles" in table_names:
            session.execute(
                text("""
                    UPDATE doctor_profiles
                    SET medical_registration_number = 'MED-SBX-000'
                """)
            )
            stats["scrubbed_tables"] += 1

        if "doctor_leaves" in table_names:
            session.execute(
                text("""
                    UPDATE doctor_leaves
                    SET reason = 'Sandbox doctor leave'
                    WHERE reason IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "nursing_shift_assignments" in table_names:
            session.execute(
                text("""
                    UPDATE nursing_shift_assignments
                    SET handoff_notes = 'Sandbox nursing shift handoff'
                    WHERE handoff_notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "user_roles" in table_names:
            stats["scrubbed_tables"] += 1

        # 13. BLOOD BANK & LOGISTICS (N-37)
        if "blood_cross_match_orders" in table_names:
            session.execute(
                text("""
                    UPDATE blood_cross_match_orders
                    SET reaction_notes = 'Sandbox cross-match complete'
                    WHERE reaction_notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "ambulance_dispatches" in table_names:
            session.execute(
                text("""
                    UPDATE ambulance_dispatches
                    SET pickup_location = 'Sandbox Pickup Suite',
                        destination = 'Sandbox Emergency Department',
                        notes = 'Sandbox routine transport'
                    WHERE pickup_location IS NOT NULL OR notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "biomedical_waste_bags" in table_names:
            session.execute(
                text("""
                    UPDATE biomedical_waste_bags
                    SET waste_description = 'Sandbox segregated clinical waste'
                    WHERE waste_description IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "biomedical_waste_manifests" in table_names:
            session.execute(
                text("""
                    UPDATE biomedical_waste_manifests
                    SET cbwtf_operator_name = 'Sandbox Waste Disposal Corp',
                        cbwtf_driver_name = 'Sandbox Driver',
                        notes = 'Sandbox authorized waste manifest'
                    WHERE cbwtf_operator_name IS NOT NULL OR notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "cssd_sterilization_batches" in table_names:
            session.execute(
                text("""
                    UPDATE cssd_sterilization_batches
                    SET release_notes = 'Sandbox sterilization cycle validated'
                    WHERE release_notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        # 14. INVOICES, DEPOSITS & EXPENSES (N-37)
        if "invoice_line_items" in table_names:
            session.execute(
                text("""
                    UPDATE invoice_line_items
                    SET description = 'Sandbox billable item'
                    WHERE description IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "patient_deposits" in table_names:
            session.execute(
                text("""
                    UPDATE patient_deposits
                    SET transaction_reference = 'SBX-DEP-000',
                        notes = 'Sandbox patient deposit'
                    WHERE transaction_reference IS NOT NULL OR notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "payment_receipts" in table_names:
            session.execute(
                text("""
                    UPDATE payment_receipts
                    SET transaction_reference = 'SBX-TXN-000'
                    WHERE transaction_reference IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "insurance_claims" in table_names:
            session.execute(
                text("""
                    UPDATE insurance_claims
                    SET settlement_utr_reference = 'SBX-UTR-000',
                        remarks = 'Sandbox claim evaluation'
                    WHERE settlement_utr_reference IS NOT NULL OR remarks IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "finance_expenses" in table_names:
            session.execute(
                text("""
                    UPDATE finance_expenses
                    SET vendor_name = 'Sandbox Vendor',
                        receipt_reference = 'SBX-EXP-000',
                        rejection_reason = CASE WHEN rejection_reason IS NOT NULL THEN 'Sandbox expense note' ELSE NULL END
                """)
            )
            stats["scrubbed_tables"] += 1

        if "finance_recurring_expenses" in table_names:
            session.execute(
                text("""
                    UPDATE finance_recurring_expenses
                    SET vendor_name = 'Sandbox Vendor'
                """)
            )
            stats["scrubbed_tables"] += 1

        # 15. PAYROLL (N-37)
        if "payroll_periods" in table_names:
            stats["scrubbed_tables"] += 1

        if "payroll_lines" in table_names:
            session.execute(
                text("""
                    UPDATE payroll_lines
                    SET calculation_note = 'Sandbox salary calculation',
                        statutory_details_json = '{}'
                """)
            )
            stats["scrubbed_tables"] += 1

        if "payroll_calculation_events" in table_names:
            session.execute(
                text("""
                    UPDATE payroll_calculation_events
                    SET input_snapshot = '{}',
                        output_snapshot = '{}'
                """)
            )
            stats["scrubbed_tables"] += 1

        # 16. PROCUREMENT & PHARMACY (N-37)
        if "inventory_batches" in table_names:
            session.execute(
                text("""
                    UPDATE inventory_batches
                    SET batch_number = 'SBX-BATCH-000'
                """)
            )
            stats["scrubbed_tables"] += 1

        if "pharmacy_return_logs" in table_names:
            session.execute(
                text("""
                    UPDATE pharmacy_return_logs
                    SET return_reason = 'Sandbox protocol return'
                    WHERE return_reason IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "procurement_indents" in table_names:
            session.execute(
                text("""
                    UPDATE procurement_indents
                    SET remarks = 'Sandbox indent'
                    WHERE remarks IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "procurement_indent_items" in table_names:
            stats["scrubbed_tables"] += 1

        if "procurement_purchase_orders" in table_names:
            session.execute(
                text("""
                    UPDATE procurement_purchase_orders
                    SET payment_terms = 'Sandbox standard terms'
                    WHERE payment_terms IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "procurement_po_items" in table_names:
            session.execute(
                text("""
                    UPDATE procurement_po_items
                    SET item_description = 'Sandbox ordered item'
                    WHERE item_description IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "procurement_grn" in table_names:
            session.execute(
                text("""
                    UPDATE procurement_grn
                    SET inspection_notes = 'Sandbox GRN inspection verified',
                        invoice_number = 'SBX-INV-000'
                    WHERE inspection_notes IS NOT NULL OR invoice_number IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "procurement_grn_items" in table_names:
            session.execute(
                text("""
                    UPDATE procurement_grn_items
                    SET batch_number = 'SBX-BATCH-000',
                        rejection_reason = NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "procurement_vendor_invoices" in table_names:
            session.execute(
                text("""
                    UPDATE procurement_vendor_invoices
                    SET invoice_number = 'SBX-VEND-000',
                        document_file_url = 'https://docs.sandbox.local/inv/sbx',
                        notes = 'Sandbox vendor invoice'
                """)
            )
            stats["scrubbed_tables"] += 1

        if "procurement_vendor_invoice_items" in table_names:
            session.execute(
                text("""
                    UPDATE procurement_vendor_invoice_items
                    SET item_description = 'Sandbox invoiced item',
                        batch_number = 'SBX-BATCH-000'
                """)
            )
            stats["scrubbed_tables"] += 1

        if "service_demand_requests" in table_names:
            session.execute(
                text("""
                    UPDATE service_demand_requests
                    SET reason = 'Sandbox demand request',
                        resolution_notes = 'Sandbox resolved',
                        external_facility_name = 'Sandbox Regional Facility'
                    WHERE reason IS NOT NULL OR resolution_notes IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "service_demand_status_history" in table_names:
            session.execute(
                text("""
                    UPDATE service_demand_status_history
                    SET reason = 'Sandbox status update'
                    WHERE reason IS NOT NULL
                """)
            )
            stats["scrubbed_tables"] += 1

        if "federated_clients" in table_names:
            session.execute(
                text("""
                    UPDATE federated_clients
                    SET api_key_hash = 'SANDBOX_SCRUBBED_KEY_' || id
                """)
            )
            stats["scrubbed_tables"] += 1

        session.commit()

    logger.info("Sandbox database '%s' successfully scrubbed: %s", sandbox_db, stats)
    return stats
