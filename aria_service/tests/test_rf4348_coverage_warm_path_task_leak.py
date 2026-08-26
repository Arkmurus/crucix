"""R-F4348 (C-292) — the warm path of get_coverage_nonblocking leaked one
permanently-running get_coverage() task per call.

MEASURED IN PRODUCTION (aria-intel, 2026-08-26). Five blackout wedge dumps
taken 300s apart:

    dump          total pending tasks    pending get_coverage
    1787706279            276                    61
    1787706579            298                    71
    1787706879            329                    81
    1787707179            359                    92
    1787707479            380                   101

Exactly +10 leaked get_coverage tasks per 300s, monotonic, ZERO completions.
202 of the 380 pending tasks were ecosystem_map (101 get_coverage + their 101
`_safe` children). `/health` reported `state_backend_read_timeouts` (500 hits
/ 169 distinct keys in a 900s window) and the memory-leak detector reported
+75.94 MB/interval at 7736 MB RSS — both downstream of this one leak: every
abandoned build pins the parsed module graph in its frame and holds five
concurrent state_store reads open.

THE DEFECT. The COLD path shares one `_COVERAGE_TASK` singleton, so N callers
await ONE build. The WARM path did:

    return await asyncio.wait_for(asyncio.shield(get_coverage()), max_wait_s)

`get_coverage()` there constructs a BRAND NEW coroutine on every call — there
is no dedup — and `asyncio.shield` deliberately prevents the timeout from
cancelling it. So each caller starts its own full build and, on timeout,
abandons it still running. `GET /api/aria/health` is polled continuously, so
the leak rate is the poll rate.

The shield is correct on the cold path, where it protects the ONE shared build
the docstring describes ("never throw away a 6-second parse"). It was carried
onto the warm path, which has no shared task for it to protect, and there it
converts every timeout into an orphan.

These tests fail on the pre-fix tree (10 leaked tasks) and pass after.
"""
import asyncio

import pytest

from aria_service.intel import ecosystem_map as em


def _pending_coverage_tasks() -> list[asyncio.Task]:
    """Pending tasks whose coroutine is get_coverage — the leak, counted the
    same way the production wedge dump counts it (by the coroutine on the
    frame, not by our own bookkeeping, so the test cannot be satisfied by
    better bookkeeping alone)."""
    out = []
    for t in asyncio.all_tasks():
        if t.done():
            continue
        coro = t.get_coro()
        name = getattr(coro, "__qualname__", "") or getattr(coro, "__name__", "")
        if name.endswith("get_coverage"):
            out.append(t)
    return out


@pytest.fixture
def warm_slow_coverage(monkeypatch):
    """Warm cache + a build slower than the caller's budget — exactly the
    production condition.

    `_CACHE['data']` is populated, so callers take the WARM branch. The build
    is slow because the state_store reads `_gather_signals` makes were
    themselves timing out at 5s each.

    `get_coverage` is stubbed rather than driven for real: the defect is
    entirely in `get_coverage_nonblocking`'s task management, and stubbing
    keeps the test off a ~6s full-tree AST parse. The stub counts its own
    starts, which is the number of concurrent builds the wrapper triggers.
    """
    monkeypatch.setitem(em._CACHE, "data", {"nodes": [], "meta": {}})

    started = {"n": 0}

    async def get_coverage():
        started["n"] += 1
        await asyncio.sleep(30)
        return {"health_sensors": {}, "meta": {}}

    get_coverage.__qualname__ = "get_coverage"
    monkeypatch.setattr(em, "get_coverage", get_coverage)
    monkeypatch.setattr(em, "_COVERAGE_TASK", None)
    return started


@pytest.mark.asyncio
async def test_warm_path_does_not_leak_a_task_per_call(warm_slow_coverage):
    """THE LEAK. Ten timed-out warm-path calls must not leave ten builds running.

    Pre-fix this leaves 10 pending get_coverage tasks; production leaked one
    every 30s indefinitely on the same path.
    """
    before = len(_pending_coverage_tasks())

    for _ in range(10):
        assert await em.get_coverage_nonblocking(max_wait_s=0.05) is None

    await asyncio.sleep(0)  # let anything cancellable settle

    leaked = len(_pending_coverage_tasks()) - before
    try:
        assert leaked <= 1, (
            f"{leaked} get_coverage tasks left running after 10 timed-out calls. "
            "The warm path must share ONE in-flight build (as the cold path "
            "already does) instead of starting and abandoning one per caller."
        )
    finally:
        for t in _pending_coverage_tasks():
            t.cancel()
        await asyncio.gather(*_pending_coverage_tasks(), return_exceptions=True)


@pytest.mark.asyncio
async def test_warm_path_runs_one_build_for_many_callers(warm_slow_coverage):
    """The cost side of the same defect: ten callers must trigger ONE build, not
    ten. Counted at `_gather_signals`, i.e. the work itself — this is what was
    saturating the state_store read pool."""
    started = warm_slow_coverage

    await asyncio.gather(*[
        em.get_coverage_nonblocking(max_wait_s=0.05) for _ in range(10)
    ])

    try:
        assert started["n"] <= 1, (
            f"{started['n']} concurrent coverage builds for 10 callers — each one "
            "holds five concurrent state_store reads open, which is what drove "
            "the live read-timeout storm."
        )
    finally:
        for t in _pending_coverage_tasks():
            t.cancel()
        await asyncio.gather(*_pending_coverage_tasks(), return_exceptions=True)


@pytest.mark.asyncio
async def test_shield_still_protects_the_shared_build(warm_slow_coverage):
    """The shield must NOT be 'fixed' by dropping it. Its documented purpose —
    a timed-out caller must not cancel a build others are waiting on, and must
    not throw away a ~6s parse — still holds, now applied to the SHARED task.

    Without this a future edit could pass the two tests above by simply
    cancelling on timeout, reintroducing the cold-cache-forever bug R-F3062 was
    written to prevent.
    """
    assert await em.get_coverage_nonblocking(max_wait_s=0.05) is None

    tasks = _pending_coverage_tasks()
    assert len(tasks) == 1, (
        f"expected exactly one shared in-flight build, got {len(tasks)}")
    assert not tasks[0].cancelled() and not tasks[0].done(), (
        "the shared build was cancelled by a caller's timeout — that is the "
        "R-F3062 regression (a 6s parse thrown away, cache cold forever)")

    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
