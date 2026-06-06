"""R-F1377 — async chatjob/readdoc tasks must hold STRONG references.

The operator's symptom (2026-06-06): WhatsApp document parsing and review
"hitting timeout". Chain: WA listener POSTs /api/aria/read-document
{async:true} (parse) and /api/aria/chat {async_mode:true} (the review reply),
then polls the job stores. Both endpoints launched their background job with a
bare ``asyncio.create_task`` — asyncio keeps only a WEAK reference, so under a
saturated event loop the task can be garbage-collected mid-flight (the exact
R-F1363 pt1 class that silently killed coder fix requests). A GC'd job leaves
the store at "processing" forever; the listener polls to its 15-minute ceiling
and the user sees "extraction timed out" / "ARIA is temporarily unavailable".

Fix: ``_hold_job_task`` pins each job task in module-level ``_ASYNC_JOB_TASKS``
until done (done-callback self-cleans), mirroring ``_CODER_BG_TASKS``.

Capability tests drive the REAL endpoints (TestClient) and assert:
  1. while the job runs, the task is held in _ASYNC_JOB_TASKS (GC-proof),
  2. the job reaches a terminal status (proves the task executed),
  3. after completion the set self-cleans (no unbounded growth).

Project convention: asyncio.run / TestClient, mocks at the LLM boundary.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest


def _make_app_with_llm():
    from fastapi import FastAPI
    from aria_service.routes import aria as aria_routes

    class _FakeLLM:
        is_configured = True
        name = "fake"

        async def complete(self, system_prompt, user_message, *,
                           max_tokens=4096, timeout=60.0, **kwargs):
            return SimpleNamespace(
                text="ok", model="fake-1", input_tokens=1, output_tokens=1,
                routed_via="",
            )

    app = FastAPI()
    app.dependency_overrides[aria_routes._router_auth_dep] = lambda: None
    app.include_router(aria_routes.router)
    app.state.llm_provider = _FakeLLM()
    app.state.current_data = None
    return app


def _poll_job(client, url: str, deadline_s: float = 30.0) -> dict:
    """Poll a job result URL until terminal status or deadline."""
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


class TestReadDocJobPinned:
    def test_rf1377_readdoc_async_job_held_and_completes(self):
        """Capability: async read-document job is pinned while running and
        reaches a terminal state — never GC-able into eternal 'processing'."""
        from fastapi.testclient import TestClient
        from aria_service.routes import aria as aria_routes

        app = _make_app_with_llm()
        with TestClient(app) as client:
            r = client.post(
                "/api/aria/read-document",
                json={
                    "async": True,
                    "filename": "rf1377-probe.txt",
                    "content": "Plain text capability probe for R-F1377.",
                    "content_type": "text/plain",
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert body.get("async") is True and body.get("job_id")

            # 1. The task is STRONGLY held (this is the fix's contract —
            #    pre-R-F1377 the set didn't exist / stayed empty).
            held_names = {t.get_name() for t in aria_routes._ASYNC_JOB_TASKS}
            assert any(n.startswith("readdoc.") for n in held_names), (
                f"readdoc job task not pinned; held={held_names}"
            )

            # 2. The job executes to a terminal state (done OR failed-with-
            #    reason both prove execution; eternal 'processing' is the bug).
            final = _poll_job(client, body["poll_url"])
            assert final.get("status") in ("done", "failed"), final

        # 3. Self-clean: no readdoc task lingers after completion.
        held_after = {t.get_name() for t in aria_routes._ASYNC_JOB_TASKS}
        assert not any(n.startswith("readdoc.") for n in held_after)


class TestChatJobPinned:
    def test_rf1377_chat_async_job_held_and_completes(self):
        """Capability: the async chat job (the WA doc-REVIEW reply path,
        R-F982 routes every WA chat through it) is pinned and terminal."""
        from fastapi.testclient import TestClient
        from aria_service.routes import aria as aria_routes

        app = _make_app_with_llm()
        with TestClient(app) as client:
            r = client.post(
                "/api/aria/chat",
                json={
                    "message": "R-F1377 capability probe — reply briefly.",
                    "async_mode": True,
                    "session_id": "rf1377-test",
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert body.get("async") is True and body.get("job_id")

            held_names = {t.get_name() for t in aria_routes._ASYNC_JOB_TASKS}
            assert any(n.startswith("chatjob.") for n in held_names), (
                f"chat job task not pinned; held={held_names}"
            )

            final = _poll_job(client, body["poll_url"])
            assert final.get("status") in ("done", "failed"), final

        held_after = {t.get_name() for t in aria_routes._ASYNC_JOB_TASKS}
        assert not any(n.startswith("chatjob.") for n in held_after)


class TestOcrJobPinned:
    def test_rf1377_ocr_async_job_held_and_terminal(self):
        """Capability: the async OCR job (scanned-document parsing, R-F1311)
        is pinned and reaches a terminal state. A non-image payload makes the
        extractor fail FAST — 'failed' still proves the task executed; the bug
        was eternal 'processing'."""
        import base64
        from fastapi.testclient import TestClient
        from aria_service.routes import aria as aria_routes

        app = _make_app_with_llm()
        with TestClient(app) as client:
            r = client.post(
                "/api/aria/ocr",
                json={
                    "async": True,
                    "filename": "rf1377-probe.png",
                    "image": base64.b64encode(b"not-a-real-image").decode(),
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert body.get("async") is True and body.get("job_id")

            held_names = {t.get_name() for t in aria_routes._ASYNC_JOB_TASKS}
            assert any(n.startswith("ocrjob.") for n in held_names), (
                f"ocr job task not pinned; held={held_names}"
            )

            final = _poll_job(client, body["poll_url"])
            assert final.get("status") in ("done", "failed"), final

        held_after = {t.get_name() for t in aria_routes._ASYNC_JOB_TASKS}
        assert not any(n.startswith("ocrjob.") for n in held_after)


class TestHoldHelperUnit:
    def test_rf1377_hold_job_task_pins_and_self_cleans(self):
        """Unit: helper pins a task until done, then discards it."""
        import asyncio
        from aria_service.routes import aria as aria_routes

        async def _scenario():
            async def _work():
                await asyncio.sleep(0.01)
                return 42

            t = asyncio.create_task(_work(), name="chatjob.unit-test")
            aria_routes._hold_job_task(t)
            assert t in aria_routes._ASYNC_JOB_TASKS
            result = await t
            assert result == 42
            # done-callbacks run on the loop's next cycle
            await asyncio.sleep(0)
            assert t not in aria_routes._ASYNC_JOB_TASKS

        asyncio.run(_scenario())
