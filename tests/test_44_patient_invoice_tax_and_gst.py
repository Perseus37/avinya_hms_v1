"""Tests for patient invoice GST tax calculation, lines, and compliance (M-07)."""

import pytest


def test_invoice_creation_defaults_to_tax_exempt(client, superuser_headers, billing_headers):
    """Scenario 1: Fresh invoice defaults to is_tax_exempt=True and zero tax."""
    p = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "Tax Exempt Patient", "date_of_birth": "1995-01-01T00:00:00Z", "gender": "FEMALE"},
    ).json()

    inv = client.post(
        "/v1/api/billing/invoices",
        headers=billing_headers,
        json={"patient_id": p["id"]},
    ).json()

    assert inv["is_tax_exempt"] is True
    assert inv["tax_amount_paise"] == 0
    assert inv["cgst_amount_paise"] == 0
    assert inv["sgst_amount_paise"] == 0
    assert inv["igst_amount_paise"] == 0
    assert inv["net_amount_paise"] == 0


def test_invoice_line_item_with_gst_tax(client, superuser_headers, billing_headers):
    """
    Scenario 2: Adding a line item with 18% GST calculates tax,
    splits CGST 9% and SGST 9%, updates invoice net amount, and sets is_tax_exempt=False.
    """
    cat = client.post(
        "/v1/api/diagnostics/categories",
        headers=superuser_headers,
        json={"department_id": 1, "code": "CAT_TAX", "name": "Taxable Cat"},
    ).json()
    svc = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={
            "category_id": cat["id"],
            "code": "SVC_DELUXE_ROOM",
            "name": "Deluxe Room Luxury Rent",
            "service_type": "PROCEDURE",
            "base_price_paise": 1000000,  # Rs. 10,000 (taxable room > Rs 5,000)
            "is_order_set": False,
        },
    ).json()

    p = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "Deluxe Room Patient", "date_of_birth": "1988-06-20T00:00:00Z", "gender": "MALE"},
    ).json()

    inv = client.post(
        "/v1/api/billing/invoices",
        headers=billing_headers,
        json={"patient_id": p["id"]},
    ).json()

    # Add line item with 18% GST (9% CGST + 9% SGST)
    item_res = client.post(
        f"/v1/api/billing/invoices/{inv['id']}/items",
        headers=billing_headers,
        json={
            "service_id": svc["id"],
            "description": "Deluxe Room Rent (Taxable)",
            "quantity": 1,
            "unit_price_paise": 1000000,
            "gst_rate_percent": 18,
            "hsn_sac_code": "9963",
        },
    )
    assert item_res.status_code == 201

    updated = client.get(f"/v1/api/billing/invoices/{inv['id']}", headers=billing_headers).json()
    assert updated["total_amount_paise"] == 1000000  # Base: Rs 10,000
    assert updated["tax_amount_paise"] == 180000    # 18% GST: Rs 1,800
    assert updated["cgst_amount_paise"] == 90000     # 9% CGST: Rs 900
    assert updated["sgst_amount_paise"] == 90000     # 9% SGST: Rs 900
    assert updated["net_amount_paise"] == 1180000   # Total + Tax: Rs 11,800
    assert updated["is_tax_exempt"] is False

    # Check line item fields
    line_item = updated["line_items"][0]
    assert line_item["gst_rate_percent"] == 18
    assert line_item["tax_amount_paise"] == 180000
    assert line_item["hsn_sac_code"] == "9963"


def test_remove_line_item_updates_tax_totals(client, superuser_headers, billing_headers):
    """Scenario 3: Removing a taxable line item decreases invoice tax and reverts to tax exempt if zero."""
    cat = client.post(
        "/v1/api/diagnostics/categories",
        headers=superuser_headers,
        json={"department_id": 1, "code": "CAT_TAX_2", "name": "Taxable Cat 2"},
    ).json()
    svc = client.post(
        "/v1/api/diagnostics/catalog",
        headers=superuser_headers,
        json={
            "category_id": cat["id"],
            "code": "SVC_TAX_TEMP",
            "name": "Temporary Taxable Item",
            "service_type": "PROCEDURE",
            "base_price_paise": 500000,
            "is_order_set": False,
        },
    ).json()

    p = client.post(
        "/v1/api/patients",
        headers=superuser_headers,
        json={"full_name": "Reversal Patient", "date_of_birth": "1992-03-15T00:00:00Z", "gender": "OTHER"},
    ).json()

    inv = client.post(
        "/v1/api/billing/invoices",
        headers=billing_headers,
        json={"patient_id": p["id"]},
    ).json()

    client.post(
        f"/v1/api/billing/invoices/{inv['id']}/items",
        headers=billing_headers,
        json={
            "service_id": svc["id"],
            "description": "Taxable Item",
            "quantity": 1,
            "unit_price_paise": 500000,
            "gst_rate_percent": 12,
        },
    )

    inv_before = client.get(f"/v1/api/billing/invoices/{inv['id']}", headers=billing_headers).json()
    item_id = inv_before["line_items"][0]["id"]
    assert inv_before["tax_amount_paise"] == 60000

    # Delete line item with billing:reverse permission (superuser)
    del_res = client.delete(
        f"/v1/api/billing/invoices/{inv['id']}/items/{item_id}",
        headers=superuser_headers,
    )
    assert del_res.status_code == 200

    inv_after = client.get(f"/v1/api/billing/invoices/{inv['id']}", headers=billing_headers).json()
    assert inv_after["total_amount_paise"] == 0
    assert inv_after["tax_amount_paise"] == 0
    assert inv_after["cgst_amount_paise"] == 0
    assert inv_after["sgst_amount_paise"] == 0
    assert inv_after["is_tax_exempt"] is True

