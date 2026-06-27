"""R-F2003 — brain-side honest opportunities endpoint.

The web-tier opportunity engine pads its list with static editorial templates
(curated HIGH-priority markets always appear; needs/notes fall back to
hand-authored strings) — real-signal-where-it-exists, but misleading at the
edges. This brain endpoint is honest BY CONSTRUCTION: it serves only what
`signal_correlator.correlate_signals()` emits (markets with real correlated
signals), shapes each with its backing evidence, and returns an empty list when
there is nothing — never a template.

These call the ACTUAL endpoint function and assert the user-visible contract.
"""
import asyncio

import aria_service.intel.signal_correlator as sc
import aria_service.intel.deal_pipeline as dp
from aria_service.routes.aria import opportunities_ep


def test_opportunities_are_grounded_only(monkeypatch):
    async def fake_correlate():
        return [
            {"country": "angola", "score": 12.5, "insight_type": "OPPORTUNITY_WINDOW",
             "recommendation": "ACT NOW — 90-120 day window",
             "signals": [{"type": "active_tender", "text": "patrol vessel RFP"},
                         {"type": "warm_contact", "text": "MoD contact"}]},
        ]
    async def fake_pipeline(*a, **k):
        return [{"id": "d1", "country": "angola"}]
    monkeypatch.setattr(sc, "correlate_signals", fake_correlate)
    monkeypatch.setattr(dp, "get_pipeline", fake_pipeline)

    res = asyncio.run(opportunities_ep())
    assert res["ok"] is True and res["count"] == 1
    o = res["opportunities"][0]
    assert o["market"] == "Angola"          # title-cased from country
    assert o["grounded"] is True
    assert o["signal_count"] == 2           # carries the REAL evidence
    assert o["score"] == 12.5               # summed signal weight, not a constant
    assert o["source"] == "signal_correlator"
    assert res["active_pipeline_count"] == 1
    assert "signal-backed" in res["basis"].lower()


def test_empty_is_honest_not_padded(monkeypatch):
    """No signals → empty list + ok=True (never an invented template card)."""
    async def empty():
        return []
    async def fake_pipeline(*a, **k):
        return []
    monkeypatch.setattr(sc, "correlate_signals", empty)
    monkeypatch.setattr(dp, "get_pipeline", fake_pipeline)

    res = asyncio.run(opportunities_ep())
    assert res["ok"] is True
    assert res["count"] == 0
    assert res["opportunities"] == []       # honest emptiness, no static padding


def test_pipeline_failure_is_nonfatal(monkeypatch):
    async def one():
        return [{"country": "kenya", "score": 9.0, "insight_type": "SIGNAL",
                 "recommendation": "", "signals": [{"type": "active_tender", "text": "x"}]}]
    async def boom(*a, **k):
        raise RuntimeError("pipeline down")
    monkeypatch.setattr(sc, "correlate_signals", one)
    monkeypatch.setattr(dp, "get_pipeline", boom)

    res = asyncio.run(opportunities_ep())   # must not raise
    assert res["ok"] is True and res["count"] == 1
    assert res["active_pipeline_count"] == 0
