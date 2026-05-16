"""R-F581 (2026-05-16) — ECCN/Wassenaar lookup table tests."""
from __future__ import annotations

import pytest


def test_dataset_loads():
    from aria_service.intel.sources.eccn_lookup import _load_data
    data = _load_data()
    assert isinstance(data, dict)
    assert "categories" in data
    assert len(data["categories"]) >= 15, "seed dataset should have ≥15 entries"


def test_dataset_version_string():
    from aria_service.intel.sources.eccn_lookup import dataset_version
    v = dataset_version()
    assert isinstance(v, str)
    assert v != ""


def test_list_all_eccns_includes_core_defence_categories():
    from aria_service.intel.sources.eccn_lookup import list_all_eccns
    codes = set(list_all_eccns())
    for must in ("9A010", "5A001", "6A005", "6A008", "7A003",
                 "ML1", "ML4", "ML10", "ML15"):
        assert must in codes, f"ECCN {must} missing from seed dataset"


def test_lookup_by_keyword_drone():
    """Capability: a description containing 'drone' must surface 9A010
    (UAV ECCN) as the top hit."""
    from aria_service.intel.sources.eccn_lookup import lookup_by_keyword
    hits = lookup_by_keyword("Bayraktar TB2 drone with payload capacity")
    assert len(hits) >= 1
    eccns = [h["eccn"] for h in hits]
    assert "9A010" in eccns
    top = hits[0]
    assert top["tier"] == "1a"
    assert top["source"] == "bis_ccl_seed_rf581"
    assert "honesty_note" in top
    assert top["match_count"] >= 1


def test_lookup_by_keyword_radar():
    from aria_service.intel.sources.eccn_lookup import lookup_by_keyword
    hits = lookup_by_keyword("AESA radar fire-control system")
    eccns = [h["eccn"] for h in hits]
    assert "6A008" in eccns


def test_lookup_by_keyword_munitions():
    from aria_service.intel.sources.eccn_lookup import lookup_by_keyword
    hits = lookup_by_keyword("anti-tank guided missile ATGM")
    eccns = [h["eccn"] for h in hits]
    assert "ML4" in eccns


def test_lookup_by_keyword_uav_components_distinct_from_uavs():
    from aria_service.intel.sources.eccn_lookup import lookup_by_keyword
    hits = lookup_by_keyword("ground control station for UAV operations")
    eccns = [h["eccn"] for h in hits]
    assert "9A012" in eccns  # GCS goes under 9A012


def test_lookup_word_boundary_no_false_positives():
    """Capability: keyword 'sar' must NOT match 'sarcastic' or 'sariwon'
    via substring overlap."""
    from aria_service.intel.sources.eccn_lookup import lookup_by_keyword
    # No SAR/synthetic-aperture content in this text
    hits = lookup_by_keyword("This is a sarcastic remark about the bid.")
    sar_hits = [h for h in hits if "sar" in h.get("matched_keywords", [])]
    assert sar_hits == [], (
        "R-F581: 'sar' (synthetic-aperture-radar) keyword should NOT "
        "match 'sarcastic' as substring."
    )


def test_lookup_skips_empty_input():
    from aria_service.intel.sources.eccn_lookup import lookup_by_keyword
    assert lookup_by_keyword("") == []
    assert lookup_by_keyword("   ") == []


def test_lookup_by_keyword_respects_limit():
    from aria_service.intel.sources.eccn_lookup import lookup_by_keyword
    hits = lookup_by_keyword("missile drone radar gun aircraft", limit=2)
    assert len(hits) <= 2


def test_lookup_by_keyword_sort_by_match_count():
    """Capability: multi-keyword match outranks single-keyword match."""
    from aria_service.intel.sources.eccn_lookup import lookup_by_keyword
    # 'aircraft' matches ML10; 'thermal imager' matches ML15.
    # If both appear, both should be in results — and the one with
    # MORE matched keywords ranks first.
    hits = lookup_by_keyword("military helicopter aircraft with thermal imager")
    assert len(hits) >= 2
    # Top hit should have match_count >= 1 (definitely);
    # whichever has more matches wins
    assert hits[0]["match_count"] >= hits[-1]["match_count"]


def test_lookup_by_eccn_returns_full_entry():
    from aria_service.intel.sources.eccn_lookup import lookup_by_eccn
    entry = lookup_by_eccn("9A010")
    assert entry is not None
    assert entry["eccn"] == "9A010"
    assert "uav" in (kw.lower() for kw in entry.get("keywords", []))
    assert entry["regime"] == "EAR"
    assert "bis.doc.gov" in entry["reference_url"]


def test_lookup_by_eccn_case_insensitive():
    from aria_service.intel.sources.eccn_lookup import lookup_by_eccn
    a = lookup_by_eccn("9a010")
    b = lookup_by_eccn("9A010")
    assert a is not None
    assert a == b


def test_lookup_by_eccn_unknown_returns_none():
    from aria_service.intel.sources.eccn_lookup import lookup_by_eccn
    assert lookup_by_eccn("0A999") is None
    assert lookup_by_eccn("") is None


def test_every_eccn_entry_carries_reference_url():
    """Capability: per clause 3, every ECCN hit must include a primary
    BIS/Wassenaar URL the operator can cite."""
    from aria_service.intel.sources.eccn_lookup import _load_data
    data = _load_data()
    bad: list[str] = []
    for eccn, entry in (data.get("categories") or {}).items():
        url = entry.get("reference_url", "")
        if not url.startswith("https://"):
            bad.append(f"{eccn} has bad reference_url: {url!r}")
    assert not bad, f"R-F581: missing/bad reference URLs: {bad}"


def test_every_eccn_entry_has_keywords():
    from aria_service.intel.sources.eccn_lookup import _load_data
    data = _load_data()
    empty = [
        e for e, entry in (data.get("categories") or {}).items()
        if not (entry.get("keywords") or [])
    ]
    assert not empty, f"R-F581: ECCNs with no keywords (unsearchable): {empty}"
