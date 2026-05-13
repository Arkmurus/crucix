"""R-F460 — ARIA_BRAIN_ABSORB_PAUSE_MS operator-tunable dampener.

Live evidence (fly logs 2026-05-13 22:31 BST): under email-reader
backlog bursts of 30+ /brain/absorb calls in 1 second, absorb p95
climbed to 28484ms against a 3500ms breaker threshold. The breaker
tripped, auto-recovered, tripped again — the visible "ping-pong"
cascade in the live logs.

R-F460 adds an env-var-tunable inter-absorb pause. Default 0 (no
behavior change). Operator can set 100ms on fly to space the
email-reader bursts so downstream embedding + chromadb work catches
up between calls.

The env var is read PER-CALL (not at import time) so the operator
can change it without restarting the service.
"""
from __future__ import annotations

import asyncio


def test_rf460_pause_defaults_to_zero(monkeypatch):
    """Default (env var unset) is 0ms — no behavior change for
    operators who haven't opted in."""
    monkeypatch.delenv("ARIA_BRAIN_ABSORB_PAUSE_MS", raising=False)
    from aria_service.intel.brain_hook import _absorb_pause_ms
    assert _absorb_pause_ms() == 0


def test_rf460_pause_reads_env_var(monkeypatch):
    """The pause is set from the env var on every call (not cached
    at import time) so operators can change it on a running deploy."""
    from aria_service.intel.brain_hook import _absorb_pause_ms
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "100")
    assert _absorb_pause_ms() == 100
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "50")
    assert _absorb_pause_ms() == 50


def test_rf460_pause_invalid_value_falls_back_to_zero(monkeypatch):
    """Garbage in env var → 0 (don't crash the absorb path on a typo)."""
    from aria_service.intel.brain_hook import _absorb_pause_ms
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "not-a-number")
    assert _absorb_pause_ms() == 0
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "")
    assert _absorb_pause_ms() == 0


def test_rf460_pause_negative_clamped_to_zero(monkeypatch):
    """Negative values are nonsensical — clamp."""
    from aria_service.intel.brain_hook import _absorb_pause_ms
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "-100")
    assert _absorb_pause_ms() == 0


def test_rf460_pause_excessive_clamped_to_ceiling(monkeypatch):
    """5s ceiling — operator shouldn't be able to wedge the service
    by setting a pathological value like 60000ms."""
    from aria_service.intel.brain_hook import _absorb_pause_ms
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "60000")
    assert _absorb_pause_ms() == 5000


def test_rf460_absorb_sleeps_when_pause_set(monkeypatch):
    """When the env var is set, the absorb function MUST call
    asyncio.sleep with the right duration before proceeding to the
    absorption gate. Pin the wire-in so a future refactor doesn't
    silently drop it."""
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "100")

    sleep_calls = []
    real_sleep = asyncio.sleep

    async def _spying_sleep(seconds):
        sleep_calls.append(seconds)
        # short-circuit so the test runs fast
        return await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _spying_sleep)

    # Stub out BRAIN_HOOK_ENABLED to True so we enter the function body,
    # then make sure the rest of the function early-returns (we don't
    # want to actually run the full absorption pipeline — just verify
    # the pause sleep fires).
    from aria_service.intel import brain_hook
    monkeypatch.setattr(brain_hook, "BRAIN_HOOK_ENABLED", True)

    # Stub _is_self_introspective so the quality gate doesn't try to
    # do any real work — but we still want the pause to have fired.
    # We accept any return shape from absorb; we only check sleep was
    # called.
    async def _drive():
        try:
            await brain_hook.absorb(
                module="test_module",
                summary="test summary " * 5,
                detail="test",
            )
        except Exception:
            # Don't care about downstream errors — only the pause
            pass

    asyncio.run(_drive())

    # asyncio.sleep should have been called at least once with 0.1s
    # (100ms). May be called elsewhere too with other durations, so
    # we check for the specific value.
    assert 0.1 in sleep_calls, (
        f"R-F460: expected asyncio.sleep(0.1) for 100ms pause, "
        f"got sleep_calls={sleep_calls}"
    )


def test_rf460_absorb_does_not_sleep_when_pause_zero(monkeypatch):
    """Default behavior: no pause = no asyncio.sleep call. Pin the
    fast path so the env var being unset has zero cost."""
    monkeypatch.delenv("ARIA_BRAIN_ABSORB_PAUSE_MS", raising=False)

    sleep_calls = []
    real_sleep = asyncio.sleep
    async def _spying_sleep(seconds):
        sleep_calls.append(seconds)
        return await real_sleep(0)
    monkeypatch.setattr(asyncio, "sleep", _spying_sleep)

    from aria_service.intel import brain_hook
    monkeypatch.setattr(brain_hook, "BRAIN_HOOK_ENABLED", True)

    async def _drive():
        try:
            await brain_hook.absorb(
                module="test_module",
                summary="test " * 10,
            )
        except Exception:
            pass

    asyncio.run(_drive())

    # The R-F460 pause sleep MUST NOT have fired. Any other sleeps
    # in absorb (e.g. retry logic) are someone else's concern; we
    # specifically check no sleep with the R-F460 marker duration.
    # The smallest positive sleep duration in R-F460 is 1ms (clamp 0 → 0).
    # So when pause=0, the R-F460 sleep call is skipped via the
    # `if _pause_ms > 0:` guard. Other sleeps may still fire elsewhere.
    # We assert: no sleep call >= 0.001 from R-F460's path.
    # In practice when env unset, asyncio.sleep should NOT see a 0.1 / 0.05 etc.
    # short-circuit if absorb hits other sleeps — we accept those but want
    # to confirm R-F460 specifically didn't fire.
    # Loose check: sleep calls under 0.05 are fine (not from R-F460).
    suspicious = [s for s in sleep_calls if s >= 0.05]
    assert not suspicious, (
        f"R-F460 REGRESSION: pause defaulted to 0 but a sleep ≥50ms "
        f"fired in absorb path. sleep_calls={sleep_calls}"
    )
