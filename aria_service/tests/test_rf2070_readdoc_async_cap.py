"""R-F2070 — async read-document wall-clock cap.

Operator symptom (2026-06-28, Korvera redline): a WhatsApp document upload was
acknowledged ("📥 Reading…") but never delivered — "document service didn't
respond — resend". Root cause (live probe): the ASYNC read-document job had NO
wall-clock cap ("read to completion"), so a pathological / corrupt / huge file
sat in 'processing' indefinitely (a malformed PDF was still 'processing' at
308s). The WA listener then waited its full 15-minute poll window and gave up.

Fix: wrap the async extraction in asyncio.wait_for(timeout=ARIA_READ_DOC_ASYNC_
TIMEOUT_S, default 600s, well under the 15-min client window). A stuck extraction
now FAILS CLEANLY so the listener's R-F2070 auto-resubmit (or the honest paste-
the-text message) fires promptly instead of a dead 15-minute wait.

Capability test drives the REAL /api/aria/read-document async endpoint via
TestClient with a hung extractor and a 1s cap, and asserts the job reaches a
terminal 'failed' state carrying the cap reason — NOT an eternal 'processing'.
"""
from __future__ import annotations

import asyncio
import time

from types import SimpleNamespace


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


def _poll_job(client, url: str, deadline_s: float = 20.0) -> dict:
    t0 = time.time()
    last: dict = {}
    while time.time() - t0 < deadline_s:
        r = client.get(url)
        assert r.status_code == 200
        last = r.json()
        if last.get("status") in ("done", "failed"):
            return last
        time.sleep(0.1)
    return last


def test_rf2070_async_readdoc_hung_extraction_fails_at_cap(monkeypatch):
    """A hung extraction must hit the cap and fail cleanly — never 'processing'
    forever (the 2026-06-28 'document service didn't respond' symptom)."""
    from fastapi.testclient import TestClient
    from aria_service.routes import aria as aria_routes

    # Tiny cap so the test is fast; the production default is 600s.
    monkeypatch.setenv("ARIA_READ_DOC_ASYNC_TIMEOUT_S", "1")

    async def _hang(_request):
        await asyncio.sleep(30)        # simulate the "processing forever" hang
        return {"extracted_text": "should never get here"}

    # _r873_run resolves _read_document_ep_impl as a module global at call time.
    monkeypatch.setattr(aria_routes, "_read_document_ep_impl", _hang)

    app = _make_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/aria/read-document",
            json={
                "async": True,
                "filename": "rf2070-hang-probe.pdf",
                "content": "doesn't matter — the extractor is monkeypatched to hang",
                "content_type": "application/pdf",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("async") is True and body.get("job_id"), body

        final = _poll_job(client, body["poll_url"], deadline_s=15.0)
        # The headline contract: the job is TERMINAL and FAILED — not stuck.
        assert final.get("status") == "failed", (
            f"hung extraction must fail at the cap, got: {final}"
        )
        # And it carries the R-F2070 cap reason, not some unrelated error.
        assert "cap" in (final.get("error") or "").lower(), final


def test_rf2070_async_readdoc_fast_extraction_still_succeeds(monkeypatch):
    """The cap must not break the happy path: a quick extraction still returns
    'done' with its result."""
    from fastapi.testclient import TestClient
    from aria_service.routes import aria as aria_routes

    monkeypatch.setenv("ARIA_READ_DOC_ASYNC_TIMEOUT_S", "10")

    async def _fast(_request):
        return {"extracted_text": "Clause 1 Indemnity. Clause 2 Termination.",
                "summary": "2-clause agreement"}

    monkeypatch.setattr(aria_routes, "_read_document_ep_impl", _fast)

    app = _make_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/aria/read-document",
            json={"async": True, "filename": "rf2070-ok.pdf",
                  "content": "x", "content_type": "application/pdf"},
        )
        body = r.json()
        final = _poll_job(client, body["poll_url"], deadline_s=10.0)
        assert final.get("status") == "done", final
        assert "Indemnity" in (final.get("result", {}).get("extracted_text") or ""), final
