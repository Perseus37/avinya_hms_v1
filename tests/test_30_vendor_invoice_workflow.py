from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import sessionmaker

from backend.domains.finance.models import ExpenseRecord
from backend.domains.procurement.models import VendorInvoice


def test_vendor_invoice_review_match_and_approval_are_end_to_end(
    client, superuser_headers, multi_tenant_env, monkeypatch, tmp_path
):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "ATTACHMENTS_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(
        "backend.domains.procurement.router.trigger_donut_invoice_extraction",
        lambda *_: None,
    )
    monkeypatch.setattr(
        "backend.domains.procurement.documents.scan_file_with_clamav",
        lambda _: None,
    )
    vendor_response = client.post(
        "/v1/api/procurement/vendors",
        headers=superuser_headers,
        json={
            "code": "VND-WORKFLOW-01",
            "name": "Invoice Workflow Vendor",
            "category": "GENERAL_SUPPLIES",
            "contact_person": "Test Contact",
            "phone": "1234567890",
            "email": "workflow@example.com",
            "address": "1 Test Street",
        },
    )
    assert vendor_response.status_code == 201, vendor_response.text
    vendor_id = vendor_response.json()["id"]

    po_response = client.post(
        "/v1/api/procurement/purchase-orders",
        headers=superuser_headers,
        json={
            "vendor_id": vendor_id,
            "delivery_due_date": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
            "payment_terms": "Net 30",
            "items": [
                {
                    "item_description": "Test supply",
                    "quantity_ordered": 2,
                    "unit_price_paise": 100,
                }
            ],
        },
    )
    assert po_response.status_code == 201, po_response.text
    po = po_response.json()
    approval = client.patch(
        f"/v1/api/procurement/purchase-orders/{po['id']}/approve",
        headers=superuser_headers,
    )
    assert approval.status_code == 200, approval.text

    upload = client.post(
        "/v1/api/procurement/invoices/upload-document",
        params={"vendor_id": vendor_id},
        headers=superuser_headers,
        files={"file": ("invoice.pdf", b"%PDF-1.7\nsynthetic invoice", "application/pdf")},
    )
    assert upload.status_code == 200, upload.text
    invoice_id = upload.json()["invoice_id"]

    invoice_date = datetime.now(timezone.utc) - timedelta(days=1)
    due_date = datetime.now(timezone.utc) + timedelta(days=29)
    review_payload = {
        "po_id": po["id"],
        "invoice_number": "INV-MANUAL-001",
        "invoice_date": invoice_date.isoformat(),
        "due_date": due_date.isoformat(),
        "subtotal_paise": 200,
        "tax_amount_paise": 36,
        "discount_paise": 0,
        "total_amount_paise": 236,
        "items": [
            {
                "po_item_id": po["items"][0]["id"],
                "item_description": "Test supply",
                "batch_number": "BATCH-001",
                "expiry_date": due_date.isoformat(),
                "quantity": 2,
                "unit_price_paise": 100,
                "tax_percentage": 18,
                "total_price_paise": 200,
            }
        ],
    }
    review = client.patch(
        f"/v1/api/procurement/invoices/{invoice_id}/review",
        headers=superuser_headers,
        json=review_payload,
    )
    assert review.status_code == 200, review.text
    assert review.json()["invoice_number"] == "INV-MANUAL-001"
    assert review.json()["items"][0]["po_item_id"] == po["items"][0]["id"]

    invoice_list = client.get(
        "/v1/api/procurement/invoices",
        params={
            "vendor_id": vendor_id,
            "invoice_status": "PENDING_VERIFICATION",
        },
        headers=superuser_headers,
    )
    assert invoice_list.status_code == 200, invoice_list.text
    assert invoice_list.json()["total"] == 1
    assert invoice_list.json()["items"][0]["id"] == invoice_id
    invoice_detail = client.get(
        f"/v1/api/procurement/invoices/{invoice_id}",
        headers=superuser_headers,
    )
    assert invoice_detail.status_code == 200
    assert invoice_detail.json()["items"][0]["po_item_id"] == po["items"][0]["id"]

    over_quantity_review = {
        **review_payload,
        "subtotal_paise": 300,
        "tax_amount_paise": 54,
        "total_amount_paise": 354,
        "items": [
            {
                **review_payload["items"][0],
                "quantity": 3,
                "total_price_paise": 300,
            }
        ],
    }
    invalid_quantity_review = client.patch(
        f"/v1/api/procurement/invoices/{invoice_id}/review",
        headers=superuser_headers,
        json=over_quantity_review,
    )
    assert invalid_quantity_review.status_code == 200
    over_quantity_match = client.patch(
        f"/v1/api/procurement/invoices/{invoice_id}/reconcile",
        headers=superuser_headers,
    )
    assert over_quantity_match.status_code == 200
    assert over_quantity_match.json()["status"] == "PENDING_VERIFICATION"
    corrected_review = client.patch(
        f"/v1/api/procurement/invoices/{invoice_id}/review",
        headers=superuser_headers,
        json=review_payload,
    )
    assert corrected_review.status_code == 200

    reconciliation = client.patch(
        f"/v1/api/procurement/invoices/{invoice_id}/reconcile",
        headers=superuser_headers,
    )
    assert reconciliation.status_code == 200, reconciliation.text
    assert reconciliation.json()["status"] == "MATCHED_APPROVED"

    approval = client.post(
        f"/v1/api/procurement/invoices/{invoice_id}/approve",
        headers=superuser_headers,
    )
    assert approval.status_code == 200, approval.text
    duplicate_approval = client.post(
        f"/v1/api/procurement/invoices/{invoice_id}/approve",
        headers=superuser_headers,
    )
    assert duplicate_approval.status_code == 409
    posted_invoice_edit = client.patch(
        f"/v1/api/procurement/invoices/{invoice_id}/review",
        headers=superuser_headers,
        json=review_payload,
    )
    assert posted_invoice_edit.status_code == 409

    tenant_session = sessionmaker(bind=multi_tenant_env["tenant_engines"]["hms_default"])
    with tenant_session() as db:
        invoice = db.get(VendorInvoice, invoice_id)
        assert invoice.linked_expense_id is not None
        assert (
            db.query(ExpenseRecord)
            .filter(ExpenseRecord.id == invoice.linked_expense_id)
            .count()
            == 1
        )


def test_invoice_review_requires_consistent_totals(client, superuser_headers, monkeypatch, tmp_path):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "ATTACHMENTS_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(
        "backend.domains.procurement.router.trigger_donut_invoice_extraction",
        lambda *_: None,
    )
    monkeypatch.setattr(
        "backend.domains.procurement.documents.scan_file_with_clamav",
        lambda _: None,
    )
    vendor_response = client.post(
        "/v1/api/procurement/vendors",
        headers=superuser_headers,
        json={
            "code": "VND-WORKFLOW-02",
            "name": "Invoice Totals Vendor",
            "category": "GENERAL_SUPPLIES",
            "contact_person": "Test Contact",
            "phone": "1234567890",
            "email": "totals@example.com",
            "address": "1 Test Street",
        },
    )
    assert vendor_response.status_code == 201, vendor_response.text
    upload = client.post(
        "/v1/api/procurement/invoices/upload-document",
        params={"vendor_id": vendor_response.json()["id"]},
        headers=superuser_headers,
        files={"file": ("invoice.pdf", b"%PDF-1.7\nsynthetic invoice", "application/pdf")},
    )
    assert upload.status_code == 200, upload.text

    invalid_review = client.patch(
        f"/v1/api/procurement/invoices/{upload.json()['invoice_id']}/review",
        headers=superuser_headers,
        json={
            "invoice_number": "INV-BAD-TOTALS",
            "invoice_date": datetime.now(timezone.utc).isoformat(),
            "due_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "subtotal_paise": 100,
            "tax_amount_paise": 18,
            "discount_paise": 0,
            "total_amount_paise": 999,
            "items": [
                {
                    "item_description": "Test supply",
                    "batch_number": "BATCH-001",
                    "expiry_date": datetime.now(timezone.utc).isoformat(),
                    "quantity": 1,
                    "unit_price_paise": 100,
                    "tax_percentage": 18,
                    "total_price_paise": 100,
                }
            ],
        },
    )
    assert invalid_review.status_code == 422
