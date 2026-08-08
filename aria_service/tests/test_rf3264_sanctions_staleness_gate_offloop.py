"""R-F3264 — the sanctions staleness gate blocked the event loop.

Found from a LIVE R-F704 wedge stack captured 2026-07-27 on the fully
leak-fixed build. The main thread was not idle this time:

    sanctions_canonical/store.py:385 in newest_entry_refresh
    main.py:485 in _sanctions_refresh_once
    main.py:1236 in _sanctions_refresh_loop
    asyncio/runners.py:119 in run

That is the BLOCKED case, as distinct from the starved case an earlier capture
showed (a bare `asyncio.runners.run` with no application frame) — the
distinction R-F3252 rewrote the stall message to make a reader draw.

Two defects, one stall:

1. `main.py:485` calls `newest_entry_refresh()` DIRECTLY. The docstring three
   lines above it says the refresh "runs off the event loop", and the expensive
   part does — `refresh_all` is wrapped in `asyncio.to_thread`. The cheap-looking
   GATE in front of it was left synchronous. A claim in a docstring that the
   code does not have is the same defect class as the stall message that named
   a cause it never measured.

2. `SELECT MAX(last_refreshed) FROM entries` has no index to use. `entries`
   carries indexes on `normalised_name` and `source`; `last_refreshed` has
   none, so MAX() is a FULL TABLE SCAN — 24,953 rows on the live box, through
   sqlite3's synchronous driver, on the loop thread, every refresh tick.

Fixing only (1) would move a table scan onto a worker thread and leave it a
table scan. Fixing only (2) would leave synchronous sqlite on the loop, which
is wrong at any speed. Both.
"""

from __future__ import annotations

import inspect

import pytest

# R-F3784/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def test_the_staleness_gate_runs_off_the_event_loop():
    """THE regression. The call that appeared on the blocked main thread must
    not be awaited inline."""
    from aria_service import main

    src = module_source(main)
    # The gate must be handed to a worker thread, like the refresh it guards.
    assert "to_thread(_ss.newest_entry_refresh" in src or \
           "to_thread(\n            _ss.newest_entry_refresh" in src, (
        "newest_entry_refresh() is still called inline on the event loop — it "
        "is a synchronous sqlite3 scan and it is what the live wedge stack "
        "caught blocking the loop")
    assert "newest = _ss.newest_entry_refresh()" not in src, (
        "the direct, on-loop call is still present")


def test_last_refreshed_is_indexed_so_max_is_not_a_table_scan(tmp_path, monkeypatch):
    """MAX() over an unindexed column scans every row. On the live box that is
    24,953 sanctions entries, per refresh tick."""
    import sqlite3

    from aria_service.intel.sanctions_canonical import store as ss

    db = tmp_path / "sanctions.db"
    # connect() resolves through _db_path(), which reads this env var. There is
    # no module-level _DB_PATH — patching one silently does nothing and the
    # test then writes to the REAL store, which is how the first cut of this
    # file hit a UNIQUE violation from its own earlier run.
    monkeypatch.setenv("ARIA_SANCTIONS_CANONICAL_DB", str(db))
    with ss.connect() as conn:          # creates the schema
        conn.execute(
            "INSERT INTO entries (source, source_uid, formatted_name, "
            "normalised_name, last_refreshed) VALUES ('ofac','u1','X','x',1.0)")
        conn.commit()

    with sqlite3.connect(str(db)) as probe:
        plan = probe.execute(
            "EXPLAIN QUERY PLAN SELECT MAX(last_refreshed) FROM entries"
        ).fetchall()
    text = " ".join(str(r) for r in plan).lower()
    assert "scan" not in text or "index" in text, (
        f"MAX(last_refreshed) still scans the table — query plan: {plan}")


def test_the_gate_still_returns_the_true_row_age(tmp_path, monkeypatch):
    """R-F2417's property must survive: this reflects the TRUE age of the rows
    being screened, which is what stops a sustained refresh outage over stale
    rows reading as 'freshness unknown' and then as a false clean."""
    from aria_service.intel.sanctions_canonical import store as ss

    db = tmp_path / "s.db"
    monkeypatch.setenv("ARIA_SANCTIONS_CANONICAL_DB", str(db))
    assert ss.newest_entry_refresh() is None, "empty store must report None"

    with ss.connect() as conn:
        conn.execute(
            "INSERT INTO entries (source, source_uid, formatted_name, "
            "normalised_name, last_refreshed) VALUES ('ofac','a1','A','a',100.0)")
        conn.execute(
            "INSERT INTO entries (source, source_uid, formatted_name, "
            "normalised_name, last_refreshed) VALUES ('un','b1','B','b',250.0)")
        conn.commit()

    assert ss.newest_entry_refresh() == 250.0
    assert ss.newest_entry_refresh(source="ofac") == 100.0
