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


def _live_connection_threads() -> int:
    """Live aiosqlite connection worker threads.

    Counted by thread NAME. aiosqlite 0.22.x runs each connection on a plain
    `Thread` named "Thread-N (_connection_worker_thread)" — `Connection` is not
    itself a Thread subclass, so an isinstance check silently counts zero and
    every assertion built on it passes vacuously. That is the same frame the
    live wedge stack reports, which is what makes this the right instrument:
    it measures exactly what was measured on the box.
    """
    return sum(1 for t in threading.enumerate()
               if "_connection_worker_thread" in t.name and t.is_alive())


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
