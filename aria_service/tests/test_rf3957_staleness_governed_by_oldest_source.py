"""R-F3957 / C-47 — a 400-day-stale sanctions list screened CLEAN, because the
freshness gate aggregated with MAX.

The H1 staleness gate asked for the age of the *freshest* successful refresh
across the in-scope sources:

    lookup.py:633
        age = _freshest_refresh_age_seconds(in_scope)

so the stalest source governed nothing. One healthy list refreshed a second ago
made every other in-scope list look current, however old it actually was.
Reproduced:

    OFAC SDN last refreshed 400 days ago; EU refreshed 1 second ago
      freshest-refresh age : 0.0 days      <- what the gate read
      true OFAC data age   : 400.0 days
      VERDICT: CLEAR   freshness_age_days: None

For a never-false-clean gate the correct aggregation is the OLDEST in-scope
source: a screen is only as current as the least current list it consulted.
The row-count check (H2) cannot cover for it, because rows persist — a list
that stopped updating a year ago still has all of last year's rows.

Compounding, and fixed here too: a CLEAR verdict carried no age at all, so a
reader could not tell a screen against one-hour-old data from one against
29-day-old data. `freshness_age_days` is now reported on every verdict that
consulted the store, not only on the one that failed.

NOT fixed here, deliberately: the 30-day threshold against a ~20-hour refresh
cadence is loose. Tightening a threshold is the band-aid §1 forbids while the
aggregation is wrong — a tighter threshold on the wrong number is still the
wrong number. Revisit once this has run in production.
"""
from __future__ import annotations

import json
import os
import tempfile
import time

_TMPDIR = tempfile.mkdtemp(prefix="rf3957_")
os.environ["ARIA_SANCTIONS_CANONICAL_DB"] = os.path.join(_TMPDIR, "canon.db")
os.environ["ARIA_SANCTIONS_MAX_STALENESS_DAYS"] = "30"

from aria_service.intel.sanctions_canonical import store, lookup  # noqa: E402

_OFAC = "ofac_sdn"
_EU = "eu_consolidated"
# Shares no tokens with anything seeded, so it can only ever be a would-be CLEAR.
_QUERY = "Zzqx Vellamin Torbraith"


def _reset():
    with store.connect() as conn:
        conn.execute("DELETE FROM entries")
        conn.execute("DELETE FROM refresh_log")


def _seed(source: str, name: str, last_refreshed_ts: float):
    with store.connect() as conn:
        conn.execute(
            """INSERT INTO entries
               (source, source_uid, formatted_name, normalised_name, entity_type,
                countries, addresses, aliases, programs, designation_at,
                raw_excerpt, last_refreshed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (source, f"uid-{source}", name, name.lower(), "Entity",
             json.dumps([]), json.dumps([]), json.dumps([]), json.dumps([]),
             None, "", last_refreshed_ts),
        )


# ── the aggregation itself ───────────────────────────────────────────────────

def test_oldest_source_governs_not_the_freshest():
    now = time.time()
    _reset()
    _seed(_OFAC, "Acme Trading Ltd", now - 400 * 86400)
    _seed(_EU, "Beta Holdings SA", now - 60)
    store.record_refresh(_OFAC, started_at=now - 400 * 86400,
                         finished_at=now - 400 * 86400 + 1,
                         rows_loaded=1, success=True, error="")
    store.record_refresh(_EU, started_at=now - 60, finished_at=now - 59,
                         rows_loaded=1, success=True, error="")

    age = lookup._stalest_refresh_age_seconds([_OFAC, _EU])
    assert age is not None
    assert age / 86400.0 > 399, (
        f"the gate is still reading the freshest source: {age / 86400.0:.1f} days"
    )


def test_the_400_day_list_no_longer_screens_clean():
    """The capability test — this is the exact reproduction from the report."""
    now = time.time()
    _reset()
    _seed(_OFAC, "Acme Trading Ltd", now - 400 * 86400)
    _seed(_EU, "Beta Holdings SA", now - 60)
    store.record_refresh(_OFAC, started_at=now - 400 * 86400,
                         finished_at=now - 400 * 86400 + 1,
                         rows_loaded=1, success=True, error="")
    store.record_refresh(_EU, started_at=now - 60, finished_at=now - 59,
                         rows_loaded=1, success=True, error="")

    res = lookup.check_sanctions(_QUERY, sources=[_OFAC, _EU])
    assert res["verdict"] != "CLEAR", (
        f"a 400-day-stale OFAC list screened CLEAR because EU was fresh: {res}"
    )
    assert res["verdict"] == "INSUFFICIENT_DATA"
    assert res.get("reason") == "sanctions_data_stale"
    assert res.get("freshness_age_days", 0) > 399


def test_all_sources_fresh_still_clears():
    """The gate must still be able to pass — otherwise it is not a gate."""
    now = time.time()
    _reset()
    _seed(_OFAC, "Acme Trading Ltd", now - 3600)
    _seed(_EU, "Beta Holdings SA", now - 3600)
    for s in (_OFAC, _EU):
        store.record_refresh(s, started_at=now - 3600, finished_at=now - 3599,
                             rows_loaded=1, success=True, error="")

    res = lookup.check_sanctions(_QUERY, sources=[_OFAC, _EU])
    assert res["verdict"] == "CLEAR", res


def test_single_source_scope_is_unaffected():
    """With one in-scope source, oldest and freshest are the same thing."""
    now = time.time()
    _reset()
    _seed(_OFAC, "Acme Trading Ltd", now - 3600)
    store.record_refresh(_OFAC, started_at=now - 3600, finished_at=now - 3599,
                         rows_loaded=1, success=True, error="")
    res = lookup.check_sanctions(_QUERY, sources=[_OFAC])
    assert res["verdict"] == "CLEAR", res


# ── a source with no successful refresh must not be skipped into silence ─────

def test_a_source_that_never_refreshed_successfully_falls_back_to_data_age():
    """The residual hole the MAX aggregation hid.

    Skipping non-success rows is correct (R-F2373), but skipping the SOURCE
    entirely means a list that has only ever failed contributes nothing to the
    age — so a second, healthy list clears it. Its true data age must count.
    """
    now = time.time()
    _reset()
    _seed(_OFAC, "Acme Trading Ltd", now - 200 * 86400)   # genuinely 200d old
    _seed(_EU, "Beta Holdings SA", now - 60)
    # OFAC has ONLY failed attempts — no successful refresh row at all.
    store.record_refresh(_OFAC, started_at=now - 60, finished_at=now - 59,
                         rows_loaded=0, success=False, error="feed 503")
    store.record_refresh(_EU, started_at=now - 60, finished_at=now - 59,
                         rows_loaded=1, success=True, error="")

    res = lookup.check_sanctions(_QUERY, sources=[_OFAC, _EU])
    assert res["verdict"] != "CLEAR", (
        f"a list that has never refreshed successfully was cleared by its "
        f"healthy neighbour: {res}"
    )


# ── unknown freshness stays a SOFT signal ────────────────────────────────────

def test_direct_seeded_store_with_no_refresh_metadata_still_clears():
    """R-F2373's soft-signal rule survives: no metadata is not evidence of age."""
    now = time.time()
    _reset()
    _seed(_OFAC, "Acme Trading Ltd", now - 3600)
    res = lookup.check_sanctions(_QUERY, sources=[_OFAC])
    assert res["verdict"] == "CLEAR", res


def test_stalest_returns_none_when_nothing_is_known():
    _reset()
    assert lookup._stalest_refresh_age_seconds([_OFAC, _EU]) is None


# ── a CLEAR must state how current it is ─────────────────────────────────────

def test_clear_reports_its_own_freshness():
    now = time.time()
    _reset()
    _seed(_OFAC, "Acme Trading Ltd", now - 2 * 86400)
    store.record_refresh(_OFAC, started_at=now - 2 * 86400,
                         finished_at=now - 2 * 86400 + 1,
                         rows_loaded=1, success=True, error="")
    res = lookup.check_sanctions(_QUERY, sources=[_OFAC])
    assert res["verdict"] == "CLEAR"
    assert res.get("freshness_age_days") is not None, (
        "a CLEAR carried no age at all, so a screen against 29-day-old data "
        "was indistinguishable from one against one-hour-old data"
    )
    assert 1.5 < res["freshness_age_days"] < 2.5
    # Read the CONFIGURED threshold, not a literal: sibling test modules set
    # ARIA_SANCTIONS_MAX_STALENESS_DAYS at import time, so a hardcoded 30 here
    # would fail on collection order rather than on behaviour.
    assert res.get("max_staleness_days") == round(
        lookup._max_staleness_seconds() / 86400.0, 1,
    )


def test_the_gate_no_longer_calls_the_freshest_helper():
    from ._source_probe import function_code
    src = function_code(lookup, "check_sanctions")
    assert "_freshest_refresh_age_seconds(" not in src, (
        "the H1 staleness gate is reading the freshest source again — the "
        "stalest list would stop governing the verdict"
    )
    assert "_stalest_refresh_age_seconds(" in src
