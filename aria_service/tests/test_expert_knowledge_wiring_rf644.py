"""R-F644 — Wiring of expert-knowledge layer (R-F638-F643) into dd_orchestrator.

Tests verify:
  - All 6 expert-knowledge modules are imported by _run_compliance
  - All 6 render functions are called
  - Output flows into report.compliance.findings
  - Defensive try/except wraps the block
  - End-to-end: ADSM-style target dict produces hard_stop findings
"""
from __future__ import annotations

import asyncio
import pathlib


def _src() -> str:
    return pathlib.Path(
        "C:/code/crucix/aria_service/intel/dd_orchestrator.py"
    ).read_text(encoding="utf-8", errors="ignore")


# ══════════════════════════════════════════════════════════════════
# PART A — source-level wiring pins
# ══════════════════════════════════════════════════════════════════

def test_rf644_all_six_expert_modules_imported_in_run_compliance():
    src = _src()
    run_idx = src.find("async def _run_compliance")
    assert run_idx > 0
    block = src[run_idx:run_idx + 20000]
    for mod in ("weapon_origin_catalogue", "eliminated_weapons_watchlist",
                "evasion_typology_detector", "end_user_granularity",
                "mou_clause_gate_analyser", "goods_list_aggregator_detector"):
        assert mod in block, f"R-F644: missing import of {mod}"


def test_rf644_block_inside_run_compliance_after_cultural():
    """Expert-knowledge block must come AFTER the R-F635 cultural
    block (so cultural section renders before the smoking-gun
    findings)."""
    src = _src()
    run_idx = src.find("async def _run_compliance")
    block = src[run_idx:run_idx + 20000]
    cult_idx = block.find("4a-CULTURAL")
    expert_idx = block.find("4a-EXPERT-KNOWLEDGE")
    assert cult_idx > 0
    assert expert_idx > 0
    assert expert_idx > cult_idx


def test_rf644_block_before_export_control_classification():
    """And BEFORE the existing 4b export-control classification."""
    src = _src()
    run_idx = src.find("async def _run_compliance")
    block = src[run_idx:run_idx + 20000]
    expert_idx = block.find("4a-EXPERT-KNOWLEDGE")
    ec_idx = block.find("4b. Export control classification")
    assert expert_idx < ec_idx


def test_rf644_render_finding_calls_for_all_six_modules():
    src = _src()
    run_idx = src.find("async def _run_compliance")
    block = src[run_idx:run_idx + 20000]
    # F638
    assert "_woc.render_finding_for_text" in block
    # F639
    assert "_ewl.render_finding_for_text" in block
    # F640
    assert "_etd.render_findings_for_ctx" in block
    # F641
    assert "_eug.render_finding" in block
    # F642
    assert "_mcga.render_finding" in block
    # F643
    assert "_glad.render_finding" in block


def test_rf644_findings_appended_to_compliance_findings():
    src = _src()
    run_idx = src.find("async def _run_compliance")
    block = src[run_idx:run_idx + 20000]
    expert_idx = block.find("4a-EXPERT-KNOWLEDGE")
    expert_region = block[expert_idx:expert_idx + 12000]
    # Count appends within the expert region — should be multiple
    appends = expert_region.count("report.compliance.findings.append")
    assert appends >= 5, (
        f"R-F644: expected >=5 findings.append in expert block, got {appends}"
    )


def test_rf644_defensive_try_except_wraps_block():
    src = _src()
    run_idx = src.find("async def _run_compliance")
    block = src[run_idx:run_idx + 20000]
    expert_idx = block.find("4a-EXPERT-KNOWLEDGE")
    expert_region = block[expert_idx:expert_idx + 12000]
    assert "try:" in expert_region
    assert "except Exception" in expert_region
    # And specifically — the outer try wrapping the whole block
    assert "_expert_err" in expert_region


# ══════════════════════════════════════════════════════════════════
# PART B — end-to-end via direct module calls (faster than dd_orchestrator)
# ══════════════════════════════════════════════════════════════════

def test_rf644_endtoend_adsm_list_produces_hard_stop_findings():
    """End-to-end: an ADSM-style target dict, when run through the six
    modules in the order the orchestrator wires them, produces
    hard_stop findings on Kornet, SS-20, and typology."""
    from aria_service.intel import weapon_origin_catalogue as woc
    from aria_service.intel import eliminated_weapons_watchlist as ewl
    from aria_service.intel import evasion_typology_detector as etd
    from aria_service.intel import end_user_granularity as eug
    from aria_service.intel import goods_list_aggregator_detector as glad

    goods_items = [
        "Cornet guided anti-tank missile (Russian) - HE - 2,000",
        "155 mm Laser-Guided projectile (GPI) - 20,000",
        "SS-20 rockets - 10,000",
        "5.56×45 mm ammunition Tracer 20,000,000",
        "7.62×39 mm ammunition Standard 50,000,000",  # mismatch
        "105 mm tank round (M60) HE - 20,000",  # diversion
    ]
    end_user_statement = "Saudi Government"
    buyer_iso2 = "SA"
    routing_iso2 = "TR"

    # F638: weapon catalogue per item
    weapon_findings: list = []
    for item in goods_items:
        f = woc.render_finding_for_text(item)
        if f:
            weapon_findings.append(f)
    assert len(weapon_findings) >= 2  # at least Kornet + GP1
    severities = {f["severity"] for f in weapon_findings}
    assert "hard_stop" in severities  # Kornet
    assert "amber" in severities       # NORINCO GP1

    # F639: eliminated watchlist per item
    elim_findings: list = []
    for item in goods_items:
        f = ewl.render_finding_for_text(item)
        if f:
            elim_findings.append(f)
    assert len(elim_findings) >= 1  # SS-20
    assert any(f["severity"] == "hard_stop" for f in elim_findings)

    # F643: aggregator
    agg = glad.render_finding(goods_items, buyer_country_iso2=buyer_iso2)
    assert agg is not None
    assert agg["severity"] in ("red", "hard_stop")

    # F641: end-user granularity
    eu = eug.render_finding(end_user_statement, country_iso2=buyer_iso2)
    assert eu["severity"] == "red"

    # F640: typology composite — Turkey routing + Russian + Saudi
    ctx = etd.DealContext(
        weapon_origins_iso2=("RU", "CN"),
        weapon_sanctions_statuses=("prohibited", "restricted"),
        routing_location_iso2=routing_iso2,
        buyer_country_iso2=buyer_iso2,
        buyer_named_end_user_specific=False,
        has_inf_eliminated_items=True,
        goods_list_mixed_nato_and_non_nato_calibres=True,
        no_timeline_indicated=True,
        no_commission_discussed=True,
        informal_channel_received=True,
    )
    typology_findings = etd.render_findings_for_ctx(ctx)
    assert len(typology_findings) >= 2  # TR-RU-MENA + INF-ELIM-ON-LIST + aggregator
    severities = {f["severity"] for f in typology_findings}
    assert "hard_stop" in severities  # INF-ELIM


def test_rf644_clean_deal_produces_minimal_or_no_findings():
    """Negative: a coherent US-Saudi deal with named end-user and US
    routing should NOT produce hard_stop findings."""
    from aria_service.intel import weapon_origin_catalogue as woc
    from aria_service.intel import evasion_typology_detector as etd
    from aria_service.intel import end_user_granularity as eug
    from aria_service.intel import goods_list_aggregator_detector as glad

    goods_items = [
        "Excalibur Ib 155mm precision-guided projectile - 1,000",
        "M829A4 120mm APFSDS - 500",
        "M107 155mm HE - 5,000",
    ]

    # F638
    weapon_findings = [
        woc.render_finding_for_text(i) for i in goods_items
    ]
    weapon_findings = [f for f in weapon_findings if f]
    for f in weapon_findings:
        assert f["severity"] == "info"

    # F643 — clean US-only volumes, no aggregator signals expected
    agg = glad.render_finding(goods_items, buyer_country_iso2="SA")
    assert agg is None  # no signals

    # F641 — proper Saudi end-user (Royal Saudi Land Forces) for SA buyer
    eu = eug.render_finding("Royal Saudi Land Forces", country_iso2="SA")
    assert eu["severity"] == "info"

    # F640 — clean US-origin to Saudi via US routing
    ctx = etd.DealContext(
        weapon_origins_iso2=("US",),
        weapon_sanctions_statuses=("cleared",),
        routing_location_iso2="US",
        buyer_country_iso2="SA",
        buyer_named_end_user_specific=True,
    )
    typology = etd.render_findings_for_ctx(ctx)
    assert typology == []  # zero typology matches on clean US-Saudi deal


def test_rf644_mou_gate_detected_on_adsm_45e_text():
    """The actual Cl 4.5(e) gate must be detected when MOU text is fed."""
    from aria_service.intel import mou_clause_gate_analyser as mcga
    mou_text = """
    Section 4. NON CIRCUMVENTION AND PROTECTION OF OPPORTUNITY
    4.5 Arkmurus shall not be required to disclose the identity of any
    Introduced Party, or any other item of Protected Information, unless
    and until:
    (a) this Agreement has been signed by ADSM;
    (b) ADSM has provided a formal Letter of Intent;
    (c) ADSM has confirmed in writing the final end user;
    (d) the Parties have agreed in writing the applicable Commission; and
    (e) Arkmurus is satisfied, acting reasonably, that the proposed
    Transaction may proceed in accordance with applicable compliance,
    export control, sanctions, end user and licensing requirements.
    """
    findings = mcga.render_finding(mou_text)
    assert len(findings) >= 1
    assert any("4.5" in f["title"] for f in findings)
