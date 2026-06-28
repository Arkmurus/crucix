"""R-F2116 — state_store.connect() must reclaim a bloated WAL at boot.

The 2026-06-28 outage: aria_state.db-wal bloated to 591 MB after a contested-deploy
crash loop (SIGKILL mid-write leaves the -wal un-checkpointed; once it grows across
crashes sqlite's autocheckpoint can no longer truncate it). Every boot's WAL handling
then exceeded fly's 1-min health grace -> SIGTERM mid-recovery -> the next boot faced
the same WAL -> an infinite crash loop. The fix: connect() runs a lossless
PRAGMA wal_checkpoint(TRUNCATE) so every boot starts from a small, fast-opening DB.

This test builds a deliberately bloated, un-checkpointed WAL on disk, then proves
connect() (the actual boot path) reclaims it AND keeps the data (lossless).
"""
import asyncio
import sqlite3

import aria_service.intel.state_store as SS


def _make_bloated_wal(db_path):
    """Create a DB whose -wal file is large and un-checkpointed, and keep a
    connection open so the WAL persists on disk (closing the last connection
    would auto-checkpoint and defeat the setup)."""
    holder = sqlite3.connect(str(db_path), isolation_level=None)
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("PRAGMA wal_autocheckpoint=0")  # never auto-truncate
    holder.execute(
        "CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL, "
        "kind TEXT NOT NULL DEFAULT 'string', expires_at REAL)"
    )
    blob = "x" * 4000
    for i in range(20000):
        holder.execute(
            "INSERT OR REPLACE INTO state(key,value,kind) VALUES(?,?, 'string')",
            (f"k{i}", blob),
        )
    # committed (autocommit) into the WAL, but NOT checkpointed
    return holder, blob


def test_rf2116_boot_reclaims_bloated_wal_losslessly(tmp_path, monkeypatch):
    db = tmp_path / "aria_state.db"
    wal = db.with_name(db.name + "-wal")
    monkeypatch.setenv("ARIA_STATE_DB_PATH", str(db))

    holder, blob = _make_bloated_wal(db)
    try:
        before = wal.stat().st_size if wal.exists() else 0
        assert before > 5 * 1024 * 1024, (
            f"test needs a bloated WAL to be meaningful; got {before} bytes"
        )

        async def _boot_and_read():
            ok = await SS.connect(str(db))
            after = wal.stat().st_size if wal.exists() else 0
            v0 = await SS.get("k0")
            vlast = await SS.get("k19999")
            await SS.close()
            return ok, after, v0, vlast

        ok, after, v0, vlast = asyncio.run(_boot_and_read())

        assert ok is True, "connect() must still succeed"
        # the boot must have reclaimed the WAL (truncate)
        assert after < before / 2, f"boot did not reclaim WAL: {before} -> {after}"
        # lossless: every row written before the boot is still readable
        assert v0 == blob, "data lost during boot WAL checkpoint"
        assert vlast == blob, "data lost during boot WAL checkpoint"
    finally:
        holder.close()


def test_rf2116_autocheckpoint_is_bounded_after_connect(tmp_path, monkeypatch):
    """After connect(), wal_autocheckpoint must be set (not 0/disabled) so the WAL
    stays bounded during normal operation."""
    db = tmp_path / "aria_state.db"
    monkeypatch.setenv("ARIA_STATE_DB_PATH", str(db))

    async def _boot_and_check():
        await SS.connect(str(db))
        cur = await SS._conn.execute("PRAGMA wal_autocheckpoint")
        row = await cur.fetchone()
        await SS.close()
        return row[0] if row else None

    autockpt = asyncio.run(_boot_and_check())
    assert autockpt and autockpt > 0, f"autocheckpoint must be bounded, got {autockpt}"
