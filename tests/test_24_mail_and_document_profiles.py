from email.message import EmailMessage
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import boto3
from azure.communication.email import EmailClient
from fastapi import HTTPException
import pytest
from backend.domains.reports import router as reports
from backend.control_plane.api.routers.saas_billing import stripe_subscription_webhook
from backend.control_plane.api.routers import provisioning
from backend.core import mailer
from backend.core.config import settings
from backend.core.mailer import MailAttachment


def test_report_timestamps_use_the_configured_timezone(monkeypatch):
    monkeypatch.setattr(reports, "APP_TIMEZONE", ZoneInfo("America/New_York"))

    assert reports._fmt_local_time(
        datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    ) == "2026-01-15 07:00 EST"
    assert reports._fmt_local_time(datetime(2026, 1, 15, 7, 0)) == (
        "2026-01-15 07:00 EST"
    )


def test_stripe_webhook_is_unavailable_without_optional_credentials(monkeypatch):
    from starlette.requests import Request

    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", None)
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", None)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/saas/webhook/stripe",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 1234),
            "scheme": "http",
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        import asyncio

        asyncio.run(stripe_subscription_webhook(request))

    assert exc_info.value.status_code == 503


def test_smtp_email_supports_pdf_attachments(monkeypatch):
    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            captured["connection"] = (host, port, timeout)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def send_message(self, message):
            captured["message"] = message

    monkeypatch.setattr(settings, "MAIL_PROVIDER", "smtp")
    monkeypatch.setattr(settings, "SMTP_HOST", "mail.test")
    monkeypatch.setattr(settings, "SMTP_PORT", 25)
    monkeypatch.setattr(settings, "SMTP_FROM", "noreply@example.test")
    monkeypatch.setattr(settings, "SMTP_USER", None)
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)

    mailer.send_email(
        "billing@example.test",
        "Invoice available",
        "Your invoice is attached.",
        attachments=(MailAttachment("invoice.pdf", b"%PDF-test", "application/pdf"),),
    )

    message = captured["message"]
    assert captured["connection"] == ("mail.test", 25, 15)
    assert isinstance(message, EmailMessage)
    assert message["To"] == "billing@example.test"
    assert any(part.get_filename() == "invoice.pdf" for part in message.walk())


def test_aws_ses_email_uses_sdk_credentials_and_raw_mime(monkeypatch):
    captured = {}

    class FakeSesClient:
        def send_raw_email(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(settings, "MAIL_PROVIDER", "aws_ses")
    monkeypatch.setattr(settings, "AWS_SES_REGION", "us-east-1")
    monkeypatch.setattr(settings, "SMTP_FROM", "verified@example.test")
    monkeypatch.setattr(boto3, "client", lambda service, region_name: FakeSesClient())
    mailer.send_email("recipient@example.test", "Subject", "Body")

    assert captured["Source"] == "verified@example.test"
    assert captured["Destinations"] == ["recipient@example.test"]
    assert b"Subject: Subject" in captured["RawMessage"]["Data"]


def test_azure_communication_email_uses_configured_sender(monkeypatch):
    captured = {}

    class FakePoller:
        def result(self):
            return {"status": "Succeeded"}

    class FakeEmailClient:
        def begin_send(self, payload):
            captured["payload"] = payload
            return FakePoller()

    monkeypatch.setattr(settings, "MAIL_PROVIDER", "azure_communication_services")
    monkeypatch.setattr(settings, "AZURE_COMMUNICATION_CONNECTION_STRING", "endpoint=test;accesskey=fake")
    monkeypatch.setattr(settings, "AZURE_COMMUNICATION_SENDER", "sender@example.test")
    monkeypatch.setattr(
        EmailClient,
        "from_connection_string",
        staticmethod(lambda connection: FakeEmailClient()),
    )
    mailer.send_email("recipient@example.test", "Subject", "Body")

    assert captured["payload"]["senderAddress"] == "sender@example.test"
    assert captured["payload"]["recipients"]["to"][0]["address"] == "recipient@example.test"


def test_provisioning_welcome_email_never_includes_generated_credentials(monkeypatch):
    captured = {}

    def fake_send_email(recipient, subject, body):
        captured.update({"recipient": recipient, "subject": subject, "body": body})

    monkeypatch.setattr(provisioning, "send_email", fake_send_email)
    result = {
        "hospital_name": "Example Hospital",
        "branch_code": "EXAMPLE-01",
        "pricing_model": "BUNDLE_TOTAL_HOSPITAL",
        "billing_cycle": "ANNUAL_LICENSE",
        "subdomain_url": "https://example.test",
        "admin_email": "admin@example.test",
        "admin_password": "one-time-secret",
        "federation_api_key": "federation-secret",
        "backup_encryption_key": "backup-secret",
    }

    delivered, error = provisioning._dispatch_welcome_email("admin@example.test", result)

    assert delivered is True
    assert error is None
    assert captured["recipient"] == "admin@example.test"
    for secret in ("one-time-secret", "federation-secret", "backup-secret"):
        assert secret not in captured["body"]
    assert result["subdomain_url"] in captured["body"]
