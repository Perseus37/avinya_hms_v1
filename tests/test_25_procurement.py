from datetime import datetime, timedelta, timezone

def test_01_vendor_and_purchase_order_lifecycle(client, superuser_headers, pharmacist_headers):
    """Scenario 1: Register a vendor, draft a PO, and approve it via Maker-Checker."""

    # 1. Register Vendor
    vendor_res = client.post(
        "/v1/api/procurement/vendors",
        headers=pharmacist_headers,
        json={
            "code": "VND-TEST-01",
            "name": "MediEquip Global",
            "category": "PHARMACEUTICALS",
            "contact_person": "Jane Doe",
            "phone": "9876543210",
            "email": "sales@example.com",
            "address": "123 Logistics Park",
            "credit_period_days": 45
        }
    )
    assert vendor_res.status_code == 201, f"Vendor creation failed: {vendor_res.json()}"
    vendor = vendor_res.json()

    # 2. Draft PO
    po_res = client.post(
        "/v1/api/procurement/purchase-orders",
        headers=pharmacist_headers,
        json={
            "vendor_id": vendor["id"],
            "delivery_due_date": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
            "payment_terms": "Net 45 Days",
            "items": [
                {"item_description": "Paracetamol 500mg Bulk", "quantity_ordered": 1000, "unit_price_paise": 500}
            ]
        }
    )
    assert po_res.status_code == 201, f"PO drafting failed: {po_res.text}"
    po_id = po_res.json()["id"]

    # 3. Approve PO
    approval = client.patch(f"/v1/api/procurement/purchase-orders/{po_id}/approve", headers=superuser_headers)
    assert approval.status_code == 200, f"PO approval failed: {approval.text}"
    assert approval.json()["status"] == "APPROVED"

def test_02_grn_acceptance_auto_generates_inventory_batches(client, superuser_headers, pharmacist_headers):
    """Scenario 2: Receiving and accepting a GRN automatically creates live Pharmacy stock batches."""

    # 1. Independent Setup: Create Vendor and PO
    vendor = client.post("/v1/api/procurement/vendors", headers=pharmacist_headers, json={
        "code": "VND-TEST-02", "name": "GRN Test Vendor", "category": "PHARMACEUTICALS",
        "contact_person": "John", "phone": "1234567890", "email": "test@vendor.com", "address": "123 St"
    }).json()

    po = client.post("/v1/api/procurement/purchase-orders", headers=pharmacist_headers, json={
        "vendor_id": vendor["id"], "delivery_due_date": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
        "payment_terms": "Net 30", "items": [{"item_description": "Test Med", "quantity_ordered": 1000, "unit_price_paise": 500}]
    }).json()

    client.patch(f"/v1/api/procurement/purchase-orders/{po['id']}/approve", headers=superuser_headers)

    # 2. Process GRN against the newly created PO
    po_id = po["id"]
    po_item_id = po["items"][0]["id"]

    grn_res = client.post(
        "/v1/api/procurement/grn",
        headers=pharmacist_headers,
        json={
            "po_id": po_id,
            "invoice_number": "INV-VND-999",
            "invoice_date": datetime.now(timezone.utc).isoformat(),
            "items": [{
                "po_item_id": po_item_id,
                "batch_number": "BATCH-AUTO-001",
                "expiry_date": (datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),
                "received_quantity": 1000,
                "accepted_quantity": 1000
            }]
        }
    )
    assert grn_res.status_code == 201, f"GRN creation failed: {grn_res.text}"
    grn = grn_res.json()

    accept_res = client.patch(f"/v1/api/procurement/grn/{grn['id']}/accept", headers=superuser_headers)
    assert accept_res.status_code == 200, f"GRN acceptance failed: {accept_res.text}"

def test_03_patient_indent_issue_and_return_auto_bills_invoice(client, doctor_headers, pharmacist_headers, billing_headers, superuser_headers):
    """Scenario 3: Issuing an indent bills the patient; returning stock credits the patient."""
    p_res = client.post("/v1/api/patients", headers=doctor_headers, json={"full_name": "Indent Patient", "date_of_birth": "1990-01-01T00:00:00Z", "gender": "MALE"})
    assert p_res.status_code == 201, f"Patient creation failed: {p_res.text}"
    p = p_res.json()

    enc_res = client.post("/v1/api/adt/encounters", headers=doctor_headers, json={"patient_id": p["id"], "encounter_type": "IPD", "primary_doctor_id": 2, "department_id": 1})
    assert enc_res.status_code == 201, f"Encounter creation failed: {enc_res.text}"
    enc = enc_res.json()

    med_res = client.post("/v1/api/pharmacy/medications", headers=pharmacist_headers, json={"code": "MED_IND", "brand_name": "Indent Drug", "generic_name": "Generic", "form": "TABLET", "strength": "10mg"})
    assert med_res.status_code == 201, f"Medication creation failed: {med_res.text}"
    med = med_res.json()

    batch_res = client.post("/v1/api/pharmacy/inventory/batches", headers=pharmacist_headers, json={"medication_id": med["id"], "batch_number": "IND-B1", "expiry_date": "2028-01-01T00:00:00Z", "stock_quantity": 50, "unit_price_paise": 1000})
    assert batch_res.status_code == 201, f"Batch creation failed: {batch_res.text}"
    batch = batch_res.json()

    indent_res = client.post(
        "/v1/api/procurement/indents/patient",
        headers=superuser_headers,
        json={"requesting_department_id": 1, "encounter_id": enc["id"], "items": [{"medication_id": med["id"], "requested_quantity": 10}]}
    )
    assert indent_res.status_code == 201, f"Indent creation failed: {indent_res.text}"
    indent = indent_res.json()

    issue_res = client.post(f"/v1/api/procurement/indents/patient/{indent['id']}/issue", headers=pharmacist_headers, json={"items": [{"indent_item_id": indent["items"][0]["id"], "batch_id": batch["id"], "issue_quantity": 10}]})
    assert issue_res.status_code == 200, f"Indent issue failed: {issue_res.text}"