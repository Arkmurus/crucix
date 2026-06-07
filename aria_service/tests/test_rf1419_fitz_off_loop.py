"""R-F1419 — PyMuPDF text extraction moved off the event loop.

The /read-document path ran fitz.open + per-page page.get_text() INLINE on
the event loop inside a background create_task. On a large/many-page PDF the
loop wedged for seconds-to-minutes (R-F1398 fixed the OCR path; this text
path was the remaining on-loop wedge — the WA doc-review route). The fix:
a sync helper `_extract_pdf_text_sync` run via asyncio.to_thread.

These tests drive the REAL helper against a REAL PyMuPDF document (fitz is
installed in the venv), proving the extraction logic is intact, and assert
the route wraps it in to_thread (the off-loop property).
"""
from __future__ import annotations

import asyncio

import pytest

fitz = pytest.importorskip("fitz")  # PyMuPDF — skip if unavailable

from aria_service.routes.aria import _extract_pdf_text_sync


def _make_pdf(pages_text: list[str]) -> bytes:
    """Build a real multi-page PDF in memory."""
    doc = fitz.open()
    for txt in pages_text:
        page = doc.new_page()
        page.insert_text((72, 72), txt)
    data = doc.tobytes()
    doc.close()
    return data


def test_extracts_text_with_page_markers():
    raw = _make_pdf(["Hello from page one", "Second page content here"])
    full_text, total_pages = _extract_pdf_text_sync(raw)
    assert total_pages == 2
    assert "[Page 1]" in full_text and "[Page 2]" in full_text
    assert "Hello from page one" in full_text
    assert "Second page content here" in full_text


def test_blank_pages_skipped_count_intact():
    # a blank page contributes no [Page N] block but still counts in total_pages
    raw = _make_pdf(["only page with text", ""])
    full_text, total_pages = _extract_pdf_text_sync(raw)
    assert total_pages == 2
    assert "[Page 1]" in full_text
    assert "only page with text" in full_text


def test_runs_via_to_thread_off_loop():
    """The helper must be awaitable off-loop (proves it can be wrapped)."""
    raw = _make_pdf(["off-loop extraction works"])

    async def _drive():
        return await asyncio.to_thread(_extract_pdf_text_sync, raw)

    full_text, total_pages = asyncio.run(_drive())
    assert total_pages == 1
    assert "off-loop extraction works" in full_text


def test_route_wraps_helper_in_to_thread():
    """Structural guard: the read-document path must call the helper via
    asyncio.to_thread (not inline) — a regression to inline fitz would
    re-introduce the wedge."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "routes" / "aria.py"
    text = src.read_text(encoding="utf-8")
    assert "asyncio.to_thread(\n                    _extract_pdf_text_sync" in text \
        or "asyncio.to_thread(_extract_pdf_text_sync" in text, (
        "read-document must run _extract_pdf_text_sync via asyncio.to_thread"
    )
    # The per-page fitz loop must exist EXACTLY ONCE — inside the helper,
    # never inline in the route body (a 2nd occurrence = the wedge regressed).
    assert text.count("for pg_idx, page in enumerate(doc):") == 1, (
        "per-page fitz loop must live only in _extract_pdf_text_sync, "
        "not inline in the route"
    )


def test_corrupt_pdf_raises_not_wedges():
    # garbage bytes → fitz raises promptly (no hang); caller's except handles it
    with pytest.raises(Exception):
        _extract_pdf_text_sync(b"this is not a pdf")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
