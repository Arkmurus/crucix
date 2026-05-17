"""R-F643 — Goods-list aggregator-pattern detector tests."""
from __future__ import annotations


# Reconstructed ADSM-style goods list (subset of the 2026-05-17 list)
_ADSM_LINE_ITEMS = [
    "5.56×45 mm ammunition Tracer 20,000,000",
    "5.56×45 mm ammunition Standard 10,000,000",
    "7.62×51 mm ammunition Tracer 20,000,000",
    "7.62×39 mm ammunition Standard 50,000,000",  # Soviet calibre — mismatch
    "12.7×99 mm (.50 cal) ammunition Standard 20,000,000",
    "105 mm tank round (M60) HE 20,000",  # Retired Saudi fleet
    "120 mm round tank (Abrams) Armor-Piercing 10,000",
    "155 mm howitzer projectile (US M107) 10,000",
]


def test_rf643_adsm_list_fires_hard_stop_or_red():
    """The ADSM list has mixed alliance + 100M+ small arms + retired
    fleet item. Score should be high."""
    from aria_service.intel.goods_list_aggregator_detector import (
        analyse_line_items,
    )
    result = analyse_line_items(_ADSM_LINE_ITEMS, buyer_country_iso2="SA")
    assert result.score >= 4
    assert result.severity in ("red", "hard_stop")
    assert result.mixed_alliance is True
    assert result.has_diversion_flag is True
    assert result.has_strategic_volumes is True


def test_rf643_coherent_nato_list_low_score():
    """A coherent NATO-only operational list should score low."""
    from aria_service.intel.goods_list_aggregator_detector import (
        analyse_line_items,
    )
    line_items = [
        "5.56×45 NATO standard 50,000 rounds",
        "120mm Abrams APFSDS 200 rounds",
        "155mm M107 HE 1,000 rounds",
    ]
    result = analyse_line_items(line_items, buyer_country_iso2="SA")
    assert result.severity in ("info", "amber")
    assert result.mixed_alliance is False
    assert result.has_diversion_flag is False


def test_rf643_mixed_alliance_alone_amber():
    """Mixed calibres without huge volumes / diversion = amber."""
    from aria_service.intel.goods_list_aggregator_detector import (
        analyse_line_items,
    )
    line_items = [
        "5.56×45 NATO 5000 rounds",
        "7.62×39 Soviet 5000 rounds",
    ]
    result = analyse_line_items(line_items)
    assert result.mixed_alliance is True
    # Score = 3 (mixed alliance), should be amber
    assert result.severity == "amber"


def test_rf643_strategic_volume_alone():
    """50M rounds of a NATO calibre alone — strategic volume signal."""
    from aria_service.intel.goods_list_aggregator_detector import (
        analyse_line_items,
    )
    line_items = ["5.56×45 NATO 50,000,000 rounds"]
    result = analyse_line_items(line_items)
    assert result.has_strategic_volumes is True


def test_rf643_diversion_flag_for_saudi_buyer():
    """M60 ammunition + Saudi buyer = diversion flag (M60 retired)."""
    from aria_service.intel.goods_list_aggregator_detector import (
        analyse_line_items,
    )
    line_items = ["105mm M60 tank ammunition HE 5,000 rounds"]
    result = analyse_line_items(line_items, buyer_country_iso2="SA")
    assert result.has_diversion_flag is True


def test_rf643_diversion_not_flagged_for_active_m60_buyer():
    """M60 ammo for a country that DOES operate M60 (e.g. Egypt) is
    not a diversion flag."""
    from aria_service.intel.goods_list_aggregator_detector import (
        analyse_line_items,
    )
    line_items = ["105mm M60 tank ammunition HE 5,000 rounds"]
    result = analyse_line_items(line_items, buyer_country_iso2="EG")
    assert result.has_diversion_flag is False


def test_rf643_empty_inputs_safe():
    from aria_service.intel.goods_list_aggregator_detector import (
        analyse_line_items,
    )
    r1 = analyse_line_items([])
    r2 = analyse_line_items(None)  # type: ignore[arg-type]
    assert r1.score == 0
    assert r2.score == 0


def test_rf643_render_finding_for_adsm_list():
    from aria_service.intel.goods_list_aggregator_detector import (
        render_finding,
    )
    f = render_finding(_ADSM_LINE_ITEMS, buyer_country_iso2="SA")
    assert f is not None
    assert f["severity"] in ("red", "hard_stop")
    assert "aggregator-pattern" in f["title"]
    assert f["mixed_alliance"] is True
    assert f["has_diversion_flag"] is True


def test_rf643_render_finding_none_for_clean_list():
    from aria_service.intel.goods_list_aggregator_detector import (
        render_finding,
    )
    f = render_finding(["5.56×45 NATO 100 rounds"])
    assert f is None  # no signals
