"""R-F2470 capability test — lpush must not drop a write when the list's seq
counter has drifted BELOW the actual MAX(seq) in list_entries.

The live symptom (2026-07-08 monitoring):
  lpush crucix:aria:dd:watchlist:alerts failed:
    UNIQUE constraint failed: list_entries.list_key, seq
i.e. a DD watchlist alert was SILENTLY DROPPED because the counter-derived seq
collided with an existing row. The fix derives seq from MAX(seq)+1.
"""
import os
import tempfile

import pytest

from aria_service.intel import state_store as _ss


class TestLpushSeqDrift:
    @pytest.fixture(autouse=True)
    async def _fresh_store(self, monkeypatch):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = tmp.name
        monkeypatch.setenv("ARIA_STATE_DB_PATH", db_path)
        try:
            await _ss.close()
        except Exception:
            pass
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
    async def test_rf2470_lpush_survives_seq_counter_drift(self):
        key = "test:rf2470:dd:watchlist:alerts"
        # DRIFT: a list_entries row already exists at seq=1 while the seq counter
        # is behind at 0 — the exact divergence (INSERT-OR-IGNORE materialization /
        # wedge reset) that made the old counter-derived seq collide.
        await _ss._conn.execute(
            "INSERT INTO list_entries(list_key, seq, value) VALUES(?, 1, ?)",
            (key, "ghost_at_seq_1"),
        )
        seq_key = _ss._list_seq_counter(key)
        await _ss._conn.execute(
            "INSERT INTO state(key, value, kind) VALUES(?, '0', 'string') "
            "ON CONFLICT(key) DO UPDATE SET value='0'",
            (seq_key,),
        )
        await _ss._conn.commit()

        # Old code: counter 0->1, INSERT seq=1 -> UNIQUE collision -> (critical) raise / drop.
        # New code: seq = MAX(seq)=1 + 1 = 2 -> no collision -> alert persisted.
        await _ss.lpush(key, "watchlist_alert_after_drift", critical=True)

        vals = await _ss.lrange(key, 0, -1)
        assert "watchlist_alert_after_drift" in vals, f"alert was dropped: {vals}"
        assert "ghost_at_seq_1" in vals, vals

    @pytest.mark.asyncio
    async def test_rf2470_many_pushes_no_collision(self):
        key = "test:rf2470:seq:burst"
        for i in range(25):
            await _ss.lpush(key, f"v{i}", critical=True)
        vals = await _ss.lrange(key, 0, -1)
        assert len(vals) == 25, f"expected 25 entries, got {len(vals)}"
