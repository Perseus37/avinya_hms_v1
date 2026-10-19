import logging
from sqlalchemy.orm import Session

from backend.domains.reports.models import ReportTemplate
from backend.domains.identity.models import (
    Department,
    GenderEnum,
    Permission,
    Role,
    User,
    UserRole,
    UserStatusEnum,
)
from backend.domains.patients.models import (
    AllergyCategoryEnum,
    AllergyMaster,
    AllergySeverityEnum,
    ChiefComplaintMaster,
    ComorbidityMaster,
    DiagnosisMaster,
)
from backend.domains.reports.templates import STANDARD_TEMPLATES
from backend.domains.identity.passwords import pwd_context

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("avinya.seed")

# Granular Resource:Action Permissions Across All Modules
GRANULAR_PERMISSIONS = [
    # Patients & MPI
    ("patients:read", "View patient profiles and charted allergies", "Patients"),
    ("patients:write", "Register patients and chart allergies", "Patients"),
    ("patients:delete", "Deactivate patient records or allergies", "Patients"),
    # Clinical MDM
    ("mdm:read", "View clinical dictionaries and taxonomies", "ClinicalMDM"),
    ("mdm:write", "Create and edit ICD-10, allergies, and complaints", "ClinicalMDM"),
    ("mdm:delete", "Deactivate clinical taxonomy items", "ClinicalMDM"),
    # OPD Scheduling
    ("opd:read", "View outpatient appointment schedules", "OPD"),
    ("opd:write", "Book, reschedule, and advance OPD appointments", "OPD"),
    ("opd:cancel", "Cancel outpatient appointments", "OPD"),
    # ADT & Bed Movements
    ("adt:read", "View active encounters and inpatient admissions", "ADT"),
    ("adt:write", "Create encounters, admit patients, and transfer beds", "ADT"),
    ("adt:discharge", "Execute clinical discharges and cancel admissions", "ADT"),
    # Ward & Facility Assets
    ("wards:read", "View spatial ward map, rooms, beds, and shifts", "WardManagement"),
    ("wards:write", "Create/update floors, wards, rooms, beds, and shifts", "WardManagement"),
    ("wards:delete", "Deactivate floors, wards, rooms, and move beds to maintenance", "WardManagement"),
    # Diagnostics & LIS
    ("diagnostics:read", "View diagnostic catalog, orders, and lab queues", "Diagnostics"),
    ("diagnostics:write", "Order diagnostic tests and enter lab results", "Diagnostics"),
    ("diagnostics:verify", "Verify lab results (separation of duties) and cancel orders", "Diagnostics"),
    # Pharmacy & Inventory
    ("pharmacy:read", "View formulary, inventory batches, and prescriptions", "Pharmacy"),
    ("pharmacy:write", "Receive stock, write prescriptions, and dispense medications", "Pharmacy"),
    ("pharmacy:void", "Void inventory batches and cancel prescriptions", "Pharmacy"),
    # Billing & Financial Ledger
    ("billing:read", "View patient invoices and payment receipts", "Billing"),
    ("billing:write", "Create invoices, append catalog items, and record payments", "Billing"),
    ("billing:reverse", "Void invoices, remove line items, and reverse payments", "Billing"),
    # IAM & User Administration
    ("iam:read", "View users, roles, departments, and permissions", "IAM"),
    ("iam:write", "Create/update users, doctors, roles, and assign permissions", "IAM"),
    ("iam:delete", "Delete custom roles and suspend user accounts", "IAM"),
    # procurement
    ("procurement:read", "View vendors, POs, GRNs, and vendor invoices", "Procurement"),
    ("procurement:write", "Draft POs, register vendors, and upload invoices", "Procurement"),
    ("procurement:approve", "Approve POs, GRNs, and financial matching", "Procurement"),
    # Hospital support services
    ("ambulance:read", "View ambulance vehicles and dispatches", "Ambulance"),
    ("ambulance:write", "Register vehicles and manage emergency dispatches", "Ambulance"),
    ("dietary:read", "View patient meal orders and delivery status", "Dietary"),
    ("dietary:write", "Create and fulfill patient meal orders", "Dietary"),
    ("cssd:read", "View sterile instrument sets and cycle batches", "CSSD"),
    ("cssd:write", "Register instrument sets and document sterilization cycles", "CSSD"),
    ("mortuary:read", "View mortuary custody records", "Mortuary"),
    ("mortuary:write", "Receive and release mortuary cases", "Mortuary"),
    # Privacy governance
    ("privacy:read", "View consent records and retention policies", "Privacy"),
    ("privacy:write", "Record consent decisions and retention policies", "Privacy"),
    ("privacy:approve", "Approve privacy policy revisions and release legal holds", "Privacy"),
    ("break_glass:request", "Request audited emergency patient access", "Privacy"),
    # Workforce and payroll
    ("hr:read", "View staff attendance, leave, salary profiles and payroll", "Workforce"),
    ("hr:write", "Record attendance, leave requests and salary profiles", "Workforce"),
    ("hr:approve", "Approve leave requests and finalize payroll periods", "Workforce"),
    ("hr:payroll", "Create payroll periods and calculate payroll lines", "Workforce"),
    ("payroll:read", "View confidential salary and payroll records", "Payroll"),
]

STANDARD_ALLERGIES = [
    ("ALG_PEN", "Penicillin / Beta-Lactams", AllergyCategoryEnum.DRUG, AllergySeverityEnum.ANAPHYLACTIC),
    ("ALG_SULFA", "Sulfonamides (Sulfa Drugs)", AllergyCategoryEnum.DRUG, AllergySeverityEnum.SEVERE),
    ("ALG_NSAID", "NSAIDs (Aspirin / Ibuprofen / Diclofenac)", AllergyCategoryEnum.DRUG, AllergySeverityEnum.SEVERE),
    ("ALG_CEPH", "Cephalosporins", AllergyCategoryEnum.DRUG, AllergySeverityEnum.SEVERE),
    ("ALG_LATEX", "Natural Rubber Latex", AllergyCategoryEnum.ENVIRONMENTAL, AllergySeverityEnum.MODERATE),
    ("ALG_CONTRAST", "Iodinated Radiocontrast Media", AllergyCategoryEnum.DRUG, AllergySeverityEnum.SEVERE),
    ("ALG_PEANUT", "Peanuts / Tree Nuts", AllergyCategoryEnum.FOOD, AllergySeverityEnum.ANAPHYLACTIC),
]

STANDARD_ICD10_DIAGNOSES = [
    ("I10", "Essential (primary) hypertension", "Essential (primary) systemic arterial hypertension"),
    ("E11.9", "Type 2 diabetes mellitus without complications", "Type 2 diabetes mellitus without complications"),
    ("J06.9", "Acute upper respiratory infection, unspecified", "Acute upper respiratory tract infection, unspecified"),
    ("A09", "Infectious gastroenteritis and colitis", "Infectious gastroenteritis and colitis, unspecified"),
    ("J18.9", "Pneumonia, unspecified organism", "Community-acquired or unspecified pneumonia"),
    ("N39.0", "Urinary tract infection, site not specified", "Urinary tract infection, site not specified"),
    ("K21.9", "Gastro-esophageal reflux disease without esophagitis", "GERD without erosive esophagitis"),
    ("I21.9", "Acute myocardial infarction, unspecified", "Acute myocardial infarction, unspecified"),
]

STANDARD_COMPLAINTS = [
    ("CC_FEVER", "Fever / Pyrexia"),
    ("CC_CHEST_PAIN", "Acute Chest Pain / Discomfort"),
    ("CC_DYSPNEA", "Shortness of Breath (Dyspnea)"),
    ("CC_ABD_PAIN", "Abdominal Pain"),
    ("CC_HEADACHE", "Severe Headache / Migraine"),
    ("CC_COUGH", "Persistent Cough"),
    ("CC_TRAUMA", "Physical Trauma / Injury"),
]

STANDARD_COMORBIDITIES = [
    ("CM_HTN", "Systemic Hypertension", "I10"),
    ("CM_T2DM", "Type 2 Diabetes Mellitus", "E11.9"),
    ("CM_CAD", "Coronary Artery Disease", "I25.10"),
    ("CM_CKD", "Chronic Kidney Disease", "N18.9"),
    ("CM_COPD", "Chronic Obstructive Pulmonary Disease", "J44.9"),
    ("CM_ASTHMA", "Bronchial Asthma", "J45.909"),
]


def ensure_permissions_exist(db: Session) -> dict[str, Permission]:
    """Idempotently ensures all granular permissions exist in a tenant database."""
    existing = {p.code: p for p in db.query(Permission).all()}
    for code, desc, module in GRANULAR_PERMISSIONS:
        if code not in existing:
            perm = Permission(code=code, description=desc, module=module)
            db.add(perm)
            existing[code] = perm
    db.flush()
    return existing


def seed_universal_hospital_data(
    db: Session,
    hospital_name: str,
    branch_code: str,
    admin_email: str,
    admin_password: str,
    seed_icd10: bool = True,
    seed_allergies: bool = True,
    seed_complaints: bool = True,
) -> bool:
    """Seeds granular permissions, standard role templates, universal dictionaries, and one admin."""
    perm_map = ensure_permissions_exist(db)

    if db.query(User).first():
        db.commit()
        return False

    core_dept = Department(
        code="DEPT_GEN_MED",
        name="General & Internal Medicine",
        ndhm_speciality_code="IN001",
        is_active=True,
    )
    db.add(core_dept)
    db.flush()

    role_admin = Role(
        code="ROLE_ADMIN",
        name=f"{hospital_name} Administrator",
        description=f"Full system access for {hospital_name} ({branch_code})",
        is_system_role=True,
    )
    role_doctor = Role(
        code="ROLE_DOCTOR",
        name="Attending Physician",
        description="Read/Write clinical orders, OPD, ADT, diagnostics, and emergency access",
        is_system_role=True,
    )
    role_nurse = Role(
        code="ROLE_NURSE",
        name="Registered Nurse",
        description="Ward map, ADT bed movement, and emergency patient access",
        is_system_role=True,
    )
    role_pharm = Role(
        code="ROLE_PHARMACIST",
        name="Clinical Pharmacist",
        description="Pharmacy inventory and dispensing access",
        is_system_role=True,
    )
    role_billing = Role(
        code="ROLE_BILLING",
        name="Billing Executive",
        description="Patient invoicing and payment collection",
        is_system_role=True,
    )
    role_auditor = Role(
        code="ROLE_AUDITOR_READONLY",
        name="Clinical & Financial Auditor (Read-Only)",
        description="Strictly read-only access across modules",
        is_system_role=False,
    )

    role_admin.permissions.extend(
        perm for code, perm in perm_map.items() if code != "break_glass:request"
    )
    role_doctor.permissions.extend([
        perm_map["patients:read"], perm_map["patients:write"],
        perm_map["mdm:read"], perm_map["opd:read"], perm_map["opd:write"],
        perm_map["adt:read"], perm_map["adt:write"], perm_map["adt:discharge"],
        perm_map["wards:read"], perm_map["diagnostics:read"], perm_map["diagnostics:write"],
        perm_map["pharmacy:read"], perm_map["pharmacy:write"],
        perm_map["break_glass:request"],
    ])
    role_nurse.permissions.extend([
        perm_map["patients:read"], perm_map["wards:read"], perm_map["wards:write"],
        perm_map["adt:read"], perm_map["adt:write"], perm_map["break_glass:request"],
    ])
    role_pharm.permissions.extend([
        perm_map["patients:read"], perm_map["pharmacy:read"],
        perm_map["pharmacy:write"], perm_map["pharmacy:void"],
        perm_map["procurement:read"], perm_map["procurement:write"],
    ])
    role_billing.permissions.extend([
        perm_map["patients:read"], perm_map["billing:read"], perm_map["billing:write"],
        perm_map["procurement:approve"],
    ])
    role_auditor.permissions.extend([
        perm for code, perm in perm_map.items()
        if code.endswith(":read") and code != "payroll:read"
    ])

    db.add_all([role_admin, role_doctor, role_nurse, role_pharm, role_billing, role_auditor])
    db.flush()

    admin_user = User(
        login_id="ADMIN001",
        email=admin_email,
        hashed_password=pwd_context.hash(admin_password),
        full_name=f"{hospital_name} System Admin",
        gender=GenderEnum.OTHER,
        status=UserStatusEnum.ACTIVE,
        is_superuser=True,
    )
    db.add(admin_user)
    db.flush()
    db.add(UserRole(user_id=admin_user.id, role_id=role_admin.id, department_id=core_dept.id))

    if seed_allergies:
        for code, name, category, severity in STANDARD_ALLERGIES:
            db.add(
                AllergyMaster(
                    department_id=core_dept.id,
                    code=code,
                    name=name,
                    category=category,
                    default_severity=severity,
                    is_active=True,
                    created_by_id=admin_user.id,
                    updated_by_id=admin_user.id,
                )
            )

    if seed_icd10:
        for icd, short_desc, full_desc in STANDARD_ICD10_DIAGNOSES:
            db.add(
                DiagnosisMaster(
                    department_id=core_dept.id,
                    icd10_code=icd,
                    short_description=short_desc,
                    full_description=full_desc,
                    is_billable=True,
                    is_active=True,
                    created_by_id=admin_user.id,
                    updated_by_id=admin_user.id,
                )
            )

    if seed_complaints:
        for code, name in STANDARD_COMPLAINTS:
            db.add(
                ChiefComplaintMaster(
                    department_id=core_dept.id,
                    code=code,
                    name=name,
                    is_active=True,
                    created_by_id=admin_user.id,
                    updated_by_id=admin_user.id,
                )
            )
        for code, name, icd in STANDARD_COMORBIDITIES:
            db.add(
                ComorbidityMaster(
                    department_id=core_dept.id,
                    code=code,
                    name=name,
                    icd10_code=icd,
                    is_active=True,
                    created_by_id=admin_user.id,
                    updated_by_id=admin_user.id,
                )
            )

    db.commit()
    
    for tpl_data in STANDARD_TEMPLATES:
        existing_tpl = db.query(ReportTemplate).filter(ReportTemplate.code == tpl_data["code"]).first()
        if not existing_tpl:
            db.add(
                ReportTemplate(
                    **tpl_data,
                    is_system_template=True,
                    is_active=True,
                )
            )
    db.commit()
    return True