"""R-F2849 — the loop-lag monitor must report real starvation.

Proven mechanism (2026-07-22 controlled experiment): an I/O-bound search is 4.6x
slower under CPU contention (loop lag 0.3ms idle -> 1029ms loaded). Production had NO
loop-lag telemetry, so this was invisible. This test proves the gauge reports it —
including under a real non-yielding CPU task, the exact condition that starves
adverse-media searches.
"""
import asyncio
import time

import pytest

from aria_service.intel import loop_monitor as lm


@pytest.fixture(autouse=True)
def _clear():
    lm._SAMPLES.clear()
    yield
    lm._SAMPLES.clear()


def test_snapshot_is_unknown_before_any_sample():
    """No data must read UNKNOWN, never healthy — absence is not health."""
    snap = lm.snapshot()
    assert snap["status"] == "unknown"
    assert snap["samples"] == 0
    assert "p95_ms" not in snap  # no fabricated numbers


def test_snapshot_bands_reflect_the_samples():
    for _ in range(50):
        lm.record_lag(2.0)          # a healthy loop
    assert lm.snapshot()["status"] == "healthy"

    lm._SAMPLES.clear()
    for _ in range(50):
        lm.record_lag(300.0)        # busy — competing but turning
    assert lm.snapshot()["status"] == "busy"

    lm._SAMPLES.clear()
    for _ in range(50):
        lm.record_lag(1500.0)       # starved — I/O waiting behind CPU
    s = lm.snapshot()
    assert s["status"] == "starved"
    assert s["p95_ms"] >= 1000


def test_percentiles_are_ordered_and_bounded():
    for v in range(100):
        lm.record_lag(float(v))
    s = lm.snapshot()
    assert s["p50_ms"] <= s["p95_ms"] <= s["max_ms"]
    assert s["samples"] == 100


def test_window_is_bounded_so_memory_is_fixed():
    for _ in range(lm._SAMPLES.maxlen + 500):
        lm.record_lag(1.0)
    assert len(lm._SAMPLES) == lm._SAMPLES.maxlen


@pytest.mark.asyncio
async def test_monitor_reads_near_zero_on_an_idle_loop():
    """CAPABILITY: an idle loop must report a healthy, near-zero lag."""
    task = asyncio.create_task(lm.loop_lag_monitor(interval_s=0.02))
    await asyncio.sleep(0.2)         # ~10 samples
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    s = lm.snapshot()
    assert s["samples"] >= 3
    assert s["p95_ms"] < 30, f"idle loop should be quiet, got p95={s['p95_ms']}ms"


@pytest.mark.asyncio
async def test_monitor_DETECTS_a_non_yielding_cpu_task():
    """CAPABILITY: the whole point — a CPU hog must show up as lag.

    This reproduces the proven mechanism: a non-yielding CPU burst holds the loop, and
    the monitor's own sleep overshoots by roughly the burst length.
    """
    task = asyncio.create_task(lm.loop_lag_monitor(interval_s=0.02))
    await asyncio.sleep(0.05)        # let it settle
    # A non-yielding CPU burst on the SAME loop.
    t0 = time.monotonic()
    x = 0.0
    while time.monotonic() - t0 < 0.5:      # 500ms of pure CPU, no await
        x += (x * 1.000001) + 1.0
    await asyncio.sleep(0.1)         # let the monitor sample after the burst
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    s = lm.snapshot()
    assert s["max_ms"] >= 200, (
        f"a 500ms non-yielding CPU burst must register as loop lag; got "
        f"max={s['max_ms']}ms — the monitor is blind to the exact condition it exists "
        "to catch"
    )


def test_snapshot_never_touches_the_event_loop():
    """It must be callable from a SYNC health route while the loop is wedged."""
    import inspect
    assert not inspect.iscoroutinefunction(lm.snapshot), (
        "snapshot() must be sync so /health can read it via starlette's threadpool "
        "even when the event loop is stalled (the R-F2484 discriminator)"
    )
    lm.record_lag(5.0)
    assert lm.snapshot()["samples"] == 1   # works with no running loop
