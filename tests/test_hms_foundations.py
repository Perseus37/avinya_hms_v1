from datetime import datetime, timezone

from fastapi import Depends
from fastapi.testclient import TestClient
from passlib.context import CryptContext
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import pytest

import database
from database import Base, get_db
from dependencies import get_current_user
from main import app
from models import (
    AllergyCategoryEnum,
    AllergyMaster,
    AllergySeverityEnum,
    BodySystemEnum,
    ChiefComplaintMaster,
    Department,
    Floor,
    Patient,
    PatientAllergy,
    PhysicalExaminationMaster,
    PhysicalRoom,
    Role,
    User,
    UserStatusEnum,
    Ward,
    WardTypeEnum,
    GenderEnum,
)
from schemas import PaginatedResponse


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


fake_redis = FakeRedis()
SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()

    admin_user = User(
        login_id="admin_test",
        email="admin@test.com",
        hashed_password=pwd_context.hash("adminpass123"),
        full_name="System Admin",
        phone_number="1234567890",
        gender=GenderEnum.MALE,
        status=UserStatusEnum.ACTIVE,
        is_superuser=True,
    )
    department = Department(code="CARD", name="Cardiology", ndhm_speciality_code="CARD-01", is_active=True)
    role = Role(code="ROLE_ADMIN", name="Hospital Administrator", description="Test role", is_system_role=True)

    session.add_all([admin_user, department, role])
    session.commit()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    def override_get_current_user(db: Session = Depends(get_db)):
        user = db.query(User).filter(User.email == "admin@test.com").first()
        db.info["audit_user_id"] = user.id
        return user

    def override_get_redis():
        return fake_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[database.get_redis] = override_get_redis

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    session.close()
    Base.metadata.drop_all(bind=engine)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_models_include_audit_columns():
    assert "created_at" in Department.__table__.columns
    assert "updated_at" in Department.__table__.columns
    assert "created_by_id" in Department.__table__.columns
    assert "updated_by_id" in Department.__table__.columns
    assert "created_at" in User.__table__.columns
    assert "updated_at" in User.__table__.columns


def test_protected_create_records_audit_actor(client):
    response = client.post("/api/ip/floors", json={"floor_number": 9, "name": "Audit Floor"})
    assert response.status_code == 201

    session = TestingSessionLocal()
    try:
        floor = session.query(Floor).filter_by(floor_number=9).one()
        assert floor.created_by_id == 1
        assert floor.updated_by_id == 1
    finally:
        session.close()


def test_paginated_response_schema_exists():
    assert "items" in PaginatedResponse.model_fields
    assert "total" in PaginatedResponse.model_fields
    assert "page" in PaginatedResponse.model_fields
    assert "page_size" in PaginatedResponse.model_fields


def test_auth_login_returns_token(client):
    session = TestingSessionLocal()
    try:
        user = User(
            login_id="doctor_test",
            email="doctor@test.com",
            hashed_password=pwd_context.hash("testpassword123"),
            full_name="Dr Test",
            phone_number="5550000000",
            gender=GenderEnum.MALE,
            status=UserStatusEnum.ACTIVE,
            is_superuser=False,
        )
        session.add(user)
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/api/auth/login",
        json={"email": "doctor@test.com", "password": "testpassword123"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert "access_token" in payload
    assert payload["token_type"] == "bearer"


def test_patient_api_and_clinical_safety(client):
    session = TestingSessionLocal()
    try:
        department = session.query(Department).filter_by(code="CARD").first()
        allergy = AllergyMaster(
            department_id=department.id,
            code="ALG-PEN",
            name="Penicillin",
            category=AllergyCategoryEnum.DRUG,
            default_severity=AllergySeverityEnum.SEVERE,
            is_active=True,
        )
        patient = Patient(
            mrn="MRN-TEST-001",
            full_name="John Patient",
            date_of_birth=datetime(1985, 6, 15, tzinfo=timezone.utc),
            gender=GenderEnum.MALE,
            is_active=True,
        )
        session.add_all([allergy, patient])
        session.commit()

        patient_allergy = PatientAllergy(
            patient_id=patient.id,
            allergy_id=allergy.id,
            severity=AllergySeverityEnum.SEVERE,
            reaction_description="Rash",
            recorded_by_user_id=1,
            is_active=True,
        )
        session.add(patient_allergy)
        session.commit()
    finally:
        session.close()

    patient_response = client.post(
        "/api/patients",
        json={
            "mrn": "MRN-TEST-001",
            "full_name": "John Patient",
            "date_of_birth": "1985-06-15T00:00:00Z",
            "gender": "MALE",
        },
    )
    assert patient_response.status_code in {200, 201, 409}

    list_response = client.get("/api/patients")
    assert list_response.status_code == 200
    payload = list_response.json()
    assert "items" in payload
    assert "total" in payload
    assert "page" in payload
    assert "page_size" in payload

    allergy_response = client.post(
        "/api/clinical/allergies",
        json={
            "department_id": 1,
            "code": "ALG-TEST-1",
            "name": "Penicillin",
            "category": "DRUG",
            "default_severity": "SEVERE",
        },
    )
    assert allergy_response.status_code in {200, 201}

    safety_response = client.post(
        "/api/clinical/orders/check-allergy",
        json={"patient_id": 1, "allergen_keywords": ["Penicillin"]},
    )
    assert safety_response.status_code in {200, 409}


def test_ward_api_routes(client):
    session = TestingSessionLocal()
    try:
        floor = Floor(floor_number=2, name="Second Floor", is_active=True)
        session.add(floor)
        session.commit()

        ward = Ward(
            floor_id=floor.id,
            department_id=1,
            code="WARD-101",
            name="Ward 101",
            ward_type=WardTypeEnum.GENERAL,
            is_active=True,
        )
        session.add(ward)
        session.commit()

        room = PhysicalRoom(
            ward_id=ward.id,
            room_number="401",
            room_type="SINGLE",
            bed_capacity=1,
            is_active=True,
        )
        session.add(room)
        session.commit()
    finally:
        session.close()

    floor_response = client.post(
        "/api/ip/floors",
        json={"floor_number": 4, "name": "Fourth Floor"},
    )
    assert floor_response.status_code == 201

    room_response = client.post(
        "/api/ip/rooms",
        json={
            "ward_id": 1,
            "room_number": "401",
            "room_type": "SINGLE",
            "bed_capacity": 1,
        },
    )
    assert room_response.status_code in {200, 201}

    bed_response = client.post(
        "/api/ip/beds",
        json={
            "room_id": 1,
            "bed_number": "401-A",
            "bed_type": "STANDARD",
            "base_daily_rate_paise": 250000,
        },
    )
    assert bed_response.status_code in {200, 201}

    floors_response = client.get("/api/ip/floors?page=1&page_size=10")
    assert floors_response.status_code == 200
    body = floors_response.json()
    assert "items" in body
    assert "total" in body
    assert "page" in body
    assert "page_size" in body

    deactivated_ward = client.delete("/api/ip/wards/1")
    assert deactivated_ward.status_code == 200

    deactivated_room = client.delete("/api/ip/rooms/1")
    assert deactivated_room.status_code == 200

    deactivated_bed = client.delete("/api/ip/beds/1")
    assert deactivated_bed.status_code == 200

    map_response = client.get("/api/ip/beds/map-view")
    assert map_response.status_code == 200
    assert isinstance(map_response.json(), list)


def test_pagination_contract_for_patient_list(client):
    response = client.get("/api/patients?page=1&page_size=5")
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert "total" in body
    assert "page" in body
    assert "page_size" in body


def test_clinical_master_update_and_delete_routes(client):
    session = TestingSessionLocal()
    try:
        complaint = ChiefComplaintMaster(
            department_id=1,
            code="CC-001",
            name="Chest pain",
            is_active=True,
        )
        exam = PhysicalExaminationMaster(
            department_id=1,
            body_system=BodySystemEnum.CARDIOVASCULAR,
            code="EX-001",
            name="Blood pressure",
            is_active=True,
        )
        session.add_all([complaint, exam])
        session.commit()
    finally:
        session.close()

    complaint_update = client.put(
        "/api/clinical/complaints/1",
        json={
            "department_id": 1,
            "code": "CC-002",
            "name": "Shortness of breath",
        },
    )
    assert complaint_update.status_code == 200

    complaint_delete = client.delete("/api/clinical/complaints/1")
    assert complaint_delete.status_code == 200

    exam_update = client.put(
        "/api/clinical/physical-exams/1",
        json={
            "department_id": 1,
            "body_system": "CARDIOVASCULAR",
            "code": "EX-002",
            "name": "Pulse rate",
        },
    )
    assert exam_update.status_code == 200

    exam_delete = client.delete("/api/clinical/physical-exams/1")
    assert exam_delete.status_code == 200
