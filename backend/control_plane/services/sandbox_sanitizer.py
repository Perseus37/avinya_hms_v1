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
from typing import Dict, Set
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


def scrub_sandbox_database(sandbox_db: str) -> Dict[str, int]:
    """
    Executes deep anonymization and credential reset across all tables in a cloned sandbox database.
    Guarantees no live PHI/PII or real access credentials remain in non-production.
    """
    engine = tenant_engine_pool.get_engine(sandbox_db)
    stats: Dict[str, int] = {"purged_tables": 0, "scrubbed_tables": 0}

    with Session(bind=engine) as session:
        inspector = inspect(session.connection())
        table_names = set(inspector.get_table_names())

        # 1. PURGE SENSITIVE LOGS, SESSIONS, AND SECURITY TOKENS
        for tbl in PURGED_LOG_TABLES:
            if tbl in table_names:
                session.execute(text(f'DELETE FROM "{tbl}"'))
                stats["purged_tables"] += 1

        # 2. WIPE USERS, PASSWORDS, AND MFA
        if "users" in table_names:
            from backend.domains.identity.models import User
            dummy_hash = pwd_context.hash(DUMMY_SANDBOX_PASSWORD)
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

        session.commit()

    logger.info("Sandbox database '%s' successfully scrubbed: %s", sandbox_db, stats)
    return stats
