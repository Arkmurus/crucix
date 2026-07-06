"""R-F2378 — WAL checkpoints must be bounded and off the writer connection."""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import state_store as ss


@pytest.mark.asyncio
async def test_runtime_checkpoint_timeout_returns_without_blocking_worker(monkeypatch, tmp_path):
    db_path = tmp_path / "aria_state.db"
    wal_path = tmp_path / "aria_state.db-wal"
    wal_path.write_bytes(b"x" * 4096)

    monkeypatch.setattr(ss, "_DB_PATH", db_path, raising=False)
    monkeypatch.setattr(ss, "_conn", object(), raising=False)
    monkeypatch.setattr(ss, "_WAL_TRUNCATE_THRESHOLD_BYTES", 1, raising=False)
    monkeypatch.setattr(ss, "_WAL_CHECKPOINT_TIMEOUT_S", 0.05, raising=False)

    async def hangs(reason="maintenance"):
        await asyncio.sleep(10)

    monkeypatch.setattr(ss, "_bounded_wal_checkpoint", hangs)

    await asyncio.wait_for(ss._maybe_checkpoint_wal(), timeout=1.0)


@pytest.mark.asyncio
async def test_bounded_checkpoint_uses_maintenance_connection(monkeypatch, tmp_path):
    db_path = tmp_path / "aria_state.db"
    monkeypatch.setattr(ss, "_DB_PATH", db_path, raising=False)
    monkeypatch.setattr(ss, "_WAL_CHECKPOINT_TIMEOUT_S", 1.0, raising=False)

    calls: list[str] = []

    class FakeConn:
        async def execute(self, sql):
            calls.append(sql)
        async def commit(self):
            calls.append("COMMIT")
        async def close(self):
            calls.append("CLOSE")

    class FakeAioSqlite:
        async def connect(self, path):
            calls.append(f"CONNECT:{path}")
            return FakeConn()

    import sys
    monkeypatch.setitem(sys.modules, "aiosqlite", FakeAioSqlite())

    await ss._bounded_wal_checkpoint("test")

    assert calls[0].startswith("CONNECT:")
    assert "PRAGMA wal_checkpoint(TRUNCATE)" in calls
    assert calls[-1] == "CLOSE"
