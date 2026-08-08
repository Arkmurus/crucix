"""R-F1398 — Tesseract OCR must never block the event loop.

Proven live 2026-06-07 (R-F704 wedge dump, 11:24:18Z, main-thread stack):
  ocr.py _ocr_via_tesseract -> _try_tesseract -> extract_text_from_image
  -> pdf_deep_ingest.ingest_pdf_multi_page -> main.py <module>
The async _ocr_via_tesseract ran 4 PSM tesseract passes + PIL preprocessing
INLINE on the event loop thread; a scanned multi-page PDF blocked the loop
210s+ (heartbeat stale 5.78s -> 210.64s, 11:22:47 -> 11:26:49), /health/live
failed 3x8s checks, and the WA listener declared the brain down while the
extraction was actually progressing.

Fix: the body moved to _tesseract_extract_sync and the async wrapper runs it
via asyncio.to_thread. Capability assertions drive the REAL wrapper.
"""
import asyncio
import threading
import time

import pytest

from aria_service.intel import ocr

# R-F3781/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


@pytest.mark.asyncio
async def test_tesseract_runs_off_the_event_loop_and_loop_stays_alive(monkeypatch):
    main_thread = threading.get_ident()
    seen = {}

    def _fake_sync(image_data):
        seen["thread"] = threading.get_ident()
        time.sleep(0.3)  # simulates the multi-PSM tesseract block
        return {"text": "hello world from ocr", "method": "tesseract",
                "confidence": 0.75}

    monkeypatch.setattr(ocr, "_tesseract_extract_sync", _fake_sync)

    ticks = {"n": 0}

    async def _heartbeat():
        for _ in range(30):
            await asyncio.sleep(0.01)
            ticks["n"] += 1

    hb = asyncio.create_task(_heartbeat())
    res = await ocr._ocr_via_tesseract(b"x" * 200)
    await hb

    assert res and res["text"].startswith("hello")
    # the extraction body ran in a WORKER thread, not on the loop thread
    assert seen["thread"] != main_thread
    # the event loop kept ticking while the "OCR" was running — pre-R-F1398
    # the inline body would have frozen the heartbeat for the whole block
    assert ticks["n"] >= 15


@pytest.mark.asyncio
async def test_sync_body_unavailable_returns_none_gracefully(monkeypatch):
    """The wrapper inherits the body's None-on-unavailable contract."""
    monkeypatch.setattr(ocr, "_tesseract_extract_sync", lambda b: None)
    assert await ocr._ocr_via_tesseract(b"x" * 200) is None


def test_chain_still_routes_through_async_wrapper():
    """extract_text_from_image's tesseract step must still await the
    (now thread-offloading) wrapper — source-level guard."""
    import inspect
    src = module_source(ocr)
    assert "return await _ocr_via_tesseract(image_data)" in src
    assert "asyncio.to_thread(_tesseract_extract_sync, image_data)" in src
