"""R-F3165 — the issuer's annual report was read through an OCR pipeline it never needed.

MEASURED (Babcock, dd_52cc50527dd0, live):

    "the issuer's document did not finish parsing within 45s (9,339,633 bytes).
     This is a PROCESSING limit on our side — it is NOT a statement about the
     filing, which may be perfectly readable."

That message is R-F3146 working: the double download was gone and the timeout was
named honestly instead of rendering as a bare colon, which EXPOSED the next real
constraint instead of hiding it. The parse itself does not fit in the budget.

WHY: `document_reader.read_document` is a 4-strategy pipeline for unknown documents —
pdfplumber text (:196) → pdfplumber TABLE extraction (:208) → Tesseract OCR (:220) →
LLM vision. pdfplumber is an order of magnitude slower than PyMuPDF on a ~300-page
annual report, and tables and OCR are pure cost here: a balance sheet's figures are in
the TEXT LAYER, and a filing with no text layer cannot support a solvency verdict
anyway (R-F3017 route 2).

§1 forbids answering a timeout by raising the timeout. This REMOVES the work: take the
text layer directly, and keep the heavy pipeline as the fallback for genuine scans.
Measured on a 300-page synthetic report: 0.03s, against a >45s failure.
"""
import asyncio

import pytest

from aria_service.intel import financial_health as fh


def _make_pdf(pages: int = 300, with_balance_sheet: bool = True) -> bytes:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for i in range(pages):
        p = doc.new_page()
        p.insert_text((72, 72), f"Page {i} strategic report governance remuneration")
        if with_balance_sheet and i == int(pages * 0.6):
            p.insert_text((72, 120),
                          "CONSOLIDATED BALANCE SHEET Total assets 5,231.0 "
                          "Total liabilities 3,110.0 Net assets 2,121.0")
    return doc.tobytes()


def _make_scan() -> bytes:
    """A PDF with NO text layer — the case OCR genuinely exists for."""
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    doc.new_page()
    return doc.tobytes()


def test_rf3165_text_layer_is_extracted():
    txt = fh._pdf_text_layer(_make_pdf())
    assert "Total assets" in txt
    assert len(txt) > 5000


def test_rf3165_no_text_layer_returns_empty_not_an_exception():
    """"" is the caller's signal to fall back to the OCR pipeline."""
    assert fh._pdf_text_layer(_make_scan()).strip() == ""


def test_rf3165_page_cap_is_honoured():
    txt = fh._pdf_text_layer(_make_pdf(pages=50), max_pages=5)
    assert "Page 4" in txt and "Page 40" not in txt


class _Resp:
    def __init__(self, content, status=200):
        self.status_code, self.content = status, content


def _patch_fetch(monkeypatch, content):
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kw): return _Resp(content)
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)


class _LLM:
    async def complete(self, system, prompt, **kw):
        self.prompt = prompt
        return type("R", (), {"text": '{"currency":null}'})()


_SOURCES = [{"url": "https://www.babcockinternational.com/annual-report-2025.pdf",
             "title": "Annual Report and Financial Statements 2025"}]
_NAME = "Babcock International Group plc"


def test_rf3165_capability_heavy_reader_is_not_called_for_a_text_pdf(monkeypatch):
    """THE FIX: a readable annual report must never reach the OCR pipeline."""
    _patch_fetch(monkeypatch, _make_pdf())
    called = {"n": 0}

    async def _reader(source, **kw):
        called["n"] += 1
        return type("R", (), {"text": ""})()

    import aria_service.intel.document_reader as dr
    monkeypatch.setattr(dr, "read_document", _reader)

    llm = _LLM()
    asyncio.run(fh.extract_issuer_financials(_SOURCES, _NAME, llm, timeout=30.0))

    assert called["n"] == 0, (
        "R-F3165 REGRESSION: the pdfplumber/OCR pipeline ran for a PDF that has a "
        "perfectly good text layer — that is what blew the 45s budget")
    assert "Total assets" in getattr(llm, "prompt", ""), (
        "the model must still be shown the balance sheet")


def test_rf3165_capability_scan_still_falls_back_to_ocr(monkeypatch):
    """Do NOT over-correct: a genuine scan must still reach the heavy pipeline."""
    _patch_fetch(monkeypatch, _make_scan())
    called = {"n": 0}

    async def _reader(source, **kw):
        called["n"] += 1
        return type("R", (), {"text": "x" * 5000})()

    import aria_service.intel.document_reader as dr
    monkeypatch.setattr(dr, "read_document", _reader)

    asyncio.run(fh.extract_issuer_financials(_SOURCES, _NAME, _LLM(), timeout=30.0))
    assert called["n"] == 1, "a PDF with no text layer must fall back to the reader"


def test_rf3165_capability_timeout_still_named_honestly(monkeypatch):
    """R-F3146's contract must survive: never a bare colon."""
    _patch_fetch(monkeypatch, _make_pdf(pages=10))

    def _slow(data, max_pages=800):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(fh, "_pdf_text_layer", _slow)
    out = asyncio.run(
        fh.extract_issuer_financials(_SOURCES, _NAME, _LLM(), timeout=5.0))
    assert out["ok"] is False
    assert "did not finish parsing" in out["reason"]
    assert not out["reason"].rstrip().endswith(":")


def test_rf3165_capability_damaged_pdf_falls_back_not_crashes(monkeypatch):
    """A corrupt text layer must degrade to the pipeline, not fail the DD."""
    _patch_fetch(monkeypatch, b"%PDF-1.7 not really a pdf")
    called = {"n": 0}

    async def _reader(source, **kw):
        called["n"] += 1
        return type("R", (), {"text": "y" * 5000})()

    import aria_service.intel.document_reader as dr
    monkeypatch.setattr(dr, "read_document", _reader)

    out = asyncio.run(
        fh.extract_issuer_financials(_SOURCES, _NAME, _LLM(), timeout=20.0))
    assert called["n"] == 1
    assert isinstance(out, dict)
