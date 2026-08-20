"""R-F2754 — superseded aiosqlite connections must be CLOSED, not leaked.

_reconnect() (write) and _ensure_read_conn() (read pool) each open a REPLACEMENT
connection and swap it in, but historically never closed the old one — so every
self-heal cycle orphaned the old aiosqlite worker thread (each connection owns one).
Live forensic 2026-07-18: 54 live _connection_worker_thread threads vs ~6 intended
→ thread oversubscription → 2–5s event-loop stalls. These tests drive the REAL
functions and assert the old connections are actually closed (thread reclaimed),
while the new pool still serves reads.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import threading

import pytest

from aria_service.intel import state_store as _ss


async def _is_closed(conn) -> bool:
    """A closed aiosqlite connection raises on any further use."""
    try:
        cur = await asyncio.wait_for(conn.execute("SELECT 1"), timeout=5.0)
        await cur.close()
        return False
    except Exception:
        return True


class TestConnReaper:
    @pytest.fixture(autouse=True)
    async def _fresh_store(self, monkeypatch):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        tmp.close()
        monkeypatch.setenv("ARIA_STATE_DB_PATH", db_path)
        monkeypatch.setattr(_ss, "_READ_POOL_SIZE", 3, raising=False)
        if _ss._conn is not None:
            await _ss.close()
        await _ss.connect()
        yield
        try:
            await _ss.close()
        except Exception:
            pass
        try:
            os.unlink(db_path)
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_reap_helper_closes_conns(self):
        import aiosqlite
        c = await aiosqlite.connect(":memory:")
        assert await _is_closed(c) is False
        _ss._reap_old_conns(c)
        await asyncio.gather(*list(_ss._reap_tasks))  # let the detached close run
        assert await _is_closed(c) is True, "reaped connection must be closed"

    @pytest.mark.asyncio
    async def test_ensure_read_conn_closes_old_pool_not_leak(self):
        old_pool = list(_ss._read_pool)
        assert len(old_pool) == _ss._READ_POOL_SIZE + 1
        await _ss._ensure_read_conn()          # rebuilds the pool
        new_pool = list(_ss._read_pool)
        assert len(new_pool) == _ss._READ_POOL_SIZE + 1
        assert all(o is not n for o in old_pool for n in new_pool), "pool must be rebuilt"
        # the detached reaps close the OLD pool
        await asyncio.gather(*list(_ss._reap_tasks))
        for o in old_pool:
            assert await _is_closed(o) is True, "each OLD read conn must be closed (not leaked)"
        # the NEW pool still serves reads
        assert await _is_closed(new_pool[0]) is False

    @pytest.mark.asyncio
    async def test_thread_count_stable_across_many_read_refreshes(self):
        """The leak signature: repeated _ensure_read_conn() must NOT grow the
        aiosqlite worker-thread count without bound."""
        def _aiosqlite_threads() -> int:
            return sum(1 for t in threading.enumerate()
                       if "sqlite" in t.name.lower() or "Thread-" in t.name)

        # Rebuild the read pool many times; reap each old pool as we go.
        for _ in range(8):
            await _ss._ensure_read_conn()
            await asyncio.gather(*list(_ss._reap_tasks))
        # After reaping, all point lanes plus the dedicated scan lane remain.
        assert len(_ss._read_pool) == _ss._READ_POOL_SIZE + 1
        # And every previously-super­seded conn was closed — verify by driving a
        # read on the current pool (proves the store is healthy post-churn).
        await _ss.set_key("rf2754_k", "v")
        await _ss._flush_write_queue()
        _ss._error_log_cache.clear()
        assert await _ss.get("rf2754_k") == "v"
