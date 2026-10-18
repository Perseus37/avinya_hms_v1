from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.core.config import settings
from backend.domains.procurement.documents import resolve_vendor_invoice_document


def _create_vendor(client, headers):
    response = client.post(
        "/v1/api/procurement/vendors",
        headers=headers,
        json={
            "code": "VND-DOC-01",
            "name": "Document Test Vendor",
            "category": "GENERAL_SUPPLIES",
            "contact_person": "Test Contact",
            "phone": "1234567890",
            "email": "docs@example.com",
            "address": "1 Test Street",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_invoice_document_is_persisted_and_authorized_download_is_tenant_scoped(
    client, superuser_headers, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "ATTACHMENTS_STORAGE_DIR", str(tmp_path))
    extraction_calls = []
    monkeypatch.setattr(
        "backend.domains.procurement.router.trigger_donut_invoice_extraction",
        lambda *args: extraction_calls.append(args),
    )
    vendor_id = _create_vendor(client, superuser_headers)
    content = b"%PDF-1.7\nsynthetic invoice\n"

    response = client.post(
        "/v1/api/procurement/invoices/upload-document",
        params={"vendor_id": vendor_id},
        headers=superuser_headers,
        files={"file": ("../../invoice.pdf", content, "application/pdf")},
    )

    assert response.status_code == 200, response.text
    invoice_id = response.json()["invoice_id"]
    second_response = client.post(
        "/v1/api/procurement/invoices/upload-document",
        params={"vendor_id": vendor_id},
        headers=superuser_headers,
        files={"file": ("second-invoice.pdf", content, "application/pdf")},
    )
    assert second_response.status_code == 200, second_response.text
    stored_files = list(tmp_path.rglob("*.pdf"))
    assert len(stored_files) == 2
    assert stored_files[0].read_bytes() == content
    assert stored_files[0].parent.name == "procurement-invoices"
    assert len(extraction_calls) == 2 and extraction_calls[0][1] == invoice_id
    assert {
        Path(call[2]) for call in extraction_calls
    } == set(stored_files)

    download = client.get(
        f"/v1/api/procurement/invoices/{invoice_id}/document",
        headers=superuser_headers,
    )
    assert download.status_code == 200
    assert download.content == content
    assert download.headers["content-type"] == "application/pdf"
    assert download.headers["x-content-type-options"] == "nosniff"
    assert "vendor-invoice-" in download.headers["content-disposition"]

    other_tenant_key = "another-hospital/procurement-invoices/invoice.pdf"
    with pytest.raises(HTTPException) as exc_info:
        resolve_vendor_invoice_document("default", other_tenant_key)
    assert exc_info.value.status_code == 404


@pytest.mark.parametrize(
    ("filename", "content", "expected_status"),
    [
        ("invoice.pdf", b"not a PDF", 415),
        ("invoice.exe", b"%PDF-1.7", 415),
    ],
)
def test_invoice_upload_rejects_unsupported_or_mismatched_content(
    client, superuser_headers, monkeypatch, tmp_path, filename, content, expected_status
):
    monkeypatch.setattr(settings, "ATTACHMENTS_STORAGE_DIR", str(tmp_path))
    vendor_id = _create_vendor(client, superuser_headers)

    response = client.post(
        "/v1/api/procurement/invoices/upload-document",
        params={"vendor_id": vendor_id},
        headers=superuser_headers,
        files={"file": (filename, content, "application/octet-stream")},
    )

    assert response.status_code == expected_status
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_invoice_upload_enforces_configured_size_limit(
    client, superuser_headers, monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "ATTACHMENTS_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "MAX_ATTACHMENT_UPLOAD_BYTES", 8)
    vendor_id = _create_vendor(client, superuser_headers)

    response = client.post(
        "/v1/api/procurement/invoices/upload-document",
        params={"vendor_id": vendor_id},
        headers=superuser_headers,
        files={"file": ("invoice.pdf", BytesIO(b"%PDF-1.7 content"), "application/pdf")},
    )

    assert response.status_code == 413
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]
