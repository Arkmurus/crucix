"""R-F1947 — gate-#2 floor (osint×east_africa) credit-chain fix.

The reading_session credit step only lifts a region cell's mastery when
`_region in detect_regions(explored_content)` (student.py ~1357). The east_africa
pattern matched only ethiopia/somalia/eac/etc., while Kenya/Nairobi/Tanzania/
Uganda were miscategorised under central_africa — so the floor cell's OWN query
("osint Ethiopia Kenya East Africa …") returned Kenya-heavy results that tagged
central_africa, `_grounded` stayed False, `update_regional_mastery` never fired,
and the gate-#2 floor (≈0.269) was frozen no matter how often it was visited.

This was the real blocker R-F1925b missed (that change was a no-op — the floor
cell was already in the target set; the failure is in CREDIT, not SELECTION).

These drive the real detect_regions used by the credit gate.
"""
from __future__ import annotations

import pathlib

from aria_service.intel.student import detect_regions


def test_core_east_african_states_map_to_east_africa():
    for text in [
        "Kenya defence procurement in Nairobi",
        "Mombasa port security tender",
        "Tanzania military budget Dodoma",
        "Uganda army Kampala contract",
        "Ethiopia Addis Ababa procurement",
        "East African Community EAC defence cooperation",
    ]:
        regions = detect_regions(text)
        assert "east_africa" in regions, f"{text!r} should geo-confirm east_africa, got {regions}"


def test_floor_cell_query_now_geo_confirms():
    """The osint×east_africa cell's own query phrase content must satisfy the
    R-F1744 credit gate (_region in detect_regions), or the floor never lifts."""
    # mirrors _REGION_QUERY_PHRASE['east_africa'] = 'Ethiopia Kenya East Africa'
    assert "east_africa" in detect_regions("osint Ethiopia Kenya East Africa defence procurement 2026")


def test_central_africa_not_regressed():
    for text in ["DRC Kinshasa M23 conflict", "Democratic Republic of the Congo", "Brazzaville Congo"]:
        regions = detect_regions(text)
        assert "central_africa" in regions, f"{text!r} should still be central_africa, got {regions}"
    # and a pure-Kenya text must NOT be tagged central_africa anymore
    assert "central_africa" not in detect_regions("Kenya Nairobi")


def test_credit_instrumentation_present_sourcepin():
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "aria_service" / "intel" / "student.py").read_text(encoding="utf-8", errors="ignore")
    # the not-credited diagnostic branch (visited but no mastery lift) must exist
    assert "R-F1947 gate-2 cell" in src and "NOT credited" in src
