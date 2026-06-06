"""R-F1366 — DeepSeek pinned as ARIA-Coder's main LLM (generation + review).

Operator directive (2026-06-06): "make deepseek the main LLM for aria coder
so deepseek can help fix aria."

The problem: /api/aria/coder/llm routes through app.state.llm_provider,
whose chain head becomes the sovereign 14B (aria_llm) whenever ARIA_LLM_URL
is set (R-F93). The 14B is viable for chat but too weak to write complete
fixes — the coder must keep using the funded DeepSeek even while the 14B
serves chat.

Covers:
  - FallbackProvider.complete(prefer_provider=...) per-call reorder:
    preferred provider tried first, normal chain order as fallback,
    cooldowns still respected, unknown preference ignored.
  - MeteredProvider / RateLimitedProvider forward prefer_provider.
  - CAPABILITY: POST /api/aria/coder/llm with aria_llm at the chain head
    is served by DEEPSEEK (the operator-visible outcome). Pre-R-F1366 this
    fails: the chain head (aria_llm) answers.
  - ARIA_CODER_LLM_PROVIDER env overrides the deepseek default.
  - claude_reviewer: deepseek is the first review tier and falls back to
    LLM_API_KEY when DEEPSEEK_API_KEY is unset (the live machine keys
    DeepSeek via LLM_API_KEY; the reviewer used to skip the tier and
    auto-flag every fix).

Project convention: tests use asyncio.run(...) directly (no pytest-asyncio
markers needed — asyncio_mode=auto also covered).
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from aria_service.llm.fallback import FallbackProvider


def _run(coro):
    return asyncio.run(coro)


class _StubProvider:
    """Minimal LLMProvider stand-in. Records calls; returns a result naming
    itself so tests can assert WHICH provider served."""

    def __init__(self, name: str, fail: bool = False):
        self.name = name
        self.is_configured = True
        self.calls = 0
        self._fail = fail

    async def complete(self, system_prompt, user_message, *,
                       max_tokens=4096, timeout=60.0, **kwargs):
        self.calls += 1
        if self._fail:
            raise RuntimeError(f"{self.name} is down")
        return SimpleNamespace(
            text='{"title": "ok"}', model=f"{self.name}-model",
            input_tokens=10, output_tokens=20, routed_via="",
        )


# ════════════════════════════════════════════════════════════════════════════
# FallbackProvider.complete(prefer_provider=...)
# ════════════════════════════════════════════════════════════════════════════

class TestFallbackPreferProvider:
    def test_rf1366_prefer_provider_reorders(self):
        """deepseek is tried FIRST even though aria_llm is the chain head."""
        aria_llm = _StubProvider("aria_llm")
        deepseek = _StubProvider("deepseek")
        chain = FallbackProvider([aria_llm, deepseek])
        result = _run(chain.complete(
            "sys", "user", prefer_provider="deepseek",
        ))
        assert result.model == "deepseek-model"
        assert deepseek.calls == 1
        assert aria_llm.calls == 0

    def test_rf1366_prefer_provider_falls_back_on_failure(self):
        """Preferred provider erroring → normal chain serves (resilience)."""
        aria_llm = _StubProvider("aria_llm")
        deepseek = _StubProvider("deepseek", fail=True)
        chain = FallbackProvider([aria_llm, deepseek])
        result = _run(chain.complete(
            "sys", "user", prefer_provider="deepseek",
        ))
        assert result.model == "aria_llm-model"
        assert deepseek.calls == 1  # tried first
        assert aria_llm.calls == 1  # served as fallback

    def test_rf1366_prefer_provider_respects_cooldown(self):
        """Preferred provider cooling down → skipped, chain head serves."""
        aria_llm = _StubProvider("aria_llm")
        deepseek = _StubProvider("deepseek")
        chain = FallbackProvider([aria_llm, deepseek])
        chain._stats["deepseek"]["cooldown_until"] = time.time() + 300
        result = _run(chain.complete(
            "sys", "user", prefer_provider="deepseek",
        ))
        assert result.model == "aria_llm-model"
        assert deepseek.calls == 0

    def test_rf1366_prefer_provider_unknown_ignored(self):
        """A preference for a provider not in the chain is a no-op."""
        aria_llm = _StubProvider("aria_llm")
        deepseek = _StubProvider("deepseek")
        chain = FallbackProvider([aria_llm, deepseek])
        result = _run(chain.complete(
            "sys", "user", prefer_provider="not-in-chain",
        ))
        assert result.model == "aria_llm-model"

    def test_rf1366_no_prefer_unchanged(self):
        """Regression guard: without prefer_provider the chain head serves
        exactly as before — the chat path is untouched."""
        aria_llm = _StubProvider("aria_llm")
        deepseek = _StubProvider("deepseek")
        chain = FallbackProvider([aria_llm, deepseek])
        result = _run(chain.complete("sys", "user"))
        assert result.model == "aria_llm-model"
        assert deepseek.calls == 0


# ════════════════════════════════════════════════════════════════════════════
# Wrapper forwarding (the live chain is RateLimited(Metered(Fallback)))
# ════════════════════════════════════════════════════════════════════════════

class _KwargCapture:
    """Inner provider capturing the kwargs complete() was called with."""
    name = "capture"
    is_configured = True

    def __init__(self):
        self.seen_kwargs: dict = {}

    async def complete(self, system_prompt, user_message, *,
                       max_tokens=4096, timeout=60.0, **kwargs):
        self.seen_kwargs = dict(kwargs)
        return SimpleNamespace(
            text="ok", model="capture-model",
            input_tokens=1, output_tokens=1, routed_via="",
        )


class TestWrapperForwarding:
    def test_rf1366_metered_forwards_prefer_provider(self):
        from aria_service.llm.metered import MeteredProvider
        inner = _KwargCapture()
        wrapped = MeteredProvider(inner)
        _run(wrapped.complete("sys", "user", prefer_provider="deepseek"))
        assert inner.seen_kwargs.get("prefer_provider") == "deepseek"

    def test_rf1366_metered_omits_empty_prefer(self):
        """Empty preference must NOT be forwarded — single-provider setups
        (no fallback chain) don't accept the kwarg."""
        from aria_service.llm.metered import MeteredProvider
        inner = _KwargCapture()
        wrapped = MeteredProvider(inner)
        _run(wrapped.complete("sys", "user"))
        assert "prefer_provider" not in inner.seen_kwargs

    def test_rf1366_rate_limiter_forwards_prefer_provider(self):
        from aria_service.llm.rate_limiter import RateLimitedProvider
        inner = _KwargCapture()
        wrapped = RateLimitedProvider(inner)
        _run(wrapped.complete("sys", "user", prefer_provider="deepseek"))
        assert inner.seen_kwargs.get("prefer_provider") == "deepseek"

    def test_rf1366_rate_limiter_omits_empty_prefer(self):
        from aria_service.llm.rate_limiter import RateLimitedProvider
        inner = _KwargCapture()
        wrapped = RateLimitedProvider(inner)
        _run(wrapped.complete("sys", "user"))
        assert "prefer_provider" not in inner.seen_kwargs


# ════════════════════════════════════════════════════════════════════════════
# CAPABILITY — the coder endpoint is served by DeepSeek with aria_llm at head
# ════════════════════════════════════════════════════════════════════════════

def _make_app_with_llm(llm_provider):
    """Isolated FastAPI app wired to the aria router (R-F803 pattern)."""
    from fastapi import FastAPI
    from aria_service.routes import aria as aria_routes

    app = FastAPI()
    app.dependency_overrides[aria_routes._router_auth_dep] = lambda: None
    app.include_router(aria_routes.router)
    app.state.llm_provider = llm_provider
    return app


def _wrapped_chain(providers):
    """Build the chain exactly as main.py does: RateLimited(Metered(Fallback))."""
    from aria_service.llm.metered import MeteredProvider
    from aria_service.llm.rate_limiter import RateLimitedProvider
    return RateLimitedProvider(MeteredProvider(FallbackProvider(providers)))


class TestCoderEndpointDeepSeekPrimary:
    def test_rf1366_capability_coder_llm_served_by_deepseek(self, monkeypatch):
        """THE capability test: the sovereign 14B sits at the chain head
        (ARIA_LLM_URL up for chat) but a coder LLM call is served by
        DeepSeek. Pre-R-F1366 this fails — aria_llm answers."""
        from fastapi.testclient import TestClient
        monkeypatch.delenv("ARIA_CODER_LLM_PROVIDER", raising=False)
        aria_llm = _StubProvider("aria_llm")
        deepseek = _StubProvider("deepseek")
        app = _make_app_with_llm(_wrapped_chain([aria_llm, deepseek]))
        client = TestClient(app)
        r = client.post(
            "/api/aria/coder/llm",
            json={
                "prompt": "Plan a fix for the gap in module X",
                "task": "plan",
                "prefer_model": "deepseek",
                "response_format": "json",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["_aria_meta"]["model"] == "deepseek-model", (
            "coder call was served by the chain head, not DeepSeek"
        )
        assert deepseek.calls == 1
        assert aria_llm.calls == 0

    def test_rf1366_env_override_repoints_coder(self, monkeypatch):
        """ARIA_CODER_LLM_PROVIDER lets the operator re-point the coder
        (e.g. back to the sovereign once it's strong enough)."""
        from fastapi.testclient import TestClient
        monkeypatch.setenv("ARIA_CODER_LLM_PROVIDER", "aria_llm")
        aria_llm = _StubProvider("aria_llm")
        deepseek = _StubProvider("deepseek")
        # deepseek deliberately FIRST: the override must still pick aria_llm
        app = _make_app_with_llm(_wrapped_chain([deepseek, aria_llm]))
        client = TestClient(app)
        r = client.post(
            "/api/aria/coder/llm",
            json={"prompt": "Plan a fix for the gap in module X",
                  "task": "plan", "response_format": "json"},
        )
        assert r.status_code == 200
        assert r.json()["_aria_meta"]["model"] == "aria_llm-model"
        assert deepseek.calls == 0

    def test_rf1366_single_provider_still_works(self, monkeypatch):
        """A bare (non-chain) provider has no .providers — the endpoint must
        not crash and must not forward the kwarg (R-F803 regression)."""
        from fastapi.testclient import TestClient
        monkeypatch.delenv("ARIA_CODER_LLM_PROVIDER", raising=False)

        class _Bare:
            name = "deepseek"
            is_configured = True

            async def complete(self, system_prompt, user_message, *,
                               max_tokens=4096, timeout=60.0):
                # NOTE: no **kwargs — forwarding prefer_provider here raises
                return SimpleNamespace(
                    text="hello", model="bare-model",
                    input_tokens=1, output_tokens=1, routed_via="",
                )

        app = _make_app_with_llm(_Bare())
        client = TestClient(app)
        r = client.post(
            "/api/aria/coder/llm",
            json={"prompt": "Plan a fix for the gap in module X",
                  "task": "plan", "response_format": "text"},
        )
        assert r.status_code == 200
        assert r.json()["model"] == "bare-model"


# ════════════════════════════════════════════════════════════════════════════
# claude_reviewer — deepseek first tier + LLM_API_KEY fallback
# ════════════════════════════════════════════════════════════════════════════

class TestReviewerDeepSeekPrimary:
    def _clean_env(self, monkeypatch):
        for var in ("OLLAMA_URL", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
                    "GROQ_API_KEY", "GEMINI_API_KEY", "LLM_API_KEY",
                    "LLM_PROVIDER", "ARIA_CODER_CLAUDE_REVIEW_ENABLED"):
            monkeypatch.delenv(var, raising=False)

    def test_rf1366_reviewer_deepseek_is_first_tier(self, monkeypatch):
        """With its key set, deepseek is the FIRST reviewer tried — the
        operator-designated main LLM for the coder loop."""
        from aria_service.autonomous.claude_reviewer import ClaudeReviewer
        self._clean_env(monkeypatch)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
        providers = ClaudeReviewer()._build_provider_chain()
        names = [getattr(p, "name", "") for p in providers]
        assert names and names[0] == "deepseek", names

    def test_rf1366_reviewer_deepseek_keys_via_llm_api_key(self, monkeypatch):
        """Live machines key DeepSeek via LLM_API_KEY (LLM_PROVIDER=deepseek)
        — the reviewer must reach it even without DEEPSEEK_API_KEY. This was
        the 'reviewer auto-flags everything' gap."""
        from aria_service.autonomous.claude_reviewer import ClaudeReviewer
        self._clean_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("LLM_API_KEY", "sk-test-via-llm-key")
        providers = ClaudeReviewer()._build_provider_chain()
        names = [getattr(p, "name", "") for p in providers]
        assert "deepseek" in names, names

    def test_rf1366_reviewer_no_keys_no_deepseek(self, monkeypatch):
        """No DeepSeek key anywhere → tier correctly absent (self-check
        floor still guards — R-F923)."""
        from aria_service.autonomous.claude_reviewer import ClaudeReviewer
        self._clean_env(monkeypatch)
        providers = ClaudeReviewer()._build_provider_chain()
        names = [getattr(p, "name", "") for p in providers]
        assert "deepseek" not in names, names
