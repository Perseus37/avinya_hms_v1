import json

from sqlalchemy import text
from sqlalchemy.orm import Session


MODULES = (
    ("opd", "OPD Appointments & Queue TV Engine", "Doctor schedules and waiting room feeds.", 2500000),
    ("ipd_adt", "IPD, Bed Matrix & Spatial ADT", "Ward tracking and clinical workflows.", 4000000),
    ("pharmacy", "Pharmacy Formulary & FEFO", "Stock deductions and e-prescriptions.", 3000000),
    ("diagnostics", "Laboratory Information System", "Order sets and pathologist sign-offs.", 3500000),
    ("pacs_radiology", "Radiology PACS & DICOM", "DICOM viewing and dose audits.", 4500000),
    ("billing", "Core Patient Billing & POS", "UPI/Cards and thermal receipts.", 2500000),
    ("insurance_tpa", "Insurance Pre-Auth & Claims", "Co-pay and room rent capping.", 4000000),
    ("ot_surgery", "Operation Theatre & Safety", "WHO sign-offs and implant tracing.", 4500000),
    ("blood_bank", "Blood Bank & Cross-Matching", "Hemovigilance and transfusion guards.", 3500000),
    ("er_mlc", "Emergency Triage & MLC", "Triage prioritization and police intimations.", 3000000),
    ("executive_bi", "CFO Revenue Cycle & BI", "AR aging and doctor productivity.", 5000000),
    ("procurement", "Supply Chain & Procurement", "Vendor POs, GRNs, and 3-way matching.", 3000000),
    ("ambulance", "Ambulance Operations", "Vehicle dispatch, response status and transfer coordination.", 2000000),
    ("dietary", "Dietary Services", "Patient diet orders, meal preparation and delivery.", 1500000),
    ("cssd", "Central Sterile Services", "Instrument set tracking and sterilization cycle release controls.", 2000000),
    ("mortuary", "Mortuary Services", "Decedent custody, case intake and witnessed release records.", 1500000),
    ("workforce", "Workforce & Payroll", "Staff attendance, leave requests, salary profiles and auditable payroll calculations.", 2500000),
)

BUNDLES = (
    ("BUNDLE_TOTAL_HOSPITAL", "Total Flagship", ("opd", "ipd_adt", "pharmacy", "diagnostics", "pacs_radiology", "billing", "insurance_tpa", "ot_surgery", "blood_bank", "er_mlc", "executive_bi", "procurement", "ambulance", "dietary", "cssd", "mortuary", "workforce"), 39000000),
    ("BUNDLE_SURGICAL_SUITE", "Surgical Pack", ("ipd_adt", "ot_surgery", "pharmacy", "billing", "insurance_tpa", "procurement"), 17500000),
    ("BUNDLE_DIAGNOSTICS_HUB", "Diagnostics Hub", ("diagnostics", "pacs_radiology", "billing", "pharmacy"), 11000000),
    ("BUNDLE_OPD_DAYCARE", "OPD Daycare", ("opd", "pharmacy", "billing"), 6500000),

)

BLUEPRINTS = (
    ("TIER_1_QUATERNARY_500_BED", "500-Bed Quaternary Flagship", "2 Floors, ER Bay, Medical ICU, Surgical Ward + 2 OTs.", 500, 2, 2),
    ("BOUTIQUE_MATERNITY_NICU", "Maternity, Fertility & NICU", "LDR Deluxe Suite + Level III NICU + OB-GYN OT.", 50, 1, 1),
    ("TRAUMA_EMERGENCY_HUB", "Acute Trauma & ER Response", "6 Red Resuscitation ICU Bays + 24/7 Ortho-Trauma Theatre.", 100, 1, 1),
    ("POLYCLINIC_DAYCARE_OPD", "Polyclinic & Daycare OPD", "Outpatient Consulting + 4 Daycare Observation Beds.", 4, 1, 0),
    ("BLANK_CANVAS", "Blank Canvas (100% Custom)", "Zero pre-built floors or beds.", 0, 0, 0),
)

MIGRATION_SCHEMAS = (
    ("PATIENTS", {"mrn": "string", "full_name": "string", "date_of_birth": "date", "gender": "string", "phone_number": "string"}),
    ("STAFF_USERS", {"login_id": "string", "email": "string", "hashed_password": "string", "full_name": "string", "gender": "string", "status": "string"}),
    ("INVOICES", {"invoice_number": "string", "patient_mrn": "string", "total_amount_paise": "number", "status": "string"}),
)


def seed_control_plane(db: Session) -> None:
    """Seed system catalogs after their control-plane Alembic migration."""
    for code, name, description, price in MODULES:
        db.execute(
            text("""
                INSERT INTO module_catalog
                    (code, name, description, base_annual_price_paise, is_active)
                VALUES (:code, :name, :description, :price, true)
                ON CONFLICT (code) DO NOTHING
            """),
            {"code": code, "name": name, "description": description, "price": price},
        )

    for code, name, modules, price in BUNDLES:
        db.execute(
            text("""
                INSERT INTO bundle_catalog
                    (code, name, modules_json, bundle_annual_price_paise, is_active)
                VALUES (:code, :name, :modules, :price, true)
                ON CONFLICT (code) DO NOTHING
            """),
            {"code": code, "name": name, "modules": json.dumps(modules), "price": price},
        )

    for key, name, description, beds, floors, theatres in BLUEPRINTS:
        db.execute(
            text("""
                INSERT INTO blueprint_catalog
                    (key, name, description, beds, floors_count, theatres_count, is_active)
                VALUES (:key, :name, :description, :beds, :floors, :theatres, true)
                ON CONFLICT (key) DO NOTHING
            """),
            {
                "key": key,
                "name": name,
                "description": description,
                "beds": beds,
                "floors": floors,
                "theatres": theatres,
            },
        )

    for entity_type, schema in MIGRATION_SCHEMAS:
        db.execute(
            text("""
                INSERT INTO migration_schemas (entity_type, schema_json, is_active)
                VALUES (:entity_type, :schema, true)
                ON CONFLICT (entity_type) DO UPDATE
                SET schema_json = EXCLUDED.schema_json, is_active = true
            """),
            {"entity_type": entity_type, "schema": json.dumps(schema)},
        )

    db.commit()
