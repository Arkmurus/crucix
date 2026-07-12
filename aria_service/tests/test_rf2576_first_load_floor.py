"""R-F2576 — absolute first-load sanctions floor.

The R-F2570 self-calibrating floor exempts prior=0 (first load), so a broken FIRST load on
a fresh container would commit thin data and screen sanctioned entities CLEAR. The download
pipeline (load_from_file) now passes an absolute per-source floor to replace_source; direct
seeds (default floor 0) are unaffected so test fixtures keep working.
"""
from __future__ import annotations

import pytest

from aria_service.intel.sanctions_canonical.normalise import normalise_name


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    monkeypatch.setenv("ARIA_SANCTIONS_CANONICAL_DB", str(tmp_path / "rf2576.db"))
    yield


def _rows(source: str, n: int):
    out = []
    for i in range(n):
        nm = f"ENTITY {source.upper()} {i}"
        out.append({
            "source_uid": f"{source}:{i}", "formatted_name": nm,
            "normalised_name": normalise_name(nm), "entity_type": "Entity",
            "countries": [], "addresses": [],
            "aliases": [{"formatted": nm, "normalised": normalise_name(nm), "alias_type": "primary"}],
            "programs": [], "designation_at": None, "raw_excerpt": "",
        })
    return out


def test_first_load_below_absolute_floor_is_refused():
    from aria_service.intel.sanctions_canonical import store
    with pytest.raises(store.CoverageDriftError):
        store.replace_source("ofac_sdn", _rows("ofac_sdn", 3), absolute_floor=5000)
    assert store.count_entries("ofac_sdn") == 0     # thin first load NOT committed


def test_first_load_without_floor_is_allowed():
    # default absolute_floor=0 → direct-seeded fixtures unaffected
    from aria_service.intel.sanctions_canonical import store
    store.replace_source("ofac_sdn", _rows("ofac_sdn", 3))
    assert store.count_entries("ofac_sdn") == 3


def test_load_above_absolute_floor_is_allowed():
    from aria_service.intel.sanctions_canonical import store
    store.replace_source("ofac_sdn", _rows("ofac_sdn", 6000), absolute_floor=5000)
    assert store.count_entries("ofac_sdn") == 6000


def test_loaders_wire_an_absolute_floor():
    from aria_service.intel.sanctions_canonical import ofac_sdn, eu_consolidated
    assert ofac_sdn._MIN_EXPECTED_ROWS >= 5000     # OFAC realistically ~19k
    assert eu_consolidated._MIN_EXPECTED_ROWS >= 500  # EU realistically ~6k


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
