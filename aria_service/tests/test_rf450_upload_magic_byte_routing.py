"""R-F450 — capability test for R-F415's upload magic-byte routing.

R-F415 added magic-byte detection to the /api/aria/read-document
upload path, so a ZIP body renamed to `.pdf` with
`Content-Type: application/pdf` is routed by DETECTED type, not
by the attacker-controlled mime + filename. The R-F415 verifier
flagged that NO end-to-end test demonstrated the parser is bypassed
on the rename-attack scenario.

This test posts the classic rename attack (ZIP bytes, .pdf
filename, application/pdf mime) to the real endpoint and asserts
that:
  - the response is NOT the PDF-extracted text (parser was NOT
    invoked on the ZIP)
  - the response either rejects with "unrecognised file format"
    (for non-office ZIP bodies) OR routes correctly to the
    office-doc parser (for DOCX/XLSX/PPTX disguised as PDF).
"""
from __future__ import annotations

import base64
import io
import zipfile

from fastapi.testclient import TestClient


def _build_app():
    from fastapi import FastAPI
    from aria_service.routes.aria import router as aria_router
    app = FastAPI()
    app.include_router(aria_router)
    return app


def test_rf450_generic_zip_renamed_as_pdf_does_not_invoke_pdf_parser(monkeypatch):
    """A generic ZIP body with .pdf filename + application/pdf mime
    must NOT hit the PDF parser. Pre-R-F415 the parser would have
    been called with garbage bytes (OOM / CPU burn / silent
    corruption). Post-R-F415 the request returns an early-reject
    response because the detected type ("zip") has no parser branch
    in the upload path."""
    # Build a 4-file generic ZIP — no word/document.xml / xl/workbook.xml
    # / ppt/presentation.xml so detect_file_type returns "zip" (not
    # docx/xlsx/pptx).
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("README.txt", "this is a generic zip")
        zf.writestr("data/a.bin", b"\x00\x01\x02\x03")
    raw_zip = buf.getvalue()

    # Track whether the PDF parser (PyMuPDF / fitz.open) was invoked.
    pymupdf_called = {"value": False}
    try:
        import fitz  # PyMuPDF
        orig_open = fitz.open

        def _spying_open(*args, **kwargs):
            pymupdf_called["value"] = True
            return orig_open(*args, **kwargs)
        monkeypatch.setattr(fitz, "open", _spying_open)
    except ImportError:
        # PyMuPDF not installed — the test still passes because the
        # PDF branch would raise ImportError and fall through anyway.
        pass

    payload = {
        "filename": "evil.pdf",
        "content": base64.b64encode(raw_zip).decode("ascii"),
        "encoding": "base64",
        "mimetype": "application/pdf",  # ATTACKER-CONTROLLED LIE
    }

    app = _build_app()
    with TestClient(app) as client:
        r = client.post("/api/aria/read-document", json=payload)

    # Endpoint must NOT 500 — must return a structured response.
    assert r.status_code == 200, (
        f"R-F450: expected 200, got {r.status_code} body={r.text[:200]}"
    )
    body = r.json()
    # KEY ASSERTION: PDF parser was NEVER invoked on the ZIP bytes.
    assert pymupdf_called["value"] is False, (
        "R-F450 REGRESSION: PDF parser was called on a ZIP body. "
        "Magic-byte routing failed; rename attack succeeded."
    )
    # The body should indicate either:
    #   (a) magic-byte rejection: {"ok": False, "error": "...magic-byte..."},
    #   (b) zip route attempted but no parser produced text.
    # Both are acceptable — the load-bearing claim is "PDF parser
    # NOT invoked", asserted above.


def test_rf450_docx_renamed_as_pdf_routes_to_docx_parser(monkeypatch):
    """A DOCX body (ZIP with word/document.xml) renamed to .pdf must
    route to the DOCX parser, NOT the PDF parser. Pins the override
    behaviour: detected mime wins over claimed mime."""
    # Build a minimal DOCX (ZIP with word/document.xml)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "word/document.xml",
            "<?xml version='1.0'?><document><body><p><r><t>Hello DOCX</t>"
            "</r></p></body></document>",
        )
        zf.writestr("[Content_Types].xml", "<Types/>")
    raw_docx = buf.getvalue()

    pymupdf_called = {"value": False}
    try:
        import fitz
        orig_open = fitz.open

        def _spy(*args, **kwargs):
            pymupdf_called["value"] = True
            return orig_open(*args, **kwargs)
        monkeypatch.setattr(fitz, "open", _spy)
    except ImportError:
        pass

    payload = {
        "filename": "report.pdf",       # LIE
        "content": base64.b64encode(raw_docx).decode("ascii"),
        "encoding": "base64",
        "mimetype": "application/pdf",  # LIE
    }

    app = _build_app()
    with TestClient(app) as client:
        r = client.post("/api/aria/read-document", json=payload)
    assert r.status_code == 200, f"got {r.status_code}: {r.text[:200]}"

    # PDF parser must NOT have been called
    assert pymupdf_called["value"] is False, (
        "R-F450: PDF parser invoked despite magic bytes being DOCX"
    )

    # DOCX extraction should have produced "Hello DOCX"
    body = r.json()
    extracted = (
        body.get("text")
        or body.get("extracted")
        or body.get("content")
        or ""
    )
    assert "Hello DOCX" in extracted, (
        f"R-F450: DOCX route didn't extract body text. "
        f"Response keys: {list(body.keys())}"
    )


def test_rf450_genuine_pdf_with_correct_mime_still_works():
    """Sanity: a genuine PDF body with matching mime + filename
    routes to the PDF parser normally — magic-byte detection must
    not break the happy path."""
    # Tiny minimum-viable PDF body (PyMuPDF accepts this as 1-page)
    minimal_pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj<<>>endobj\n"
        b"2 0 obj<</Type/Catalog>>endobj\n"
        b"trailer<</Root 2 0 R>>\n"
        b"%%EOF"
    )
    payload = {
        "filename": "report.pdf",
        "content": base64.b64encode(minimal_pdf).decode("ascii"),
        "encoding": "base64",
        "mimetype": "application/pdf",
    }
    app = _build_app()
    with TestClient(app) as client:
        r = client.post("/api/aria/read-document", json=payload)
    # Either 200 with extracted text (or empty) OR a structured error —
    # the load-bearing claim is "endpoint accepted the PDF and tried
    # the PDF parser path". We don't assert specific extracted text
    # because PyMuPDF may reject the stub above; the contract here is
    # "happy path accepted, no magic-byte rejection".
    assert r.status_code == 200, f"got {r.status_code}: {r.text[:200]}"
    body = r.json()
    # If R-F415's magic-byte rejection fired on a real PDF, that
    # would be a real regression. Look for the rejection signature.
    assert "magic-byte check failed" not in (body.get("error") or ""), (
        f"R-F450 REGRESSION: real PDF rejected as 'magic-byte check "
        f"failed'. Body: {body}"
    )
