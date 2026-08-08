"""R-F3265 — vetting could not read the documents it was given.

`decode_text_best_effort` handled .txt/.csv/.md/.json and returned "" for
everything else, with a comment saying a real PDF/OCR path was separate work.
It is not separate any more — the image already carries every dependency:

    fitz (PyMuPDF)   PDF text            installed
    pytesseract      OCR                 installed, + tesseract-ocr eng/por/fra/spa
    python-docx      DOCX                installed
    email (stdlib)   .eml + attachments  always

and `intel/pdf_deep_ingest.py` has been doing PDF text + per-image OCR for the
DD side all along. Vetting was returning "" beside it.

The cost of that: an applicant's passport, payslip or reference arrives as a
PDF or a phone photo, extraction reports `extraction_unavailable`, the document
lands as OTHER, it satisfies no requirement, and it evidences no period — the
officer types everything by hand. Every real upload took that path.

WHAT THIS DOES NOT CHANGE. Extraction still only ever PROPOSES. A document that
is now readable can be classified and can offer covers_from/covers_to, but
reading a date out of a payslip is not verifying an engagement — that remains a
human's or a direct reference's job. The RECEIVED/ACCEPTED distinction and the
confidence floor are untouched, and a failed read is still a disclosed gap
rather than a silent pass.
"""

from __future__ import annotations

import pytest

from aria_service.vetting.documents import decode_text_best_effort

# R-F3784/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


def _pdf_bytes(text: str) -> bytes:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), text)
    return doc.tobytes()


def _docx_bytes(text: str) -> bytes:
    docx = pytest.importorskip("docx")
    import io

    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ── the regression: real formats must actually be read ───────────────────

def test_a_pdf_is_read_instead_of_returning_empty():
    """THE defect. Every applicant document arrives as a PDF."""
    marker = "PAYSLIP Acme Holdings Ltd period 2024-03"
    text = decode_text_best_effort(_pdf_bytes(marker), "payslip.pdf")
    assert "PAYSLIP" in text and "Acme" in text, (
        f"a PDF still reads as empty — extraction will report "
        f"extraction_unavailable and the document will land as OTHER. Got: {text[:120]!r}")


def test_a_docx_is_read():
    marker = "EMPLOYER REFERENCE for Jane Doe"
    text = decode_text_best_effort(_docx_bytes(marker), "reference.docx")
    assert "EMPLOYER REFERENCE" in text, f"docx unread: {text[:120]!r}"


def test_an_email_is_read_including_its_attachment():
    """Referees reply by email, and the reference is usually the ATTACHMENT.
    Reading only the covering note would miss the evidence entirely."""
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = "Reference for Jane Doe"
    msg["From"] = "hr@acme.example"
    msg.set_content("Please find the reference attached.")
    msg.add_attachment(_pdf_bytes("CONFIRMED employment 2021-2024"),
                       maintype="application", subtype="pdf",
                       filename="reference.pdf")

    text = decode_text_best_effort(msg.as_bytes(), "reply.eml")
    assert "Reference for Jane Doe" in text, "the email headers/body were not read"
    assert "CONFIRMED employment" in text, (
        "the ATTACHMENT was not read — for a referee reply the attachment is "
        "the evidence, and the covering note is not")


def test_an_unreadable_document_still_returns_empty_not_a_guess():
    """A failed read must stay a disclosed gap. Inventing plausible text from
    bytes we never decoded is the fabrication this module exists to avoid."""
    assert decode_text_best_effort(b"\x00\x01\x02not a real pdf", "broken.pdf") == ""
    assert decode_text_best_effort(b"", "empty.pdf") == ""


def test_plain_text_formats_still_work():
    """The formats that already worked must not regress."""
    assert "hello" in decode_text_best_effort(b"hello world", "notes.txt")
    assert "a,b" in decode_text_best_effort(b"a,b\n1,2", "table.csv")


def test_extraction_is_bounded_so_one_document_cannot_hang_an_upload():
    """An upload is a user-facing request. A 500-page scan must not hold it
    open — the extractor caps how much it reads."""
    import inspect

    from aria_service.vetting import documents as d

    src = module_source(d)
    assert "_MAX_EXTRACT_PAGES" in src, (
        "extraction has no page bound — a large scan can stall an upload")
    assert "_MAX_EXTRACT_CHARS" in src, "extraction has no character bound"


def test_ocr_is_a_fallback_not_the_default():
    """OCR is expensive. It must run only when the page yielded no text —
    running it on every page of a digital PDF burns CPU on the upload path."""
    import inspect

    from aria_service.vetting import documents as d

    src = function_source(d, "_extract_pdf")
    assert "if" in src and "ocr" in src.lower(), (
        "OCR does not appear to be conditional on the text layer being empty")
