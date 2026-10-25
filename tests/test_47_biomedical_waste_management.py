"""Tests for M-09 Biomedical Waste (BMW) Management under Bio-Medical Waste Management Rules 2016."""

from datetime import date
import pytest


def test_biomedical_waste_bag_registration_and_validation(
    client, superuser_headers, auditor_headers
):
    # 1. Non-authorized user cannot register waste bags (403 Forbidden)
    res_forbidden = client.post(
        "/v1/api/biomedical-waste/bags",
        headers=auditor_headers,
        json={
            "color_category": "YELLOW",
            "weight_grams": 2500,
            "source_department": "OT-1",
        },
    )
    assert res_forbidden.status_code == 403

    # 2. Register Yellow bag (human anatomical / soiled waste)
    res_yellow = client.post(
        "/v1/api/biomedical-waste/bags",
        headers=superuser_headers,
        json={
            "color_category": "YELLOW",
            "weight_grams": 2500,
            "source_department": "OT-1",
            "waste_description": "Post-op soiled dressings and surgical drape waste",
        },
    )
    assert res_yellow.status_code == 201, res_yellow.text
    yellow_data = res_yellow.json()
    assert yellow_data["color_category"] == "YELLOW"
    assert yellow_data["weight_grams"] == 2500
    assert yellow_data["weight_kg"] == 2.5
    assert yellow_data["source_department"] == "OT-1"
    assert yellow_data["status"] == "LOGGED"
    assert yellow_data["barcode"].startswith("BMW-YEL-")

    # 3. Register Red bag (contaminated recyclable plastics)
    res_red = client.post(
        "/v1/api/biomedical-waste/bags",
        headers=superuser_headers,
        json={
            "color_category": "RED",
            "weight_grams": 1800,
            "source_department": "ICU-BAY-2",
            "waste_description": "IV bottles, tubing, catheters, and empty urine bags",
        },
    )
    assert res_red.status_code == 201, res_red.text
    red_data = res_red.json()
    assert red_data["color_category"] == "RED"
    assert red_data["weight_kg"] == 1.8

    # 4. Register White translucent container (sharps / needles)
    res_white = client.post(
        "/v1/api/biomedical-waste/bags",
        headers=superuser_headers,
        json={
            "color_category": "WHITE",
            "weight_grams": 600,
            "source_department": "EMERGENCY_TRIAGE",
            "waste_description": "Puncture-proof sharps container with scalpels and needles",
        },
    )
    assert res_white.status_code == 201, res_white.text
    white_data = res_white.json()
    assert white_data["color_category"] == "WHITE"

    # 5. Register Blue box (glassware and ampoules)
    res_blue = client.post(
        "/v1/api/biomedical-waste/bags",
        headers=superuser_headers,
        json={
            "color_category": "BLUE",
            "weight_grams": 1200,
            "source_department": "CENTRAL_LAB",
            "waste_description": "Broken glassware and discarded medicine ampoules",
        },
    )
    assert res_blue.status_code == 201, res_blue.text
    blue_data = res_blue.json()
    assert blue_data["color_category"] == "BLUE"

    # 6. Validation: Non-positive weight rejected (422)
    res_zero_wt = client.post(
        "/v1/api/biomedical-waste/bags",
        headers=superuser_headers,
        json={
            "color_category": "YELLOW",
            "weight_grams": 0,
            "source_department": "OT-1",
        },
    )
    assert res_zero_wt.status_code == 422

    # 7. Validation: Duplicate custom barcode rejected (409)
    custom_barcode = "BMW-CUST-20261025-001"
    res_cust1 = client.post(
        "/v1/api/biomedical-waste/bags",
        headers=superuser_headers,
        json={
            "barcode": custom_barcode,
            "color_category": "YELLOW",
            "weight_grams": 1000,
            "source_department": "DIALYSIS",
        },
    )
    assert res_cust1.status_code == 201, res_cust1.text

    res_cust2 = client.post(
        "/v1/api/biomedical-waste/bags",
        headers=superuser_headers,
        json={
            "barcode": custom_barcode,
            "color_category": "YELLOW",
            "weight_grams": 1200,
            "source_department": "DIALYSIS",
        },
    )
    assert res_cust2.status_code == 409


def test_biomedical_waste_storage_transition_and_cbwtf_manifest(
    client, superuser_headers
):
    # 1. Create two test bags
    res_b1 = client.post(
        "/v1/api/biomedical-waste/bags",
        headers=superuser_headers,
        json={
            "color_category": "YELLOW",
            "weight_grams": 3500,
            "source_department": "SURGICAL_WARD",
        },
    )
    b1_barcode = res_b1.json()["barcode"]

    res_b2 = client.post(
        "/v1/api/biomedical-waste/bags",
        headers=superuser_headers,
        json={
            "color_category": "RED",
            "weight_grams": 2200,
            "source_department": "DIALYSIS_UNIT",
        },
    )
    b2_barcode = res_b2.json()["barcode"]

    # 2. Transition b1 to central hospital storage
    res_store = client.patch(
        f"/v1/api/biomedical-waste/bags/{b1_barcode}/store",
        headers=superuser_headers,
    )
    assert res_store.status_code == 200
    assert res_store.json()["status"] == "STORED"
    assert res_store.json()["stored_at"] is not None

    # 3. Create CBWTF Daily Handover Manifest
    manifest_payload = {
        "cbwtf_operator_name": "Ramky Enviro Engineers Ltd",
        "cbwtf_vehicle_number": "KA-04-E-8822",
        "cbwtf_driver_name": "Suresh Gowda",
        "bag_identifiers": [b1_barcode, b2_barcode],
        "notes": "Daily morning collection verified and weighed on calibrated scale.",
    }
    res_manifest = client.post(
        "/v1/api/biomedical-waste/manifests",
        headers=superuser_headers,
        json=manifest_payload,
    )
    assert res_manifest.status_code == 201, res_manifest.text
    manifest_data = res_manifest.json()
    manifest_id = manifest_data["id"]

    assert manifest_data["manifest_number"].startswith("BMW-MAN-")
    assert manifest_data["cbwtf_operator_name"] == "Ramky Enviro Engineers Ltd"
    assert manifest_data["cbwtf_vehicle_number"] == "KA-04-E-8822"
    assert manifest_data["total_bags_count"] == 2
    assert manifest_data["total_weight_grams"] == 3500 + 2200  # 5700g
    assert manifest_data["total_weight_kg"] == 5.7
    assert manifest_data["yellow_weight_grams"] == 3500
    assert manifest_data["red_weight_grams"] == 2200
    assert manifest_data["white_weight_grams"] == 0
    assert manifest_data["blue_weight_grams"] == 0

    # 4. Verify bags status updated to HANDED_OVER
    res_list_handed = client.get(
        "/v1/api/biomedical-waste/bags?status=HANDED_OVER",
        headers=superuser_headers,
    )
    assert res_list_handed.status_code == 200
    handed_barcodes = {b["barcode"] for b in res_list_handed.json()}
    assert b1_barcode in handed_barcodes
    assert b2_barcode in handed_barcodes

    # 5. Re-handover attempt of already handed-over bag rejected (409)
    res_rehandover = client.post(
        "/v1/api/biomedical-waste/manifests",
        headers=superuser_headers,
        json={
            "cbwtf_operator_name": "Ramky Enviro Engineers Ltd",
            "cbwtf_vehicle_number": "KA-04-E-8822",
            "cbwtf_driver_name": "Suresh Gowda",
            "bag_identifiers": [b1_barcode],
        },
    )
    assert res_rehandover.status_code == 409

    # 6. Retrieve Manifest Detail with Bag Items
    res_detail = client.get(
        f"/v1/api/biomedical-waste/manifests/{manifest_id}",
        headers=superuser_headers,
    )
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["manifest"]["id"] == manifest_id
    assert len(detail["bags"]) == 2


def test_biomedical_waste_compliance_summary(client, superuser_headers):
    # Query SPCB/CPCB compliance summary
    res_summary = client.get(
        "/v1/api/biomedical-waste/compliance/summary",
        headers=superuser_headers,
    )
    assert res_summary.status_code == 200, res_summary.text
    summary = res_summary.json()

    assert "total_bags_generated" in summary
    assert "total_weight_grams" in summary
    assert "total_weight_kg" in summary
    assert "by_category" in summary
    assert "YELLOW" in summary["by_category"]
    assert "RED" in summary["by_category"]
    assert "WHITE" in summary["by_category"]
    assert "BLUE" in summary["by_category"]
    assert "by_department" in summary
    assert "Bio-Medical Waste Management Rules 2016" in summary["statutory_rules"]

