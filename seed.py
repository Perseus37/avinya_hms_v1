# from database import SessionLocal
# from models import Ward, PhysicalRoom, Bed, BedStatus, AllergyMaster, Department, Patient, PatientAllergy, DoctorProfile, User
# db = SessionLocal()
# w = Ward(name="ICU Ward")
# db.add(w); db.flush()
# r = PhysicalRoom(ward_id=w.id, room_number="ICU-101")
# db.add(r); db.flush()
# b = Bed(room_id=r.id, bed_number="BED-01", status=BedStatus.AVAILABLE)
# db.add(b); db.commit(); db.close()

# db.add(AllergyMaster(name="Penicillin")); db.commit(); db.close()

# p = Patient(mrn="MRN-001")
# db.add(p); db.flush()
# pa = PatientAllergy(patient_id=p.id, allergy_id=1) # 1 is Penicillin
# db.add(pa); db.commit(); db.close()

import logging
from datetime import datetime, timezone
from passlib.context import CryptContext
from database import SessionLocal
from models import (
    Base, Department, Role, Permission, RolePermission, User, UserRole, DoctorProfile,
    Floor, Ward, PhysicalRoom, Bed, AllergyMaster, Patient, PatientAllergy,
    GenderEnum, UserStatusEnum, WardTypeEnum, RoomTypeEnum, BedTypeEnum, BedStatusEnum,
    AllergyCategoryEnum, AllergySeverityEnum, BodySystemEnum
)

# --- CONFIGURE STRUCTURED LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("avinya.seeder")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def run_seed():
    logger.info("Starting Avinya HMS database seeding process...")
    db = SessionLocal()
    
    try:
        # 1. Seed Departments
        logger.info("Seeding departments...")
        dept_cardio = Department(code="CARD", name="Cardiology", ndhm_speciality_code="CARD-01", is_active=True)
        dept_emergency = Department(code="EMER", name="Emergency Medicine", ndhm_speciality_code="EMER-01", is_active=True)
        db.add_all([dept_cardio, dept_emergency])
        db.flush()
        logger.info("Departments seeded successfully.")

        # 2. Seed Permissions
        logger.info("Seeding system permissions...")
        p1 = Permission(code="view_ward_map", description="View spatial ward and bed layouts", module="WardManagement")
        p2 = Permission(code="manage_clinical_mdm", description="Create/Update clinical taxonomies", module="ClinicalEMR")
        p3 = Permission(code="write_cpoe_orders", description="Issue prescriptions and clinical orders", module="CPOE")
        db.add_all([p1, p2, p3])
        db.flush()
        logger.info("Permissions seeded successfully.")

        # 3. Seed Roles & Role-Permissions Mapping
        logger.info("Seeding roles and mapping permissions...")
        admin_role = Role(code="ROLE_ADMIN", name="Hospital Administrator", description="Full system access", is_system_role=True)
        doctor_role = Role(code="ROLE_DOCTOR", name="Attending Physician", description="Clinical and EMR access", is_system_role=True)
        db.add_all([admin_role, doctor_role])
        db.flush()

        # Map admin to all permissions
        admin_role.permissions.extend([p1, p2, p3])
        doctor_role.permissions.extend([p1, p3])
        db.flush()
        logger.info("Roles and permissions mapped successfully.")

        # 4. Seed Admin & Doctor Users
        logger.info("Seeding user accounts...")
        admin_user = User(
            login_id="admin_01",
            email="admin@avinya.com",
            hashed_password=pwd_context.hash("SecureAdminPass123!"),
            full_name="System Administrator",
            phone_number="+19876543210",
            gender=GenderEnum.MALE,
            status=UserStatusEnum.ACTIVE,
            is_superuser=True
        )
        doctor_user = User(
            login_id="doc_house",
            email="house@avinya.com",
            hashed_password=pwd_context.hash("DoctorPass123!"),
            full_name="Dr. Gregory House",
            phone_number="+19876543211",
            gender=GenderEnum.MALE,
            status=UserStatusEnum.ACTIVE,
            is_superuser=False
        )
        db.add_all([admin_user, doctor_user])
        db.flush()

        # Link users to roles and departments via user_roles
        db.add(UserRole(user_id=admin_user.id, role_id=admin_role.id, department_id=dept_emergency.id))
        db.add(UserRole(user_id=doctor_user.id, role_id=doctor_role.id, department_id=dept_cardio.id))
        
        # Add Doctor Profile extension
        doc_profile = DoctorProfile(
            user_id=doctor_user.id,
            clinical_id="DOC-CARD-001",
            department_id=dept_cardio.id,
            qualification="MBBS, MD Cardiology",
            specialization="Interventional Cardiology",
            medical_council_name="State Medical Council",
            medical_registration_number="SMC-994821",
            registration_valid_till=datetime(2030, 12, 31, tzinfo=timezone.utc),
            consultation_fee_paise=150000 # 1500.00 currency units
        )
        db.add(doc_profile)
        db.flush()
        logger.info("Users and doctor profiles seeded successfully.")

        # 5. Seed Infrastructure (Floors, Wards, Rooms, Beds)
        logger.info("Seeding physical ward infrastructure...")
        floor_3 = Floor(floor_number=3, name="Third Floor - Critical Care", is_active=True)
        db.add(floor_3)
        db.flush()

        icu_ward = Ward(
            floor_id=floor_3.id,
            department_id=dept_cardio.id,
            code="WARD-ICU-01",
            name="Cardiac Intensive Care Unit",
            ward_type=WardTypeEnum.ICU,
            is_active=True
        )
        db.add(icu_ward)
        db.flush()

        room_301 = PhysicalRoom(
            ward_id=icu_ward.id,
            room_number="301",
            room_type=RoomTypeEnum.SINGLE,
            bed_capacity=2,
            is_active=True
        )
        db.add(room_301)
        db.flush()

        bed_a = Bed(
            room_id=room_301.id,
            bed_number="301-A",
            bed_type=BedTypeEnum.MOTORIZED,
            status=BedStatusEnum.AVAILABLE,
            base_daily_rate_paise=500000 # 5000.00
        )
        bed_b = Bed(
            room_id=room_301.id,
            bed_number="301-B",
            bed_type=BedTypeEnum.ICU,
            status=BedStatusEnum.AVAILABLE,
            base_daily_rate_paise=750000 # 7500.00
        )
        db.add_all([bed_a, bed_b])
        db.flush()
        logger.info("Infrastructure hierarchy seeded successfully.")

        # 6. Seed Clinical Taxonomies & CPOE Test Data
        logger.info("Seeding clinical taxonomies and sample patient safety profiles...")
        allergy_penicillin = AllergyMaster(
            department_id=dept_cardio.id,
            code="ALG-PEN",
            name="Penicillin",
            category=AllergyCategoryEnum.DRUG,
            default_severity=AllergySeverityEnum.SEVERE,
            is_active=True
        )
        db.add(allergy_penicillin)
        db.flush()

        sample_patient = Patient(
            mrn="MRN-2026-0001",
            full_name="John Doe",
            date_of_birth=datetime(1985, 6, 15, tzinfo=timezone.utc),
            gender=GenderEnum.MALE,
            is_active=True
        )
        db.add(sample_patient)
        db.flush()

        patient_allergy = PatientAllergy(
            patient_id=sample_patient.id,
            allergy_id=allergy_penicillin.id,
            severity=AllergySeverityEnum.SEVERE,
            reaction_description="Anaphylactic shock reported during previous administration.",
            recorded_by_user_id=doctor_user.id,
            is_active=True
        )
        db.add(patient_allergy)
        db.commit()
        logger.info("Clinical taxonomies and safety interceptor test profiles seeded successfully.")
        logger.info("Database seeding completed with status: SUCCESS")

    except Exception as e:
        db.rollback()
        logger.error(f"Database seeding failed due to error: {str(e)}", exc_info=True)
        raise
    finally:
        db.close()

if __name__ == "__main__":
    run_seed()