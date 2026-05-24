"""R-F824 — Tests for /api/aria/coder/request + /coder/status + /coder/gaps.

Operator's vision: ARIA codes via natural language from WhatsApp / web
chat / Telegram. This file pins the HTTP entry point + progress tracking
behaviour.

Critical invariants tested
──────────────────────────
1. /coder/request returns 503 when the engine isn't started (app.state.
   aria_coder missing) — graceful degradation, no cryptic stack trace.
2. Validates description length (10-2000 chars).
3. Returns immediately with a fix_id; the actual work runs in a
   background task (no blocking the HTTP response on a 30-min pipeline).
4. /coder/status returns the latest progress event + history list.
5. /coder/status validates the fix_id (no path traversal / SQL).
6. operator_fix_request bypasses _wait_for_approval (operator_initiated=True).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _run(coro):
    return asyncio.run(coro)


# ════════════════════════════════════════════════════════════════════════════
# /api/aria/coder/request — HTTP endpoint
# ════════════════════════════════════════════════════════════════════════════

def _make_app_with_coder(coder):
    """Build an isolated FastAPI app with the aria router + injected coder."""
    from fastapi import FastAPI
    from aria_service.routes import aria as aria_routes

    app = FastAPI()
    app.dependency_overrides[aria_routes._router_auth_dep] = lambda: None
    app.include_router(aria_routes.router)
    app.state.aria_coder = coder
    app.state.llm_provider = MagicMock()  # required by other endpoints
    return app


def _make_mock_coder():
    """Coder stub with the methods the endpoint touches."""
    coder = MagicMock()

    # Redis interface (used directly by /coder/request for queued events,
    # and by /coder/status via coder.get_progress)
    redis = MagicMock()
    redis.setex = AsyncMock()
    redis.lpush = AsyncMock()
    redis.expire = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.lrange = AsyncMock(return_value=[])
    coder.redis = redis

    # operator_fix_request is what runs in the background
    coder.operator_fix_request = AsyncMock()

    # get_progress is what /coder/status calls
    async def _get_progress(fix_id):
        return {"fix_id": fix_id, "found": False}
    coder.get_progress = _get_progress
    return coder


class TestCoderRequest:
    def test_503_when_engine_not_started(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from aria_service.routes import aria as aria_routes

        app = FastAPI()
        app.dependency_overrides[aria_routes._router_auth_dep] = lambda: None
        app.include_router(aria_routes.router)
        # No app.state.aria_coder set
        client = TestClient(app)

        r = client.post(
            "/api/aria/coder/request",
            json={"description": "Fix the ACLED source returning 0 events"},
        )
        assert r.status_code == 503
        assert "engine not started" in r.json()["detail"].lower()

    def test_400_when_description_too_short(self) -> None:
        from fastapi.testclient import TestClient
        app = _make_app_with_coder(_make_mock_coder())
        client = TestClient(app)
        r = client.post(
            "/api/aria/coder/request", json={"description": "short"},
        )
        assert r.status_code == 400

    def test_400_when_description_missing(self) -> None:
        from fastapi.testclient import TestClient
        app = _make_app_with_coder(_make_mock_coder())
        client = TestClient(app)
        r = client.post("/api/aria/coder/request", json={})
        assert r.status_code == 400

    def test_400_when_description_too_long(self) -> None:
        from fastapi.testclient import TestClient
        app = _make_app_with_coder(_make_mock_coder())
        client = TestClient(app)
        r = client.post(
            "/api/aria/coder/request",
            json={"description": "X" * 2500},
        )
        assert r.status_code == 400

    def test_happy_path_returns_fix_id_immediately(self) -> None:
        """CAPABILITY: a valid request returns FAST with a fix_id, even
        though the actual pipeline would take minutes. The work runs in
        a background task that we don't wait on."""
        from fastapi.testclient import TestClient
        coder = _make_mock_coder()
        # The background task could take forever — we still want a
        # fast HTTP response.

        async def slow_fix(description, module_hint=None):
            await asyncio.sleep(60)  # would block the client if awaited

        coder.operator_fix_request = slow_fix
        app = _make_app_with_coder(coder)
        client = TestClient(app)

        r = client.post(
            "/api/aria/coder/request",
            json={
                "description": "Add SSB Turkey to the tender monitor",
                "source": "whatsapp",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["queued"] is True
        assert "fix_id" in body
        assert "poll_url" in body
        assert body["poll_url"].startswith("/api/aria/coder/status/")
        assert "alternate_poll_url" in body
        # Sanity: didn't burn 60s in the test (background runs async)

    def test_queued_event_written_to_redis(self) -> None:
        """Initial 'queued' progress event lands so the operator can
        poll /coder/status/{fix_id} immediately and see 'queued' status
        before stages start firing."""
        from fastapi.testclient import TestClient
        coder = _make_mock_coder()
        app = _make_app_with_coder(coder)
        client = TestClient(app)

        r = client.post(
            "/api/aria/coder/request",
            json={
                "description": "Improve sanctions cross-jurisdiction discipline",
                "source": "web",
            },
        )
        assert r.status_code == 200
        # We expect setex was called at least once per id (fix_id + gap_id)
        assert coder.redis.setex.await_count >= 2
        # The first call should be for the latest key with a stage:queued
        first_call = coder.redis.setex.await_args_list[0]
        key, ttl, value = first_call.args
        assert "progress" in key
        assert "queued" in value


# ════════════════════════════════════════════════════════════════════════════
# /api/aria/coder/status/{fix_id}
# ════════════════════════════════════════════════════════════════════════════

class TestCoderStatus:
    def test_503_when_engine_not_started(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from aria_service.routes import aria as aria_routes

        app = FastAPI()
        app.dependency_overrides[aria_routes._router_auth_dep] = lambda: None
        app.include_router(aria_routes.router)
        client = TestClient(app)

        r = client.get("/api/aria/coder/status/abc12345")
        assert r.status_code == 503

    def test_400_on_invalid_fix_id(self) -> None:
        """Reject non-hex characters in the fix_id (path-traversal guard)."""
        from fastapi.testclient import TestClient
        app = _make_app_with_coder(_make_mock_coder())
        client = TestClient(app)

        # FastAPI will already URL-decode; we test the parameter validator
        r = client.get("/api/aria/coder/status/zzznotahex@$")
        assert r.status_code == 400

    def test_returns_progress_payload(self) -> None:
        from fastapi.testclient import TestClient
        coder = _make_mock_coder()

        async def fake_progress(fix_id):
            return {
                "fix_id": fix_id,
                "found": True,
                "latest": {"stage": "writing_code", "message": "Writing X..."},
                "history": [
                    {"stage": "queued", "message": "Q"},
                    {"stage": "context", "message": "C"},
                    {"stage": "planning", "message": "P"},
                ],
            }

        coder.get_progress = fake_progress
        app = _make_app_with_coder(coder)
        client = TestClient(app)

        r = client.get("/api/aria/coder/status/abc12345")
        assert r.status_code == 200
        body = r.json()
        assert body["found"] is True
        assert body["latest"]["stage"] == "writing_code"
        assert len(body["history"]) == 3

    def test_returns_not_found_when_unknown(self) -> None:
        from fastapi.testclient import TestClient
        coder = _make_mock_coder()  # get_progress returns found=False
        app = _make_app_with_coder(coder)
        client = TestClient(app)

        r = client.get("/api/aria/coder/status/ffffffffffff")
        assert r.status_code == 200
        assert r.json()["found"] is False


# ════════════════════════════════════════════════════════════════════════════
# /api/aria/coder/gaps
# ════════════════════════════════════════════════════════════════════════════

class TestCoderGaps:
    def test_503_when_engine_not_started(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from aria_service.routes import aria as aria_routes

        app = FastAPI()
        app.dependency_overrides[aria_routes._router_auth_dep] = lambda: None
        app.include_router(aria_routes.router)
        client = TestClient(app)

        r = client.get("/api/aria/coder/gaps")
        assert r.status_code == 503

    def test_returns_empty_when_no_scan_yet(self) -> None:
        from fastapi.testclient import TestClient
        coder = _make_mock_coder()
        app = _make_app_with_coder(coder)
        client = TestClient(app)

        r = client.get("/api/aria/coder/gaps")
        assert r.status_code == 200
        body = r.json()
        assert body["gaps"] == []

    def test_returns_gaps_from_redis(self) -> None:
        from fastapi.testclient import TestClient
        coder = _make_mock_coder()
        import json
        coder.redis.get = AsyncMock(return_value=json.dumps([
            {"gap_id": "abc", "severity": 3, "title": "Test gap"},
        ]).encode("utf-8"))
        app = _make_app_with_coder(coder)
        client = TestClient(app)

        r = client.get("/api/aria/coder/gaps")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        assert body["gaps"][0]["title"] == "Test gap"


# ════════════════════════════════════════════════════════════════════════════
# operator_fix_request bypasses approval polling
# ════════════════════════════════════════════════════════════════════════════

class TestOperatorInitiatedSkipsApproval:
    def test_operator_initiated_flag_passes_through_to_fix_gap(self) -> None:
        """CAPABILITY: operator_fix_request must call fix_gap with
        operator_initiated=True. Without this, fix_gap would call
        _wait_for_approval which polls Redis for an approve/reject —
        and since no one is around to click approve, it would
        deadlock for 30 minutes before timing out."""
        from aria_service.autonomous.self_coder import ARIACoder
        from pathlib import Path
        import tempfile

        coder = ARIACoder(
            redis_client=MagicMock(),
            aria_service_url="http://test",
            gap_detector=MagicMock(),
            llm=MagicMock(),
            validator=MagicMock(),
            codebase=MagicMock(),
            test_runner=MagicMock(),
            deployer=MagicMock(),
            r_counter=MagicMock(),
            workspace_base=Path(tempfile.mkdtemp(prefix="rf824_")),
        )
        captured = {}

        async def fake_fix_gap(gap, operator_initiated=False, force_stage_only=False):
            captured["gap"] = gap
            captured["operator_initiated"] = operator_initiated
            captured["force_stage_only"] = force_stage_only
            from aria_service.autonomous.self_coder import FixResult
            return FixResult(success=True, fix_id="x", gap_id=gap.gap_id)

        coder.fix_gap = fake_fix_gap

        async def body():
            return await coder.operator_fix_request(
                "Add SSB Turkey to the tender monitor", module_hint=None,
            )

        result = _run(body())
        assert result.success
        # Critical assertion — fix_gap was called with operator_initiated=True
        assert captured["operator_initiated"] is True
        assert captured["gap"].title.startswith("Add SSB Turkey")
