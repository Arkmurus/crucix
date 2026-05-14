"""R-F521 — make ARIA_BRAIN_ABSORB_PAUSE_MS apply GLOBALLY, not per-coroutine.

Live failure 2026-05-14 12:21:17 BST cold boot: ~17 knowledge_xxx
modules fired brain_hook.absorb() concurrently during startup seeding.
ARIA_BRAIN_ABSORB_PAUSE_MS was set to 500 (per R-F460), but the pause
was implemented as a per-call asyncio.sleep — N parallel coroutines
all slept 500ms in the SAME wall-clock window, then all hit the
embedding + chromadb pipeline at once. absorb p95 climbed to 16635ms
(>3500ms trip threshold), circuit breaker tripped, pending_action
recorded as HIGH/operator_action. Repeated on every fly machine
restart.

R-F521 fix: global lock + last-absorb timestamp. Wall-clock rate is
now enforced across the whole process: regardless of how many
absorbs are queued, at most one starts every pause_ms.

Tests pin the serialization behavior using monkey-patched
time.monotonic + asyncio.sleep so they run in deterministic time
without real waiting.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import brain_hook


@pytest.fixture(autouse=True)
def _reset_absorb_state():
    """Reset the module-level lock + timestamp so test order doesn't
    leak state. Re-imported lazily on each test's first call."""
    brain_hook._absorb_global_lock = None
    brain_hook._last_absorb_monotonic = 0.0
    yield
    brain_hook._absorb_global_lock = None
    brain_hook._last_absorb_monotonic = 0.0


# ───────────── R-F521 — pause is zero by default ─────────────


def test_rf521_zero_pause_is_no_op(monkeypatch):
    """ARIA_BRAIN_ABSORB_PAUSE_MS unset (or 0) skips both the lock
    and the sleep — fast path preserved."""
    monkeypatch.delenv("ARIA_BRAIN_ABSORB_PAUSE_MS", raising=False)

    sleep_calls = []

    async def _fake_sleep(s):
        sleep_calls.append(s)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    asyncio.run(brain_hook._wait_for_absorb_slot())
    assert sleep_calls == [], "Expected NO sleep call when pause_ms=0"


# ───────────── R-F521 — first call doesn't wait, subsequent ones do ─────────


def test_rf521_first_call_does_not_sleep(monkeypatch):
    """The first absorb call after process start sees
    _last_absorb_monotonic=0.0, elapsed=now*1000 (huge), so the
    "have we waited enough?" check passes immediately."""
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "500")

    sleep_calls = []

    async def _fake_sleep(s):
        sleep_calls.append(s)

    # Patch time.monotonic to a known non-zero value
    monkeypatch.setattr(brain_hook.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    asyncio.run(brain_hook._wait_for_absorb_slot())
    assert sleep_calls == [], "First call should not need to wait"
    # State updated for next call
    assert brain_hook._last_absorb_monotonic == 1000.0


def test_rf521_immediate_second_call_waits_full_pause(monkeypatch):
    """If second call arrives 0ms after first, it must wait the full
    pause_ms before proceeding."""
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "500")

    sleep_calls = []

    async def _fake_sleep(s):
        sleep_calls.append(s)

    # Time stays at 1000.0 — simulates instant back-to-back calls
    monkeypatch.setattr(brain_hook.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    # First call: no sleep, sets _last_absorb_monotonic=1000.0
    asyncio.run(brain_hook._wait_for_absorb_slot())
    assert sleep_calls == []

    # Second call: time hasn't advanced, so elapsed=0ms, should sleep 500ms
    asyncio.run(brain_hook._wait_for_absorb_slot())
    assert sleep_calls == [0.5], (
        f"Expected one 500ms sleep on second call, got {sleep_calls}"
    )


def test_rf521_partial_elapsed_waits_remainder(monkeypatch):
    """If 300ms has elapsed of a 500ms pause, second call should
    sleep only the remaining 200ms (not the full 500ms)."""
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "500")

    sleep_calls = []
    # Step the simulated clock forward 300ms between the two _wait calls.
    # Use a single mutable cursor so asyncio internals reading time.monotonic
    # (e.g. during loop close) don't exhaust an iterator.
    current_time = [1000.0]

    async def _fake_sleep(s):
        sleep_calls.append(s)
        current_time[0] += s

    monkeypatch.setattr(brain_hook.time, "monotonic", lambda: current_time[0])
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    asyncio.run(brain_hook._wait_for_absorb_slot())  # first, no sleep
    current_time[0] += 0.3  # 300ms elapsed
    asyncio.run(brain_hook._wait_for_absorb_slot())  # second, should sleep ~200ms
    assert len(sleep_calls) == 1, f"Expected exactly 1 sleep, got {sleep_calls}"
    assert abs(sleep_calls[0] - 0.2) < 0.001, (
        f"Expected ~200ms remainder sleep, got {sleep_calls[0]}"
    )


# ───────────── R-F521 — concurrent calls serialize (the live failure mode) ──


def test_rf521_three_concurrent_calls_serialize(monkeypatch):
    """The 2026-05-14 12:21:17 BST repro: N concurrent absorb tasks
    must serialize, not all sleep in the same wall-clock window.

    With pause_ms=500 and 3 concurrent calls, expect:
      - Call 1: 0 sleep (first slot)
      - Call 2: ~500ms sleep
      - Call 3: ~500ms sleep
    All three different — proves serialization.

    Pre-R-F521: all three would sleep 500ms concurrently, finishing
    at the same time, and the test would fail with [0.5, 0.5, 0.5]
    plus interleaved time updates."""
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "500")

    sleep_calls = []
    # Simulated monotonic clock that advances by the requested sleep
    # amount on each sleep call, so the elapsed check sees real time
    # passing.
    current_time = [1000.0]

    async def _fake_sleep(s):
        sleep_calls.append(s)
        current_time[0] += s

    monkeypatch.setattr(brain_hook.time, "monotonic", lambda: current_time[0])
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    async def _drive():
        # Fire 3 concurrent — gather, await all together
        await asyncio.gather(
            brain_hook._wait_for_absorb_slot(),
            brain_hook._wait_for_absorb_slot(),
            brain_hook._wait_for_absorb_slot(),
        )

    asyncio.run(_drive())

    # Exactly 2 sleeps (the first call doesn't wait; calls 2 and 3 each wait)
    assert len(sleep_calls) == 2, (
        f"Pre-R-F521 bug repro: expected exactly 2 sleeps (calls 2 and 3 "
        f"serializing behind call 1), got {sleep_calls}. If this shows "
        f"[0.5, 0.5, 0.5] or 3+ entries, the global lock didn't engage and "
        f"absorbs are still firing in parallel."
    )
    # Each subsequent call waits the full pause
    for s in sleep_calls:
        assert abs(s - 0.5) < 0.001


# ───────────── R-F521 — clamp + env var live-reload still works ─────────


def test_rf521_env_var_live_reload(monkeypatch):
    """_absorb_pause_ms() reads the env on every call — R-F521 preserves
    this so the operator can still flyctl-secret-tune the pause without
    a redeploy. Lift to 1000ms mid-stream and confirm pickup."""
    sleep_calls = []
    current_time = [1000.0]

    async def _fake_sleep(s):
        sleep_calls.append(s)
        current_time[0] += s

    monkeypatch.setattr(brain_hook.time, "monotonic", lambda: current_time[0])
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "500")
    asyncio.run(brain_hook._wait_for_absorb_slot())  # first, no sleep

    # Operator bumps it mid-run
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "1000")
    asyncio.run(brain_hook._wait_for_absorb_slot())  # second: 1000ms sleep

    assert sleep_calls == [1.0], (
        f"Mid-run env-var bump from 500ms to 1000ms didn't take effect: "
        f"sleep_calls={sleep_calls}"
    )
