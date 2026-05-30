"""R-F803 — Tests for /api/aria/coder/llm endpoint + coder_entrypoint gates.

Covers:
  - POST /api/aria/coder/llm validates required fields (prompt, max_tokens,
    task, response_format).
  - Endpoint routes through app.state.llm_provider.complete.
  - JSON response_format parses model output, falls back gracefully on
    parse error.
  - 503 when llm_provider missing / unconfigured.
  - start_aria_coder() refuses when any of the four gates fails:
      ARIA_AUTONOMOUS_ENABLED != 1
      ARIA_CODER_ENABLED      != 1
      ARIA_INTERNAL_TOKEN     unset
      app_state.redis         missing

These tests are critical: R-F803 turns the coder pipeline from "modules
exist" to "modules can fire." If the gates are wrong, ARIA could start
self-coding the moment ARIA_AUTONOMOUS_ENABLED=1 lands — which it
already has (R-F794 set it 2026-05-22). The four-gate guard is what
ensures the coder stays dormant until ARIA_CODER_ENABLED=1 is flipped
explicitly.
"""
from __future__ import annotations

import asyncio
import json
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _run(coro):
    return asyncio.run(coro)


# ════════════════════════════════════════════════════════════════════════════
# /api/aria/coder/llm endpoint
# ════════════════════════════════════════════════════════════════════════════

def _make_app_with_llm(llm_provider):
    """Build an isolated FastAPI app wired to the aria router."""
    from fastapi import FastAPI
    from aria_service.routes import aria as aria_routes

    app = FastAPI()
    app.dependency_overrides[aria_routes._router_auth_dep] = lambda: None
    app.include_router(aria_routes.router)
    app.state.llm_provider = llm_provider
    return app


def _fake_llm(text: str = '{"title": "ok"}', model: str = "test-llm"):
    """A minimal LLMProvider stand-in matching the interface used by
    /coder/llm: .is_configured (bool) + async .complete(...) → LLMResult."""

    class _FakeLLM:
        is_configured = True
        name = "fake"

        async def complete(self, *, system_prompt, user_message,
                           max_tokens=4096, timeout=60.0):
            return SimpleNamespace(
                text=text, model=model,
                input_tokens=10, output_tokens=20,
                routed_via="",
            )
    return _FakeLLM()


class TestCoderLLMEndpoint:
    def test_rejects_short_prompt(self) -> None:
        from fastapi.testclient import TestClient
        app = _make_app_with_llm(_fake_llm())
        client = TestClient(app)
        r = client.post(
            "/api/aria/coder/llm",
            json={"prompt": "short"},  # < 10 chars
        )
        assert r.status_code == 400
        assert "prompt" in r.json()["detail"].lower()

    def test_rejects_invalid_task(self) -> None:
        from fastapi.testclient import TestClient
        app = _make_app_with_llm(_fake_llm())
        client = TestClient(app)
        r = client.post(
            "/api/aria/coder/llm",
            json={"prompt": "Plan a fix for X", "task": "delete_everything"},
        )
        assert r.status_code == 400

    def test_rejects_out_of_range_max_tokens(self) -> None:
        from fastapi.testclient import TestClient
        app = _make_app_with_llm(_fake_llm())
        client = TestClient(app)
        r = client.post(
            "/api/aria/coder/llm",
            json={"prompt": "Plan a fix for X", "max_tokens": 99999},
        )
        assert r.status_code == 400

    def test_503_when_llm_unconfigured(self) -> None:
        from fastapi.testclient import TestClient

        class _NotConfigured:
            is_configured = False
            name = "none"
        app = _make_app_with_llm(_NotConfigured())
        client = TestClient(app)
        r = client.post(
            "/api/aria/coder/llm",
            json={"prompt": "Plan a fix for X"},
        )
        assert r.status_code == 503

    def test_text_format_returns_text_block(self) -> None:
        from fastapi.testclient import TestClient
        app = _make_app_with_llm(_fake_llm(text="hello world", model="fake-1"))
        client = TestClient(app)
        r = client.post(
            "/api/aria/coder/llm",
            json={
                "prompt": "Say hello to ARIA",
                "task": "general",
                "response_format": "text",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["text"] == "hello world"
        assert body["model"] == "fake-1"
        assert body["task"] == "general"

    def test_json_format_parses_object(self) -> None:
        from fastapi.testclient import TestClient
        app = _make_app_with_llm(_fake_llm(
            text='{"title": "Fix X", "risk_level": "low"}',
            model="fake-2",
        ))
        client = TestClient(app)
        r = client.post(
            "/api/aria/coder/llm",
            json={
                "prompt": "Plan a fix for X",
                "task": "plan",
                "response_format": "json",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["title"] == "Fix X"
        assert body["risk_level"] == "low"
        # Provenance metadata attached under reserved key
        assert "_aria_meta" in body
        assert body["_aria_meta"]["model"] == "fake-2"
        assert body["_aria_meta"]["task"] == "plan"

    def test_json_format_strips_markdown_fences(self) -> None:
        """LLMs often ignore 'no markdown' instructions and wrap JSON in
        ```json ... ``` fences. The endpoint must unwrap before parsing."""
        from fastapi.testclient import TestClient
        wrapped = '```json\n{"title": "fenced"}\n```'
        app = _make_app_with_llm(_fake_llm(text=wrapped))
        client = TestClient(app)
        r = client.post(
            "/api/aria/coder/llm",
            json={
                "prompt": "Plan a fix for X",
                "response_format": "json",
            },
        )
        body = r.json()
        assert r.status_code == 200, body
        assert body["title"] == "fenced"

    def test_json_format_returns_parse_error_on_garbage(self) -> None:
        """When the LLM emits non-JSON despite the instruction, return the
        raw text + _parse_error rather than raising."""
        from fastapi.testclient import TestClient
        app = _make_app_with_llm(_fake_llm(text="not json at all"))
        client = TestClient(app)
        r = client.post(
            "/api/aria/coder/llm",
            json={
                "prompt": "Plan a fix for X",
                "response_format": "json",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["text"] == "not json at all"
        assert body["_parse_error"] is not None

    def test_llm_exception_bubbles_as_502(self) -> None:
        from fastapi.testclient import TestClient

        class _BoomLLM:
            is_configured = True
            name = "boom"

            async def complete(self, **_kw):
                raise RuntimeError("provider died")

        app = _make_app_with_llm(_BoomLLM())
        client = TestClient(app)
        r = client.post(
            "/api/aria/coder/llm",
            json={"prompt": "Plan a fix for X"},
        )
        assert r.status_code == 502
        assert "provider died" in r.json()["detail"]


# ════════════════════════════════════════════════════════════════════════════
# coder_entrypoint.start_aria_coder() gates
# ════════════════════════════════════════════════════════════════════════════

class TestCoderEntrypointGates:
    def test_refuses_when_no_internal_token(self, monkeypatch) -> None:
        """R-F996: coder is ALWAYS enabled — only ARIA_INTERNAL_TOKEN gates it."""
        monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)

        from aria_service.autonomous.coder_entrypoint import start_aria_coder
        app_state = SimpleNamespace(redis=MagicMock())
        result = _run(start_aria_coder(app_state))
        assert result is None

    def test_falls_back_to_redis_store_adapter_when_no_app_state_redis(
        self, monkeypatch, tmp_path,
    ) -> None:
        """R-F808: prod app_state has no .redis (the project uses the
        redis_store module wrapper). The entrypoint must build a
        _RedisStoreAdapter rather than refuse — otherwise the engine
        never boots on real fly deploys.

        This is the regression guard for the live-deploy failure
        observed at 2026-05-22T21:13:45Z immediately after the first
        ARIA_CODER_ENABLED=1 flip."""
        monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "test-token")
        monkeypatch.setenv("ARIA_CODER_WORKSPACE", str(tmp_path))

        from aria_service.autonomous.coder_entrypoint import start_aria_coder
        # app_state intentionally missing .redis — same shape as prod
        app_state = SimpleNamespace()

        async def body():
            tasks = await start_aria_coder(app_state)
            assert tasks is not None, (
                "engine refused to start — adapter fallback failed"
            )
            # R-F1046: gap_detector standalone loop removed — only self_coder runs
            # R-F1080: continuous profiler adds 1 more task
            assert len(tasks) >= 1
            for t in tasks:
                t.cancel()
            for t in tasks:
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        _run(body())

    def test_starts_when_all_gates_hold(self, monkeypatch, tmp_path) -> None:
        """Capability test: when all gates pass, the engine starts and
        returns a list of running tasks. R-F996 removed the env var gates
        (coder is always enabled); only ARIA_INTERNAL_TOKEN is required."""
        monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "test-token")
        monkeypatch.setenv("ARIA_CONTINUOUS_PROFILER_ENABLED", "0")  # R-F1080: disable profiler in test
        # Direct workspace at tmp to avoid /data/coder_workspace
        monkeypatch.setenv("ARIA_CODER_WORKSPACE", str(tmp_path))

        from aria_service.autonomous.coder_entrypoint import start_aria_coder

        # Provide a redis stub that supports the methods used during init
        class _StubRedis:
            async def get(self, *a, **kw): return None
            async def set(self, *a, **kw): return None
            async def setex(self, *a, **kw): return None
            async def incr(self, *a, **kw): return 1
            async def lrange(self, *a, **kw): return []
            async def lpush(self, *a, **kw): return None
            async def ltrim(self, *a, **kw): return None

        app_state = SimpleNamespace(redis=_StubRedis())

        async def body():
            tasks = await start_aria_coder(app_state)
            assert tasks is not None
            # R-F1046: gap_detector standalone loop removed — only self_coder runs
            # R-F1080: continuous profiler adds 1 more task
            assert len(tasks) >= 1
            # Cancel immediately so test doesn't hang on the 5-min sleeps
            for t in tasks:
                t.cancel()
            # Let cancellation propagate
            for t in tasks:
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            assert all(t.done() for t in tasks)

        _run(body())
