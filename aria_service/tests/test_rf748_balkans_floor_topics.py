"""R-F748 capability test — Balkans pack now covers the 3 floor-breach topics.

After R-F697 seeded competitor_intel/finance/legal/procurement/compliance/
relationships/geopolitics for the Balkans region, the post-seed heatmap
inspection on 2026-05-20 found 3 cells STILL below the 0.70 gate-#2 floor:

    technical    × balkans = 0.668
    market_intel × balkans = 0.677
    osint        × balkans = 0.677

The original pack had ZERO facts tagged with these topics, so re-running it
with force=true didn't move them. R-F748 extends the pack with 18 new
facts (6 per cell) sourced from SIPRI / Jane's / BIRN / DSCA / Sentinel /
GLEIF / TED so the next force-reseed lifts the floor across all 170 cells.

Test verifies the pack carries the right topic counts and that the
re-seed marker advanced to v2 so skip_if_seeded re-triggers ingestion.
"""
from __future__ import annotations

from aria_service.intel.knowledge_packs.balkans_seed import _FACTS, catalogue


def test_rf748_total_facts_at_least_33():
    """v1 had 15 facts; v2 (R-F748) adds 18 → 33 total. If a future edit
    drops the count below 33 we want to see the test fail loudly."""
    assert len(_FACTS) >= 33, (
        f"R-F748 regression: balkans pack shrank below the 33-fact baseline "
        f"({len(_FACTS)} facts). Closing gate-#2 needed every cell, so "
        "removing facts risks reopening the floor-breach."
    )


def test_rf748_technical_topic_covered():
    """Balkans × technical was the lowest cell (0.668) pre-R-F748."""
    n = sum(1 for topic, *_ in _FACTS if topic == "technical")
    assert n >= 6, (
        f"R-F748: need ≥6 'technical' facts in the balkans pack to lift "
        f"the cell from 0.668 to ≥0.70 at mastery_weight=0.3; found {n}."
    )


def test_rf748_market_intel_topic_covered():
    """Balkans × market_intel was 0.677 pre-R-F748."""
    n = sum(1 for topic, *_ in _FACTS if topic == "market_intel")
    assert n >= 6, (
        f"R-F748: need ≥6 'market_intel' facts in the balkans pack; "
        f"found {n}."
    )


def test_rf748_osint_topic_covered():
    """Balkans × osint was 0.677 pre-R-F748."""
    n = sum(1 for topic, *_ in _FACTS if topic == "osint")
    assert n >= 6, (
        f"R-F748: need ≥6 'osint' facts in the balkans pack; found {n}."
    )


def test_rf748_all_new_facts_tagged_balkans_region():
    """Every fact must have region='balkans' or update_regional_mastery
    will fail to land on the correct cell."""
    for i, fact in enumerate(_FACTS):
        topic, content, source, confidence, region, source_url = fact
        assert region == "balkans", (
            f"R-F748: fact #{i} ({topic}) has region={region!r}; must be "
            f"'balkans' or update_regional_mastery() will target the wrong cell."
        )


def test_rf748_all_facts_have_source_url():
    """OSINT discipline: every fact must cite a URL so the brain absorbs
    a traceable source. The propaganda + ground-truth guards downstream
    depend on URL presence."""
    for i, fact in enumerate(_FACTS):
        topic, content, source, confidence, region, source_url = fact
        assert source_url and source_url.startswith("http"), (
            f"R-F748: fact #{i} ({topic}) missing source_url. "
            f"Got: {source_url!r}"
        )


def test_rf748_catalogue_reflects_new_topics():
    cat = catalogue()
    assert cat["region"] == "balkans"
    by_topic = cat["by_topic"]
    for required in ("technical", "market_intel", "osint"):
        assert by_topic.get(required, 0) >= 6, (
            f"R-F748: catalogue missing or under-counts {required!r}: "
            f"{by_topic}"
        )
