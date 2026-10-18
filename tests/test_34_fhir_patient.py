from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.domains.identity.models import GenderEnum
from backend.domains.patients.models import Patient


def test_fhir_patient_read_and_search_are_tenant_scoped(
    client, multi_tenant_env, apollo_admin_headers
):
    engine = multi_tenant_env["tenant_engines"]["hms_apollo"]
    with Session(engine) as db:
        patient = Patient(
            mrn="FHIR-001",
            full_name="Asha Rao",
            date_of_birth=datetime(1990, 5, 1, tzinfo=timezone.utc),
            gender=GenderEnum.FEMALE,
            phone_number="+919999000001",
            email="asha@example.invalid",
            address="12 Lake Road",
            village="Rangapur",
            city="Hyderabad",
            district="Rangareddy",
            state="Telangana",
            pincode="500001",
        )
        db.add(patient)
        db.commit()
        patient_id = patient.id

    headers = apollo_admin_headers
    read = client.get(f"/v1/api/fhir/R4/Patient/{patient_id}", headers=headers)
    assert read.status_code == 200, read.text
    resource = read.json()
    assert resource["resourceType"] == "Patient"
    assert resource["id"] == str(patient_id)
    assert resource["gender"] == "female"
    assert resource["birthDate"] == "1990-05-01"
    assert resource["identifier"][0] == {
        "use": "usual",
        "system": "urn:avinya:hms:mrn",
        "value": "FHIR-001",
    }
    assert resource["address"][0]["district"] == "Rangareddy"

    search = client.get(
        "/v1/api/fhir/R4/Patient?name=Asha&identifier=urn%3Aavinya%3Ahms%3Amrn%7CFHIR-001",
        headers=headers,
    )
    assert search.status_code == 200, search.text
    bundle = search.json()
    assert bundle["resourceType"] == "Bundle"
    assert bundle["total"] == 1
    assert bundle["entry"][0]["resource"]["id"] == str(patient_id)
    assert client.get(
        f"/v1/api/fhir/R4/Patient/{patient_id}",
        headers={"X-Hospital-Subdomain": "default", "Authorization": headers["Authorization"]},
    ).status_code == 401
