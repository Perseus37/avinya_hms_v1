from datetime import datetime, timedelta, timezone


def test_service_demand_intake_lifecycle_and_aggregate_analytics(
    client, doctor_headers
):
    patient_response = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Demand Workflow Patient",
            "date_of_birth": "1980-02-03T00:00:00Z",
            "gender": "FEMALE",
        },
    )
    assert patient_response.status_code == 201, patient_response.text
    today = datetime.now(timezone.utc).date()
    create = client.post(
        "/v1/api/opd/service-demand",
        headers=doctor_headers,
        json={
            "requested_service_name": "Specialist sleep study",
            "reason": "EQUIPMENT_UNAVAILABLE",
            "source": "OPD",
            "patient_id": patient_response.json()["id"],
            "department_id": 1,
        },
    )
    assert create.status_code == 201, create.text
    demand_id = create.json()["id"]
    assert create.json()["status"] == "OPEN"
    assert create.json()["requested_by_id"] is not None

    missing_facility = client.patch(
        f"/v1/api/opd/service-demand/{demand_id}",
        headers=doctor_headers,
        json={"status": "REFERRED_OUT"},
    )
    assert missing_facility.status_code == 422

    referred = client.patch(
        f"/v1/api/opd/service-demand/{demand_id}",
        headers=doctor_headers,
        json={
            "status": "REFERRED_OUT",
            "external_facility_name": "Regional Sleep Centre",
            "resolution_notes": "Referral information given to patient",
        },
    )
    assert referred.status_code == 200, referred.text
    assert referred.json()["status"] == "REFERRED_OUT"

    resolved = client.patch(
        f"/v1/api/opd/service-demand/{demand_id}",
        headers=doctor_headers,
        json={"status": "RESOLVED", "resolution_notes": "Patient attended referral"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["resolved_at"] is not None

    closed = client.patch(
        f"/v1/api/opd/service-demand/{demand_id}",
        headers=doctor_headers,
        json={"status": "CLOSED"},
    )
    assert closed.status_code == 200, closed.text
    invalid_reopen = client.patch(
        f"/v1/api/opd/service-demand/{demand_id}",
        headers=doctor_headers,
        json={"status": "OPEN"},
    )
    assert invalid_reopen.status_code == 409
    history = client.get(
        f"/v1/api/opd/service-demand/{demand_id}/history",
        headers=doctor_headers,
    )
    assert history.status_code == 200, history.text
    assert [event["to_status"] for event in history.json()] == [
        "OPEN",
        "REFERRED_OUT",
        "RESOLVED",
        "CLOSED",
    ]
    assert history.json()[1]["changed_by_id"] is not None

    listed = client.get(
        "/v1/api/opd/service-demand",
        headers=doctor_headers,
        params={
            "start_date": today.isoformat(),
            "end_date": today.isoformat(),
            "reason": "EQUIPMENT_UNAVAILABLE",
            "department_id": 1,
        },
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1

    analytics = client.get(
        "/v1/api/analytics/service-demand",
        headers=doctor_headers,
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
    )
    assert analytics.status_code == 200, analytics.text
    body = analytics.json()
    assert body["requests_by_reason"]["EQUIPMENT_UNAVAILABLE"] == 1
    assert body["requests_by_current_status"]["CLOSED"] == 1
    assert body["requests_by_department"][0]["unique_patient_count"] == 1
    serialized = str(body)
    assert "Demand Workflow Patient" not in serialized
    assert "Specialist sleep study" not in serialized
    assert analytics.headers["cache-control"] == "private, no-store"


def test_service_demand_rejects_invalid_references_and_date_window(
    client, doctor_headers
):
    invalid_patient = client.post(
        "/v1/api/opd/service-demand",
        headers=doctor_headers,
        json={
            "requested_service_name": "Unavailable service",
            "reason": "SERVICE_UNAVAILABLE",
            "source": "OTHER",
            "patient_id": 999999,
        },
    )
    assert invalid_patient.status_code == 404

    invalid_window = client.get(
        "/v1/api/opd/service-demand",
        headers=doctor_headers,
        params={"start_date": "2020-01-01"},
    )
    assert invalid_window.status_code == 422


def test_opd_appointment_waitlist_promotes_to_booked_slot_atomically(
    client, doctor_headers
):
    patient_response = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Appointment Waitlist Patient",
            "date_of_birth": "1991-06-12T00:00:00Z",
            "gender": "MALE",
        },
    )
    assert patient_response.status_code == 201, patient_response.text
    day = datetime.now(timezone.utc).date() + timedelta(days=3)
    start = datetime.combine(
        day, datetime.min.time().replace(hour=10), tzinfo=timezone.utc
    )
    end = start + timedelta(hours=2)
    create = client.post(
        "/v1/api/opd/appointment-waitlist",
        headers=doctor_headers,
        json={
            "patient_id": patient_response.json()["id"],
            "doctor_id": 2,
            "department_id": 1,
            "requested_start_at": start.isoformat(),
            "requested_end_at": end.isoformat(),
            "reason_for_visit": "Follow-up consultation",
        },
    )
    assert create.status_code == 201, create.text
    entry_id = create.json()["id"]
    assert create.json()["status"] == "WAITING"

    outside = client.post(
        f"/v1/api/opd/appointment-waitlist/{entry_id}/book",
        headers=doctor_headers,
        json={"appointment_time": (end + timedelta(minutes=1)).isoformat()},
    )
    assert outside.status_code == 422

    booked = client.post(
        f"/v1/api/opd/appointment-waitlist/{entry_id}/book",
        headers=doctor_headers,
        json={"appointment_time": (start + timedelta(minutes=30)).isoformat()},
    )
    assert booked.status_code == 200, booked.text
    appointment = booked.json()
    assert appointment["patient_id"] == patient_response.json()["id"]
    assert appointment["status"] == "SCHEDULED"

    entry = client.get(
        "/v1/api/opd/appointment-waitlist",
        headers=doctor_headers,
        params={"patient_id": patient_response.json()["id"]},
    )
    assert entry.status_code == 200, entry.text
    assert entry.json()["total"] == 1
    assert entry.json()["items"][0]["status"] == "BOOKED"
    assert entry.json()["items"][0]["appointment_id"] == appointment["id"]

    history = client.get(
        f"/v1/api/opd/appointment-waitlist/{entry_id}/history",
        headers=doctor_headers,
    )
    assert history.status_code == 200, history.text
    assert [event["to_status"] for event in history.json()] == ["WAITING", "BOOKED"]


def test_opd_appointment_waitlist_checks_schedule_conflicts(
    client, doctor_headers
):
    patient_response = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Waitlist Conflict Patient",
            "date_of_birth": "1992-07-14T00:00:00Z",
            "gender": "FEMALE",
        },
    )
    assert patient_response.status_code == 201, patient_response.text
    day = datetime.now(timezone.utc).date() + timedelta(days=4)
    scheduled_at = datetime.combine(
        day, datetime.min.time().replace(hour=11), tzinfo=timezone.utc
    )
    existing = client.post(
        "/v1/api/opd/appointments",
        headers=doctor_headers,
        json={
            "patient_id": patient_response.json()["id"],
            "doctor_id": 2,
            "department_id": 1,
            "appointment_time": scheduled_at.isoformat(),
        },
    )
    assert existing.status_code == 201, existing.text
    waitlist = client.post(
        "/v1/api/opd/appointment-waitlist",
        headers=doctor_headers,
        json={
            "patient_id": patient_response.json()["id"],
            "doctor_id": 2,
            "department_id": 1,
            "requested_start_at": (scheduled_at - timedelta(hours=1)).isoformat(),
            "requested_end_at": (scheduled_at + timedelta(hours=1)).isoformat(),
        },
    )
    assert waitlist.status_code == 201, waitlist.text
    conflict = client.post(
        f"/v1/api/opd/appointment-waitlist/{waitlist.json()['id']}/book",
        headers=doctor_headers,
        json={"appointment_time": scheduled_at.isoformat()},
    )
    assert conflict.status_code == 409
    still_waiting = client.get(
        "/v1/api/opd/appointment-waitlist",
        headers=doctor_headers,
        params={"patient_id": patient_response.json()["id"]},
    )
    assert any(
        entry["id"] == waitlist.json()["id"] and entry["status"] == "WAITING"
        for entry in still_waiting.json()["items"]
    )


def test_opd_consultation_timing_compares_actual_and_allocated_slot(
    client, doctor_headers
):
    patient_response = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "Consultation Timing Patient",
            "date_of_birth": "1987-11-24T00:00:00Z",
            "gender": "OTHER",
        },
    )
    assert patient_response.status_code == 201, patient_response.text
    encounter_response = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={
            "patient_id": patient_response.json()["id"],
            "encounter_type": "OPD",
            "primary_doctor_id": 2,
            "department_id": 1,
        },
    )
    assert encounter_response.status_code == 201, encounter_response.text
    today = datetime.now(timezone.utc).date()
    appointment = client.post(
        "/v1/api/opd/appointments",
        headers=doctor_headers,
        json={
            "patient_id": patient_response.json()["id"],
            "doctor_id": 2,
            "department_id": 1,
            "appointment_time": datetime.combine(
                today, datetime.min.time(), tzinfo=timezone.utc
            ).isoformat(),
        },
    )
    assert appointment.status_code == 201, appointment.text
    assert appointment.json()["planned_duration_minutes"] == 15
    appointment_id = appointment.json()["id"]

    arrived = client.patch(
        f"/v1/api/opd/appointments/{appointment_id}/status",
        headers=doctor_headers,
        json={"status": "ARRIVED"},
    )
    assert arrived.status_code == 200, arrived.text
    consulting = client.patch(
        f"/v1/api/opd/appointments/{appointment_id}/status",
        headers=doctor_headers,
        json={
            "status": "IN_CONSULTATION",
            "encounter_id": encounter_response.json()["id"],
        },
    )
    assert consulting.status_code == 200, consulting.text
    completed = client.patch(
        f"/v1/api/opd/appointments/{appointment_id}/status",
        headers=doctor_headers,
        json={"status": "COMPLETED"},
    )
    assert completed.status_code == 200, completed.text

    response = client.get(
        "/v1/api/analytics/care-timings",
        headers=doctor_headers,
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
    )
    assert response.status_code == 200, response.text
    body = response.json()["outpatient"]
    assert body["observed_arrival_to_consult_wait"]["sample_count"] == 1
    assert body["completed_consultation_duration"]["sample_count"] == 1
    assert body["actual_vs_planned_consultation"]["sample_count"] == 1
    assert body["actual_vs_planned_consultation"][
        "completed_consultations_under_planned"
    ] == 1
    assert body["completed_consultation_duration"]["average_minutes"] is not None


def test_ipd_care_timings_include_stay_and_bed_stage(
    client, doctor_headers, superuser_headers
):
    floor = client.post(
        "/v1/api/ip/floors",
        headers=superuser_headers,
        json={"floor_number": 941, "name": "Timing Floor"},
    )
    assert floor.status_code == 201, floor.text
    ward = client.post(
        "/v1/api/ip/wards",
        headers=superuser_headers,
        json={
            "floor_id": floor.json()["floor_id"],
            "department_id": 1,
            "code": "WARD_TIMING",
            "name": "Timing Ward",
            "ward_type": "GENERAL",
        },
    )
    assert ward.status_code == 201, ward.text
    room = client.post(
        "/v1/api/ip/rooms",
        headers=superuser_headers,
        json={
            "ward_id": ward.json()["id"],
            "room_number": "TIMING-1",
            "room_type": "SINGLE",
            "bed_capacity": 1,
        },
    )
    assert room.status_code == 201, room.text
    bed = client.post(
        "/v1/api/ip/beds",
        headers=superuser_headers,
        json={
            "room_id": room.json()["room_id"],
            "bed_number": "TIMING-1A",
            "bed_type": "STANDARD",
            "base_daily_rate_paise": 10000,
        },
    )
    assert bed.status_code == 201, bed.text

    patient = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": "IPD Timing Patient",
            "date_of_birth": "1972-03-15T00:00:00Z",
            "gender": "FEMALE",
        },
    )
    assert patient.status_code == 201, patient.text
    encounter = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={
            "patient_id": patient.json()["id"],
            "encounter_type": "IPD",
            "primary_doctor_id": 2,
            "department_id": 1,
        },
    )
    assert encounter.status_code == 201, encounter.text
    admission = client.post(
        "/v1/api/adt/admissions",
        headers=doctor_headers,
        json={
            "encounter_id": encounter.json()["id"],
            "admission_type": "ROUTINE",
            "admitting_doctor_id": 2,
            "assigned_bed_id": bed.json()["bed_id"],
        },
    )
    assert admission.status_code == 201, admission.text

    today = datetime.now(timezone.utc).date()
    response = client.get(
        "/v1/api/analytics/care-timings",
        headers=doctor_headers,
        params={"start_date": today.isoformat(), "end_date": today.isoformat()},
    )
    assert response.status_code == 200, response.text
    inpatient = response.json()["inpatient"]
    assert inpatient["admissions_started_in_window"] == 1
    assert inpatient["active_admissions_as_of_generated_at"] == 1
    assert inpatient["admission_to_initial_bed_assignment"]["sample_count"] == 1
    assert inpatient["bed_stage_duration_by_bed_type"][0]["bed_type"] == "STANDARD"
    assert inpatient["bed_stage_duration_by_bed_type"][0]["stage_count"] == 1
