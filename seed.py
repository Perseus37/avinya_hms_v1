from database import SessionLocal
from models import Ward, PhysicalRoom, Bed, BedStatus, AllergyMaster, Department, Patient, PatientAllergy, DoctorProfile, User
db = SessionLocal()
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