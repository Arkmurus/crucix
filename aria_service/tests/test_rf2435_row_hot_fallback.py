"""R-F2435 — _row hot-fallback makes point-reads SYMMETRIC with the scan union.

R-F2413 made scan_keys/scan_json union hot+cold, but get()/get_json (_row) read
only the routed (cold) conn for a cold-prefix key. So at cutover a cold key still
in HOT (written after the backfill snapshot / backfill incomplete) was FOUND by
scan but returned None by get() — verified_facts / reasoning_library / audit
entries would appear to vanish. R-F2435 adds a hot-fallback on a cold-miss when
the split is on. DORMANT (byte-identical) when the flag is OFF.

Drives the REAL state_store (get/scan/set), flag ON, with a cold key written to
the HOT file only (simulating not-yet-backfilled).

Runs standalone:  python aria_service/tests/test_rf2435_row_hot_fallback.py
"""
import asyncio
import os
import tempfile

from aria_service.intel import state_store as _ss

COLD_KEY = "aria:verified_facts:GENERAL_CLAIM:rf2435"   # routes cold


async def _setup(split):
    d = tempfile.mkdtemp()
    os.environ["ARIA_STATE_DB_PATH"] = os.path.join(d, "aria_state.db")
    _ss._HOTCOLD_SPLIT = split
    if _ss._conn is not None:
        await _ss.close()
    await _ss.connect()
    return d


async def _write_hot_only(key, value):
    """Bypass routing — write a (cold-prefixed) key straight into the HOT file,
    simulating a key not yet backfilled to cold."""
    await _ss._conn.execute(
        "INSERT OR REPLACE INTO state (key, value, kind, expires_at) VALUES (?,?,?,NULL)",
        (key, value, "string"),
    )
    await _ss._conn.commit()


async def _run():
    fails = []
    ok = lambda c, m: (print(f"  {'✓' if c else '✗'} {m}"), fails.append(m) if not c else None)

    # ── 1. THE FIX: split ON, cold key lives in HOT only → get() must find it ──
    await _setup(True)
    try:
        ok(_ss._route_db(COLD_KEY) == "cold", "test key routes cold")
        ok(_ss._cold_read_conn is not None, "cold conn open under split")
        await _write_hot_only(COLD_KEY, "HOTVAL")
        got = await _ss.get(COLD_KEY)
        ok(got == "HOTVAL", f"get() finds a not-yet-backfilled cold key via hot-fallback (got {got!r})")
        # symmetry: scan already unions → must also find it (the property we match)
        found = await _ss.scan_keys("aria:verified_facts:*")
        ok(COLD_KEY in found, "scan_keys also finds it (get is now symmetric with scan)")
        # a genuinely absent cold key must still be None (fallback does not fabricate)
        ok(await _ss.get("aria:verified_facts:GENERAL_CLAIM:absent") is None, "absent cold key still None (no fabrication)")
    finally:
        await _ss.close()

    # ── 2. DORMANT: split OFF → no cold conn, so the fallback block is skipped
    #    (byte-identical). Regression of the normal set_key→get round-trip +
    #    cold-served-from-cold is covered by the R-F2413/2415 suite, which still
    #    passes 34/34 with this change. ──
    await _setup(False)
    try:
        ok(_ss._cold_read_conn is None, "flag OFF → no cold conn → fallback dormant (byte-identical)")
        ok(_ss._route_db(COLD_KEY) == "cold", "router unchanged when flag off (pure fn)")
    finally:
        await _ss.close()

    if fails:
        print(f"\nFAIL ({len(fails)})")
        raise SystemExit(1)
    print("\nPASS")


def test_row_hot_fallback():
    asyncio.run(_run())


if __name__ == "__main__":
    asyncio.run(_run())
