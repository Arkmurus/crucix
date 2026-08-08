"""R-F3251 — a failed read-pool rebuild orphans its connection threads.

Found by a live 20-cycle log review (2026-07-27). Every event-loop stall in the
window carried `aiosqlite/core.py:_connection_worker_thread` frames, and the
wedge stack captured on the box showed **56** live aiosqlite connection worker
threads (stable across three measurements; an earlier dump held 140) against a
design of about six: `_READ_POOL_SIZE=3`, one writer, two cold-storage.

The main-thread stack in that same capture was bare —

    asyncio/runners.py:119 in run      <- no application frame at all

so the loop was NOT blocked by a coroutine. Tens of extra SQLite worker threads
contending for the GIL is what makes the heartbeat go stale.

WHERE THEY COME FROM. `_ensure_read_conn` rebuilds the whole read pool:

    _old_pool = list(_read_pool)
    new_pool = []
    for _ in range(_READ_POOL_SIZE):
        _rc = await aiosqlite.connect(...)   # (a)
        await _configure_read_conn(_rc)      # (b)
        new_pool.append(_rc)
    _read_pool = new_pool
    _reap_old_conns(*_old_pool)              # (c)
    except Exception:
        logger.warning(...)                  # (d)

R-F2754 fixed the SUCCESS path — (c) reaps the superseded pool. The FAILURE
path was left. If (a) or (b) raises partway through — which is exactly what
happens when the store is already under stress, and this function is called
BECAUSE it is under stress — control jumps to (d). `new_pool` holds the
connections opened so far, is never assigned to `_read_pool`, and is never
closed. Their worker threads run on, unreferenced.

Up to `_READ_POOL_SIZE` threads per failed rebuild, on the self-heal path that
fires most often precisely when the box is struggling.

The assertion here is the THREAD COUNT, not the exception: a rebuild is allowed
to fail, it is not allowed to lose threads.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

# R-F3784/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


def _live_connection_threads() -> int:
    """Live aiosqlite connection worker threads.

    Counted by thread NAME. aiosqlite 0.22.x runs each connection on a plain
    `Thread` named "Thread-N (_connection_worker_thread)" — `Connection` is not
    itself a Thread subclass, so an isinstance check silently counts zero and
    every assertion built on it passes vacuously. That is the same frame the
    live wedge stack reports, which is what makes this the right instrument:
    it measures exactly what was measured on the box.
    """
    # Must match BOTH names. redis_store patches aiosqlite's worker function,
    # and CPython names a thread after its target — so in any process that
    # imports redis_store (production, and this suite) the worker is
    # "Thread-N (_patched_worker)". Matching only aiosqlite's own name counts
    # zero forever, which is how this file's first instrument was wrong twice.
    return sum(1 for t in threading.enumerate()
               if t.is_alive() and (
                   "_connection_worker_thread" in t.name
                   or "_patched_worker" in t.name))


async def _settle(baseline: int, tries: int = 40) -> int:
    """Closes are fire-and-forget by design (a wedged close must never block
    the caller), so give the reaper a moment before measuring."""
    for _ in range(tries):
        if _live_connection_threads() <= baseline:
            break
        await asyncio.sleep(0.1)
    return _live_connection_threads()


@pytest.mark.asyncio
async def test_a_failed_pool_rebuild_does_not_orphan_connection_threads(tmp_path, monkeypatch):
    """THE leak. The rebuild is allowed to fail; it may not lose threads."""
    from aria_service.intel import state_store as ss

    monkeypatch.setattr(ss, "_DB_PATH", tmp_path / "pool.db", raising=False)
    monkeypatch.setattr(ss, "_READ_POOL_SIZE", 3, raising=False)
    monkeypatch.setattr(ss, "_read_pool", [], raising=False)
    monkeypatch.setattr(ss, "_read_conn", None, raising=False)

    calls = {"n": 0}
    real_configure = ss._configure_read_conn

    async def _fail_on_the_third(conn):
        calls["n"] += 1
        if calls["n"] >= 3:
            raise RuntimeError("simulated: store under stress mid-rebuild")
        return await real_configure(conn)

    monkeypatch.setattr(ss, "_configure_read_conn", _fail_on_the_third)

    before = _live_connection_threads()

    # Several rebuilds, as the self-heal path does under sustained stress.
    for _ in range(3):
        calls["n"] = 0
        await ss._ensure_read_conn()      # swallows the error by design

    after = await _settle(before)
    assert after <= before, (
        f"{after - before} aiosqlite connection thread(s) orphaned by 3 failed "
        f"pool rebuilds. On the live box this is the 56-140 threads against a "
        f"design of ~6, and the GIL contention behind the event-loop stalls.")


@pytest.mark.asyncio
async def test_a_successful_rebuild_still_reaps_the_old_pool(tmp_path, monkeypatch):
    """R-F2754's property must survive the fix — the success path still has to
    close the connections it supersedes."""
    from aria_service.intel import state_store as ss

    monkeypatch.setattr(ss, "_DB_PATH", tmp_path / "ok.db", raising=False)
    monkeypatch.setattr(ss, "_READ_POOL_SIZE", 2, raising=False)
    monkeypatch.setattr(ss, "_read_pool", [], raising=False)
    monkeypatch.setattr(ss, "_read_conn", None, raising=False)

    # Asserted by OBSERVING THE REAPER, not by counting process-global threads.
    # A thread count is shared with every other test in the process, so a test
    # built on it passes or fails on execution order rather than on the property
    # — which is exactly what this test did before. Watching which connections
    # are handed to `_reap_old_conns` is deterministic and says the same thing.
    reaped: list = []
    real_reap = ss._reap_old_conns
    monkeypatch.setattr(
        ss, "_reap_old_conns",
        lambda *conns: (reaped.extend(conns), real_reap(*conns))[1])

    await ss._ensure_read_conn()                    # builds a pool of 2
    assert len(ss._read_pool) == 2
    first_pool = list(ss._read_pool)

    await ss._ensure_read_conn()                    # rebuild — supersedes them
    assert len(ss._read_pool) == 2
    assert ss._read_pool != first_pool, "the rebuild did not replace the pool"

    for conn in first_pool:
        assert conn in reaped, (
            "a superseded read connection was not handed to the reaper — "
            "R-F2754's property regressed and its thread leaks")

    # Leave nothing behind for whatever runs next.
    real_reap(*list(ss._read_pool))
    ss._read_pool, ss._read_conn = [], None


@pytest.mark.asyncio
async def test_a_rebuild_failure_is_still_swallowed_not_raised(tmp_path, monkeypatch):
    """The self-heal path must never raise into its caller — reclaiming threads
    may not cost us that."""
    from aria_service.intel import state_store as ss

    monkeypatch.setattr(ss, "_DB_PATH", tmp_path / "swallow.db", raising=False)
    monkeypatch.setattr(ss, "_READ_POOL_SIZE", 2, raising=False)
    monkeypatch.setattr(ss, "_read_pool", [], raising=False)
    monkeypatch.setattr(ss, "_read_conn", None, raising=False)

    async def _always_fails(conn):
        raise RuntimeError("simulated")

    monkeypatch.setattr(ss, "_configure_read_conn", _always_fails)
    await ss._ensure_read_conn()          # must not raise
    await _settle(_live_connection_threads())


# ── R-F3262: the same gap in the sibling self-heal path ──────────────────

@pytest.mark.asyncio
async def test_a_failed_reconnect_does_not_orphan_its_connection(tmp_path, monkeypatch):
    """R-F3251 fixed `_ensure_read_conn`; `_reconnect` had the identical gap.

    Post-fix measurement on the live box showed aiosqlite worker threads down
    from 56 to 20 — a real reduction, but still far above the ~6 the pool
    design implies, so a second source had to exist.

    `_reconnect` opens a replacement, runs four PRAGMAs, and only then assigns
    it to `_conn`. Its own comments note that `journal_mode=WAL` can raise
    "database is locked" during a multi-GB WAL replay. When it does, control
    jumps to the `except`, and the connection that was already open is neither
    assigned nor closed — orphaned, exactly like the read pool was. And this is
    the SELF-HEAL path: it runs when the store is already wedged, which is
    precisely when another abandoned thread hurts most.
    """
    import aiosqlite

    from aria_service.intel import state_store as ss

    monkeypatch.setattr(ss, "_DB_PATH", tmp_path / "reconn.db", raising=False)
    monkeypatch.setattr(ss, "_conn", None, raising=False)
    monkeypatch.setattr(ss, "_reconnect_in_progress", False, raising=False)

    real_execute = aiosqlite.Connection.execute

    async def _wal_replay_locked(self, sql, *a, **kw):
        if "journal_mode" in str(sql).lower():
            raise aiosqlite.OperationalError("database is locked")
        return await real_execute(self, sql, *a, **kw)

    monkeypatch.setattr(aiosqlite.Connection, "execute", _wal_replay_locked)

    await asyncio.sleep(0.2)
    before = _live_connection_threads()

    for _ in range(3):
        ss._reconnect_in_progress = False
        await ss._reconnect()          # swallows the error by design

    after = await _settle(before)
    assert after <= before, (
        f"{after - before} aiosqlite connection thread(s) orphaned by 3 failed "
        f"reconnects — the same defect R-F3251 fixed in the read pool, on the "
        f"self-heal path that runs when the store is already struggling")


# ── R-F3263: the leak must be readable without waiting for a crash ───────

def test_connection_gauge_reports_workers_and_stuck_reaps():
    """Until this existed, the only way to see a connection leak building was
    an R-F704 wedge stack — written only WHEN A STALL HAPPENS. The number that
    warns you was available exclusively after the damage."""
    from aria_service.intel import state_store as ss

    g = ss.connection_gauge()
    for key in ("workers", "stuck_reaps", "expected", "excess"):
        assert key in g, f"gauge is missing {key}"
        assert isinstance(g[key], int)
    assert g["excess"] == max(0, g["workers"] - g["expected"])


def test_the_gauge_counts_a_real_connection(tmp_path):
    """It must see a live connection, and it must count the RIGHT predicate.

    Asserted by agreement with an independent count taken at the same instant,
    not by an absolute delta. Other tests in this process open and close their
    own connections, so a before/after delta measures their timing as much as
    the gauge — which is the order-dependence this file already had to fix
    once. Agreement is deterministic; a delta is not.
    """
    import aiosqlite

    from aria_service.intel import state_store as ss

    async def _probe():
        conn = await aiosqlite.connect(str(tmp_path / "g.db"))
        try:
            # aiosqlite starts the worker inside connect(), but `Thread.start()`
            # returning and the thread appearing in `threading.enumerate()` are
            # not the same instant. Standalone that gap is invisible; under a
            # loaded suite it is not, and asserting through it measures
            # scheduler timing rather than the gauge. Bounded wait, then assert.
            for _ in range(50):
                if ss.connection_gauge()["workers"] >= 1:
                    break
                await asyncio.sleep(0.02)

            gauge = ss.connection_gauge()["workers"]
            direct = _live_connection_threads()
            assert gauge == direct, (
                f"the gauge ({gauge}) disagrees with a direct count of the same "
                f"predicate ({direct}) — it is not counting what it claims to")
            assert gauge >= 1, (
                "a connection is open and the gauge still reads zero after a "
                "second — this is the isinstance mistake this file already "
                "made once")
        finally:
            await conn.close()

    asyncio.run(_probe())


def test_expected_counts_the_whole_process_not_just_state_store():
    """The first pass of this investigation called 20 workers '3x design' by
    counting state_store's six and forgetting the six module singletons
    (brain_ingest_queue, dialogue_state, user_model, bookmarks, reading_queue,
    search_index). Overstating a gap sends the next reader hunting a leak twice
    the real size."""
    from aria_service.intel import state_store as ss

    assert ss.connection_gauge()["expected"] == ss._READ_POOL_SIZE + 1 + 2 + 6


def test_the_gauge_survives_the_redis_store_worker_patch():
    """R-F3263 — the gauge must count the PATCHED thread name.

    `redis_store` replaces `aiosqlite.core._connection_worker_thread` with its
    own `_patched_worker`, and CPython names a thread after its target. So in
    production every worker is "Thread-N (_patched_worker)". A gauge matching
    only aiosqlite's own name reads zero forever — an alarm that can never
    fire, which is worse than no alarm because it reads as healthy.
    """
    import inspect

    from aria_service.intel import state_store as ss

    src = function_source(ss, "connection_gauge")
    assert "_patched_worker" in src, (
        "the gauge does not know about redis_store's patched worker name — it "
        "will read 0 in production")
    assert "_connection_worker_thread" in src, (
        "the gauge dropped the unpatched name — it would miss a process that "
        "has not imported redis_store")


@pytest.mark.asyncio
async def test_the_gauge_is_actually_reachable_through_stats(tmp_path, monkeypatch):
    """R-F3263 — a gauge with no consumer is the defect this whole session has
    been about. `stats()` is what /health renders, so the numbers have to
    arrive there or they are unreadable in production exactly when needed.
    """
    from aria_service.intel import state_store as ss

    monkeypatch.setattr(ss, "_DB_PATH", tmp_path / "stats.db", raising=False)
    await ss._ensure_read_conn()
    try:
        s = await ss.stats()
        assert "connections" in s, (
            "stats() does not carry the connection gauge — it is a function "
            "nobody calls, which is how it was written the first time")
        for key in ("workers", "stuck_reaps", "expected", "excess"):
            assert key in s["connections"], f"stats().connections lacks {key}"
    finally:
        ss._reap_old_conns(*list(ss._read_pool))
        ss._read_pool, ss._read_conn = [], None


def test_the_gauge_has_an_http_route_not_just_a_function():
    """R-F3263 — `stats()` carries the gauge but NOTHING renders stats() over
    HTTP, so wiring it there alone left it as unreachable as before. The route
    is what makes the number answerable without ssh-ing to the box mid-stall.
    """
    import inspect

    from aria_service.routes import aria as aria_routes

    src = module_source(aria_routes)
    assert '"/admin/state/connections"' in src, (
        "no HTTP route exposes the connection gauge — it is reachable only "
        "from inside the process, which is where it was already stuck")
    assert "connection_gauge()" in src, (
        "the route does not call the gauge")
