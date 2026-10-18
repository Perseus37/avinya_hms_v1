import os
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from fastapi import HTTPException, status

from backend.core.config import settings

_ALLOWED_SIGNATURES = {
    ".pdf": b"%PDF-",
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
}
_TENANT_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_CHUNK_SIZE = 1024 * 1024


def store_vendor_invoice_document(
    tenant_slug: str, filename: str | None, source: BinaryIO
) -> tuple[str, Path]:
    """Store a bounded, signature-checked invoice under an unguessable tenant key."""
    if not _TENANT_SLUG.fullmatch(tenant_slug):
        raise RuntimeError("Tenant slug is not a safe storage path component.")

    suffix = Path(filename or "").suffix.lower()
    signature = _ALLOWED_SIGNATURES.get(suffix)
    if signature is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Invoice documents must be PDF, PNG, or JPEG files.",
        )

    root = Path(settings.ATTACHMENTS_STORAGE_DIR).resolve()
    tenant_root = root / tenant_slug
    tenant_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(tenant_root, 0o700)
    tenant_dir = tenant_root / "procurement-invoices"
    tenant_dir.mkdir(exist_ok=True, mode=0o700)
    os.chmod(tenant_dir, 0o700)
    document_id = uuid.uuid4().hex
    destination = tenant_dir / f"{document_id}{suffix}"
    temporary = tenant_dir / f".{document_id}.part"
    total_bytes = 0
    header = bytearray()

    try:
        with temporary.open("xb") as output:
            os.chmod(temporary, 0o600)
            while chunk := source.read(_CHUNK_SIZE):
                total_bytes += len(chunk)
                if total_bytes > settings.MAX_ATTACHMENT_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="Invoice document exceeds the configured upload limit.",
                    )
                if len(header) < len(signature):
                    header.extend(chunk[: len(signature) - len(header)])
                output.write(chunk)

        if not header.startswith(signature):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Invoice document content does not match its file type.",
            )
        if total_bytes == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invoice document cannot be empty.",
            )

        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    key = PurePosixPath(tenant_slug, "procurement-invoices", destination.name).as_posix()
    return key, destination


def resolve_vendor_invoice_document(tenant_slug: str, document_key: str) -> Path:
    """Resolve a stored invoice key while enforcing its tenant directory boundary."""
    if not _TENANT_SLUG.fullmatch(tenant_slug):
        raise RuntimeError("Tenant slug is not a safe storage path component.")

    key = PurePosixPath(document_key)
    expected_prefix = PurePosixPath(tenant_slug, "procurement-invoices")
    if key.is_absolute() or key.parts[:2] != expected_prefix.parts or len(key.parts) != 3:
        raise HTTPException(status_code=404, detail="Invoice document not found.")

    root = Path(settings.ATTACHMENTS_STORAGE_DIR).resolve()
    tenant_root = (root / tenant_slug / "procurement-invoices").resolve()
    document_path = (root.joinpath(*key.parts)).resolve()
    try:
        document_path.relative_to(tenant_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Invoice document not found.") from exc

    if document_path.suffix.lower() not in _ALLOWED_SIGNATURES or not document_path.is_file():
        raise HTTPException(status_code=404, detail="Invoice document not found.")
    return document_path
