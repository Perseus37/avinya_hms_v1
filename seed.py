import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import SessionLocal
from models import (
    AllergyMaster,
    Bed,
    BedStatusEnum,
    BedTypeEnum,
    Department,
    DoctorProfile,
    Floor,
    GenderEnum,
    Permission,
    PhysicalRoom,
    Role,
    RoomTypeEnum,
    ServiceCatalog,
    ServiceCategory,
    User,
    UserRole,
    UserStatusEnum,
    Ward,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("avinya.seed")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def seed_enterprise_data():
    """Seeds initial foundational master data with environment-injected credentials."""
    db: Session = SessionLocal()
    try:
        if db.query(User).first():
            logger.info("Database already seeded. Skipping initialization.")
            return

        # H12 FIX: Pull credentials from environment variables or generate a random one-time password
        admin_email = os.getenv("SEED_ADMIN_EMAIL", "admin@avinya.com")
        admin_password = os.getenv("SEED_ADMIN_PASSWORD")
        doctor_email = os.getenv("SEED_DOCTOR_EMAIL", "dr.sharma@avinya.com")
        doctor_password = os.getenv("SEED_DOCTOR_PASSWORD")

        if not admin_password:
            admin_password = secrets.token_urlsafe(16)
            logger.warning("SEED_ADMIN_PASSWORD not set. Generated one-time admin password: %s", admin_password)
        if not doctor_password:
            doctor_password = secrets.token_urlsafe(16)
            logger.warning("SEED_DOCTOR_PASSWORD not set. Generated one-time doctor password: %s", doctor_password)

        # 1. Seed Departments
        logger.info("Seeding clinical departments...")
        dept_gen = Department(code="DEPT_GEN_MED", name="General Medicine", ndhm_speciality_code="IN001", is_active=True)
        dept_er = Department(code="DEPT_ER", name="Emergency & Trauma", ndhm_speciality_code="IN002", is_active=True)
        db.add_all([dept_gen, dept_er])
        db.flush()

        # 2. Seed Permissions (Including C6 IAM Split: manage_users)
        logger.info("Seeding system permissions...")
        p1 = Permission(code="view_ward_map", description="View spatial ward and bed layouts", module="WardManagement")
        p2 = Permission(code="manage_clinical_mdm", description="Create/Update clinical taxonomies", module="ClinicalEMR")
        p3 = Permission(code="write_cpoe_orders", description="Issue prescriptions and clinical orders", module="CPOE")
        p4 = Permission(code="process_billing", description="Manage invoices and payments", module="Financial")
        p5 = Permission(code="manage_pharmacy", description="Dispense drugs and manage stock", module="Pharmacy")
        p6 = Permission(code="manage_users", description="Create and modify users and roles", module="IAM")
        db.add_all([p1, p2, p3, p4, p5, p6])
        db.flush()

        # 3. Seed Roles
        logger.info("Seeding roles and mapping permissions...")
        admin_role = Role(code="ROLE_ADMIN", name="Hospital Administrator", description="Full system access", is_system_role=True)
        doctor_role = Role(code="ROLE_DOCTOR", name="Attending Physician", description="Clinical and EMR access", is_system_role=True)
        db.add_all([admin_role, doctor_role])
        db.flush()

        admin_role.permissions.extend([p1, p2, p3, p4, p5, p6])
        doctor_role.permissions.extend([p1, p3])
        db.flush()

        # 4. Seed Superuser & Attending Doctor
        logger.info("Seeding personnel accounts...")
        admin_user = User(
            login_id="EMP001",
            email=admin_email,
            hashed_password=pwd_context.hash(admin_password),
            full_name="System Administrator",
            gender=GenderEnum.OTHER,
            status=UserStatusEnum.ACTIVE,
            is_superuser=True,
        )
        doctor_user = User(
            login_id="DOC001",
            email=doctor_email,
            hashed_password=pwd_context.hash(doctor_password),
            full_name="Dr. Rajesh Sharma",
            gender=GenderEnum.MALE,
            status=UserStatusEnum.ACTIVE,
            is_superuser=False,
        )
        db.add_all([admin_user, doctor_user])
        db.flush()

        db.add(UserRole(user_id=admin_user.id, role_id=admin_role.id, department_id=dept_gen.id))
        db.add(UserRole(user_id=doctor_user.id, role_id=doctor_role.id, department_id=dept_gen.id))

        doc_profile = DoctorProfile(
            user_id=doctor_user.id,
            clinical_id="CLIN-DOC-001",
            department_id=dept_gen.id,
            qualification="MBBS, MD (Internal Medicine)",
            specialization="Internal Medicine",
            medical_council_name="Telangana State Medical Council",
            medical_registration_number="TSMC-2015-88492",
            registration_valid_till=datetime.now(timezone.utc) + timedelta(days=365 * 5),
            consultation_fee_paise=80000,
            consultation_duration_minutes=15,
        )
        db.add(doc_profile)

        # 5. Seed Spatial Ward Hierarchy
        logger.info("Seeding spatial hospital layout...")
        floor_1 = Floor(floor_number=1, name="Ground Floor - Emergency & Acute Care", is_active=True)
        db.add(floor_1)
        db.flush()

        ward_icu = Ward(floor_id=floor_1.id, department_id=dept_er.id, code="WARD_MICU", name="Medical ICU", ward_type="ICU", is_active=True)
        db.add(ward_icu)
        db.flush()

        room_101 = PhysicalRoom(ward_id=ward_icu.id, room_number="ICU-101", room_type=RoomTypeEnum.ICU_BAY, bed_capacity=2, is_active=True)
        db.add(room_101)
        db.flush()

        bed_a = Bed(room_id=room_101.id, bed_number="ICU-101-A", bed_type=BedTypeEnum.ICU_VENTILATOR, status=BedStatusEnum.AVAILABLE, base_daily_rate_paise=1500000)
        bed_b = Bed(room_id=room_101.id, bed_number="ICU-101-B", bed_type=BedTypeEnum.ICU_STANDARD, status=BedStatusEnum.AVAILABLE, base_daily_rate_paise=1000000)
        db.add_all([bed_a, bed_b])

        # 6. Seed Clinical Master Data & Service Catalog
        allergy_pen = AllergyMaster(department_id=dept_gen.id, code="ALG_PEN", name="Penicillin", is_active=True)
        db.add(allergy_pen)

        cat_lab = ServiceCategory(code="CAT_HEMA", name="Hematology & Clinical Pathology", is_active=True)
        db.add(cat_lab)
        db.flush()

        srv_cbc = ServiceCatalog(category_id=cat_lab.id, department_id=dept_gen.id, code="LAB_CBC", name="Complete Blood Count (CBC)", base_price_paise=45000, is_order_set=False, is_active=True)
        db.add(srv_cbc)

        db.commit()
        logger.info("Enterprise database seed completed successfully!")
    except Exception as e:
        db.rollback()
        logger.error("Seeding failed: %s", str(e))
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_enterprise_data()