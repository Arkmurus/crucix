"""R-F2137 — state_store must periodically TRUNCATE the runtime WAL so it
cannot grow unbounded between boots.

PRAGMA wal_autocheckpoint is PASSIVE: it transfers committed frames into the
main DB but NEVER shrinks the -wal FILE. Under sustained writes + reader
pinning the -wal high-water mark only grows at runtime (110 MB observed during
the 2026-06-29 incident, feeding 'database is locked' lock contention). R-F2116
only TRUNCATEs at boot; R-F2137 adds a periodic checkpoint(TRUNCATE) in the
write-drain worker, gated on a size threshold.

This drives the real `_maybe_checkpoint_wal()` path against a deliberately
re-grown WAL and asserts it (a) truncates a large WAL losslessly and (b) is a
no-op below the threshold.
"""
import asyncio

import aria_service.intel.state_store as SS


async def _regrow_wal(blob_rows=40000):
    """After connect()'s boot checkpoint, disable PASSIVE autocheckpoint and
    write enough to re-grow the -wal past the test threshold (simulates the
    runtime growth that autocheckpoint can't reclaim)."""
    await SS._conn.execute("PRAGMA wal_autocheckpoint=0")
    blob = "y" * 4000
    for i in range(blob_rows):
        await SS._conn.execute(
            "INSERT OR REPLACE INTO state(key,value,kind) VALUES(?,?, 'string')",
            (f"r{i}", blob),
        )
    await SS._conn.commit()


def test_rf2137_runtime_checkpoint_truncates_grown_wal(tmp_path, monkeypatch):
    db = tmp_path / "aria_state.db"
    wal = db.with_name(db.name + "-wal")
    monkeypatch.setenv("ARIA_STATE_DB_PATH", str(db))
    # Threshold read at import time → patch the module constant directly.
    monkeypatch.setattr(SS, "_WAL_TRUNCATE_THRESHOLD_BYTES", 5 * 1024 * 1024)

    async def _run():
        ok = await SS.connect(str(db))
        assert ok, "connect() must succeed"
        await SS.set_key("canary", "v1")          # a value to prove losslessness
        await _regrow_wal()
        grown = wal.stat().st_size if wal.exists() else 0
        await SS._maybe_checkpoint_wal()           # the real R-F2137 path
        after = wal.stat().st_size if wal.exists() else 0
        canary = await SS.get("canary")
        await SS.close()
        return grown, after, canary

    grown, after, canary = asyncio.run(_run())
    assert grown > 5 * 1024 * 1024, f"test needs a grown WAL, got {grown}"
    assert after < grown / 2, f"R-F2137 did not truncate the WAL: {grown} -> {after}"
    assert canary == "v1", "data lost during runtime WAL checkpoint"


def test_rf2137_no_op_below_threshold(tmp_path, monkeypatch):
    """Below the threshold the checkpoint is skipped — no error, WAL untouched
    (the common steady-state case must pay no checkpoint IO)."""
    db = tmp_path / "aria_state.db"
    wal = db.with_name(db.name + "-wal")
    monkeypatch.setenv("ARIA_STATE_DB_PATH", str(db))
    monkeypatch.setattr(SS, "_WAL_TRUNCATE_THRESHOLD_BYTES", 500 * 1024 * 1024)

    async def _run():
        await SS.connect(str(db))
        await SS.set_key("k", "v")
        before = wal.stat().st_size if wal.exists() else 0
        await SS._maybe_checkpoint_wal()           # must be a guarded no-op
        after = wal.stat().st_size if wal.exists() else 0
        await SS.close()
        return before, after

    before, after = asyncio.run(_run())
    assert after == before, f"below-threshold checkpoint must not touch WAL: {before} -> {after}"
