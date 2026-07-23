"""
R-F1568 — capability test: the emergency pause / kill switch must brake
AUTONOMOUS LLM spend at the provider layer (MeteredProvider), not just
between cycles, and must NEVER block interactive operator/user chat.

These drive the REAL MeteredProvider.complete()/stream() path with a stub
inner provider, monkeypatching the pause reader (autonomous.safety
.is_engine_paused) and asserting the inner provider was NOT invoked when an
autonomous call is issued while paused.
"""
from __future__ import annotations

import pytest

from aria_service.llm import metered as metered_mod
from aria_service.llm.metered import MeteredProvider, EnginePausedError


class _StubProvider:
    """Records whether it was actually invoked. If the kill switch fires
    BEFORE provider invocation (the point of R-F1568), these stay False."""

    name = "stub"
    is_configured = True
    model = "stub-model"

    def __init__(self):
        self.complete_called = False
        self.stream_called = False

    async def complete(self, system_prompt, user_message, *, max_tokens=4096,
                       timeout=60.0, **kw):
        self.complete_called = True

        class _R:
            model = "stub-model"
            input_tokens = 1
            output_tokens = 1
            routed_via = ""
            text = "ok"
        return _R()

    async def stream(self, system_prompt, user_message, *, max_tokens=4096,
                     timeout=120.0, on_done=None, **kw):
        self.stream_called = True
        if on_done:
            on_done(None)
        yield "ok"


@pytest.fixture(autouse=True)
def _no_monthly_cap(monkeypatch):
    """Neutralise the monthly-cap enforcement so we isolate the kill switch.
    Also reset the process-wide paused cache between tests."""
    async def _noop(self):
        return None
    monkeypatch.setattr(MeteredProvider, "_enforce_spend_caps", _noop)
    MeteredProvider._paused_cache = None
    yield
    MeteredProvider._paused_cache = None


def _patch_pause(monkeypatch, paused: bool):
    """Monkeypatch the verified pause reader autonomous.safety.is_engine_paused
    that _is_paused_cached imports lazily."""
    from aria_service.autonomous import safety as safety_mod

    async def _is_paused():
        return paused
    monkeypatch.setattr(safety_mod, "is_engine_paused", _is_paused)


@pytest.mark.asyncio
async def test_paused_autonomous_complete_refused_before_provider(monkeypatch):
    _patch_pause(monkeypatch, True)
    stub = _StubProvider()
    mp = MeteredProvider(stub)
    with pytest.raises(EnginePausedError):
        await mp.complete("sys", "user", autonomous=True)
    assert stub.complete_called is False, "provider was invoked despite kill switch"


@pytest.mark.asyncio
async def test_paused_interactive_complete_proceeds(monkeypatch):
    """Interactive (default autonomous=False) chat is NEVER blocked, even
    while the engine is paused."""
    _patch_pause(monkeypatch, True)
    stub = _StubProvider()
    mp = MeteredProvider(stub)
    res = await mp.complete("sys", "user")  # default interactive
    assert stub.complete_called is True
    assert res.text == "ok"


@pytest.mark.asyncio
async def test_not_paused_autonomous_complete_proceeds(monkeypatch):
    _patch_pause(monkeypatch, False)
    stub = _StubProvider()
    mp = MeteredProvider(stub)
    res = await mp.complete("sys", "user", autonomous=True)
    assert stub.complete_called is True
    assert res.text == "ok"


@pytest.mark.asyncio
async def test_paused_autonomous_stream_refused_before_provider(monkeypatch):
    _patch_pause(monkeypatch, True)
    stub = _StubProvider()
    mp = MeteredProvider(stub)
    with pytest.raises(EnginePausedError):
        async for _ in mp.stream("sys", "user", autonomous=True):
            pass
    assert stub.stream_called is False, "stream provider invoked despite kill switch"


@pytest.mark.asyncio
async def test_paused_interactive_stream_proceeds(monkeypatch):
    _patch_pause(monkeypatch, True)
    stub = _StubProvider()
    mp = MeteredProvider(stub)
    chunks = []
    async for c in mp.stream("sys", "user"):  # default interactive
        chunks.append(c)
    assert stub.stream_called is True
    assert chunks == ["ok"]


@pytest.mark.asyncio
async def test_pause_read_is_cached(monkeypatch):
    """The paused read is cached briefly so it is not a Redis hit per call.
    After the first read the reader should not be called again within TTL."""
    from aria_service.autonomous import safety as safety_mod
    calls = {"n": 0}

    async def _is_paused():
        calls["n"] += 1
        return True
    monkeypatch.setattr(safety_mod, "is_engine_paused", _is_paused)

    stub = _StubProvider()
    mp = MeteredProvider(stub)
    for _ in range(3):
        with pytest.raises(EnginePausedError):
            await mp.complete("sys", "user", autonomous=True)
    assert calls["n"] == 1, f"expected 1 cached read, got {calls['n']}"
