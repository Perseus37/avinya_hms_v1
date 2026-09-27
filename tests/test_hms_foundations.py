from fastapi.testclient import TestClient

from main import app
from models import Department, User
from schemas import PaginatedResponse


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_models_include_audit_columns():
    assert "created_at" in Department.__table__.columns
    assert "updated_at" in Department.__table__.columns
    assert "created_at" in User.__table__.columns
    assert "updated_at" in User.__table__.columns


def test_paginated_response_schema_exists():
    assert "items" in PaginatedResponse.model_fields
    assert "total" in PaginatedResponse.model_fields
    assert "page" in PaginatedResponse.model_fields
    assert "page_size" in PaginatedResponse.model_fields
