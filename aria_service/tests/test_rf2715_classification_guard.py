"""R-F2715 (#8) — defence product tagging must not fabricate tags on unrelated
articles. The old `_extract_entities` used raw substring matching, so "sam" in
"same", "round" in "around/ground", and "oman" in "romania" tagged sports/culture
articles with missiles/ammunition/countries. Fixed with word-boundary matching +
a defence-context gate on product tags (recall preserved for real defence articles).
"""
from __future__ import annotations

from aria_service.intel.intel_ledger import _extract_entities


def test_rf2715_sports_article_tags_no_defence_products():
    r = _extract_entities(
        "World Cup: France beat Argentina in the final round, same intensity all "
        "around the ground, a surround-sound atmosphere")
    assert r["products"] == [], f"sports article must not fabricate products: {r}"


def test_rf2715_culture_article_tags_no_defence_products():
    r = _extract_entities("The new film features a soldier's training montage, exercise and drills")
    assert r["products"] == [], f"film article must not fabricate products: {r}"


def test_rf2715_real_defence_article_still_tags():
    r = _extract_entities("Iran unveils a new surface-to-air missile; the SAM battery was deployed")
    assert "missiles" in r["products"]
    assert "Iran" in r["countries"]


def test_rf2715_oem_context_enables_products():
    r = _extract_entities("UK MoD awards ammunition contract: 155mm artillery rounds to BAE Systems")
    assert "ammunition" in r["products"]
    assert "BAE Systems" in r["oems"]


def test_rf2715_country_word_boundary_no_substring():
    # 'Oman' must come from the word Oman, not the 'oman' inside 'Romania'.
    r = _extract_entities("Romania signed a pact")
    assert "Oman" not in r["countries"]
    assert "Romania" in r["countries"]
    r2 = _extract_entities("Oman and Romania signed a pact")
    assert set(r2["countries"]) >= {"Oman", "Romania"}
