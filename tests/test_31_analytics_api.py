from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import sessionmaker

from backend.domains.diagnostics.models import ServiceCatalog, ServiceCategory, ServiceTypeEnum
from backend.domains.encounters.models import Appointment, AppointmentStatusEnum
from backend.domains.clinical.models import EncounterDiagnosis
from backend.domains.encounters.models import DiagnosisTypeEnum
from backend.domains.patients.models import (
    AllergyCategoryEnum,
    AllergyMaster,
    AllergySeverityEnum,
    DiagnosisMaster,
    PatientAllergy,
)
from backend.domains.pharmacy.models import (
    InventoryBatch,
    MedicationFormEnum,
    MedicationMaster,
    Prescription,
    PrescriptionItem,
    PrescriptionStatusEnum,
)


def _create_service(multi_tenant_env):
    session_factory = sessionmaker(
        bind=multi_tenant_env["tenant_engines"]["hms_default"]
    )
    with session_factory() as db:
        category = ServiceCategory(
            department_id=1,
            code="ANALYTICS_TEST_CATEGORY",
            name="Analytics Test Category",
            is_active=True,
        )
        db.add(category)
        db.flush()
        service = ServiceCatalog(
            category_id=category.id,
            code="ANALYTICS_TEST_SERVICE",
            name="Synthetic analytics service",
            service_type=ServiceTypeEnum.PROCEDURE,
            base_price_paise=1000,
            is_active=True,
        )
        db.add(service)
        db.commit()
        return service.id


def _create_finalized_invoice(client, headers, patient_id, encounter_id, service_id):
    invoice = client.post(
        "/v1/api/billing/invoices",
        headers=headers,
        json={"patient_id": patient_id, "encounter_id": encounter_id},
    )
    assert invoice.status_code == 201, invoice.text
    invoice_id = invoice.json()["id"]
    line_item = client.post(
        f"/v1/api/billing/invoices/{invoice_id}/items",
        headers=headers,
        json={
            "service_id": service_id,
            "description": "Synthetic analytics service",
            "quantity": 1,
            "unit_price_paise": 1000,
        },
    )
    assert line_item.status_code == 201, line_item.text
    finalized = client.patch(
        f"/v1/api/billing/invoices/{invoice_id}/finalize",
        headers=headers,
    )
    assert finalized.status_code == 200, finalized.text
    payment = client.post(
        f"/v1/api/billing/invoices/{invoice_id}/payments",
        headers=headers,
        json={
            "amount_paid_paise": 250,
            "payment_method": "UPI",
            "transaction_reference": "analytics-synthetic-payment",
        },
    )
    assert payment.status_code == 201, payment.text
    return invoice_id


def test_dashboard_returns_comparable_bounded_aggregate_metrics(
    client, billing_headers, doctor_headers, multi_tenant_env
):
    today = datetime.now(timezone.utc).date()
    patient = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Analytics Synthetic Patient",
            "date_of_birth": "1990-01-01T00:00:00Z",
            "gender": "OTHER",
        },
    )
    assert patient.status_code == 201, patient.text
    encounter = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={
            "patient_id": patient.json()["id"],
            "encounter_type": "OPD",
            "primary_doctor_id": 2,
            "department_id": 1,
        },
    )
    assert encounter.status_code == 201, encounter.text
    service_id = _create_service(multi_tenant_env)
    _create_finalized_invoice(
        client,
        billing_headers,
        patient.json()["id"],
        encounter.json()["id"],
        service_id,
    )
    response = client.get(
        "/v1/api/analytics/dashboard",
        params={
            "start_date": (today - timedelta(days=6)).isoformat(),
            "end_date": today.isoformat(),
        },
        headers=billing_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["window"]["start_date"] == (today - timedelta(days=6)).isoformat()
    assert body["window"]["end_date"] == today.isoformat()
    assert body["window"]["timezone"] == "UTC"
    assert body["window"]["definition_version"] == "1.0"
    assert body["window"]["comparison_start_date"] == (
        today - timedelta(days=13)
    ).isoformat()
    assert body["window"]["comparison_end_date"] == (
        today - timedelta(days=7)
    ).isoformat()
    assert set(body["financial"]) == {"net_billed_paise", "payments_received_paise"}
    assert set(body["volume"]) == {
        "new_patients",
        "encounters_started",
        "appointments_scheduled",
    }
    assert body["receivables"]["outstanding_paise"] == 750
    assert body["receivables"]["outstanding_invoice_count"] == 1
    assert body["receivables"]["age_basis"].startswith(
        "days since invoice record creation"
    )
    assert sum(body["receivables"]["invoice_age_buckets_paise"].values()) == 750
    assert body["financial"]["net_billed_paise"]["value"] == 1000
    assert body["financial"]["payments_received_paise"]["value"] == 250
    assert body["volume"]["new_patients"]["value"] == 1
    assert body["volume"]["encounters_started"]["value"] == 1
    assert body["financial"]["net_billed_paise"]["change_pct"] is None
    assert body["financial"]["net_billed_paise"]["previous_period_value"] == 0
    assert response.headers["cache-control"] == "private, no-store"


def test_trends_fill_each_day_and_operations_return_status_groups(
    client, billing_headers, doctor_headers, multi_tenant_env
):
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=2)
    params = {"start_date": start.isoformat(), "end_date": today.isoformat()}
    patient = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Analytics Trend Patient",
            "date_of_birth": "1990-01-01T00:00:00Z",
            "gender": "OTHER",
        },
    )
    assert patient.status_code == 201, patient.text
    encounter = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={
            "patient_id": patient.json()["id"],
            "encounter_type": "OPD",
            "primary_doctor_id": 2,
            "department_id": 1,
        },
    )
    assert encounter.status_code == 201, encounter.text
    service_id = _create_service(multi_tenant_env)
    _create_finalized_invoice(
        client,
        billing_headers,
        patient.json()["id"],
        encounter.json()["id"],
        service_id,
    )

    trends = client.get(
        "/v1/api/analytics/trends", params=params, headers=billing_headers
    )
    assert trends.status_code == 200, trends.text
    body = trends.json()
    assert body["granularity"] == "day"
    assert [point["date"] for point in body["series"]] == [
        (start + timedelta(days=offset)).isoformat() for offset in range(3)
    ]
    assert [point["bucket_end"] for point in body["series"]] == [
        (start + timedelta(days=offset)).isoformat() for offset in range(3)
    ]
    assert all(point["net_billed_paise"] >= 0 for point in body["series"])
    assert "excludes draft and void invoices" in body["definitions"]["net_billed_paise"]
    assert body["series"][-1]["encounters_started"] == 1
    assert body["series"][-1]["net_billed_paise"] == 1000
    assert body["series"][-1]["payments_received_paise"] == 250
    assert trends.headers["cache-control"] == "private, no-store"

    throughput = client.get(
        "/v1/api/analytics/operational-throughput",
        params=params,
        headers=doctor_headers,
    )
    assert throughput.status_code == 200, throughput.text
    result = throughput.json()
    assert result["window"]["timezone"] == "UTC"
    assert result["encounters_started"] == sum(
        item["count"] for item in result["encounters_by_type"]
    )
    assert result["encounters_started"] == 1
    assert all(item["count"] >= 0 for item in result["appointments_by_status"])
    assert result["data_notes"]
    assert throughput.headers["cache-control"] == "private, no-store"
    assert client.get(
        "/v1/api/analytics/operational-throughput",
        params=params,
        headers=billing_headers,
    ).status_code == 403
    assert client.get(
        "/v1/api/analytics/dashboard",
        params=params,
        headers=doctor_headers,
    ).status_code == 403


def test_trends_support_calendar_bucket_granularities_and_partial_windows(
    client, billing_headers
):
    cases = [
        (
            date(2024, 1, 5),
            date(2024, 1, 9),
            "week",
            [("2024-01-05", "2024-01-07"), ("2024-01-08", "2024-01-09")],
        ),
        (
            date(2024, 1, 30),
            date(2024, 2, 3),
            "month",
            [("2024-01-30", "2024-01-31"), ("2024-02-01", "2024-02-03")],
        ),
        (
            date(2024, 3, 31),
            date(2024, 4, 1),
            "quarter",
            [("2024-03-31", "2024-03-31"), ("2024-04-01", "2024-04-01")],
        ),
        (
            date(2024, 6, 30),
            date(2024, 7, 1),
            "half_year",
            [("2024-06-30", "2024-06-30"), ("2024-07-01", "2024-07-01")],
        ),
        (
            date(2024, 12, 31),
            date(2025, 1, 1),
            "year",
            [("2024-12-31", "2024-12-31"), ("2025-01-01", "2025-01-01")],
        ),
    ]

    for start, end, granularity, expected_buckets in cases:
        response = client.get(
            "/v1/api/analytics/trends",
            params={
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "granularity": granularity,
            },
            headers=billing_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["granularity"] == granularity
        assert [
            (point["date"], point["bucket_end"]) for point in body["series"]
        ] == expected_buckets
        assert all(point["net_billed_paise"] == 0 for point in body["series"])


def test_analytics_rejects_invalid_or_unbounded_date_windows(client, billing_headers):
    today = datetime.now(timezone.utc).date()
    too_early = today - timedelta(days=366)
    responses = [
        client.get(
            "/v1/api/analytics/trends",
            params={"start_date": today.isoformat(), "end_date": (today - timedelta(days=1)).isoformat()},
            headers=billing_headers,
        ),
        client.get(
            "/v1/api/analytics/trends",
            params={"start_date": too_early.isoformat(), "end_date": today.isoformat()},
            headers=billing_headers,
        ),
        client.get(
            "/v1/api/analytics/trends",
            params={"end_date": (today + timedelta(days=1)).isoformat()},
            headers=billing_headers,
        ),
        client.get(
            "/v1/api/analytics/trends",
            params={"granularity": "decade"},
            headers=billing_headers,
        ),
    ]
    assert [response.status_code for response in responses] == [422, 422, 422, 422]


def test_appointment_performance_reports_aggregate_outcomes_and_lead_time(
    client, billing_headers, doctor_headers, multi_tenant_env
):
    today = datetime.now(timezone.utc).date()
    patient = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Analytics Appointment Patient",
            "date_of_birth": "1990-01-01T00:00:00Z",
            "gender": "OTHER",
        },
    )
    assert patient.status_code == 201, patient.text
    encounter = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={
            "patient_id": patient.json()["id"],
            "encounter_type": "OPD",
            "primary_doctor_id": 2,
            "department_id": 1,
        },
    )
    assert encounter.status_code == 201, encounter.text

    appointment_time = datetime.combine(
        today, datetime.min.time(), tzinfo=timezone.utc
    ) + timedelta(hours=20)
    session_factory = sessionmaker(
        bind=multi_tenant_env["tenant_engines"]["hms_default"]
    )
    with session_factory() as db:
        db.add_all(
            [
                Appointment(
                    patient_id=patient.json()["id"],
                    doctor_id=2,
                    department_id=1,
                    appointment_time=appointment_time,
                    status=AppointmentStatusEnum.COMPLETED,
                    encounter_id=encounter.json()["id"],
                    created_at=appointment_time - timedelta(hours=2),
                ),
                Appointment(
                    patient_id=patient.json()["id"],
                    doctor_id=2,
                    department_id=1,
                    appointment_time=appointment_time,
                    status=AppointmentStatusEnum.NO_SHOW,
                    created_at=appointment_time - timedelta(hours=1),
                ),
                Appointment(
                    patient_id=patient.json()["id"],
                    doctor_id=2,
                    department_id=1,
                    appointment_time=appointment_time,
                    status=AppointmentStatusEnum.CANCELLED,
                    created_at=appointment_time + timedelta(hours=1),
                ),
            ]
        )
        db.commit()

    response = client.get(
        "/v1/api/analytics/appointment-performance",
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
        headers=doctor_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scheduled_appointments"] == 3
    assert body["appointments_with_encounters"] == 1
    assert body["appointments_without_encounters"] == 2
    assert body["status_breakdown"] == [
        {"status": "CANCELLED", "count": 1},
        {"status": "COMPLETED", "count": 1},
        {"status": "NO_SHOW", "count": 1},
    ]
    assert body["completed_rate_pct"] == 33.33
    assert body["no_show_rate_pct"] == 33.33
    assert body["cancelled_rate_pct"] == 33.33
    assert body["average_booking_lead_hours"] == 1.5
    assert body["data_notes"]
    assert "patient" not in body
    assert response.headers["cache-control"] == "private, no-store"
    assert (
        client.get(
            "/v1/api/analytics/appointment-performance",
            headers=billing_headers,
        ).status_code
        == 403
    )


def test_financial_revenue_cycle_is_windowed_and_uses_receipt_date(
    client, billing_headers, doctor_headers, multi_tenant_env
):
    today = datetime.now(timezone.utc).date()
    patient = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Analytics Finance Patient",
            "date_of_birth": "1990-01-01T00:00:00Z",
            "gender": "OTHER",
        },
    )
    assert patient.status_code == 201, patient.text
    encounter = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={
            "patient_id": patient.json()["id"],
            "encounter_type": "OPD",
            "primary_doctor_id": 2,
            "department_id": 1,
        },
    )
    assert encounter.status_code == 201, encounter.text
    service_id = _create_service(multi_tenant_env)
    _create_finalized_invoice(
        client,
        billing_headers,
        patient.json()["id"],
        encounter.json()["id"],
        service_id,
    )

    response = client.get(
        "/v1/api/analytics/financial-revenue-cycle",
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
        headers=billing_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["window"]["start_date"] == today.isoformat()
    assert body["revenue_waterfall"]["gross_billed_paise"] == 1000
    assert body["revenue_waterfall"]["total_discounts_paise"] == 0
    assert body["revenue_waterfall"]["net_billed_paise"] == 1000
    assert body["revenue_waterfall"]["total_collected_paise"] == 250
    assert body["revenue_waterfall"]["accounts_receivable_paise"] == 750
    assert body["revenue_by_clinical_stream"]["other_catalog_services_paise"] == 1000
    assert body["top_revenue_generating_services"][0]["service_id"] == service_id
    assert body["collections_by_payment_method"] == {"UPI": 250}
    assert body["data_notes"]
    assert response.headers["cache-control"] == "private, no-store"


def test_pharmacy_analytics_windows_prescriptions_and_keeps_inventory_as_snapshot(
    client, billing_headers, doctor_headers, multi_tenant_env
):
    today = datetime.now(timezone.utc).date()
    patient = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Analytics Pharmacy Patient",
            "date_of_birth": "1990-01-01T00:00:00Z",
            "gender": "OTHER",
        },
    )
    assert patient.status_code == 201, patient.text
    encounter = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={
            "patient_id": patient.json()["id"],
            "encounter_type": "OPD",
            "primary_doctor_id": 2,
            "department_id": 1,
        },
    )
    assert encounter.status_code == 201, encounter.text

    session_factory = sessionmaker(
        bind=multi_tenant_env["tenant_engines"]["hms_default"]
    )
    with session_factory() as db:
        medication = MedicationMaster(
            code="ANALYTICS_PHARMACY_MED",
            brand_name="Synthetic tablet",
            generic_name="Synthetic",
            form=MedicationFormEnum.TABLET,
            strength="10mg",
        )
        db.add(medication)
        db.flush()
        prescription = Prescription(
            encounter_id=encounter.json()["id"],
            prescribing_doctor_id=2,
            status=PrescriptionStatusEnum.PARTIAL,
        )
        db.add(prescription)
        db.flush()
        item = PrescriptionItem(
            prescription_id=prescription.id,
            medication_id=medication.id,
            dosage_instruction="Once daily",
            requested_quantity=4,
            dispensed_quantity=3,
        )
        db.add(item)
        db.add(
            InventoryBatch(
                medication_id=medication.id,
                batch_number="ANALYTICS-BATCH",
                expiry_date=datetime.now(timezone.utc) + timedelta(days=10),
                stock_quantity=5,
                unit_price_paise=200,
            )
        )
        db.commit()

    response = client.get(
        "/v1/api/analytics/pharmacy-and-therapeutics",
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
        headers=billing_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["window"]["start_date"] == today.isoformat()
    assert body["prescription_funnel"]["total_prescriptions"] == 1
    assert body["prescription_funnel"]["total_units_prescribed"] == 4
    assert body["prescription_funnel"]["total_units_dispensed"] == 3
    assert body["prescription_funnel"]["dispensing_fill_rate_pct"] == 75
    assert body["prescription_funnel"]["prescribed_units_by_dosage_form"]["TABLET"] == 4
    assert body["inventory_snapshot"]["total_stock_units"] == 5
    assert body["inventory_snapshot"]["total_inventory_valuation_paise"] == 1000
    assert body["fefo_inventory_risk_valuation_paise"]["expiring_0_to_30_days_paise"] == 1000
    assert body["data_notes"]
    assert response.headers["cache-control"] == "private, no-store"


def test_demographics_uses_aggregate_snapshots_and_windows_diagnosis_cohort(
    client, doctor_headers, multi_tenant_env
):
    today = datetime.now(timezone.utc).date()
    headers = doctor_headers
    initial_response = client.get(
        "/v1/api/analytics/patient-demographics-and-epidemiology",
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
        headers=headers,
    )
    assert initial_response.status_code == 200, initial_response.text
    initial = initial_response.json()

    dob = date(today.year - 40, today.month, min(today.day, 28))
    patient = client.post(
        "/v1/api/patients",
        headers=headers,
        json={
            "full_name": "Analytics Demographics Patient",
            "date_of_birth": datetime.combine(
                dob, datetime.min.time(), tzinfo=timezone.utc
            ).isoformat(),
            "gender": "OTHER",
        },
    )
    assert patient.status_code == 201, patient.text
    encounter = client.post(
        "/v1/api/adt/encounters",
        headers=headers,
        json={
            "patient_id": patient.json()["id"],
            "encounter_type": "OPD",
            "primary_doctor_id": 2,
            "department_id": 1,
        },
    )
    assert encounter.status_code == 201, encounter.text

    session_factory = sessionmaker(
        bind=multi_tenant_env["tenant_engines"]["hms_default"]
    )
    with session_factory() as db:
        allergy = AllergyMaster(
            department_id=1,
            code="ANALYTICS_DEMOGRAPHICS_ALLERGY",
            name="Synthetic analytics allergy",
            category=AllergyCategoryEnum.DRUG,
        )
        diagnosis = DiagnosisMaster(
            department_id=1,
            icd10_code="ANALYTICS-TEST",
            short_description="Synthetic analytics diagnosis",
            full_description="Synthetic test diagnosis",
            is_billable=True,
        )
        db.add_all([allergy, diagnosis])
        db.flush()
        db.add(
            PatientAllergy(
                patient_id=patient.json()["id"],
                allergy_id=allergy.id,
                severity=AllergySeverityEnum.SEVERE,
                recorded_by_user_id=2,
                is_active=True,
            )
        )
        db.add(
            EncounterDiagnosis(
                encounter_id=encounter.json()["id"],
                diagnosis_id=diagnosis.id,
                diagnosis_type=DiagnosisTypeEnum.PRIMARY,
            )
        )
        db.commit()

    response = client.get(
        "/v1/api/analytics/patient-demographics-and-epidemiology",
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    population = body["mpi_population_summary"]
    assert population["total_active_patients"] == initial["mpi_population_summary"]["total_active_patients"] + 1
    assert population["age_cohort_pyramid"]["36_to_59_middle_aged"] == initial["mpi_population_summary"]["age_cohort_pyramid"]["36_to_59_middle_aged"] + 1
    assert sum(population["age_cohort_pyramid"].values()) + population["future_birth_dates_excluded_from_age_cohorts"] == population["total_active_patients"]
    assert body["allergy_risk_stratification"]["by_severity"]["SEVERE"] == initial["allergy_risk_stratification"]["by_severity"]["SEVERE"] + 1
    assert body["allergy_risk_stratification"]["by_category"]["DRUG"] == initial["allergy_risk_stratification"]["by_category"]["DRUG"] + 1
    assert body["icd10_coding_analytics"]["total_coded_diagnoses"] == initial["icd10_coding_analytics"]["total_coded_diagnoses"] + 1
    assert body["icd10_coding_analytics"]["insurance_billable_diagnoses_count"] == initial["icd10_coding_analytics"]["insurance_billable_diagnoses_count"] + 1
    assert body["diagnosis_patient_cohorts"][0]["unique_patient_count"] == 1
    assert body["service_access_and_demand"]["new_patient_registrations_by_gender"]["OTHER"] >= 1
    assert "patient_id" not in str(body["service_access_and_demand"])
    assert body["data_notes"]
    assert response.headers["cache-control"] == "private, no-store"


def test_staff_productivity_is_windowed_and_requires_executive_bi_permission(
    client, billing_headers, superuser_headers
):
    today = datetime.now(timezone.utc).date()
    response = client.get(
        "/v1/api/analytics/doctor-and-department-productivity",
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
        headers=superuser_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["window"]["start_date"] == today.isoformat()
    assert response.json()["data_notes"]
    assert response.headers["cache-control"] == "private, no-store"
    denied = client.get(
        "/v1/api/analytics/doctor-and-department-productivity",
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
        headers=billing_headers,
    )
    assert denied.status_code == 403


def test_inventory_blood_radar_aggregates_and_caps_alert_details(
    client, superuser_headers, multi_tenant_env
):
    policy_response = client.put(
        "/v1/api/admin/policies",
        headers=superuser_headers,
        json={
            "low_stock_threshold_units": 0,
            "fefo_expiry_warning_days": 1,
            "inventory_alert_detail_limit": 7,
        },
    )
    assert policy_response.status_code == 200, policy_response.text
    session_factory = sessionmaker(
        bind=multi_tenant_env["tenant_engines"]["hms_default"]
    )
    expiry = datetime.now(timezone.utc) + timedelta(days=1)
    with session_factory() as db:
        medication = MedicationMaster(
            code="ANALYTICS_RADAR_TEST",
            brand_name="Radar test medication",
            generic_name="Synthetic",
            form=MedicationFormEnum.TABLET,
            strength="10mg",
            is_active=True,
            low_stock_threshold_units=1,
            fefo_expiry_warning_days=90,
        )
        other_medication = MedicationMaster(
            code="ANALYTICS_RADAR_OTHER",
            brand_name="Other radar medication",
            generic_name="Synthetic",
            form=MedicationFormEnum.TABLET,
            strength="20mg",
            is_active=True,
            low_stock_threshold_units=0,
            fefo_expiry_warning_days=1,
        )
        db.add_all([medication, other_medication])
        db.flush()
        db.add_all(
            [
                InventoryBatch(
                    medication_id=medication.id,
                    batch_number=f"RADAR-{index:03d}",
                    expiry_date=expiry,
                    stock_quantity=1,
                    unit_price_paise=125,
                )
                for index in range(101)
            ]
        )
        db.add(
            InventoryBatch(
                medication_id=other_medication.id,
                batch_number="RADAR-OTHER",
                expiry_date=datetime.now(timezone.utc) + timedelta(days=2),
                stock_quantity=1,
                unit_price_paise=125,
            )
        )
        db.commit()

    response = client.get(
        "/v1/api/analytics/inventory-and-blood-radar",
        headers=superuser_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    inventory = body["pharmacy_inventory"]
    assert inventory["total_batches"] == 102
    assert inventory["total_stock_units"] == 102
    assert inventory["total_inventory_valuation_paise"] == 12_750
    assert inventory["low_stock_alerts_count"] == 101
    assert inventory["low_stock_alerts_returned_count"] == 7
    assert inventory["low_stock_alerts_truncated"] is True
    assert inventory["near_or_expired_alerts_count"] == 101
    assert inventory["near_or_expired_alerts_returned_count"] == 7
    assert inventory["near_or_expired_alerts_truncated"] is True
    assert inventory["alert_detail_limit"] == 7
    assert len(inventory["low_stock_batches"]) == 7
    assert len(inventory["near_or_expired_batches"]) == 7
    assert {row["low_stock_threshold_units"] for row in inventory["low_stock_batches"]} == {1}
    assert {row["expiry_warning_days"] for row in inventory["near_or_expired_batches"]} == {90}
    assert all(not row["is_already_expired"] for row in inventory["near_or_expired_batches"])
    assert any("current snapshots" in note for note in body["data_notes"])
    assert response.headers["cache-control"] == "private, no-store"


def test_medication_inventory_thresholds_can_be_overridden_or_inherited(
    client, superuser_headers
):
    created = client.post(
        "/v1/api/pharmacy/medications",
        headers=superuser_headers,
        json={
            "code": "ANALYTICS_POLICY_API",
            "brand_name": "Policy API test",
            "generic_name": "Synthetic",
            "form": "TABLET",
            "strength": "5mg",
            "low_stock_threshold_units": 12,
            "fefo_expiry_warning_days": 45,
        },
    )
    assert created.status_code == 201, created.text
    med_id = created.json()["id"]
    assert created.json()["low_stock_threshold_units"] == 12
    assert created.json()["fefo_expiry_warning_days"] == 45

    updated = client.put(
        f"/v1/api/pharmacy/medications/{med_id}",
        headers=superuser_headers,
        json={
            "low_stock_threshold_units": None,
            "fefo_expiry_warning_days": 60,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["low_stock_threshold_units"] is None
    assert updated.json()["fefo_expiry_warning_days"] == 60
