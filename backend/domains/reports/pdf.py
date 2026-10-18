from io import BytesIO

from xhtml2pdf import pisa


MAX_PDF_BYTES = 25 * 1024 * 1024


def render_html_pdf(html: str) -> bytes:
    """Render trusted, already-sandboxed report HTML without fetching linked resources."""
    output = BytesIO()

    def reject_linked_resources(uri: str, rel: str) -> str:
        raise ValueError("PDF templates cannot load remote or local linked resources.")

    result = pisa.CreatePDF(
        src=html,
        dest=output,
        encoding="utf-8",
        link_callback=reject_linked_resources,
    )
    if result.err:
        raise ValueError("The selected report template could not be rendered as PDF.")
    document = output.getvalue()
    if len(document) > MAX_PDF_BYTES:
        raise ValueError("Generated PDF exceeds the configured document size limit.")
    if not document.startswith(b"%PDF-"):
        raise ValueError("PDF renderer returned an invalid document.")
    return document
