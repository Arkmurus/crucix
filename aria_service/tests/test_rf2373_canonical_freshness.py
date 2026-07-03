"""R-F2373 — canonical check_sanctions never-false-clean gates (H1 + H2).

These drive the REAL `check_sanctions` function (not a helper) and assert the
user-visible verdict, per CLAUDE.md §3c. Written to FAIL before the fix / PASS
after.

H1 — staleness gate (lookup.py verdict block):
  A store whose freshest SUCCESSFUL refresh is older than
  ARIA_SANCTIONS_MAX_STALENESS_DAYS (default 30) must NOT return an authoritative
  CLEAR — old rows persisting for weeks read as a stale "clean". Downgrade a
  would-be CLEAR to INSUFFICIENT_DATA + source_unavailable + reason
  "sanctions_data_stale". Must NEVER downgrade a fresh clean.

H2 — partial-coverage gate:
  When `sources is None`, readiness previously summed count_entries() across ALL
  sources, so OFAC-loaded + EU-empty screened an EU-only entity CLEAR. Require
  each EXPECTED registry source (ofac_sdn + eu_consolidated) to hold rows once
  the real refresh pipeline has run; a missing source → INSUFFICIENT_DATA +
  reason "sanctions_partial_coverage" + coverage_gap listing the empty source(s).
"""
from __future__ import annotations

import time

import pytest

from aria_service.intel.sanctions_canonical.normalise import normalise_name


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    """Fresh empty canonical-sanctions SQLite per test (mirrors R-F526/R-F2159)."""
    db_path = tmp_path / "sanctions_canonical_rf2373.db"
    monkeypatch.setenv("ARIA_SANCTIONS_CANONICAL_DB", str(db_path))
    # Pin the staleness threshold so the test is deterministic regardless of
    # any ambient env override.
    monkeypatch.setenv("ARIA_SANCTIONS_MAX_STALENESS_DAYS", "30")
    yield
    if db_path.exists():
        db_path.unlink()


def _seed(source: str, refreshed_ago_days: float = 0.0, success: bool = True):
    """Seed ONE unrelated row for `source` AND record a refresh_log entry so the
    real-refresh-pipeline gate (`_has_refresh_metadata`) sees production-shaped
    metadata. `refreshed_ago_days` controls the freshness of the recorded
    refresh."""
    from aria_service.intel.sanctions_canonical import store
    nm = f"ACME {source.upper()} HOLDINGS"
    rows = [{
        "source_uid": f"{source}:test:1",
        "formatted_name": nm,
        "normalised_name": normalise_name(nm),
        "entity_type": "Entity",
        "countries": ["Iran"],
        "addresses": ["Tehran, Iran"],
        "aliases": [{"formatted": nm, "normalised": normalise_name(nm),
                     "alias_type": "primary"}],
        "programs": ["IRAN"],
        "designation_at": None,
        "raw_excerpt": "test-seed unrelated entry",
    }]
    store.replace_source(source, rows)
    ts = time.time() - refreshed_ago_days * 86400.0
    store.record_refresh(source, started_at=ts - 1.0, finished_at=ts,
                         rows_loaded=len(rows), success=success)


# ───────────────────────────── H1 — staleness ──────────────────────────────

def test_rf2373_stale_store_downgrades_would_be_clear():
    """THE H1 regression: both expected sources loaded but refreshed 60 days ago
    (> 30d threshold) → a no-match name must NOT return CLEAR."""
    from aria_service.intel.sanctions_canonical import check_sanctions
    _seed("ofac_sdn", refreshed_ago_days=60)
    _seed("eu_consolidated", refreshed_ago_days=60)
    r = check_sanctions("Globex International Trading Ltd", jurisdiction="Cyprus")
    assert r["verdict"] == "INSUFFICIENT_DATA", (
        f"stale store must downgrade CLEAR, got {r['verdict']} — a store not "
        "refreshed for weeks reading as clean is a stale false-negative"
    )
    assert r.get("source_unavailable") is True
    assert r.get("reason") == "sanctions_data_stale", r.get("reason")


def test_rf2373_fresh_store_still_clears():
    """H1 must ONLY downgrade a stale clean — a fresh clean is preserved."""
    from aria_service.intel.sanctions_canonical import check_sanctions
    _seed("ofac_sdn", refreshed_ago_days=0)
    _seed("eu_consolidated", refreshed_ago_days=0)
    r = check_sanctions("Globex International Trading Ltd", jurisdiction="Cyprus")
    assert r["verdict"] == "CLEAR", (
        f"fresh loaded store + no match must stay CLEAR, got {r['verdict']}"
    )
    assert r.get("source_unavailable") is False
    assert "reason" not in r


def test_rf2373_stale_never_touches_a_real_hit():
    """A staleness downgrade must NEVER suppress a real match (only downgrades a
    would-be CLEAR, never a REVIEW/HARD_STOP)."""
    from aria_service.intel.sanctions_canonical import check_sanctions
    _seed("ofac_sdn", refreshed_ago_days=60)
    _seed("eu_consolidated", refreshed_ago_days=60)
    r = check_sanctions("ACME OFAC_SDN HOLDINGS", jurisdiction="Iran")
    assert r["verdict"] in ("HARD_STOP", "REVIEW"), (
        f"a seeded matching entity must still hit despite stale data, "
        f"got {r['verdict']}"
    )
    assert r["matches"], "expected at least one match"


# ────────────────────────── H2 — partial coverage ──────────────────────────

def test_rf2373_partial_coverage_downgrades_and_lists_gap():
    """THE H2 regression: OFAC loaded (fresh) but EU empty → an EU-only entity
    must NOT screen CLEAR. Verdict INSUFFICIENT_DATA + coverage_gap listing the
    empty expected source."""
    from aria_service.intel.sanctions_canonical import check_sanctions, store
    _seed("ofac_sdn", refreshed_ago_days=0)
    assert store.count_entries("eu_consolidated") == 0
    r = check_sanctions("Globex International Trading Ltd", jurisdiction="Cyprus")
    assert r["verdict"] == "INSUFFICIENT_DATA", (
        f"partial coverage must downgrade CLEAR, got {r['verdict']} — an EU-only "
        "entity screening clean against an OFAC-only store is a false-negative"
    )
    assert r.get("source_unavailable") is True
    assert r.get("reason") == "sanctions_partial_coverage", r.get("reason")
    assert "eu_consolidated" in (r.get("coverage_gap") or []), r.get("coverage_gap")


def test_rf2373_full_coverage_fresh_clears():
    """Both expected sources present + fresh → legitimate CLEAR (no coverage
    gap, no staleness downgrade)."""
    from aria_service.intel.sanctions_canonical import check_sanctions
    _seed("ofac_sdn", refreshed_ago_days=0)
    _seed("eu_consolidated", refreshed_ago_days=0)
    r = check_sanctions("Globex International Trading Ltd", jurisdiction="Cyprus")
    assert r["verdict"] == "CLEAR", r["verdict"]
    assert not r.get("coverage_gap")
