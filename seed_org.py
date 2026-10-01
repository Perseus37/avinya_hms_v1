import logging
import uuid
from datetime import datetime, timezone
from passlib.context import CryptContext
from database import SessionLocal
from models import (
    Base, Department, Role, Permission, RolePermission, User, UserRole, DoctorProfile,
    Floor, Ward, PhysicalRoom, Bed, AllergyMaster, Patient, PatientAllergy,
    ServiceCategory, ServiceCatalog, ServiceBundleMapping, DiagnosticOrder, DiagnosticTask,
    GenderEnum, UserStatusEnum, WardTypeEnum, RoomTypeEnum, BedTypeEnum, BedStatusEnum,
    AllergyCategoryEnum, AllergySeverityEnum, BodySystemEnum, ServiceTypeEnum, OrderStatusEnum, TaskStatusEnum,
    AdmissionTypeEnum, EncounterTypeEnum, EncounterStatusEnum, InpatientAdmission, BedTransferLog,
    Invoice, InvoiceLineItem, InvoiceStatusEnum, PaymentReceipt, PaymentMethodEnum,PatientEncounter, 
    Appointment, AppointmentStatusEnum
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
        dept_pathology = Department(code="PATH", name="Pathology Laboratory", ndhm_speciality_code="PATH-01", is_active=True)
        db.add_all([dept_cardio, dept_emergency, dept_pathology])
        db.flush()

        # 2. Seed Permissions
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

        # 4. Seed Users
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

        db.add(UserRole(user_id=admin_user.id, role_id=admin_role.id, department_id=dept_emergency.id))
        db.add(UserRole(user_id=doctor_user.id, role_id=doctor_role.id, department_id=dept_cardio.id))
        
        doc_profile = DoctorProfile(
            user_id=doctor_user.id,
            clinical_id="DOC-CARD-001",
            department_id=dept_cardio.id,
            qualification="MBBS, MD Cardiology",
            specialization="Interventional Cardiology",
            medical_council_name="State Medical Council",
            medical_registration_number="SMC-994821",
            registration_valid_till=datetime(2030, 12, 31, tzinfo=timezone.utc),
            consultation_fee_paise=150000,
            consultation_duration_minutes=20
        )
        db.add(doc_profile)
        db.flush()

        # 5. Seed Infrastructure
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
            base_daily_rate_paise=500000
        )
        db.add(bed_a)
        db.flush()

        # 6. Seed Clinical Taxonomies & Patient Safety Profiles
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
        db.flush()

        # 7. Seed Phase 4: Diagnostic Services & Order Sets
        logger.info("Seeding Phase 4: Diagnostic Services and Bundled Order Sets...")
        category_lab = ServiceCategory(
            department_id=dept_pathology.id,
            code="CAT-LAB",
            name="General Laboratory Checks",
            is_active=True
        )
        db.add(category_lab)
        db.flush()

        # Atomic tests
        test_rbc = ServiceCatalog(
            category_id=category_lab.id,
            code="LAB-RBC",
            name="Red Blood Cell (RBC) Count",
            service_type=ServiceTypeEnum.LAB,
            base_price_paise=25000, # 250.00
            loinc_code="789-8",
            is_order_set=False,
            is_active=True
        )
        test_wbc = ServiceCatalog(
            category_id=category_lab.id,
            code="LAB-WBC",
            name="White Blood Cell (WBC) Count",
            service_type=ServiceTypeEnum.LAB,
            base_price_paise=25000, # 250.00
            loinc_code="804-5",
            is_order_set=False,
            is_active=True
        )
        db.add_all([test_rbc, test_wbc])
        db.flush()

        # Order Set (Bundle)
        bundle_cbc = ServiceCatalog(
            category_id=category_lab.id,
            code="BUN-CBC",
            name="Complete Blood Count (CBC) Panel",
            service_type=ServiceTypeEnum.PACKAGE,
            base_price_paise=40000, # Discounted bundled price 400.00
            is_order_set=True,
            is_active=True
        )
        db.add(bundle_cbc)
        db.flush()

        # Map children to bundle
        db.add(ServiceBundleMapping(parent_service_id=bundle_cbc.id, child_service_id=test_rbc.id, quantity=1))
        db.add(ServiceBundleMapping(parent_service_id=bundle_cbc.id, child_service_id=test_wbc.id, quantity=1))
        db.flush()

        # Simulate CPOE Order processing (Explosion logic test)
        logger.info("Generating sample diagnostic CPOE order...")
        order = DiagnosticOrder(
            patient_id=sample_patient.id,
            ordering_doctor_id=doctor_user.id,
            clinical_notes="Routine CBC evaluation.",
            status=OrderStatusEnum.ACTIVE
        )
        db.add(order)
        db.flush()

        # Generate atomic tasks simulating the explosion engine
        task_1 = DiagnosticTask(order_id=order.id, service_id=test_rbc.id, status=TaskStatusEnum.PENDING)
        task_2 = DiagnosticTask(order_id=order.id, service_id=test_wbc.id, status=TaskStatusEnum.PENDING)
        db.add_all([task_1, task_2])

        db.commit()
        logger.info("Phase 4 Seeding completed successfully.")

        # 8. Seed Phase 5: Patient Encounters & IPD Admission
        logger.info("Seeding Phase 5: Encounters and IPD Admissions...")
        
        # Create an active IPD Encounter
        encounter = PatientEncounter(
            patient_id=sample_patient.id,
            encounter_type=EncounterTypeEnum.IPD,
            primary_doctor_id=doctor_user.id,
            department_id=dept_cardio.id,
            chief_complaint_notes="Admitted for cardiac monitoring and CBC evaluation.",
            status=EncounterStatusEnum.ACTIVE
        )
        db.add(encounter)
        db.flush()

        # Ensure Bed A is available for the seed logic
        bed_a.status = BedStatusEnum.AVAILABLE
        db.flush()

        # Admit the patient to Bed 301-A
        admission = InpatientAdmission(
            encounter_id=encounter.id,
            admission_type=AdmissionTypeEnum.ROUTINE,
            current_bed_id=bed_a.id,
            admitting_doctor_id=doctor_user.id
        )
        db.add(admission)
        db.flush()

        # Lock the bed to the admission
        bed_a.status = BedStatusEnum.OCCUPIED
        bed_a.current_admission_id = admission.id

        # Log the initial placement
        transfer_log = BedTransferLog(
            admission_id=admission.id,
            from_bed_id=None,
            to_bed_id=bed_a.id,
            requested_by_id=doctor_user.id,
            transfer_reason="Initial IPD Admission placement."
        )
        db.add(transfer_log)
        
        db.commit()
        logger.info("Phase 5 Seeding completed successfully.")

        # 9. Seed Phase 6: Enterprise Billing & Payments
        logger.info("Seeding Phase 6: Generating Financial Ledgers...")
        
        # Create a draft invoice for John Doe's encounter
        inv_number = f"INV-{uuid.uuid4().hex[:8].upper()}"
        invoice = Invoice(
            patient_id=sample_patient.id,
            encounter_id=encounter.id,
            invoice_number=inv_number,
            status=InvoiceStatusEnum.DRAFT,
            notes="Initial billing for IPD admission and diagnostic tests."
        )
        db.add(invoice)
        db.flush()

        # Add Line Item 1: Bed Charges
        bed_charge = InvoiceLineItem(
            invoice_id=invoice.id,
            description=f"IPD Bed Charge - {bed_a.bed_type.value}",
            quantity=1,
            unit_price_paise=bed_a.base_daily_rate_paise,
            total_price_paise=bed_a.base_daily_rate_paise
        )
        db.add(bed_charge)

        # Add Line Item 2: Diagnostic Order (CBC Package)
        lab_charge = InvoiceLineItem(
            invoice_id=invoice.id,
            service_id=bundle_cbc.id,
            description=bundle_cbc.name,
            quantity=1,
            unit_price_paise=bundle_cbc.base_price_paise,
            total_price_paise=bundle_cbc.base_price_paise
        )
        db.add(lab_charge)
        
        # Calculate Total and Finalize
        invoice.total_amount_paise = bed_charge.total_price_paise + lab_charge.total_price_paise
        invoice.status = InvoiceStatusEnum.UNPAID
        db.flush()

        # Record a full payment via Insurance
        receipt_number = f"REC-{uuid.uuid4().hex[:8].upper()}"
        payment = PaymentReceipt(
            invoice_id=invoice.id,
            receipt_number=receipt_number,
            amount_paid_paise=invoice.total_amount_paise,
            payment_method=PaymentMethodEnum.INSURANCE,
            transaction_reference="INS-AUTH-994821"
        )
        db.add(payment)
        
        # Mark as Paid
        invoice.amount_paid_paise = payment.amount_paid_paise
        invoice.status = InvoiceStatusEnum.PAID

        db.commit()
        logger.info("Phase 6 Seeding completed successfully.")

        # 10. Seed Phase 7: OPD Scheduling
        logger.info("Seeding Phase 7: Outpatient Appointments...")
        from datetime import timedelta
        
        # Schedule a follow-up appointment for John Doe 7 days from now
        future_date = datetime.now(timezone.utc) + timedelta(days=7)
        
        appointment = Appointment(
            patient_id=sample_patient.id,
            doctor_id=doctor_user.id,
            department_id=dept_cardio.id,
            appointment_time=future_date,
            reason_for_visit="Post-discharge cardiac follow-up.",
            status=AppointmentStatusEnum.SCHEDULED
        )
        db.add(appointment)
        db.flush()
        
        # Simulate front-desk check-in (Patient arrives)
        appointment.status = AppointmentStatusEnum.ARRIVED
        
        db.commit()
        logger.info("Phase 7 Seeding completed successfully.")

        # 11. Seed Phase 8: Pharmacy & Inventory
        logger.info("Seeding Phase 8: Pharmacy Supply Chain & CPOE...")
        from datetime import timedelta
        from models import MedicationMaster, MedicationFormEnum, InventoryBatch, Prescription, PrescriptionItem, PrescriptionStatusEnum
        
        # Define Medication Master
        med_para = MedicationMaster(
            code="MED-PARA-500",
            brand_name="Dolo-500",
            generic_name="Paracetamol",
            form=MedicationFormEnum.TABLET,
            strength="500mg",
            is_active=True
        )
        db.add(med_para)
        db.flush()

        # Receive Inventory Batch
        batch_1 = InventoryBatch(
            medication_id=med_para.id,
            batch_number="BCH-2026-X1",
            expiry_date=datetime.now(timezone.utc) + timedelta(days=365),
            stock_quantity=1000,
            unit_price_paise=1500  # 15.00
        )
        db.add(batch_1)
        db.flush()

        # Doctor Issues Prescription for the IPD Encounter
        prescription = Prescription(
            encounter_id=encounter.id,
            prescribing_doctor_id=doctor_user.id,
            clinical_notes="Fever management.",
            status=PrescriptionStatusEnum.ACTIVE
        )
        db.add(prescription)
        db.flush()

        # Add 10 pills to the prescription
        rx_item = PrescriptionItem(
            prescription_id=prescription.id,
            medication_id=med_para.id,
            dosage_instruction="1-1-1 after food",
            requested_quantity=10
        )
        db.add(rx_item)
        
        db.commit()
        logger.info("Phase 8 Seeding completed successfully.")

        logger.info("Database seeding completed with status: SUCCESS")

    except Exception as e:
        db.rollback()
        logger.error(f"Database seeding failed due to error: {str(e)}", exc_info=True)
        raise
    finally:
        db.close()

if __name__ == "__main__":
    run_seed()