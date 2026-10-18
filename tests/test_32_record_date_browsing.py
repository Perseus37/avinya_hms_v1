from datetime import datetime, timedelta, timezone


def test_clinical_and_financial_lists_share_bounded_utc_date_filters(
    client,
    doctor_headers,
    billing_headers,
    pharmacist_headers,
    superuser_headers,
):
    patient_response = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Date Range Test Patient",
            "date_of_birth": "1986-04-12T00:00:00Z",
            "gender": "FEMALE",
        },
    )
    assert patient_response.status_code == 201, patient_response.text
    patient_id = patient_response.json()["id"]
    encounter_response = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={
            "patient_id": patient_id,
            "encounter_type": "OPD",
            "primary_doctor_id": 2,
            "department_id": 1,
        },
    )
    assert encounter_response.status_code == 201, encounter_response.text
    encounter_id = encounter_response.json()["id"]

    invoice = client.post(
        "/v1/api/billing/invoices",
        headers=billing_headers,
        json={"patient_id": patient_id, "encounter_id": encounter_id},
    )
    assert invoice.status_code == 201, invoice.text

    medication = client.post(
        "/v1/api/pharmacy/medications",
        headers=pharmacist_headers,
        json={
            "code": "DATE_WINDOW_MED",
            "brand_name": "Date Window",
            "generic_name": "Test compound",
            "form": "TABLET",
            "strength": "10mg",
        },
    )
    assert medication.status_code == 201, medication.text
    prescription = client.post(
        "/v1/api/pharmacy/prescriptions",
        headers=doctor_headers,
        json={
            "encounter_id": encounter_id,
            "items": [
                {
                    "medication_id": medication.json()["id"],
                    "dosage_instruction": "Once daily",
                    "requested_quantity": 3,
                }
            ],
        },
    )
    assert prescription.status_code == 201, prescription.text

    category = client.post(
        "/v1/api/diagnostics/categories",
        headers=superuser_headers,
        json={
            "department_id": 1,
            "code": "DATE_WINDOW_CATEGORY",
            "name": "Date Window Tests",
        },
    )
    assert category.status_code == 201, category.text
    service = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={
            "category_id": category.json()["id"],
            "code": "DATE_WINDOW_SERVICE",
            "name": "Date Window Service",
            "service_type": "LAB",
            "base_price_paise": 100,
        },
    )
    assert service.status_code == 201, service.text
    diagnostic_order = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": patient_id, "ordered_service_ids": [service.json()["id"]]},
    )
    assert diagnostic_order.status_code == 201, diagnostic_order.text

    today = datetime.now(timezone.utc).date()
    date_params = {"start_date": today.isoformat(), "end_date": today.isoformat()}
    requests = [
        (
            "/v1/api/billing/invoices",
            billing_headers,
            invoice.json()["id"],
        ),
        (
            "/v1/api/pharmacy/prescriptions",
            doctor_headers,
            prescription.json()["id"],
        ),
        (
            "/v1/api/diagnostics/orders",
            doctor_headers,
            diagnostic_order.json()["id"],
        ),
    ]
    for path, headers, expected_id in requests:
        response = client.get(path, headers=headers, params=date_params)
        assert response.status_code == 200, response.text
        assert expected_id in {item["id"] for item in response.json()["items"]}
        assert client.get(
            path,
            headers=headers,
            params={"start_date": "2000-01-01", "end_date": "2000-01-02"},
        ).json()["items"] == []
        assert client.get(
            path,
            headers=headers,
            params={"start_date": today.isoformat()},
        ).status_code == 422
        assert client.get(
            path,
            headers=headers,
            params={
                "start_date": "2020-01-01",
                "end_date": (datetime(2020, 1, 1).date() + timedelta(days=366)).isoformat(),
            },
        ).status_code == 422
