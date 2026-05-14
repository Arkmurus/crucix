"""R-F507 — /api/aria/eval/seed/load returns 202 Accepted (BackgroundTask).

Live evidence 2026-05-14 07:34 UTC: POST /api/aria/eval/seed/load returned
HTTP 503 with content-length:0 from fly's edge after ~60s — same cascade
pattern as the /brain/absorb 503 that R-F454 fixed. Root cause for the
synchronous endpoint: 27 fresh R-F505 entries each requiring
sentence-transformer embed pushed the request past fly's edge timeout.

R-F507 wraps the seed-load in FastAPI BackgroundTasks: endpoint returns
202 immediately, embedding fires in background. Caller polls
GET /api/aria/eval/coverage to see progress.

This test pins:
  1. The endpoint returns 202 (not 200/503)
  2. The response body has the expected shape
  3. seed_golden_set is actually invoked in the background (via spy)
  4. A force=true query is forwarded to the background task
"""
from __future__ import annotations

import pytest


def _build_app():
    from fastapi import FastAPI
    from aria_service.routes.aria import router as aria_router
    from aria_service.routes.aria import _router_auth_dep
    app = FastAPI()
    app.include_router(aria_router)
    app.dependency_overrides[_router_auth_dep] = lambda: None
    return app


def test_rf507_returns_202_immediately(monkeypatch):
    """The endpoint must return 202 Accepted, not 200 — that's how the
    caller (and the breaker rules) know the work is queued, not done."""
    from fastapi.testclient import TestClient

    captured = {"called": False}

    async def _fake_seed_load(force: bool = False):
        captured["called"] = True
        captured["force"] = force
        return {"added": 0, "skipped": 453, "errored": 0}

    from aria_service.intel import eval_golden_seed
    monkeypatch.setattr(eval_golden_seed, "seed_golden_set", _fake_seed_load)

    app = _build_app()
    with TestClient(app) as c:
        r = c.post("/api/aria/eval/seed/load")
    assert r.status_code == 202, (
        f"R-F507: expected 202 Accepted, got {r.status_code} body={r.text!r}"
    )


def test_rf507_response_shape(monkeypatch):
    from fastapi.testclient import TestClient

    async def _fake_seed_load(force: bool = False):
        return {"added": 0}

    from aria_service.intel import eval_golden_seed
    monkeypatch.setattr(eval_golden_seed, "seed_golden_set", _fake_seed_load)

    app = _build_app()
    with TestClient(app) as c:
        r = c.post("/api/aria/eval/seed/load")
    body = r.json()
    assert body.get("ok") is True
    assert body.get("accepted") is True
    assert "note" in body, "response must direct caller to /eval/coverage"
    assert "coverage" in body["note"].lower()


def test_rf507_force_query_forwarded(monkeypatch):
    """force=true must reach seed_golden_set so re-runs can override
    seed_id dedupe."""
    from fastapi.testclient import TestClient

    captured = {}

    async def _fake_seed_load(force: bool = False):
        captured["force"] = force
        return {"added": 0}

    from aria_service.intel import eval_golden_seed
    monkeypatch.setattr(eval_golden_seed, "seed_golden_set", _fake_seed_load)

    app = _build_app()
    with TestClient(app) as c:
        r = c.post("/api/aria/eval/seed/load", params={"force": "true"})
    assert r.status_code == 202
    # The background task runs to completion before TestClient returns,
    # so the spy should have captured the force value.
    assert captured.get("force") is True, (
        f"R-F507: force=true did not reach seed_golden_set (captured: {captured})"
    )


def test_rf507_background_task_actually_invokes_seed_load(monkeypatch):
    """Regression guard: the BackgroundTask must actually fire — a typo
    in `add_task` would silently no-op and the test should catch it."""
    from fastapi.testclient import TestClient

    invocations = []

    async def _fake_seed_load(force: bool = False):
        invocations.append({"force": force})
        return {"added": 0}

    from aria_service.intel import eval_golden_seed
    monkeypatch.setattr(eval_golden_seed, "seed_golden_set", _fake_seed_load)

    app = _build_app()
    with TestClient(app) as c:
        c.post("/api/aria/eval/seed/load")
    assert len(invocations) == 1, (
        f"R-F507: BackgroundTask did not invoke seed_golden_set "
        f"(got {len(invocations)} calls)"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
