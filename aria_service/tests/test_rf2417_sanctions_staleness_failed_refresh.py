"""R-F2417 — canonical sanctions staleness gate must not be DEFEATED by a
sustained refresh-failure over stale data.

Pre-fix: `_freshest_refresh_age_seconds` skips every non-success refresh_log row
(correct), but when EVERY in-scope source's latest refresh ATTEMPT failed it
returned None → the H1 gate treated freshness as 'unknown' (soft) → a would-be
CLEAR passed on 40-day-stale, actively-failing data. Post-fix the gate falls
back to the TRUE data age (`entries.last_refreshed`) and downgrades to
INSUFFICIENT_DATA / sanctions_data_stale.
"""
import json
import os
import tempfile
import time

_TMPDIR = tempfile.mkdtemp(prefix="rf2417_")
os.environ["ARIA_SANCTIONS_CANONICAL_DB"] = os.path.join(_TMPDIR, "canon.db")
os.environ["ARIA_SANCTIONS_MAX_STALENESS_DAYS"] = "7"

from aria_service.intel.sanctions_canonical import store, lookup  # noqa: E402

_SRC = "ofac_sdn"  # must be in _expected_sources()
_QUERY = "Zzqx Vellamin Torbraith"  # shares no tokens with the seeded entry


def _seed_entry(last_refreshed_ts: float):
    with store.connect() as conn:
        conn.execute("DELETE FROM entries")
        conn.execute("DELETE FROM refresh_log")
        conn.execute(
            """INSERT INTO entries
               (source, source_uid, formatted_name, normalised_name, entity_type,
                countries, addresses, aliases, programs, designation_at, raw_excerpt, last_refreshed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_SRC, "uid-1", "Acme Trading Ltd", "acme trading ltd", "Entity",
             json.dumps([]), json.dumps([]), json.dumps([]), json.dumps([]), None, "", last_refreshed_ts),
        )


def test_sustained_refresh_failure_over_stale_data_is_not_clean():
    now = time.time()
    forty_days = now - 40 * 86400
    _seed_entry(last_refreshed_ts=forty_days)          # data is genuinely 40d old
    # Latest refresh ATTEMPT failed (success=False) over that stale data:
    store.record_refresh(_SRC, started_at=now - 60, finished_at=now - 59,
                         rows_loaded=0, success=False, error="feed 503")

    res = lookup.check_sanctions(_QUERY, sources=[_SRC])
    assert res["verdict"] != "CLEAR", f"stale+failing data must NOT be CLEAR, got {res}"
    assert res["verdict"] == "INSUFFICIENT_DATA"
    assert res.get("reason") == "sanctions_data_stale", res


def test_fresh_successful_refresh_still_clears():
    now = time.time()
    _seed_entry(last_refreshed_ts=now - 3600)          # 1h-old data
    store.record_refresh(_SRC, started_at=now - 3600, finished_at=now - 3599,
                         rows_loaded=1, success=True, error="")
    res = lookup.check_sanctions(_QUERY, sources=[_SRC])
    assert res["verdict"] == "CLEAR", f"fresh screened data should clear, got {res}"


def test_fresh_data_but_last_attempt_failed_still_clears():
    """Guard against over-flagging: data refreshed OK an hour ago, today's
    attempt failed — real data age is 1h, so it must still CLEAR (the fix uses
    true data age, not 'any failure → stale')."""
    now = time.time()
    _seed_entry(last_refreshed_ts=now - 3600)
    store.record_refresh(_SRC, started_at=now - 7200, finished_at=now - 7199,
                         rows_loaded=1, success=True, error="")   # succeeded 2h ago
    store.record_refresh(_SRC, started_at=now - 60, finished_at=now - 59,
                         rows_loaded=0, success=False, error="feed 503")  # failed just now
    res = lookup.check_sanctions(_QUERY, sources=[_SRC])
    # freshest SUCCESSFUL refresh is 2h old < 7d → CLEAR (fallback not even needed)
    assert res["verdict"] == "CLEAR", f"recently-refreshed data must clear despite a later failed attempt, got {res}"


if __name__ == "__main__":
    test_sustained_refresh_failure_over_stale_data_is_not_clean()
    print("PASS test_sustained_refresh_failure_over_stale_data_is_not_clean")
    test_fresh_successful_refresh_still_clears()
    print("PASS test_fresh_successful_refresh_still_clears")
    test_fresh_data_but_last_attempt_failed_still_clears()
    print("PASS test_fresh_data_but_last_attempt_failed_still_clears")
    print("ALL PASS")
