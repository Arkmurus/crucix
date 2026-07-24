"""R-F2964 (C2) — query/gate symmetry: genuine region-name literals in detect_regions.

The reading loop searches region NAMES ("…Central Africa", "…Southeast Asia") but
the credit gate detect_regions recognised only COUNTRY tokens, so region-named
content failed to ground and the cell froze. This adds the region-name literals.

detect_regions is on the LIVE chat/DD mastery-write path (aria_engine.py), so the
critical tests are the NEGATIVE CONTROLS: a new literal must not bleed into a
neighbouring region (that would be a false-positive credit = an honesty violation).
"""
from __future__ import annotations

import pytest

from aria_service.intel.student import detect_regions


@pytest.mark.parametrize("text,expected", [
    ("defence procurement Central Africa 2026", "central_africa"),
    ("West Africa small-arms flows", "west_africa"),
    ("East Africa maritime security", "east_africa"),
    ("North Africa Sahel operations", "north_africa"),
    ("southern Africa SADC standby force", "southern_africa"),
    ("South Asia missile programme", "south_asia"),
    ("Southeast Asia naval modernization", "southeast_asia"),
    ("South-East Asia procurement", "southeast_asia"),
    ("Latin America defence market", "latam_non_lusophone"),
    ("Europe PESCO joint procurement", "europe"),
])
def test_rf2964_region_names_now_ground(text, expected):
    assert expected in detect_regions(text), f"{expected} should ground from {text!r}"


@pytest.mark.parametrize("text,must_not", [
    # R-F1947 must stay intact — EAC states are east_africa, NOT central_africa
    ("Kenya Nairobi procurement", "central_africa"),
    ("South Sudan conflict", "central_africa"),
    # "gulf" was deliberately NOT added — Gulf of Guinea is West-African waters,
    # not the GCC gulf; it must not mislabel as gulf.
    ("Gulf of Guinea piracy patrol", "gulf"),
    # "southern africa" must not swallow a bare "South Asia"
    ("South Asia border", "southern_africa"),
])
def test_rf2964_negative_controls_no_bleed(text, must_not):
    assert must_not not in detect_regions(text), f"{must_not} must NOT ground from {text!r}"


def test_rf2964_query_phrase_and_gate_are_symmetric():
    """Every region-name token that _REGION_QUERY_PHRASE searches for should be
    recognised by detect_regions (search for what you'll credit). Checks the
    region NAMES specifically (the asymmetry C2 fixes)."""
    from aria_service.intel import student
    region_name_checks = {
        "central_africa": "Central Africa",
        "west_africa": "West Africa",
        "east_africa": "East Africa",
        "north_africa": "North Africa",
        "southern_africa": "southern Africa",
        "south_asia": "South Asia",
        "southeast_asia": "Southeast Asia",
        "latam_non_lusophone": "Latin America",
        "europe": "Europe",
    }
    for region, phrase in region_name_checks.items():
        assert region in detect_regions(phrase), (
            f"gate must recognise the region name '{phrase}' that the query searches for"
        )
