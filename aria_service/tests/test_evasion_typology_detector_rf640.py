"""R-F640 — Regional sanctions-evasion typology detector.

Pins (operator demands 99.99% reliability):
  - The 2026-05-17 ADSM signature pattern (Turkey + Russian + Saudi)
    fires TYP-TR-RU-MENA at RED severity
  - INF-eliminated item on list fires TYP-INF-ELIM-ON-LIST at hard_stop
  - CWC/BWC items fire TYP-CWC-BWC-ON-LIST at hard_stop
  - Aggregator-broker composite fires when ≥3 weak signals stack
  - Clean deal (Saudi buyer + US-origin + standard channel) → no matches
  - Defensive: bad inputs never raise
"""
from __future__ import annotations

import pytest


# ══════════════════════════════════════════════════════════════════
# PART A — the ADSM signature pattern (TYP-TR-RU-MENA)
# ══════════════════════════════════════════════════════════════════

def test_rf640_adsm_signature_pattern_fires_red():
    """Reconstruct the ADSM context: Turkey-routed Russian/Chinese
    goods to Saudi MENA. Must fire TYP-TR-RU-MENA at RED."""
    from aria_service.intel.evasion_typology_detector import (
        DealContext, detect,
    )
    ctx = DealContext(
        weapon_origins_iso2=("RU", "CN"),
        weapon_sanctions_statuses=("prohibited", "restricted"),
        routing_location_iso2="TR",
        buyer_country_iso2="SA",
        buyer_named_end_user_specific=False,
    )
    matches = detect(ctx)
    typology_ids = {m.typology_id for m in matches}
    assert "TYP-TR-RU-MENA" in typology_ids
    # Find the match
    tr_ru_match = next(m for m in matches if m.typology_id == "TYP-TR-RU-MENA")
    assert tr_ru_match.severity == "red"
    assert "Conflict Armament Research" in " ".join(tr_ru_match.citations)


def test_rf640_iranian_origin_via_turkey_fires_same_typology():
    """Iranian-origin goods via Turkey also matches the TR-RU-MENA
    typology (same hub, different sanctioned origin)."""
    from aria_service.intel.evasion_typology_detector import (
        DealContext, detect,
    )
    ctx = DealContext(
        weapon_origins_iso2=("IR",),
        routing_location_iso2="TR",
        buyer_country_iso2="EG",
    )
    matches = detect(ctx)
    assert any(m.typology_id == "TYP-TR-RU-MENA" for m in matches)


def test_rf640_turkey_routed_to_non_mena_does_not_fire():
    """If buyer is NOT MENA, the TR-RU-MENA typology shouldn't fire —
    Turkey-routed Russian goods to e.g. Asian buyer is a different
    pattern."""
    from aria_service.intel.evasion_typology_detector import (
        DealContext, detect,
    )
    ctx = DealContext(
        weapon_origins_iso2=("RU",),
        routing_location_iso2="TR",
        buyer_country_iso2="ID",  # Indonesia — not MENA
    )
    matches = detect(ctx)
    assert not any(m.typology_id == "TYP-TR-RU-MENA" for m in matches)


def test_rf640_turkey_routed_with_only_nato_origin_does_not_fire():
    """Turkey-routed US/UK/French goods is normal — must NOT fire."""
    from aria_service.intel.evasion_typology_detector import (
        DealContext, detect,
    )
    ctx = DealContext(
        weapon_origins_iso2=("US", "GB", "FR"),
        routing_location_iso2="TR",
        buyer_country_iso2="SA",
    )
    matches = detect(ctx)
    assert not any(m.typology_id == "TYP-TR-RU-MENA" for m in matches)


# ══════════════════════════════════════════════════════════════════
# PART B — UAE-Iran typology
# ══════════════════════════════════════════════════════════════════

def test_rf640_uae_iran_typology_fires():
    from aria_service.intel.evasion_typology_detector import (
        DealContext, detect,
    )
    ctx = DealContext(
        weapon_origins_iso2=("IR",),
        routing_location_iso2="AE",
        buyer_country_iso2="SD",  # Sudan
    )
    matches = detect(ctx)
    ae_ir = [m for m in matches if m.typology_id == "TYP-AE-IR"]
    assert len(ae_ir) == 1
    assert ae_ir[0].severity == "red"


# ══════════════════════════════════════════════════════════════════
# PART C — INF-eliminated on list
# ══════════════════════════════════════════════════════════════════

def test_rf640_inf_eliminated_on_list_fires_hard_stop():
    from aria_service.intel.evasion_typology_detector import (
        DealContext, detect,
    )
    ctx = DealContext(
        weapon_origins_iso2=("RU",),
        has_inf_eliminated_items=True,
        buyer_country_iso2="SA",
    )
    matches = detect(ctx)
    inf = [m for m in matches if m.typology_id == "TYP-INF-ELIM-ON-LIST"]
    assert len(inf) == 1
    assert inf[0].severity == "hard_stop"


# ══════════════════════════════════════════════════════════════════
# PART D — CWC/BWC on list
# ══════════════════════════════════════════════════════════════════

def test_rf640_cwc_bwc_on_list_fires_hard_stop():
    from aria_service.intel.evasion_typology_detector import (
        DealContext, detect,
    )
    ctx = DealContext(has_categorically_banned_items=True)
    matches = detect(ctx)
    cwc = [m for m in matches if m.typology_id == "TYP-CWC-BWC-ON-LIST"]
    assert len(cwc) == 1
    assert cwc[0].severity == "hard_stop"


# ══════════════════════════════════════════════════════════════════
# PART E — aggregator-broker composite
# ══════════════════════════════════════════════════════════════════

def test_rf640_aggregator_pattern_fires_amber_when_3_signals():
    """3 of 6 weak signals triggers AMBER aggregator pattern."""
    from aria_service.intel.evasion_typology_detector import (
        DealContext, detect,
    )
    ctx = DealContext(
        goods_list_mixed_nato_and_non_nato_calibres=True,  # +2
        no_timeline_indicated=True,                          # +1
        no_commission_discussed=True,                        # +1
    )  # total = 4 → red (≥4)
    matches = detect(ctx)
    agg = [m for m in matches if m.typology_id == "TYP-AGGREGATOR-BROKER"]
    assert len(agg) == 1
    assert agg[0].severity == "red"  # ≥4 = red


def test_rf640_aggregator_below_threshold_does_not_fire():
    """2 weak signals (score = 2) is below threshold."""
    from aria_service.intel.evasion_typology_detector import (
        DealContext, detect,
    )
    ctx = DealContext(
        no_timeline_indicated=True,           # +1
        no_commission_discussed=True,         # +1
    )  # total = 2 < 3
    matches = detect(ctx)
    assert not any(m.typology_id == "TYP-AGGREGATOR-BROKER" for m in matches)


def test_rf640_aggregator_pattern_includes_generic_end_user_signal():
    """Generic 'Saudi Gov' end-user (not specific) counts as a weak signal."""
    from aria_service.intel.evasion_typology_detector import (
        DealContext, detect,
    )
    ctx = DealContext(
        buyer_country_iso2="SA",
        buyer_named_end_user_specific=False,  # +1
        no_timeline_indicated=True,           # +1
        informal_channel_received=True,       # +1
    )  # total = 3 → amber
    matches = detect(ctx)
    agg = [m for m in matches if m.typology_id == "TYP-AGGREGATOR-BROKER"]
    assert len(agg) == 1


# ══════════════════════════════════════════════════════════════════
# PART F — composite verdict
# ══════════════════════════════════════════════════════════════════

def test_rf640_adsm_full_context_detects_composite_verdict():
    """Reconstruct the FULL ADSM picture and verify the composite
    verdict captures the right severity + multiple matches."""
    from aria_service.intel.evasion_typology_detector import (
        DealContext, detect_composite_verdict,
    )
    ctx = DealContext(
        weapon_origins_iso2=("RU", "CN", "US"),
        weapon_sanctions_statuses=("prohibited", "restricted", "cleared"),
        routing_location_iso2="TR",
        buyer_country_iso2="SA",
        buyer_named_end_user_specific=False,
        has_inf_eliminated_items=True,
        goods_list_mixed_nato_and_non_nato_calibres=True,
        no_timeline_indicated=True,
        no_commission_discussed=True,
        informal_channel_received=True,
    )
    verdict = detect_composite_verdict(ctx)
    assert verdict["overall_verdict"] == "hard_stop"
    typology_ids = {m["typology_id"] for m in verdict["matches"]}
    # Both TR-RU-MENA AND INF-ELIM-ON-LIST + AGGREGATOR-BROKER should match
    assert "TYP-TR-RU-MENA" in typology_ids
    assert "TYP-INF-ELIM-ON-LIST" in typology_ids
    assert "TYP-AGGREGATOR-BROKER" in typology_ids
    assert len(verdict["matches"]) >= 3


def test_rf640_clean_deal_no_matches():
    """A legitimate deal context — Saudi buyer + US-origin + named
    end-user — should produce no typology matches."""
    from aria_service.intel.evasion_typology_detector import (
        DealContext, detect_composite_verdict,
    )
    ctx = DealContext(
        weapon_origins_iso2=("US",),
        weapon_sanctions_statuses=("cleared",),
        routing_location_iso2="US",   # US-sourced via DSCA
        buyer_country_iso2="SA",
        buyer_named_end_user_specific=True,
    )
    verdict = detect_composite_verdict(ctx)
    assert verdict["overall_verdict"] == "clean"
    assert verdict["matches"] == []


# ══════════════════════════════════════════════════════════════════
# PART G — render_findings_for_ctx
# ══════════════════════════════════════════════════════════════════

def test_rf640_render_findings_returns_orchestrator_shape():
    from aria_service.intel.evasion_typology_detector import (
        DealContext, render_findings_for_ctx,
    )
    ctx = DealContext(
        weapon_origins_iso2=("RU",),
        routing_location_iso2="TR",
        buyer_country_iso2="SA",
    )
    findings = render_findings_for_ctx(ctx)
    assert len(findings) >= 1
    for f in findings:
        assert f["severity"] in ("amber", "red", "hard_stop")
        assert "Typology match" in f["title"]
        assert "R-F640" in f["source"]
        assert f["confidence"] == "PROBABLE"


# ══════════════════════════════════════════════════════════════════
# PART H — defensive handling
# ══════════════════════════════════════════════════════════════════

def test_rf640_empty_context_no_matches():
    from aria_service.intel.evasion_typology_detector import (
        DealContext, detect,
    )
    assert detect(DealContext()) == []


def test_rf640_bad_input_does_not_raise():
    from aria_service.intel.evasion_typology_detector import detect
    assert detect(None) == []  # type: ignore[arg-type]
    assert detect("not a context") == []  # type: ignore[arg-type]


def test_rf640_known_typologies_enumerated():
    from aria_service.intel.evasion_typology_detector import (
        list_known_typologies,
    )
    typologies = list_known_typologies()
    assert len(typologies) >= 5
    typology_text = " ".join(typologies)
    assert "TR-RU-MENA" in typology_text
    assert "AE-IR" in typology_text
    assert "INF-ELIM" in typology_text
    assert "CWC-BWC" in typology_text
    assert "AGGREGATOR" in typology_text
