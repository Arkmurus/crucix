"""R-F3016 — UK financial extraction (R-F2782 Phase 2): Companies House iXBRL
balance-sheet figures → a bounded SOLVENCY verdict that answers financial_capacity.

Proven live: SC241685 (small) net assets sign="-" £50,145 → DISTRESSED (genuinely
balance-sheet insolvent); 05684823 Cohort plc (listed, PDF group accounts) → None →
honest UNKNOWN. Balance sheet ONLY — P&L is filleted under the small-company
exemption, so this is solvency, never a profitability claim (never-false-clean).
"""
import asyncio
from unittest.mock import patch, AsyncMock

from aria_service.intel import companies_house as ch
from aria_service.intel import financial_health as fh
from aria_service.intel.dd_schema import _dd_decision_readiness

# minimal but representative iXBRL: current (I0, sign="-") + prior (I1) + a
# DIMENSIONAL context (DIM0 with <segment>) that must lose to the plain one.
_IXBRL = (
    '<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">'
    '<xbrli:context id="I0"><xbrli:period><xbrli:instant>2024-10-31</xbrli:instant></xbrli:period></xbrli:context>'
    '<xbrli:context id="I1"><xbrli:period><xbrli:instant>2023-10-31</xbrli:instant></xbrli:period></xbrli:context>'
    '<xbrli:context id="DIM0"><xbrli:period><xbrli:instant>2024-10-31</xbrli:instant></xbrli:period>'
    '<xbrli:segment>seg</xbrli:segment></xbrli:context>'
    '<ix:nonFraction name="core:NetAssetsLiabilities" contextRef="I0" sign="-" scale="0">50,145</ix:nonFraction>'
    '<ix:nonFraction name="core:NetAssetsLiabilities" contextRef="I1" scale="0">31,810</ix:nonFraction>'
    '<ix:nonFraction name="core:NetCurrentAssetsLiabilities" contextRef="I0" scale="0">12,000</ix:nonFraction>'
    '<ix:nonFraction name="core:CurrentAssets" contextRef="DIM0" scale="0">999</ix:nonFraction>'
    '<ix:nonFraction name="core:CurrentAssets" contextRef="I0" scale="0">40,000</ix:nonFraction>'
    '</html>'
)


# ── parser ─────────────────────────────────────────────────────────────────
def test_rf3016_parser_sign_years_and_plain_context():
    figs = ch._parse_ixbrl_balance_sheet(_IXBRL)
    assert figs["net_assets"]["current"] == -50145.0, "sign='-' must be honoured"
    assert figs["net_assets"]["prior"] == 31810.0
    assert figs["net_current_assets"]["current"] == 12000.0
    # the plain (non-segment) current-year value must beat the dimensional one
    assert figs["current_assets"]["current"] == 40000.0


def test_rf3016_ixbrl_number():
    assert ch._ixbrl_number("50,145", 'sign="-" scale="0"') == -50145.0
    assert ch._ixbrl_number("1,234", 'scale="3"') == 1234000.0
    assert ch._ixbrl_number("-", "") == 0.0        # a dash is a filed NIL
    assert ch._ixbrl_number("n/a", "") is None     # unparseable → skip


# ── verdict ────────────────────────────────────────────────────────────────
def test_rf3016_verdict_levels():
    d = fh._uk_balance_sheet_verdict({"net_assets": {"current": -50145.0, "prior": 31810.0}})
    assert d["verdict"] == "DISTRESSED", "negative net assets = balance-sheet insolvent"
    s = fh._uk_balance_sheet_verdict({"net_assets": {"current": 80000.0, "prior": 60000.0},
                                      "net_current_assets": {"current": 20000.0}})
    assert s["verdict"] == "STRONG", "positive + improving = strong"
    w = fh._uk_balance_sheet_verdict({"net_assets": {"current": 5000.0},
                                      "net_current_assets": {"current": -3000.0}})
    assert w["verdict"] == "WEAK", "positive net assets but a working-capital deficit"
    assert fh._uk_balance_sheet_verdict({}) is None, "nothing to say → None (not a false verdict)"


# ── capability wiring + readiness ──────────────────────────────────────────
def test_rf3016_capability_registered():
    assert fh.FINANCIAL_CAPABILITIES.get("registry_figures") is fh._enrich_with_registry_figures


def test_rf3016_enrichment_answers_financial_capacity():
    async def go():
        result = {"data_available": False, "has_financials": False,
                  "health_verdict": "UNKNOWN", "summary": ""}
        fake = {"figures": {"net_assets": {"current": 80000.0, "prior": 60000.0},
                            "net_current_assets": {"current": 20000.0}},
                "made_up_to": "2024-10-31", "accounts_type": "small", "source_url": "http://x"}
        with patch.object(ch, "is_enabled", return_value=True), \
             patch.object(ch, "fetch_accounts_figures", new=AsyncMock(return_value=fake)):
            ok = await fh._enrich_with_registry_figures(result, "Acme Ltd", "GB", "12345678")
        assert ok is True
        assert result["data_available"] is True and result["has_financials"] is True
        assert result["health_verdict"] == "STRONG"
        # the whole point: readiness now scores financial_capacity ANSWERED
        report = {"compliance": {"financial_health":
                  {"data_available": True, "health_verdict": result["health_verdict"]}}}
        dr = _dd_decision_readiness(report)
        assert dr["questions"]["financial_capacity"]["answered"] is True
    asyncio.run(go())


def test_rf3016_pdf_only_stays_unknown_never_false_clean():
    async def go():
        result = {"data_available": False, "has_financials": False, "health_verdict": "UNKNOWN"}
        with patch.object(ch, "is_enabled", return_value=True), \
             patch.object(ch, "fetch_accounts_figures", new=AsyncMock(return_value=None)):
            ok = await fh._enrich_with_registry_figures(result, "Big PLC", "GB", "05684823")
        assert ok is False
        assert result["data_available"] is False, "no iXBRL figures → stays UNKNOWN, never a clean bill"
    asyncio.run(go())
