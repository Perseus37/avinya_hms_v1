import httpx


def test_patient_address_fields_are_optional_and_persisted(client, doctor_headers):
    response = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Address Fields Patient",
            "date_of_birth": "1990-01-01T00:00:00Z",
            "gender": "FEMALE",
            "pincode": "560001",
            "city": "Bengaluru",
            "district": "Bengaluru Urban",
            "state": "Karnataka",
            "village": "Bengaluru GPO",
        },
    )

    assert response.status_code == 201
    assert {
        field: response.json()[field]
        for field in ("pincode", "city", "district", "state", "village")
    } == {
        "pincode": "560001",
        "city": "Bengaluru",
        "district": "Bengaluru Urban",
        "state": "Karnataka",
        "village": "Bengaluru GPO",
    }

    legacy_response = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Legacy Address Patient",
            "date_of_birth": "1990-01-01T00:00:00Z",
            "gender": "MALE",
        },
    )
    assert legacy_response.status_code == 201
    assert legacy_response.json()["address"] is None
    assert legacy_response.json()["village"] is None


def test_patient_pincode_suggestions_return_locality_fields(client, doctor_headers, monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        @staticmethod
        def json():
            return [
                {
                    "Status": "Success",
                    "PostOffice": [
                        {
                            "Name": "Bengaluru GPO",
                            "Block": "Bengaluru North",
                            "District": "Bengaluru Urban",
                            "State": "Karnataka",
                            "Pincode": "560001",
                        }
                    ],
                }
            ]

    def fake_get(url, timeout):
        assert url == "https://api.postalpincode.in/pincode/560001"
        assert timeout == 5.0
        return FakeResponse()

    monkeypatch.setattr("backend.domains.patients.router.httpx.get", fake_get)

    response = client.get(
        "/v1/api/patients/pincode-suggestions?pincode=560001",
        headers=doctor_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "pincode": "560001",
        "suggestions": [
            {
                "village": "Bengaluru GPO",
                "district": "Bengaluru Urban",
                "state": "Karnataka",
                "pincode": "560001",
            }
        ],
    }


def test_patient_pincode_suggestions_validate_input_and_report_upstream_failure(
    client, doctor_headers, monkeypatch
):
    invalid_response = client.get(
        "/v1/api/patients/pincode-suggestions?pincode=12A",
        headers=doctor_headers,
    )
    assert invalid_response.status_code == 422

    def fail_get(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("backend.domains.patients.router.httpx.get", fail_get)
    failed_response = client.get(
        "/v1/api/patients/pincode-suggestions?pincode=560001",
        headers=doctor_headers,
    )
    assert failed_response.status_code == 502
    assert "postal-code lookup provider" in failed_response.json()["message"].lower()
