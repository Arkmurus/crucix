"""Tests for the 2026-04-17 late-PM heat-map follow-up:

  - Gulf OEM structure (SAMI / EDGE / Tawazun / Barzan)
  - KSA Vision 2030 localisation tracker
  - Baykar export pipeline
  - Political risk index
  - Cross-regional correlator
  - Turkish EUC expansion
"""
from __future__ import annotations


# ═══════════════════════════════════════════════════════════════════════
# Gulf OEM structure
# ═══════════════════════════════════════════════════════════════════════

def test_gulf_oem_tb2_equivalent_routes_to_adasi():
    """'UAV' keyword should match ADASI under EDGE."""
    from aria_service.intel import gulf_oem_structure
    matches = gulf_oem_structure.find_subsidiary_for_product("uav")
    assert any(m["subsidiary"] == "ADASI" for m in matches)


def test_gulf_oem_small_arms_routes_to_caracal():
    from aria_service.intel import gulf_oem_structure
    matches = gulf_oem_structure.find_subsidiary_for_product("pistol")
    assert any(m["subsidiary"] == "Caracal" for m in matches)


def test_gulf_oem_summary_has_edge_plurality():
    from aria_service.intel import gulf_oem_structure
    s = gulf_oem_structure.summary()
    assert s["total_subsidiaries"] >= 15
    assert s["by_parent"]["EDGE"] >= 10


def test_gulf_oem_context_fires_on_uav_keyword():
    from aria_service.intel import gulf_oem_structure
    ctx = gulf_oem_structure.get_gulf_oem_context("Looking for UAV supply from Gulf")
    assert ctx
    assert "GULF-OEM" in ctx


def test_gulf_oem_list_by_parent_filter():
    from aria_service.intel import gulf_oem_structure
    sami = gulf_oem_structure.list_subsidiaries_by_parent("SAMI")
    assert all(e["parent"] == "SAMI" for e in sami)
    assert len(sami) >= 4


# ═══════════════════════════════════════════════════════════════════════
# Vision 2030 tracker
# ═══════════════════════════════════════════════════════════════════════

def test_vision_2030_target_2030_is_fifty():
    from aria_service.intel import vision_2030_tracker
    res = vision_2030_tracker.localisation_target_for_year(2030)
    assert res["target_pct"] == 50.0


def test_vision_2030_target_interpolates_between_anchors():
    from aria_service.intel import vision_2030_tracker
    res = vision_2030_tracker.localisation_target_for_year(2026)
    assert res["interpolated"] is True
    assert 25.0 <= res["target_pct"] <= 35.0


def test_vision_2030_programmes_listed():
    from aria_service.intel import vision_2030_tracker
    progs = vision_2030_tracker.all_programmes()
    assert len(progs) >= 4
    titles = " ".join(p["programme"] for p in progs)
    assert "Al Jubail" in titles or "Navantia" in titles or "155mm" in titles


def test_vision_2030_context_fires_on_ksa():
    from aria_service.intel import vision_2030_tracker
    ctx = vision_2030_tracker.get_vision_2030_context("Saudi Vision 2030 localisation target")
    assert "VISION 2030" in ctx
    assert "50%" in ctx


def test_vision_2030_context_empty_on_off_topic():
    from aria_service.intel import vision_2030_tracker
    assert vision_2030_tracker.get_vision_2030_context("What is the weather in Lisbon") == ""


# ═══════════════════════════════════════════════════════════════════════
# Baykar export pipeline
# ═══════════════════════════════════════════════════════════════════════

def test_baykar_poland_tb2_delivered():
    from aria_service.intel import baykar_export_pipeline
    entries = baykar_export_pipeline.exports_for_country("PL")
    assert any(e["platform"] == "TB2" and e["status"] == "delivered" for e in entries)


def test_baykar_nigeria_in_pipeline():
    from aria_service.intel import baykar_export_pipeline
    ng = baykar_export_pipeline.exports_for_country("NG")
    assert len(ng) >= 1


def test_baykar_angola_is_evaluating():
    """Angola is Lusophone + the thesis-relevant evaluating entry."""
    from aria_service.intel import baykar_export_pipeline
    ao = baykar_export_pipeline.exports_for_country("AO")
    assert any(e["status"] == "evaluating" for e in ao)


def test_baykar_context_country_specific():
    from aria_service.intel import baykar_export_pipeline
    ctx = baykar_export_pipeline.get_baykar_context(
        "Is there a Baykar TB2 pipeline for Nigeria we should know about?"
    )
    assert "BAYKAR" in ctx
    assert "Nigeria" in ctx


def test_baykar_summary_shows_regional_spread():
    from aria_service.intel import baykar_export_pipeline
    s = baykar_export_pipeline.summary()
    assert s["total_entries"] >= 20
    assert "by_region" in s
    assert "by_status" in s


# ═══════════════════════════════════════════════════════════════════════
# Political risk index
# ═══════════════════════════════════════════════════════════════════════

def test_political_risk_libya_critical():
    from aria_service.intel import political_risk_index
    r = political_risk_index.risk_for_country("LY")
    assert r["tier"] == "critical"


def test_political_risk_nigeria_elevated():
    from aria_service.intel import political_risk_index
    r = political_risk_index.risk_for_country("NG")
    assert r["tier"] == "elevated"


def test_political_risk_uae_stable():
    from aria_service.intel import political_risk_index
    r = political_risk_index.risk_for_country("AE")
    assert r["tier"] == "stable"


def test_political_risk_aes_alliance_all_critical():
    from aria_service.intel import political_risk_index
    for iso2 in ("NE", "ML", "BF"):
        r = political_risk_index.risk_for_country(iso2)
        assert r["tier"] == "critical", f"{iso2} should be critical"


def test_political_risk_countries_by_tier_filter():
    from aria_service.intel import political_risk_index
    crit = political_risk_index.countries_by_tier("critical")
    assert len(crit) >= 5


def test_political_risk_context_fires_on_country_name():
    from aria_service.intel import political_risk_index
    ctx = political_risk_index.get_risk_context(
        "What's the posture in Mozambique after the post-election unrest?"
    )
    assert "POLITICAL RISK" in ctx
    assert "Mozambique" in ctx


# ═══════════════════════════════════════════════════════════════════════
# Cross-regional correlator
# ═══════════════════════════════════════════════════════════════════════

def test_cross_regional_sahel_coup_triggers():
    from aria_service.intel import cross_regional_correlator
    hits = cross_regional_correlator.correlate(
        "Coup in Niger and junta control across AES Alliance states"
    )
    assert any(h["code"] == "SAHEL_COUP_TO_ECOWAS" for h in hits)


def test_cross_regional_drc_escalation_triggers_eac():
    from aria_service.intel import cross_regional_correlator
    hits = cross_regional_correlator.correlate(
        "M23 offensive approaching Goma, MONUSCO withdrawal ongoing"
    )
    assert any(h["code"] == "DRC_ESCALATION_TO_EAC" for h in hits)


def test_cross_regional_iran_israel_triggers_gulf():
    from aria_service.intel import cross_regional_correlator
    hits = cross_regional_correlator.correlate(
        "Iran strike exchange with Israel overnight"
    )
    assert any(h["code"] == "IRAN_ESCALATION_TO_GULF_PLUS" for h in hits)


def test_cross_regional_context_compact():
    from aria_service.intel import cross_regional_correlator
    ctx = cross_regional_correlator.get_cross_regional_context(
        "Ukraine aid package increased this week, Patriot to Ukraine confirmed"
    )
    assert "CROSS-REGIONAL" in ctx


def test_cross_regional_summary_has_rules():
    from aria_service.intel import cross_regional_correlator
    s = cross_regional_correlator.summary()
    assert s["rules_total"] >= 6


# ═══════════════════════════════════════════════════════════════════════
# Turkish EUC expansion
# ═══════════════════════════════════════════════════════════════════════

def test_turkey_euc_process_detail():
    from aria_service.intel import knowledge_turkey_standalone as t
    ctx = t.get_turkey_context("nihai kullanici SSB EUC re-export waiver")
    assert ctx
    # Detailed expansion content markers
    assert "SSB" in ctx
    # Re-export language present
    assert "re-export" in ctx.lower() or "waiver" in ctx.lower()


# ═══════════════════════════════════════════════════════════════════════
# WINDOW-ALERT-DAILY task presence
# ═══════════════════════════════════════════════════════════════════════

def test_window_alert_task_enabled_in_yaml():
    import yaml
    from pathlib import Path
    yaml_path = Path(__file__).resolve().parents[1] / "autonomous" / "tasks.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    task_ids = [t["id"] for t in data.get("tasks", [])]
    assert "WINDOW-ALERT-DAILY" in task_ids
    wtask = next(t for t in data["tasks"] if t["id"] == "WINDOW-ALERT-DAILY")
    assert wtask["enabled"] is True
    assert wtask.get("cost_cap_usd", 0.0) == 0.0
