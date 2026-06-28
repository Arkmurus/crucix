"""R-F2083 — async read-document resolves on extracted_text FAST; heavy learning deferred.

Operator symptom (recurring, latest 2026-06-28 13:01): a WhatsApp document
("Korvera redline") fails with "document service didn't respond". Root cause
(proven by live probe — a trivial 48-char PDF took 46–104s): the async
read-document job ran researcher.read_document (per-chunk LLM fact-extraction +
RAG embed) INLINE on the single-core event loop, despite defer_intel. A redline
(≈2× text) exceeded the 600s cap → failed → R-F2070 resubmit re-ran it → failed
again → the user got nothing.

Fix: when defer_intel is set (the async/WhatsApp path), the job returns the
already-extracted text IMMEDIATELY and runs read_document + document_intelligence
as held background tasks (the CPU-bound embed inside read_document already runs in
a separate process via encode_offload, R-F2044).

Capability test drives the REAL /api/aria/read-document async endpoint with a
read_document monkeypatched to be SLOW, and asserts the job still completes FAST
with extracted_text — i.e. the heavy chain no longer blocks the user's result.
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


def test_rf2083_async_readdoc_returns_text_without_waiting_for_learning(monkeypatch):
    """The async job must resolve on extracted_text in seconds even when the
    learning chain (read_document) is slow — that is the redline-timeout fix."""
    from fastapi.testclient import TestClient
    from aria_service.routes import aria as aria_routes

    learning_started = {"v": False}

    async def _slow_read_document(llm, content, filename="", source="", context=""):
        # Simulate the expensive per-chunk LLM + embed chain.
        learning_started["v"] = True
        await asyncio.sleep(30)
        return {"facts_learned": 99, "summary": "should not block the job"}

    # read_document is a module global in routes/aria.py (imported from researcher).
    monkeypatch.setattr(aria_routes, "read_document", _slow_read_document)

    app = _make_app()
    with TestClient(app) as client:
        t0 = time.time()
        r = client.post(
            "/api/aria/read-document",
            json={
                "async": True,
                "filename": "rf2083-redline.txt",
                "content": "KORVERA UTS AGENT AGREEMENT — Clause 1 Indemnity. " * 50,
                "content_type": "text/plain",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("async") is True and body.get("job_id"), body

        final = _poll_job(client, body["poll_url"], deadline_s=12.0)
        elapsed = time.time() - t0

        # Headline contract: the job is DONE quickly (NOT blocked ~30s by learning).
        assert final.get("status") == "done", f"job must resolve on text, got: {final}"
        assert elapsed < 10, (
            f"job took {elapsed:.1f}s — it is still waiting for the slow learning "
            f"chain (the redline-timeout bug); it must resolve on extracted_text"
        )
        res = final.get("result") or {}
        assert "KORVERA" in (res.get("extracted_text") or ""), res
        assert res.get("learning_deferred") is True, res
        assert res.get("doc_intel_deferred") is True, res


def test_rf2083_sync_readdoc_still_runs_learning_inline(monkeypatch):
    """Sync callers (no defer_intel) keep the inline learning pass — only the
    async path defers."""
    from fastapi.testclient import TestClient
    from aria_service.routes import aria as aria_routes

    called = {"v": False}

    async def _fast_read_document(llm, content, filename="", source="", context=""):
        called["v"] = True
        return {"facts_learned": 3, "extracted_text": content, "summary": "ok"}

    monkeypatch.setattr(aria_routes, "read_document", _fast_read_document)

    app = _make_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/aria/read-document",
            json={"filename": "rf2083-sync.txt",
                  "content": "A small but valid document body over twenty chars.",
                  "content_type": "text/plain"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Sync path ran read_document inline.
        assert called["v"] is True, "sync path must run read_document inline"
        assert "learning_deferred" not in body, body
