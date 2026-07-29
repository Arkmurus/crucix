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
    would auto-checkpoint and defeat the setup).

    R-F3443 — the 20,000 inserts used to run in AUTOCOMMIT (`isolation_level=None`
    with no explicit transaction), i.e. 20,000 separate commits and 20,000 fsyncs.
    MEASURED on this machine: 93.9s to build the fixture, which made the whole test
    100.6s against pytest.ini's 120s timeout — 84% of the budget.

    Why that was a whole-suite hazard and not just a slow test: on Windows
    pytest-timeout uses the THREAD method, which kills the PROCESS. Any contention
    (running in-suite, a busy machine — same-day measurements varied 110s-220s on an
    identical file set) pushed this past 120s, and crossing it took the ENTIRE RUN
    down with no summary line. The output then names `_do_shutdown`, not this test,
    so the cost was undiagnosable "the suite hangs" rather than "this test is slow".

    One explicit transaction is 2.3s — a 40x reduction — and still produces an ~83 MB
    WAL, sixteen times the 5 MB the assertion below requires. Nothing this test
    proves has changed: the WAL is still large, still un-checkpointed, and k0/k19999
    are still both present for the lossless check.
    """
    holder = sqlite3.connect(str(db_path), isolation_level=None)
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("PRAGMA wal_autocheckpoint=0")  # never auto-truncate
    holder.execute(
        "CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL, "
        "kind TEXT NOT NULL DEFAULT 'string', expires_at REAL)"
    )
    blob = "x" * 4000
    holder.execute("BEGIN")
    holder.executemany(
        "INSERT OR REPLACE INTO state(key,value,kind) VALUES(?,?, 'string')",
        ((f"k{i}", blob) for i in range(20000)),
    )
    holder.execute("COMMIT")
    # committed into the WAL, but NOT checkpointed
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


def test_rf3443_the_fixture_leaves_real_headroom_under_the_suite_timeout(tmp_path):
    """R-F3443 — this test's own cost must stay far below pytest.ini's 120s.

    Asserts HEADROOM, not a specific duration: the threshold is deliberately loose
    (30s vs the ~2.3s the batched fixture actually takes) so ordinary machine load
    cannot make it cry wolf, while a regression to the old per-row autocommit — which
    measured 93.9s here — fails it immediately.

    This matters more than one slow test. On Windows pytest-timeout's thread method
    kills the PROCESS, so a test that creeps up on the timeout does not fail alone: it
    takes the whole run down without printing a summary, and the stack dump names the
    shutdown thread rather than the culprit. Headroom IS the safety property.
    """
    import time

    t0 = time.time()
    holder, _ = _make_bloated_wal(tmp_path / "hdr.db")
    try:
        elapsed = time.time() - t0
        wal = (tmp_path / "hdr.db").with_name("hdr.db-wal")
        size = wal.stat().st_size if wal.exists() else 0
    finally:
        holder.close()

    assert size > 5 * 1024 * 1024, (
        f"the fixture must still bloat the WAL for the test to mean anything; got {size}")
    assert elapsed < 30, (
        f"WAL fixture took {elapsed:.1f}s. pytest.ini's timeout is 120s and exceeding it "
        f"KILLS THE WHOLE RUN with no summary, so this must keep real headroom. If the "
        f"inserts went back to per-row autocommit, that is the regression (93.9s measured)."
    )


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
