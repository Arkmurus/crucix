"""R-F2085 — keep the brain event loop responsive during PDF ingest.

Live-logs root cause (2026-06-28 12:42-12:46Z, reproduced 3×): a WhatsApp redline
PDF made the WA listener report "Brain unreachable for 3 consecutive checks —
aborting doc poll" → circuit OPEN → R-F2070 resubmit fast-failed → "document
service didn't respond". The brain was ALIVE but BUSY: pdf_deep_ingest runs
synchronous fitz Pixmap/tobytes + per-image Tesseract OCR ON the single event
loop (no to_thread/yield), so a multi-page PDF starved /health/live for 90s+ and
the WA listener declared the brain dead.

Fix: (1) on the async/WhatsApp doc path, skip the per-page IMAGE OCR
(ingest_images=False) — it is the heaviest in-loop CPU and the WA review only
needs text; (2) yield the loop (await asyncio.sleep(0)) between pages.

Capability test drives the REAL /api/aria/read-document endpoint with a fitz-built
PDF and asserts the async path passes ingest_images=False while the sync path
keeps True; plus the per-page yield is present in pdf_deep_ingest.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

# R-F3755/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source

# R-F3795 — the fixture builds a real PDF with fitz (PyMuPDF), which has no
# win-arm64 wheel (§16). ENVIRONMENT gap; runs in the Linux image.
from ._env_probe import requires_module


def _make_app():
    from fastapi import FastAPI
    from aria_service.routes import aria as aria_routes

    class _FakeLLM:
        is_configured = True
        name = "fake"

        async def complete(self, *a, **k):
            return SimpleNamespace(text="ok", model="fake-1",
                                   input_tokens=1, output_tokens=1, routed_via="")

    app = FastAPI()
    app.dependency_overrides[aria_routes._router_auth_dep] = lambda: None
    app.include_router(aria_routes.router)
    app.state.llm_provider = _FakeLLM()
    app.state.current_data = None
    return app


def _make_pdf_b64() -> str:
    import base64
    import fitz
    doc = fitz.open()
    for p in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {p + 1} — KORVERA agreement clause text " * 8, fontsize=10)
    raw = doc.tobytes()
    doc.close()
    return base64.b64encode(raw).decode()


@pytest.fixture
def _captured(monkeypatch):
    """Capture the ingest_images kwarg pdf_deep_ingest is called with, and stub
    the heavy LLM/embedding work so the test stays fast + offline."""
    from aria_service.intel import pdf_deep_ingest as _pdi
    from aria_service.routes import aria as aria_routes

    seen = {}

    async def _fake_ingest(raw_bytes, filename, source_context="", ingest_images=True, **kw):
        seen["ingest_images"] = ingest_images
        return {"total_pages": 0, "text_pages": 0, "images_ocrd": 0, "errors": []}

    async def _fake_read_document(llm, content, filename="", source="", context=""):
        return {"facts_learned": 0, "extracted_text": content}

    monkeypatch.setattr(_pdi, "ingest_pdf_multi_page", _fake_ingest)
    monkeypatch.setattr(aria_routes, "read_document", _fake_read_document)
    return seen


@requires_module("fitz")
def test_rf2085_async_path_skips_image_ocr(_captured):
    """The async (WhatsApp) doc path must NOT trigger per-page image OCR — that
    is the in-loop CPU that stalled /health/live and false-killed the doc read."""
    from fastapi.testclient import TestClient

    app = _make_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/aria/read-document",
            json={"async": True, "filename": "rf2085.pdf",
                  "content": _make_pdf_b64(), "encoding": "base64",
                  "mimetype": "application/pdf"},
        )
        assert r.status_code == 200, r.text
        job = r.json().get("job_id")
        assert job
        # Drive the job to a terminal state, then let the fire-and-forget ingest
        # task dispatch (the PDF block runs inside the job).
        import time
        poll = f"/api/aria/read-document/result/{job}"
        for _ in range(120):
            st = client.get(poll).json()
            if st.get("status") in ("done", "failed"):
                break
            time.sleep(0.1)
        for _ in range(60):
            if "ingest_images" in _captured:
                break
            time.sleep(0.1)
    assert _captured.get("ingest_images") is False, (
        f"async path must call pdf_deep_ingest with ingest_images=False, "
        f"got {_captured}"
    )


@requires_module("fitz")
def test_rf2085_sync_path_keeps_image_ocr(_captured):
    """Sync callers (email/small uploads) keep image OCR — only the async path skips it."""
    from fastapi.testclient import TestClient

    app = _make_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/aria/read-document",
            json={"filename": "rf2085-sync.pdf",
                  "content": _make_pdf_b64(), "encoding": "base64",
                  "mimetype": "application/pdf"},
        )
        assert r.status_code == 200, r.text
    assert _captured.get("ingest_images") is True, (
        f"sync path must keep image OCR (ingest_images=True), got {_captured}"
    )


def test_rf2085_pdf_ingest_yields_per_page():
    """pdf_deep_ingest must yield the loop between pages (await asyncio.sleep(0))
    so the per-page sync CPU can't starve /health/live."""
    import inspect
    from aria_service.intel import pdf_deep_ingest
    src = function_source(pdf_deep_ingest, "ingest_pdf_multi_page")
    assert "asyncio.sleep(0)" in src, "per-page loop must yield via asyncio.sleep(0)"
