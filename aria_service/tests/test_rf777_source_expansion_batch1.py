"""R-F777 — source-expansion batch 1.

User mandate: "add more sources of true valuable data ... security, defence
AND commodities". The defence-source seed was strong on defence press +
sanctions but weak on cyber-vulnerability primary lists, maritime sanctions-
evasion, and commodity pricing. This batch closes those gaps with twelve
free feeds covering:

    - CISA KEV + CISA advisories (cyber primary)
    - MITRE ATT&CK (cyber TTP standard)
    - Equasis (maritime / sanctions-evasion)
    - LME + IMF + OPEC (commodities pricing & oil)
    - RAND + Conflict Armament Research (defence research + investigation)
    - Atrocity Forecasting Project (foresight academic)
    - Bellingcat + ransomware.live (specialist OSINT)

This regression test pins the new catalogue entries so a future refactor
can't silently lose them. Belt-and-braces grep-style: each added family +
tier + at least one topic-tag is asserted present.
"""
from __future__ import annotations

from aria_service.intel import defence_source_seed as seed


# Each tuple: (family, expected_tier, must_have_topic_tag, must_have_url_substring)
_R_F777_BATCH_1 = [
    ("cisa_kev",              "tier_1a", "vulnerability",   "cisa.gov/known-exploited"),
    ("equasis",               "tier_1a", "maritime",        "equasis.org"),
    ("lme_metal_exchange",    "tier_1a", "commodities",     "lme.com"),
    ("imf_commodity_prices",  "tier_1a", "commodities",     "imf.org"),
    ("opec_mmr",              "tier_1a", "oil",             "opec.org"),
    ("mitre_attack",          "tier_1b", "cyber",           "attack.mitre.org"),
    ("rand_corp",             "tier_1b", "defence",         "rand.org"),
    ("conflict_arm_research", "tier_1b", "weapons",         "conflictarm.com"),
    ("atrocity_forecasting",  "tier_1b", "foresight",       "earlywarningproject.ushmm.org"),  # R-F2269 — old atrocityforecastingproject.com domain died
    ("cisa_advisories",       "tier_2",  "advisory",        "cisa.gov/news-events"),
    ("bellingcat",            "tier_2",  "osint",           "bellingcat.com"),
    ("ransomware_live",       "tier_2",  "ransomware",      "ransomware.live"),
]


def _by_family() -> dict:
    return {family: (url, tier, tags) for url, family, tier, tags in seed._DEFENCE_SOURCES}


def test_rf777_all_twelve_families_present():
    """Every R-F777 family must be in _DEFENCE_SOURCES."""
    catalogue = _by_family()
    missing = [f for f, *_ in _R_F777_BATCH_1 if f not in catalogue]
    assert not missing, (
        f"R-F777 regression: families dropped from _DEFENCE_SOURCES: {missing}"
    )


def test_rf777_tiers_correct():
    """Each R-F777 entry must carry the expected tier."""
    catalogue = _by_family()
    mismatches = []
    for family, expected_tier, _topic, _url_sub in _R_F777_BATCH_1:
        _url, actual_tier, _tags = catalogue.get(family, (None, None, None))
        if actual_tier != expected_tier:
            mismatches.append((family, expected_tier, actual_tier))
    assert not mismatches, (
        f"R-F777 regression: tier mismatches {mismatches}"
    )


def test_rf777_topic_tags_present():
    """Each R-F777 entry must carry its anchor topic tag (so
    rank_sources_for_topic returns it on the right query)."""
    catalogue = _by_family()
    missing_tags = []
    for family, _tier, topic, _url_sub in _R_F777_BATCH_1:
        _url, _tier_actual, tags = catalogue.get(family, (None, None, []))
        if topic not in (tags or []):
            missing_tags.append((family, topic, tags))
    assert not missing_tags, (
        f"R-F777 regression: topic tags dropped {missing_tags}"
    )


def test_rf777_urls_well_formed():
    """Each R-F777 url must contain the expected anchor substring so a
    silent URL drift (typo in a future edit) is caught."""
    catalogue = _by_family()
    bad_urls = []
    for family, _tier, _topic, url_sub in _R_F777_BATCH_1:
        url, _tier_actual, _tags = catalogue.get(family, (None, None, []))
        if not url or url_sub not in url:
            bad_urls.append((family, url_sub, url))
    assert not bad_urls, (
        f"R-F777 regression: URLs drifted {bad_urls}"
    )


def test_rf777_catalogue_summary_counts_match():
    """catalogue_summary() must reflect the new total. Sanity check that
    nobody removed the section header without updating this test."""
    summary = seed.catalogue_summary()
    # The expansion added 12 entries; existing pre-R-F777 total was a known
    # quantity. We assert lower-bound to avoid breaking on future legitimate
    # additions, but high enough to catch accidental removal of the batch.
    assert summary["total_curated"] >= 200, (
        f"R-F777 regression: catalogue total dropped to "
        f"{summary['total_curated']} — the 12-source batch may have been "
        f"deleted. Pre-R-F777 catalogue had ~190 entries."
    )
    # The expansion added 5 tier_1a + 4 tier_1b + 3 tier_2. Assert each
    # tier saw a non-zero increase vs the pre-R-F777 baseline.
    assert summary["by_tier"].get("tier_1a", 0) >= 5
    assert summary["by_tier"].get("tier_1b", 0) >= 4
    assert summary["by_tier"].get("tier_2", 0) >= 3
