"""R-F2056 — GIL-bound PDF extraction offloaded off the event loop.

_extract_pdf_sync is the sync unit that fetch_pdf runs via asyncio.to_thread so a
PDF-crawling DD can't stall other users (PyMuPDF releases the GIL during extract).
"""
from __future__ import annotations
import pytest
from aria_service.intel import crawl_enhancements as ce

# R-F3755/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


def test_extract_pdf_sync_extracts_text():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello DD World")
    data = doc.tobytes()
    doc.close()
    res = ce._extract_pdf_sync(data)
    assert res["error"] is None
    assert res["ok"] is True
    assert "Hello DD World" in res["text"]
    assert res["pages"] == 1


def test_extract_pdf_sync_handles_garbage():
    res = ce._extract_pdf_sync(b"this is not a pdf")
    assert res["ok"] is False
    assert res["error"]            # graceful error, no exception
    assert res["text"] == ""


def test_fetch_pdf_offloads_to_thread():
    # the GIL-heavy extraction must be invoked via asyncio.to_thread (not inline)
    import inspect
    src = function_source(ce, "fetch_pdf")
    assert "asyncio.to_thread(_extract_pdf_sync" in src
