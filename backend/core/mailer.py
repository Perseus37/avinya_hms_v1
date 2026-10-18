import base64
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional

from backend.core.config import settings

logger = logging.getLogger("avinya.mailer")


@dataclass(frozen=True)
class MailAttachment:
    filename: str
    content: bytes
    content_type: str


class MailDeliveryError(RuntimeError):
    pass


def _build_message(
    recipient: str,
    subject: str,
    text_body: str,
    html_body: Optional[str],
    attachments: tuple[MailAttachment, ...],
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.SMTP_FROM
    message["To"] = recipient
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    for attachment in attachments:
        maintype, _, subtype = attachment.content_type.partition("/")
        if not maintype or not subtype:
            raise ValueError("Mail attachment content type must use type/subtype format.")
        message.add_attachment(
            attachment.content,
            maintype=maintype,
            subtype=subtype,
            filename=attachment.filename,
        )
    return message


def send_email(
    recipient: str,
    subject: str,
    text_body: str,
    *,
    html_body: Optional[str] = None,
    attachments: tuple[MailAttachment, ...] = (),
) -> None:
    """Deliver mail through the configured SMTP, Amazon SES, or Azure ACS provider."""
    provider = settings.MAIL_PROVIDER.strip().lower()
    message = _build_message(recipient, subject, text_body, html_body, attachments)

    try:
        if provider == "smtp":
            _send_smtp(message)
        elif provider == "aws_ses":
            _send_aws_ses(message, recipient)
        elif provider == "azure_communication_services":
            _send_azure_communication_services(
                recipient, subject, text_body, html_body, attachments
            )
        else:
            raise MailDeliveryError(
                "MAIL_PROVIDER must be smtp, aws_ses, or azure_communication_services."
            )
    except MailDeliveryError:
        raise
    except Exception as exc:
        logger.exception("Email delivery failed for provider %s.", provider)
        raise MailDeliveryError(f"Email delivery failed using {provider}.") from exc


def _send_smtp(message: EmailMessage) -> None:
    if not settings.SMTP_HOST:
        raise MailDeliveryError("SMTP_HOST is required when MAIL_PROVIDER=smtp.")
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
        if settings.SMTP_USER:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD or "")
        server.send_message(message)


def _send_aws_ses(message: EmailMessage, recipient: str) -> None:
    if not settings.AWS_SES_REGION:
        raise MailDeliveryError("AWS_SES_REGION is required when MAIL_PROVIDER=aws_ses.")
    try:
        import boto3
    except ImportError as exc:
        raise MailDeliveryError("Install the AWS SES integration dependencies.") from exc

    client = boto3.client("ses", region_name=settings.AWS_SES_REGION)
    client.send_raw_email(
        Source=settings.SMTP_FROM,
        Destinations=[recipient],
        RawMessage={"Data": message.as_bytes()},
    )


def _send_azure_communication_services(
    recipient: str,
    subject: str,
    text_body: str,
    html_body: Optional[str],
    attachments: tuple[MailAttachment, ...],
) -> None:
    connection_string = settings.AZURE_COMMUNICATION_CONNECTION_STRING
    if not connection_string:
        raise MailDeliveryError(
            "AZURE_COMMUNICATION_CONNECTION_STRING is required when "
            "MAIL_PROVIDER=azure_communication_services."
        )
    sender = settings.AZURE_COMMUNICATION_SENDER or settings.SMTP_FROM
    try:
        from azure.communication.email import EmailClient
    except ImportError as exc:
        raise MailDeliveryError("Install the Azure Communication Email dependency.") from exc

    content = {"subject": subject, "plainText": text_body}
    if html_body:
        content["html"] = html_body
    payload = {
        "senderAddress": sender,
        "recipients": {"to": [{"address": recipient}]},
        "content": content,
    }
    if attachments:
        payload["attachments"] = [
            {
                "name": item.filename,
                "contentType": item.content_type,
                "contentInBase64": base64.b64encode(item.content).decode("ascii"),
            }
            for item in attachments
        ]
    client = EmailClient.from_connection_string(connection_string)
    client.begin_send(payload).result()
