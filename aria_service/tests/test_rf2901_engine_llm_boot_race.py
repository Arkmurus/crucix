"""R-F2901 — the autonomous engine must not lose a boot race with the LLM init.

The bug, observed live on the 2026-07-23 Claude-flip restart:
    12:10:48  [autonomous engine] not started — LLM provider is not configured
    12:10:49  LLM provider: fallback          <-- one second too late
    12:11:54  Capability gap: Engine not started: LLM not configured
`_bootstrap_autonomous_engine_bg` and `_init_llm_and_dialogue_bg` are both
created by `_bg_task` with no ordering between them, `start_engine()` hard-
refuses an unconfigured provider, and NOTHING retries — so the metabolism stayed
dark until the next restart (the R-F2004 failure class, 187h of darkness).

These tests drive `await_llm_provider` — the real function the bootstrap calls —
rather than re-implementing its loop, so a regression in main.py fails here.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.main import await_llm_provider

# R-F3786/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


class _State:
    def __init__(self, provider=None):
        self.llm_provider = provider


class _App:
    def __init__(self, provider=None):
        self.state = _State(provider)


class _Provider:
    def __init__(self, configured: bool = True):
        self.is_configured = configured


def _run(coro):
    return asyncio.run(coro)


def test_returns_immediately_when_provider_already_configured():
    """The common case must add ZERO delay to boot."""
    app = _App(_Provider(True))
    assert _run(await_llm_provider(app, timeout_s=5.0, poll_s=0.01)) == 0.0


def test_waits_for_a_provider_that_arrives_late():
    """THE bug: the provider lands a moment after the engine checks. Before
    R-F2901 the engine gave up here and never retried."""
    app = _App(None)

    async def _drive():
        async def _late_init():
            await asyncio.sleep(0.05)
            app.state.llm_provider = _Provider(True)

        task = asyncio.create_task(_late_init())
        waited = await await_llm_provider(app, timeout_s=5.0, poll_s=0.01)
        await task
        return waited

    waited = _run(_drive())
    assert waited > 0.0, "should have waited for the late provider"
    assert app.state.llm_provider is not None
    assert app.state.llm_provider.is_configured


def test_unconfigured_provider_is_not_accepted():
    """A present-but-unconfigured provider is exactly what start_engine refuses;
    waiting must not treat its mere presence as readiness."""
    app = _App(_Provider(False))
    waited = _run(await_llm_provider(app, timeout_s=0.05, poll_s=0.01))
    assert waited >= 0.05, "returned early on an unconfigured provider"


def test_times_out_instead_of_hanging_forever():
    """On timeout it must RETURN so the caller's capability-gap path still runs —
    a boot task that blocks forever is its own outage."""
    app = _App(None)
    waited = _run(await_llm_provider(app, timeout_s=0.05, poll_s=0.01))
    assert 0.05 <= waited < 1.0


def test_tolerates_an_app_without_state():
    """Never raise inside a boot task — that would kill the bootstrap silently."""
    class _Bare:
        pass

    waited = _run(await_llm_provider(_Bare(), timeout_s=0.03, poll_s=0.01))
    assert waited >= 0.03


def test_bootstrap_actually_calls_the_helper():
    """Guard against the fix being reverted to an inline check: the engine
    bootstrap must go through await_llm_provider before start_engine."""
    import inspect

    import aria_service.main as m

    src = module_source(m)
    start_idx = src.find("_bootstrap_autonomous_engine_bg")
    assert start_idx != -1, "bootstrap function not found — test drove nothing"
    window = src[start_idx:start_idx + 4000]
    assert "await_llm_provider" in window, (
        "the autonomous bootstrap no longer waits for the LLM provider — "
        "the boot race that left autonomy dark is back"
    )
