from datetime import datetime, timedelta, timezone


def _create_patient_with_blood_group(client, doctor_headers, blood_group="O+"):
    p = client.post(
        "/v1/api/patients",
        headers=doctor_headers,
        json={
            "full_name": f"Blood {blood_group} Patient",
            "date_of_birth": "1980-03-10T00:00:00Z",
            "gender": "FEMALE",
            "blood_group": blood_group,
        },
    ).json()
    enc = client.post(
        "/v1/api/adt/encounters",
        headers=doctor_headers,
        json={"patient_id": p["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1},
    ).json()
    return p, enc


def test_01_blood_bank_blocks_incompatible_abo_rh_cross_match_with_409_hard_stop(
    client, superuser_headers, doctor_headers
):
    """
    Scenario 1: Recipient is O+ (can only receive O+ or O- PRBC).
    - Attempting to cross-match an A+ PRBC bag for an O+ patient is blocked with 409 Biological Hard-Stop!
    - Attempting to cross-match an O+ PRBC bag for an O- patient (Rh mismatch) is blocked with 409!
    """
    now = datetime.now(timezone.utc)
    bag_a_pos = client.post(
        "/v1/api/blood-bank/bags",
        headers=superuser_headers,
        json={
            "bag_number": "BAG-APOS-001",
            "component_type": "PRBC",
            "blood_group": "A+",
            "volume_ml": 350,
            "collection_date": (now - timedelta(days=2)).isoformat(),
            "expiry_date": (now + timedelta(days=30)).isoformat(),
            "processing_fee_paise": 180000,
        },
    ).json()

    bag_o_pos = client.post(
        "/v1/api/blood-bank/bags",
        headers=superuser_headers,
        json={
            "bag_number": "BAG-OPOS-001",
            "component_type": "PRBC",
            "blood_group": "O+",
            "volume_ml": 350,
            "collection_date": (now - timedelta(days=2)).isoformat(),
            "expiry_date": (now + timedelta(days=30)).isoformat(),
            "processing_fee_paise": 180000,
        },
    ).json()

    # 1. O+ Recipient vs A+ Donor PRBC -> 409 Biological Hard-Stop
    _, enc_o_pos = _create_patient_with_blood_group(client, doctor_headers, blood_group="O+")
    res_abo_fail = client.post(
        "/v1/api/blood-bank/cross-match",
        headers=superuser_headers,
        json={"encounter_id": enc_o_pos["id"], "blood_bag_id": bag_a_pos["id"], "ordered_by_doctor_id": 2},
    )
    assert res_abo_fail.status_code == 409
    assert "biological hard-stop" in res_abo_fail.json()["message"].lower()

    # 2. O- Recipient vs O+ Donor PRBC (Rh+ into Rh-) -> 409 Biological Hard-Stop
    _, enc_o_neg = _create_patient_with_blood_group(client, doctor_headers, blood_group="O-")
    res_rh_fail = client.post(
        "/v1/api/blood-bank/cross-match",
        headers=superuser_headers,
        json={"encounter_id": enc_o_neg["id"], "blood_bag_id": bag_o_pos["id"], "ordered_by_doctor_id": 2},
    )
    assert res_rh_fail.status_code == 409


def test_02_universal_donor_o_neg_cross_match_issue_auto_billing_and_reaction_quarantine(
    client, superuser_headers, doctor_headers, nurse_headers, billing_headers
):
    """
    Scenario 2:
    - O- PRBC (Universal Red Cell Donor) is compatible with AB+ recipient -> Cross-match succeeds (201).
    - Issuing the bag transitions status to ISSUED and auto-bills 220,000 paise (₹2,200) to the encounter's DRAFT invoice.
    - Reporting a Febrile Non-Hemolytic transfusion reaction halts the transfusion and marks the bag REACTION_HALTED.
    """
    now = datetime.now(timezone.utc)
    bag_o_neg = client.post(
        "/v1/api/blood-bank/bags",
        headers=superuser_headers,
        json={
            "bag_number": "BAG-ONEG-UNIV-01",
            "component_type": "PRBC",
            "blood_group": "O-",
            "volume_ml": 350,
            "collection_date": (now - timedelta(days=1)).isoformat(),
            "expiry_date": (now + timedelta(days=35)).isoformat(),
            "processing_fee_paise": 220000,
        },
    ).json()

    p, enc_ab_pos = _create_patient_with_blood_group(client, doctor_headers, blood_group="AB+")

    # 1. Cross-match O- PRBC for AB+ patient -> 201
    cm_res = client.post(
        "/v1/api/blood-bank/cross-match",
        headers=superuser_headers,
        json={"encounter_id": enc_ab_pos["id"], "blood_bag_id": bag_o_neg["id"], "ordered_by_doctor_id": 2},
    )
    assert cm_res.status_code == 201
    cm_id = cm_res.json()["id"]
    assert cm_res.json()["blood_bag"]["status"] == "CROSS_MATCHED"

    # 2. Issue bag for transfusion -> Auto-bills 220,000 paise to DRAFT invoice
    issue_res = client.patch(f"/v1/api/blood-bank/cross-match/{cm_id}/issue", headers=nurse_headers)
    assert issue_res.status_code == 200
    assert issue_res.json()["blood_bag"]["status"] == "ISSUED"

    inv = client.get(f"/v1/api/billing/invoices?patient_id={p['id']}", headers=billing_headers).json()["items"][0]
    assert inv["total_amount_paise"] == 220000
    assert "BLOOD BANK [Bag #BAG-ONEG-UNIV-01]" in inv["line_items"][0]["description"]

    # 3. Report adverse transfusion reaction -> Quarantines bag as REACTION_HALTED
    rxn_res = client.patch(
        f"/v1/api/blood-bank/cross-match/{cm_id}/reaction",
        headers=nurse_headers,
        json={
            "reaction_type": "FEBRILE_NON_HEMOLYTIC",
            "reaction_notes": "Chills and temp spike of 1.5C at 15 mins; transfusion stopped immediately, IV line kept open with NS.",
        },
    )
    assert rxn_res.status_code == 200
    assert rxn_res.json()["blood_bag"]["status"] == "REACTION_HALTED"


def test_03_pacs_dicom_study_attaches_to_radiology_task_and_blocks_lab_tasks(
    client, superuser_headers, doctor_headers
):
    """
    Scenario 3:
    - Attaching PACS DICOM study metadata to a LAB task (Hemoglobin) is blocked with 400.
    - Attaching PACS DICOM study metadata (StudyInstanceUID, Modality CT, Viewer URL, Radiation Dose) to a RADIOLOGY task succeeds!
    """
    cat = client.post("/v1/api/diagnostics/categories", headers=superuser_headers, json={"department_id": 1, "code": "CAT_PACS", "name": "PACS Imaging"}).json()
    lab_svc = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": cat["id"], "code": "LAB_CRP_PACS", "name": "C-Reactive Protein", "service_type": "LAB", "base_price_paise": 40000, "is_order_set": False},
    ).json()
    rad_svc = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={"category_id": cat["id"], "code": "RAD_HRCT_CHEST", "name": "HRCT Chest Scan", "service_type": "RADIOLOGY", "base_price_paise": 350000, "is_order_set": False},
    ).json()

    p, _ = _create_patient_with_blood_group(client, doctor_headers, blood_group="B+")
    order = client.post(
        "/v1/api/diagnostics/orders",
        headers=doctor_headers,
        json={"patient_id": p["id"], "ordered_service_ids": [lab_svc["id"], rad_svc["id"]]},
    ).json()

    lab_task_id = next(t["id"] for t in order["tasks"] if t["service_id"] == lab_svc["id"])
    rad_task_id = next(t["id"] for t in order["tasks"] if t["service_id"] == rad_svc["id"])

    dicom_payload = {
        "study_instance_uid": "1.2.840.113619.2.55.3.604688433.781.1700000001",
        "accession_number": "ACC-HYD-2026-0091",
        "modality": "CT",
        "pacs_viewer_url": "https://pacs.avinyahms.com/viewer?study=1.2.840.113619.2.55.3.604688433.781.1700000001",
        "radiation_dose_mgy": 6.4,
        "radiologist_impression": "No focal consolidation, pleural effusion, or pneumothorax.",
    }

    # 1. Cannot attach DICOM study to a LAB task -> 400 Bad Request
    assert client.put(
        f"/v1/api/diagnostics/tasks/{lab_task_id}/dicom-study",
        headers=doctor_headers,
        json=dicom_payload,
    ).status_code == 400

    # 2. Attach DICOM study to RADIOLOGY task -> 200 OK
    rad_res = client.put(
        f"/v1/api/diagnostics/tasks/{rad_task_id}/dicom-study",
        headers=doctor_headers,
        json=dicom_payload,
    )
    assert rad_res.status_code == 200
    assert rad_res.json()["modality"] == "CT"
    assert rad_res.json()["radiation_dose_mgy"] == 6.4

    get_res = client.get(f"/v1/api/diagnostics/tasks/{rad_task_id}/dicom-study", headers=doctor_headers)
    assert get_res.status_code == 200
    assert get_res.json()["accession_number"] == "ACC-HYD-2026-0091"


def test_04_expired_blood_bag_issue_rejected_with_409(client, superuser_headers, doctor_headers):
    """
    Scenario 4: A blood bag cross-matched earlier that has now passed its expiry date
    must be rejected at issue time with HTTP 409 and marked as EXPIRED.
    """
    from sqlalchemy.orm import Session
    from backend.core.database import tenant_engine_pool
    from backend.domains.bloodbank.models import BloodBagInventory

    now = datetime.now(timezone.utc)
    bag = client.post(
        "/v1/api/blood-bank/bags",
        headers=superuser_headers,
        json={
            "bag_number": "BAG-EXPIRED-TEST-01",
            "component_type": "PRBC",
            "blood_group": "O+",
            "volume_ml": 350,
            "collection_date": (now - timedelta(days=10)).isoformat(),
            "expiry_date": (now + timedelta(days=20)).isoformat(),
            "processing_fee_paise": 150000,
        },
    ).json()

    _, enc = _create_patient_with_blood_group(client, doctor_headers, blood_group="O+")
    cm = client.post(
        "/v1/api/blood-bank/cross-match",
        headers=superuser_headers,
        json={"encounter_id": enc["id"], "blood_bag_id": bag["id"], "ordered_by_doctor_id": 2},
    ).json()

    # Simulate bag expiring in the refrigerator before issue
    engine = tenant_engine_pool.get_engine("hms_default")
    with Session(engine) as session:
        db_bag = session.get(BloodBagInventory, bag["id"])
        db_bag.expiry_date = now - timedelta(hours=1)
        session.commit()

    issue_res = client.patch(
        f"/v1/api/blood-bank/cross-match/{cm['id']}/issue",
        headers=superuser_headers,
    )
    assert issue_res.status_code == 409
    assert "expired" in issue_res.json()["message"].lower()
