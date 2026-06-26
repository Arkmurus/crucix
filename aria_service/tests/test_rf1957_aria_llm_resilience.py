"""R-F1957 — a reached-but-hung or cold-starting aria_llm endpoint must NOT stall
user traffic (root cause of the 2026-06-26 degraded-mode outage: a HTTP-reachable
endpoint whose jobs hang forever passed is_available() and each call waited 60s).

The health-checked wrapper must now:
  1. warm-gate — SKIP (fast-fail) a cold/unproven endpoint instead of calling it;
  2. clamp the per-call deadline so a hung WARM endpoint fast-fails within ~the
     clamp (not 60s), letting the fallback chain move to DeepSeek;
  3. feed user-call failures into the breaker so it trips fast.

These guards are inert while ARIA_LLM_URL is unset (wrap() returns the inner provider
unchanged), so the test activates them by monkeypatching the module config.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from aria_service.llm import resilience
from aria_service.llm.provider import LLMProvider, ProviderError


class _Hanging(LLMProvider):
    """Inner aria_llm stub that HONORS the passed timeout then errors — i.e. it
    hangs for exactly `timeout` seconds, like a stuck/cold endpoint that httpx
    would eventually time out on. Records call count + the deadline it received."""

    name = "aria_llm"

    def __init__(self) -> None:
        self.calls = 0
        self.last_timeout: float | None = None

    @property
    def is_configured(self) -> bool:
        return True

    async def complete(self, system_prompt, user_message, *, max_tokens=4096, timeout=60.0):
        self.calls += 1
        self.last_timeout = timeout
        await asyncio.sleep(timeout)
        raise ProviderError("aria_llm", "simulated hang/timeout", kind="server", retryable=True)

    async def stream(self, *a, **k):  # pragma: no cover - not exercised here
        if False:
            yield ""


def _mk_checker(monkeypatch, *, warm: bool):
    monkeypatch.setattr(resilience, "_ARIA_LLM_URL", "http://test-aria-llm")
    monkeypatch.setattr(resilience, "_ARIA_LLM_CALL_TIMEOUT", 0.5)  # fast clamp for the test
    monkeypatch.setattr(resilience, "_ARIA_LLM_WARM_TTL", 60.0)
    chk = resilience.LLMHealthChecker(endpoint="http://test-aria-llm")
    chk.last_success_at = time.time() if warm else 0.0
    monkeypatch.setattr(resilience, "_health_checker_instance", chk)
    return chk


def test_warm_gate_skips_cold_endpoint(monkeypatch):
    """Cold/unproven endpoint (no successful probe) is skipped — inner never called,
    fast-fail is instant. This is the structural fix: a cold scale-to-zero worker
    can never sink a user request into a 60s timeout."""
    chk = _mk_checker(monkeypatch, warm=False)
    inner = _Hanging()
    wrapped = resilience.LLMHealthChecker.wrap(inner)

    async def _run():
        t0 = time.monotonic()
        with pytest.raises(ProviderError):
            await wrapped.complete("sys", "user", timeout=60.0)
        return time.monotonic() - t0

    dt = asyncio.run(_run())
    assert inner.calls == 0, "warm-gate must skip the cold endpoint, not call it"
    assert dt < 0.3, f"skip must be instant, took {dt:.2f}s"


def test_warm_hang_clamps_not_60s(monkeypatch):
    """A WARM endpoint that then hangs must fast-fail within the clamp (~0.5s here),
    NOT 60s, and must feed the failure into the breaker."""
    chk = _mk_checker(monkeypatch, warm=True)
    inner = _Hanging()
    wrapped = resilience.LLMHealthChecker.wrap(inner)

    async def _run():
        t0 = time.monotonic()
        with pytest.raises(ProviderError):
            await wrapped.complete("sys", "user", timeout=60.0)
        return time.monotonic() - t0

    dt = asyncio.run(_run())
    assert inner.calls == 1, "a warm endpoint should be attempted"
    assert inner.last_timeout == 0.5, f"deadline must be clamped to 0.5s, got {inner.last_timeout}"
    assert dt < 2.0, f"must fast-fail at the clamp, took {dt:.2f}s (≈60s if unclamped)"
    assert chk.consecutive_failures >= 1, "user-call failure must feed the breaker"
