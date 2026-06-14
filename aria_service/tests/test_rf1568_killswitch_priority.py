"""R-F1568 enablement — the kill switch must gate autonomous LLM spend even when
the caller does NOT pass autonomous=True, by auto-detecting BACKGROUND priority.

The base R-F1568 fix added the gate but only fired on an explicit autonomous=True
kwarg that no autonomous caller passes — leaving it inert. This asserts the
auto-detect via the rate_limiter priority contextvar: BACKGROUND (autonomous
loops) is gated when paused; INTERACTIVE (user chat) is never gated.
"""
import asyncio

import pytest

from aria_service.llm import metered as _m
from aria_service.llm.metered import MeteredProvider, EnginePausedError
from aria_service.llm.rate_limiter import set_priority, reset_priority, Priority


class _StubInner:
    def __init__(self):
        self.complete_called = False

    @property
    def name(self):
        return "stub"

    async def complete(self, system_prompt, user_message, **kw):
        self.complete_called = True
        from aria_service.llm.provider import LLMResult
        return LLMResult(text="ok", model="m", input_tokens=1, output_tokens=1)


def _mp(monkeypatch, paused: bool):
    async def _fake_is_paused():
        return paused
    monkeypatch.setattr(_m.MeteredProvider, "_paused_cache", None, raising=False)

    async def _fake_cached(self):
        return paused
    monkeypatch.setattr(_m.MeteredProvider, "_is_paused_cached", _fake_cached)
    # neutralise the monthly cap so only the kill switch matters
    async def _noop_cap(self):
        return None
    monkeypatch.setattr(_m.MeteredProvider, "_enforce_monthly_cap", _noop_cap)


def test_background_priority_gated_when_paused(monkeypatch):
    _mp(monkeypatch, paused=True)
    inner = _StubInner()
    prov = MeteredProvider(inner)
    tok = set_priority(Priority.BACKGROUND)  # autonomous origin — no autonomous=True passed
    try:
        with pytest.raises(EnginePausedError):
            asyncio.run(
                prov.complete("sys", "msg"))
    finally:
        reset_priority(tok)
    assert inner.complete_called is False, "provider must NOT be invoked when gated"


def test_interactive_priority_never_gated(monkeypatch):
    _mp(monkeypatch, paused=True)
    inner = _StubInner()
    prov = MeteredProvider(inner)
    tok = set_priority(Priority.INTERACTIVE)  # user chat
    try:
        r = asyncio.run(prov.complete("sys", "msg"))
    finally:
        reset_priority(tok)
    assert r.text == "ok"
    assert inner.complete_called is True


def test_background_not_paused_proceeds(monkeypatch):
    _mp(monkeypatch, paused=False)
    inner = _StubInner()
    prov = MeteredProvider(inner)
    tok = set_priority(Priority.BACKGROUND)
    try:
        r = asyncio.run(prov.complete("sys", "msg"))
    finally:
        reset_priority(tok)
    assert r.text == "ok" and inner.complete_called is True


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
