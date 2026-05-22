"""R-F799 (2026-05-22): asyncio.Semaphore around brain_hook.absorb's
expensive tiers — fail-fast load shedding under contention.

R-F795 bounded each absorb's per-tier wall-time. R-F799 bounds the
number of concurrent expensive-tier sections — only N can race for
the encode lock at once. Beyond N, callers fail-fast (default 0.5s
acquire timeout) and skip the tiers. The signal counter still
records the absorb, so observability of the load-shed rate is
preserved.

Uses asyncio.run() per repo convention.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

from aria_service.intel import brain_hook


def _reset_breaker():
    brain_hook._breaker_state["open"] = False
    brain_hook._breaker_state["tripped_at"] = 0.0
    brain_hook._breaker_state["consecutive_high"] = 0
    brain_hook._breaker_state["ticket_filed_this_episode"] = False
    brain_hook._recent_latencies_ms.clear()


def _patch_tiers(monkeypatch, mastery=None, knowledge=None, neural=None):
    from aria_service.intel import student, knowledge as kn, neural_memory

    async def _ok(*a, **kw):
        return None

    async def _ok_dict(*a, **kw):
        return {}

    async def _record_signal(module, success=True, sector=""):
        return None

    monkeypatch.setattr(student, "update_mastery", mastery or _ok)
    monkeypatch.setattr(kn, "store_fact", knowledge or _ok_dict)
    monkeypatch.setattr(neural_memory, "learn_from_text", neural or _ok_dict)
    monkeypatch.setattr(brain_hook, "_record_signal", _record_signal)
    monkeypatch.setattr(brain_hook, "BRAIN_HOOK_ENABLED", True)


def _reset_semaphore():
    """Force re-creation of the semaphore so each test gets a fresh one
    bound to its own event loop with current _ABSORB_CONCURRENCY."""
    brain_hook._absorb_concurrency_sem = None


_LONG = "Long enough to pass the 50-char neural-tier gate easily."


def test_rf799_disabled_when_concurrency_zero(monkeypatch):
    """ARIA_BRAIN_ABSORB_CONCURRENCY=0 disables the semaphore — tiers
    run as before (R-F795 timeout still applies)."""
    _reset_breaker()
    _reset_semaphore()
    monkeypatch.setattr(brain_hook, "_ABSORB_CONCURRENCY", 0)
    _patch_tiers(monkeypatch)

    result = asyncio.run(brain_hook.absorb(
        module="dd_orchestrator", summary=_LONG, detail=_LONG,
    ))
    assert result["mastery_ok"] is True
    assert result["knowledge_ok"] is True
    assert result["neural_ok"] is True


def test_rf799_low_concurrency_no_contention_succeeds(monkeypatch):
    """With N=2 and only 1 absorb in flight: no contention, all
    tiers run normally."""
    _reset_breaker()
    _reset_semaphore()
    monkeypatch.setattr(brain_hook, "_ABSORB_CONCURRENCY", 2)
    _patch_tiers(monkeypatch)

    result = asyncio.run(brain_hook.absorb(
        module="dd_orchestrator", summary=_LONG, detail=_LONG,
    ))
    assert result["mastery_ok"] is True
    assert result["knowledge_ok"] is True


def test_rf799_contention_triggers_fail_fast_skip(monkeypatch):
    """When N absorbs hold the semaphore and a new absorb arrives
    while they're still running, the new one fails-fast (skips
    tiers) rather than queuing indefinitely.

    Capability test: simulates the live encode-wedge scenario where
    a flood of concurrent absorbs would otherwise pile up at the
    encode lock for 20+ minutes.
    """
    _reset_breaker()
    _reset_semaphore()
    monkeypatch.setattr(brain_hook, "_ABSORB_CONCURRENCY", 1)
    monkeypatch.setattr(brain_hook, "_ABSORB_SEM_ACQUIRE_TIMEOUT_S", 0.05)

    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_mastery(*a, **kw):
        started.set()
        await release.wait()  # holds the semaphore until we say go

    _patch_tiers(monkeypatch, mastery=_slow_mastery)

    async def _exercise():
        # Holder absorbs first — will acquire the semaphore and block.
        holder = asyncio.create_task(brain_hook.absorb(
            module="holder", summary=_LONG, detail=_LONG,
        ))
        # Wait until holder is actually inside the tier section.
        await started.wait()

        # Second absorb arrives while holder is in flight — should
        # fail to acquire within 0.05s and skip tiers.
        t0 = time.monotonic()
        contender = await brain_hook.absorb(
            module="contender", summary=_LONG, detail=_LONG,
        )
        elapsed = time.monotonic() - t0
        # Release holder so the test can finish cleanly.
        release.set()
        await holder
        return contender, elapsed

    contender, elapsed = asyncio.run(_exercise())

    # Contender should have failed fast (well under 1s).
    assert elapsed < 1.0, (
        f"R-F799 regression: contender took {elapsed:.2f}s — should have "
        f"failed fast at ~0.05s acquire timeout. Pre-R-F799 the contender "
        f"would queue behind the holder for the full encode wedge."
    )
    # Tiers skipped → all False.
    assert contender["mastery_ok"] is False
    assert contender["knowledge_ok"] is False
    assert contender["neural_ok"] is False
    # Concurrency-cap error recorded.
    assert any("concurrency cap" in e for e in contender["errors"]), (
        f"errors: {contender['errors']}"
    )


def test_rf799_holder_completes_normally(monkeypatch):
    """The absorb that DID acquire the semaphore completes all tiers
    normally — R-F799 only sheds contenders, not the in-flight holder."""
    _reset_breaker()
    _reset_semaphore()
    monkeypatch.setattr(brain_hook, "_ABSORB_CONCURRENCY", 1)
    _patch_tiers(monkeypatch)

    result = asyncio.run(brain_hook.absorb(
        module="dd_orchestrator", summary=_LONG, detail=_LONG,
    ))
    assert result["mastery_ok"] is True
    assert result["knowledge_ok"] is True
    assert result["neural_ok"] is True
    assert result["errors"] == []


def test_rf799_semaphore_released_after_completion(monkeypatch):
    """After an absorb completes, the next absorb should acquire
    immediately. Regression guard for the release in finally:."""
    _reset_breaker()
    _reset_semaphore()
    monkeypatch.setattr(brain_hook, "_ABSORB_CONCURRENCY", 1)
    monkeypatch.setattr(brain_hook, "_ABSORB_SEM_ACQUIRE_TIMEOUT_S", 0.1)
    _patch_tiers(monkeypatch)

    # Run sequentially — second should not see contention.
    r1 = asyncio.run(brain_hook.absorb(
        module="m1", summary=_LONG, detail=_LONG,
    ))
    r2 = asyncio.run(brain_hook.absorb(
        module="m2", summary=_LONG, detail=_LONG,
    ))
    assert r1["mastery_ok"] is True
    assert r2["mastery_ok"] is True
    assert r1["errors"] == []
    assert r2["errors"] == []


def test_rf799_released_on_tier_exception(monkeypatch):
    """If a tier raises an exception, the semaphore is still released
    via the finally clause. Regression guard."""
    _reset_breaker()
    _reset_semaphore()
    monkeypatch.setattr(brain_hook, "_ABSORB_CONCURRENCY", 1)
    monkeypatch.setattr(brain_hook, "_ABSORB_SEM_ACQUIRE_TIMEOUT_S", 0.5)

    async def _raise(*a, **kw):
        raise RuntimeError("simulated downstream failure")

    _patch_tiers(monkeypatch, mastery=_raise)

    # First call hits the exception, semaphore must be released.
    r1 = asyncio.run(brain_hook.absorb(
        module="m1", summary=_LONG, detail=_LONG,
    ))
    # Second call should still complete (semaphore was released).
    _patch_tiers(monkeypatch)  # reset to OK stubs
    r2 = asyncio.run(brain_hook.absorb(
        module="m2", summary=_LONG, detail=_LONG,
    ))
    assert r2["mastery_ok"] is True
    assert r2["errors"] == []
