"""R-F2242 — state_store read-connection POOL.

A single aiosqlite read connection serialized ALL key-value reads on one thread,
causing the dashboard/self-diagnostic ReadTimeouts and WA R-F1515 brain-fetch
failures under concurrent load. R-F2242 replaces it with a small pool (each
connection its own thread) behind the existing _get_read_conn() seam, with the
PRAGMA setup centralized in _configure_read_conn (R-F2132 boot-deadlock guard).

These capability tests drive the REAL pool: size, round-robin spread, per-member
PRAGMA correctness, concurrent-read correctness, and clean teardown.
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from aria_service.intel import state_store as _ss


class TestReadPool:
    @pytest.fixture(autouse=True)
    async def _fresh_store(self, monkeypatch):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = tmp.name
        tmp.close()
        monkeypatch.setenv("ARIA_STATE_DB_PATH", db_path)
        # pool size read from the module global at connect() time
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
    async def test_pool_opens_n_connections(self):
        assert len(_ss._read_pool) == 4, "pool should hold point lanes plus one scan lane"
        assert _ss._read_conn is _ss._read_pool[0], "_read_conn stays as pool[0]"

    @pytest.mark.asyncio
    async def test_point_reads_exclude_the_reserved_scan_lane(self):
        seen = {id(_ss._get_read_conn()) for _ in range(9)}
        assert seen == {id(c) for c in _ss._read_pool[:-1]}
        assert len(seen) == _ss._READ_POOL_SIZE
        assert _ss._get_scan_read_conn() is _ss._read_pool[-1]

    @pytest.mark.asyncio
    async def test_real_get_survives_a_blocked_real_scan(self):
        """R-F4211 capability: a bulk scan cannot blind an indexed point read."""
        await _ss.set_key("rf4211_live", "still-readable")
        await _ss._flush_write_queue()

        scan_started = asyncio.Event()
        release_scan = asyncio.Event()
        real_scan_conn = _ss._read_pool[-1]

        class _BlockedScanLane:
            async def execute(self, *args, **kwargs):
                scan_started.set()
                await release_scan.wait()
                return await real_scan_conn.execute(*args, **kwargs)

            def __getattr__(self, name):
                return getattr(real_scan_conn, name)

        _ss._read_pool[-1] = _BlockedScanLane()
        scan_task = asyncio.create_task(_ss.scan_keys("rf4211_*"))
        try:
            await asyncio.wait_for(scan_started.wait(), timeout=1.0)
            value = await asyncio.wait_for(_ss.get("rf4211_live"), timeout=1.0)
        finally:
            release_scan.set()
            await scan_task
            _ss._read_pool[-1] = real_scan_conn

        assert value == "still-readable"

    @pytest.mark.asyncio
    async def test_every_member_has_busy_timeout_pragma(self):
        # R-F2132 boot-deadlock guard: busy_timeout must be set on EVERY member.
        for c in _ss._read_pool:
            cur = await c.execute("PRAGMA busy_timeout")
            row = await cur.fetchone()
            await cur.close()
            assert row[0] == 120000, "busy_timeout=120000 must be set on each pool member"

    @pytest.mark.asyncio
    async def test_concurrent_reads_return_correct_values(self):
        await _ss.set_key("rf2242_k1", "v1")
        await _ss.set_key("rf2242_k2", "v2")
        # set_key enqueues to the async write worker; flush so the data is
        # committed before the concurrent reads (WAL then makes it visible to
        # every pool member).
        await _ss._flush_write_queue()
        _ss._error_log_cache.clear()  # force real DB reads, not the R-F2156 cache
        res = await asyncio.gather(
            *[_ss.get("rf2242_k1") for _ in range(10)],
            *[_ss.get("rf2242_k2") for _ in range(10)],
        )
        assert res[:10] == ["v1"] * 10
        assert res[10:] == ["v2"] * 10

    @pytest.mark.asyncio
    async def test_close_clears_the_whole_pool(self):
        await _ss.close()
        assert _ss._read_pool == [], "close() must clear the pool"
        assert _ss._read_conn is None
        # reconnect so the fixture teardown's close() is a no-op-safe path
        await _ss.connect()
