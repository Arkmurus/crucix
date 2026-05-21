"""R-F787 — global snapshot throttle serialises CPU-bound state
snapshot encoders across modules.

Live evidence (knowledge.py line 170 comment): "5 GIL holders,
213.97s loop stall" — when knowledge + intel_ledger + neural_memory
all flush at once via asyncio.to_thread, the asyncio loop thread is
starved by 3+ encoder threads holding the GIL in turn. R-F787 wraps
all snapshot encoders in a global async semaphore so at most
ARIA_SNAPSHOT_THROTTLE_CONCURRENCY (default 1) encoder thread runs
at a time.
"""
from __future__ import annotations

import asyncio
import time


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        from aria_service.intel import _snapshot_throttle as st
        st.reset_for_tests()  # rebind semaphore to this loop
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_concurrent_encoders_serialise():
    """Two coroutines call run_in_thread_throttled simultaneously
    with a function that sleeps 50ms; with concurrency=1 they should
    serialise (~100ms total), not parallelise (~50ms total)."""
    from aria_service.intel import _snapshot_throttle as st

    def _encoder(label):
        time.sleep(0.05)
        return label

    async def _go():
        t0 = time.monotonic()
        results = await asyncio.gather(
            st.run_in_thread_throttled(_encoder, "a"),
            st.run_in_thread_throttled(_encoder, "b"),
        )
        elapsed = time.monotonic() - t0
        return results, elapsed

    results, elapsed = _run(_go())
    assert set(results) == {"a", "b"}
    # Serialised: ~100ms. Parallel: ~50ms. Generous floor of 80ms
    # catches the serialisation behaviour without flaking on
    # scheduling jitter.
    assert elapsed > 0.080, (
        f"R-F787 throttle should serialise concurrent encoders "
        f"(expected >80ms for 2x 50ms tasks, got {elapsed*1000:.1f}ms)"
    )


def test_throttle_releases_on_exception():
    """If an encoder raises, the semaphore must release so the next
    caller doesn't hang. Failure mode pre-fix: encoder raises → sem
    not released → next throttled call blocks forever."""
    from aria_service.intel import _snapshot_throttle as st

    def _bad():
        raise RuntimeError("simulated encoder failure")

    def _good():
        return "ok"

    async def _go():
        try:
            await st.run_in_thread_throttled(_bad)
        except RuntimeError:
            pass
        # If the semaphore leaked, this second call hangs forever.
        # asyncio.wait_for raises TimeoutError on hang, surfacing
        # the regression.
        return await asyncio.wait_for(
            st.run_in_thread_throttled(_good), timeout=1.0,
        )

    assert _run(_go()) == "ok"


def test_concurrency_override(monkeypatch):
    """ARIA_SNAPSHOT_THROTTLE_CONCURRENCY=2 should allow 2 parallel
    encoders. Verify by running 2 sleeps in parallel and asserting
    the result completes in ~one-sleep, not two-sleeps."""
    # Reload module under the env override. Module reads the env at
    # import time so we must reimport.
    import importlib
    import aria_service.intel._snapshot_throttle as st_orig

    monkeypatch.setenv("ARIA_SNAPSHOT_THROTTLE_CONCURRENCY", "2")
    st = importlib.reload(st_orig)

    def _encoder():
        time.sleep(0.05)
        return 1

    async def _go():
        st.reset_for_tests()
        t0 = time.monotonic()
        await asyncio.gather(
            st.run_in_thread_throttled(_encoder),
            st.run_in_thread_throttled(_encoder),
        )
        return time.monotonic() - t0

    elapsed = _run(_go())
    # Parallel: ~50ms. Serial: ~100ms. With concurrency=2 we expect
    # <90ms; allow some slack.
    assert elapsed < 0.090, (
        f"R-F787 throttle with concurrency=2 should parallelise "
        f"(expected <90ms for 2x 50ms tasks, got {elapsed*1000:.1f}ms)"
    )

    # Restore module to default for downstream tests.
    monkeypatch.delenv("ARIA_SNAPSHOT_THROTTLE_CONCURRENCY", raising=False)
    importlib.reload(st_orig)
