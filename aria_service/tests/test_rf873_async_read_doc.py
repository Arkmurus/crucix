"""R-F873 — async document-extraction pipeline.

The 80s sync cap (R-F869) still 504'd on large/scanned trade-finance contracts
when the autonomous absorb storm slowed the loop. The proper fix: read-document
accepts `async: true`, enqueues a background extraction with NO sync cap, returns
a job_id immediately, and the caller polls /read-document/result/{job_id}. The
wedge can only SLOW the job, never time it out.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "routes" / "aria.py").read_text(encoding="utf-8")


def test_async_branch_present():
    assert 'if _r873_body.get("async"):' in SRC
    assert "_read_document_ep_impl(_r873_shim)" in SRC


def test_background_task_has_no_sync_cap():
    """The async job must call _read_document_ep_impl WITHOUT wrapping it in a
    wait_for timeout — otherwise it inherits the same 504 it was meant to escape."""
    # The async runner calls the impl directly inside _r873_run.
    assert "async def _r873_run():" in SRC
    # The only wait_for around the impl is the *sync*-path cap.
    assert SRC.count("wait_for(\n            _read_document_ep_impl") == 1


def test_result_poll_endpoint_present():
    assert '@router.get("/read-document/result/{job_id}")' in SRC
    assert "async def read_document_result_ep" in SRC


def test_job_store_helpers_present():
    assert "_readdoc_job_set" in SRC
    assert "_readdoc_job_get" in SRC
    assert "_READDOC_JOB_PREFIX" in SRC


def test_shim_returns_body_without_rereading():
    from aria_service.routes import aria as a
    shim = a._ReadDocBodyRequest(None, {"filename": "x.pdf", "async": True})
    got = asyncio.run(shim.json())
    assert got == {"filename": "x.pdf", "async": True}


def test_wa_listener_uses_async_and_polls():
    wa = (Path(__file__).resolve().parents[2] / "services" / "wa-listener" / "aria_wa_listener.mjs").read_text(encoding="utf-8")
    assert "async: true" in wa  # WA posts async mode for documents
    assert "read-document/result/" in wa  # …and polls the result endpoint


def test_rf880_async_defers_intelligence():
    """R-F880 — the async job path defers the heavy document_intelligence +
    brain-absorb (the embedder-from-HF step) so the job resolves on the full
    extracted text fast. Sync callers keep the inline overview."""
    assert '_r873_body["defer_intel"] = True' in SRC          # async branch flags defer
    assert '_defer_intel = bool(body.get("defer_intel"))' in SRC
    assert "async def _di_bg():" in SRC                        # backgrounded
    assert "create_task(_di_bg()" in SRC
    # sync path still awaits inline
    assert "di = await _di.process_document(" in SRC
