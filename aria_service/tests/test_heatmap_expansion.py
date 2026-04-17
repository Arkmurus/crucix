"""Tests for the 2026-04-17 PM heat-map expansion.

Covers:
  1. Heat-map regions: Turkey standalone, LatAm split, Central Africa,
     Balkans. Region pattern matching + detect_regions() routing.
  2. Procurement calendar: new SSB / Vision 2030 / Nigeria / SEACE / etc
     entries are present and normalise correctly.
  3. Tier 2 knowledge modules: keyword-gated context returns non-empty.
  4. Regional bright-line rules: triggers, severity ladder, country map.
"""
from __future__ import annotations


# ═══════════════════════════════════════════════════════════════════════
# 1. Heat-map region restructure
# ═══════════════════════════════════════════════════════════════════════

def test_turkey_is_standalone_region():
    from aria_service.intel import student
    assert "turkey" in student.REGIONS
    # Turkey must NOT be merged into europe
    regions = student.detect_regions(
        "SSB Savunma Sanayii İcra Komitesi approved Baykar TB2 export"
    )
    assert "turkey" in regions


def test_latam_split_into_lusophone_and_non_lusophone():
    from aria_service.intel import student
    assert "latam_lusophone" in student.REGIONS
    assert "latam_non_lusophone" in student.REGIONS
    # Peru must route to non_lusophone
    assert "latam_non_lusophone" in student.detect_regions(
        "Peru SEACE procurement for MINDEF modernisation"
    )
    # Brazil remains in lusophone orbit
    assert "latam_lusophone" in student.detect_regions(
        "Brazilian Embraer defence export"
    )


def test_central_africa_region_exists():
    from aria_service.intel import student
    assert "central_africa" in student.REGIONS
    assert "central_africa" in student.detect_regions(
        "DRC FARDC modernisation and Rwanda Defence Force"
    )


def test_balkans_region_exists():
    from aria_service.intel import student
    assert "balkans" in student.REGIONS
    assert "balkans" in student.detect_regions(
        "Yugoimport SDPR and Zastava export announcement from Belgrade"
    )


def test_south_se_asia_split():
    from aria_service.intel import student
    assert "south_asia" in student.REGIONS
    assert "southeast_asia" in student.REGIONS
    assert "south_asia" in student.detect_regions(
        "India DAP 2020 Make-in-India offset with HAL"
    )
    assert "southeast_asia" in student.detect_regions(
        "Indonesia Renstra modernisation with PT PAL"
    )


# ═══════════════════════════════════════════════════════════════════════
# 2. Procurement calendar — new region-specific events
# ═══════════════════════════════════════════════════════════════════════

def test_calendar_has_vision_2030_milestone():
    from aria_service.intel import procurement_calendar as pc
    titles = [e["title"] for e in pc._SEED_EVENTS]
    assert any("Vision 2030" in t for t in titles)


def test_calendar_has_ssb_committee():
    from aria_service.intel import procurement_calendar as pc
    titles = [e["title"] for e in pc._SEED_EVENTS]
    assert any("SSB" in t for t in titles)


def test_calendar_has_seace():
    from aria_service.intel import procurement_calendar as pc
    titles = [e["title"] for e in pc._SEED_EVENTS]
    assert any("SEACE" in t for t in titles)


def test_calendar_has_dac_india():
    from aria_service.intel import procurement_calendar as pc
    titles = [e["title"] for e in pc._SEED_EVENTS]
    assert any("DAC" in t for t in titles)


def test_calendar_has_dgmn_chile():
    from aria_service.intel import procurement_calendar as pc
    titles = [e["title"] for e in pc._SEED_EVENTS]
    assert any("DGMN" in t for t in titles)


# ═══════════════════════════════════════════════════════════════════════
# 3. Tier 2 knowledge modules
# ═══════════════════════════════════════════════════════════════════════

def test_north_africa_context_matches_morocco():
    from aria_service.intel import knowledge_north_africa
    ctx = knowledge_north_africa.get_north_africa_context(
        "Morocco FAR F-1 replacement programme"
    )
    assert ctx
    assert "Morocco" in ctx or "FAR" in ctx


def test_south_se_asia_context_matches_india():
    from aria_service.intel import knowledge_south_se_asia
    ctx = knowledge_south_se_asia.get_south_se_asia_context(
        "India Make-in-India DAP 2020 offset and HAL partnership"
    )
    assert ctx
    assert "India" in ctx or "HAL" in ctx or "DAP" in ctx


def test_central_africa_context_matches_drc():
    from aria_service.intel import knowledge_central_africa
    ctx = knowledge_central_africa.get_central_africa_context(
        "DRC FARDC replenishment after M23 escalation"
    )
    assert ctx
    assert "DRC" in ctx or "FARDC" in ctx


def test_balkans_context_matches_serbia():
    from aria_service.intel import knowledge_balkans
    ctx = knowledge_balkans.get_balkans_context(
        "Yugoimport SDPR export from Belgrade to African customer"
    )
    assert ctx
    assert "Yugoimport" in ctx or "Serbia" in ctx


def test_knowledge_modules_empty_on_off_topic():
    """An off-topic query must return empty string from each module."""
    from aria_service.intel import (
        knowledge_north_africa,
        knowledge_south_se_asia,
        knowledge_central_africa,
        knowledge_balkans,
    )
    q = "some totally unrelated question about cat photography"
    assert knowledge_north_africa.get_north_africa_context(q) == ""
    assert knowledge_south_se_asia.get_south_se_asia_context(q) == ""
    assert knowledge_central_africa.get_central_africa_context(q) == ""
    assert knowledge_balkans.get_balkans_context(q) == ""


# ═══════════════════════════════════════════════════════════════════════
# 4. Regional bright-line rules
# ═══════════════════════════════════════════════════════════════════════

def test_bright_line_libya_is_hard_stop():
    from aria_service.intel import regional_bright_lines
    hits = regional_bright_lines.check_text("Proposed export to Libya GNU forces")
    assert any(h["code"] == "LIBYA_EMBARGO" and h["severity"] == "hard_stop" for h in hits)


def test_bright_line_algeria_enhanced_dd():
    from aria_service.intel import regional_bright_lines
    hits = regional_bright_lines.check_text("Advisory mandate in Algeria defence ministry")
    assert any(h["code"] == "ALGERIA_DUAL_EXPOSURE" for h in hits)
    alg = next(h for h in hits if h["code"] == "ALGERIA_DUAL_EXPOSURE")
    assert alg["severity"] == "enhanced_dd"
    assert any("CAATSA" in a for a in alg["required_actions"])


def test_bright_line_aes_alliance():
    from aria_service.intel import regional_bright_lines
    hits = regional_bright_lines.check_text("Potential Niger procurement engagement")
    assert any(h["code"] == "AES_ALLIANCE" for h in hits)


def test_bright_line_drc_requires_un_goe_check():
    from aria_service.intel import regional_bright_lines
    hits = regional_bright_lines.check_text("FARDC equipment replenishment in DRC")
    drc = next((h for h in hits if h["code"] == "DRC_COUNTERPARTY"), None)
    assert drc is not None
    assert any("UN" in a for a in drc["required_actions"])


def test_bright_line_uae_houthi_overlay():
    from aria_service.intel import regional_bright_lines
    hits = regional_bright_lines.check_text("UAE Jebel Ali free-zone entity incorporation")
    assert any(h["code"] == "UAE_HOUTHI_PROLIFERATION" for h in hits)


def test_bright_line_check_country_by_iso2():
    from aria_service.intel import regional_bright_lines
    assert regional_bright_lines.check_country("KP")[0]["code"] == "DPRK_UN1718"
    assert regional_bright_lines.check_country("MM")[0]["code"] == "MYANMAR_SANCTIONS"
    assert regional_bright_lines.check_country("LY")[0]["code"] == "LIBYA_EMBARGO"
    # Unmapped country → empty
    assert regional_bright_lines.check_country("GB") == []


def test_bright_line_summary_has_hard_stops():
    from aria_service.intel import regional_bright_lines
    s = regional_bright_lines.summary()
    assert s["rules_total"] >= 6
    assert s["by_severity"].get("hard_stop", 0) >= 2
    assert s["by_severity"].get("enhanced_dd", 0) >= 3


def test_bright_line_no_false_positive_on_mundane_text():
    from aria_service.intel import regional_bright_lines
    hits = regional_bright_lines.check_text("Routine discussion about French defence industry")
    assert hits == []
