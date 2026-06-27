"""R-F2041 — /api/aria/opportunities counts ONLY procurement-backed opportunities.

Live 2026-06-27: correlate_signals returned 32 markets (29 MARKET_HEATING + 3
MARKET_SIGNAL — conflict/news volume, 0 procurement signals), and the endpoint
counted all 32 as "opportunities" → dashboard showed 33 when the truth was 0.
Opportunities must be only the actionable types (tender/budget/pipeline-backed);
MARKET_HEATING / MARKET_SIGNAL are situational awareness, returned separately.

Run: python -m pytest aria_service/tests/test_rf2041_opportunities_procurement_only.py -v
"""
from __future__ import annotations
import asyncio

from aria_service.routes import aria as aria_routes


def _ins(country, itype, score=10.0):
    return {"country": country, "score": score, "insight_type": itype,
            "recommendation": "x", "signals": [{"type": "news", "text": "t", "source": "s"}]}


def test_rf2041_only_procurement_backed_counted(monkeypatch):
    from aria_service.intel import signal_correlator as sc
    from aria_service.intel import deal_pipeline as dp

    fake = [
        _ins("Angola", "OPPORTUNITY_WINDOW", 7.0),   # real opportunity
        _ins("Iran", "MARKET_HEATING", 225.5),       # news volume — NOT an opp
        _ins("Israel", "MARKET_HEATING", 101.0),     # NOT an opp
        _ins("Chad", "MARKET_SIGNAL", 6.0),          # generic — NOT an opp
        _ins("Poland", "COMPETITIVE_VACUUM", 8.0),   # real opportunity
    ]

    async def _fake_corr():
        return fake
    async def _fake_pipe():
        return []
    monkeypatch.setattr(sc, "correlate_signals", _fake_corr)
    monkeypatch.setattr(dp, "get_pipeline", _fake_pipe)

    res = asyncio.run(aria_routes.opportunities_ep())

    assert res["count"] == 2, f"only the 2 procurement-backed insights are opportunities, got {res['count']}"
    markets = {o["market"] for o in res["opportunities"]}
    assert markets == {"Angola", "Poland"}
    # MARKET_HEATING/SIGNAL surfaced separately, never counted as opportunities
    assert res["market_signal_count"] == 3
    heating = {m["market"] for m in res["market_signals"]}
    assert heating == {"Iran", "Israel", "Chad"}


def test_rf2041_all_heating_means_zero_opportunities(monkeypatch):
    """The live case: every market is MARKET_HEATING → 0 opportunities (honest)."""
    from aria_service.intel import signal_correlator as sc
    from aria_service.intel import deal_pipeline as dp

    async def _fake_corr():
        return [_ins(c, "MARKET_HEATING") for c in ("Iran", "Ukraine", "Lebanon", "India")]
    async def _fake_pipe():
        return []
    monkeypatch.setattr(sc, "correlate_signals", _fake_corr)
    monkeypatch.setattr(dp, "get_pipeline", _fake_pipe)

    res = asyncio.run(aria_routes.opportunities_ep())
    assert res["count"] == 0, "all-heating must report 0 opportunities"
    assert res["market_signal_count"] == 4
