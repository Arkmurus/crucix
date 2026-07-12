"""R-F2570 — canonical sanctions partial-drift floor (never-false-clean).

A refresh that parses implausibly FEW rows vs the prior list (partial schema drift) must
NOT overwrite a healthy sanctions list — overwriting it would screen the dropped sanctioned
entities as CLEAR (a false clean). Two layers:
  (1) replace_source refuses the drifted refresh (keeps last-known-good rows).
  (2) check_sanctions H2 downgrades a would-be CLEAR when a source is implausibly thin vs
      its own last successful refresh.
"""
from __future__ import annotations

import time

import pytest

from aria_service.intel.sanctions_canonical.normalise import normalise_name


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    db_path = tmp_path / "sanctions_canonical_rf2570.db"
    monkeypatch.setenv("ARIA_SANCTIONS_CANONICAL_DB", str(db_path))
    monkeypatch.setenv("ARIA_SANCTIONS_MAX_STALENESS_DAYS", "30")
    monkeypatch.delenv("ARIA_SANCTIONS_DRIFT_MIN_FRACTION", raising=False)  # default 0.5
    yield
    if db_path.exists():
        db_path.unlink()


def _rows(source: str, n: int):
    out = []
    for i in range(n):
        nm = f"ENTITY {source.upper()} {i}"
        out.append({
            "source_uid": f"{source}:{i}",
            "formatted_name": nm,
            "normalised_name": normalise_name(nm),
            "entity_type": "Entity",
            "countries": [], "addresses": [],
            "aliases": [{"formatted": nm, "normalised": normalise_name(nm), "alias_type": "primary"}],
            "programs": [], "designation_at": None, "raw_excerpt": "",
        })
    return out


# ── layer 1: replace_source refuses drift, preserves last-known-good ─────────
def test_replace_source_refuses_drift_and_preserves_good():
    from aria_service.intel.sanctions_canonical import store
    store.replace_source("ofac_sdn", _rows("ofac_sdn", 10))
    assert store.count_entries("ofac_sdn") == 10
    with pytest.raises(store.CoverageDriftError):
        store.replace_source("ofac_sdn", _rows("ofac_sdn", 2))   # 2 < 50% of 10
    # the drifted refresh was REFUSED — the healthy 10 rows are still there
    assert store.count_entries("ofac_sdn") == 10


def test_replace_source_first_load_has_no_floor():
    from aria_service.intel.sanctions_canonical import store
    store.replace_source("ofac_sdn", _rows("ofac_sdn", 3))       # prior 0 → exempt
    assert store.count_entries("ofac_sdn") == 3


def test_replace_source_accepts_healthy_refresh():
    from aria_service.intel.sanctions_canonical import store
    store.replace_source("ofac_sdn", _rows("ofac_sdn", 10))
    store.replace_source("ofac_sdn", _rows("ofac_sdn", 8))       # 8 >= 50% of 10
    assert store.count_entries("ofac_sdn") == 8


# ── layer 2: check_sanctions downgrades a thin store (not CLEAR) ─────────────
def test_check_sanctions_thin_store_is_not_clear():
    from aria_service.intel.sanctions_canonical import store
    from aria_service.intel.sanctions_canonical.lookup import check_sanctions
    # both sources present but ofac is implausibly thin vs its last healthy refresh (10000)
    store.replace_source("ofac_sdn", _rows("ofac_sdn", 2))
    store.replace_source("eu_consolidated", _rows("eu_consolidated", 2))
    now = time.time()
    store.record_refresh("ofac_sdn", now - 1, now, rows_loaded=10000, success=True)
    store.record_refresh("eu_consolidated", now - 1, now, rows_loaded=2, success=True)
    r = check_sanctions("Some Unrelated Company LLC", sources=["ofac_sdn"])
    assert r["verdict"] == "INSUFFICIENT_DATA", f"thin store must not be CLEAR, got {r['verdict']}"
    assert r.get("reason") == "sanctions_partial_coverage"


def test_check_sanctions_healthy_store_still_clears():
    """No over-correction: a store whose count matches its refresh baseline still CLEARs."""
    from aria_service.intel.sanctions_canonical import store
    from aria_service.intel.sanctions_canonical.lookup import check_sanctions
    store.replace_source("ofac_sdn", _rows("ofac_sdn", 4))
    store.replace_source("eu_consolidated", _rows("eu_consolidated", 4))
    now = time.time()
    store.record_refresh("ofac_sdn", now - 1, now, rows_loaded=4, success=True)
    store.record_refresh("eu_consolidated", now - 1, now, rows_loaded=4, success=True)
    r = check_sanctions("Some Unrelated Company LLC")
    assert r["verdict"] == "CLEAR", f"healthy store + no match must CLEAR, got {r['verdict']}"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
