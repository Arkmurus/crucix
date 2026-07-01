"""R-F2230 — grounded competitive SWOT frame never fabricates.

The value taken from the competitive-intelligence-analyst template is the SWOT
STRUCTURE, grounded in real stored data — NOT the "ask an LLM to fill the cells"
step that produced the R-F2002 fabrication. These tests drive the real
grounded_swot() and assert every filled cell is backed by a source, and that
un-backed cells are INSUFFICIENT_DATA, not invented.
"""
from __future__ import annotations

from aria_service.intel.competitive_frame import grounded_swot


class TestR_F2230_GroundedSWOT:
    def test_threats_grounded_from_real_competitors(self):
        f = grounded_swot()
        assert isinstance(f["threats"], list) and f["threats"], "threats must come from real COMPETITORS"
        for t in f["threats"]:
            assert t["level"] in ("HIGH", "MEDIUM")
            assert t["source"] == "competitors.COMPETITORS"
            assert t["evidence"], "every threat must carry grounded evidence (its strategy)"

    def test_strengths_weaknesses_insufficient_without_signals(self):
        """The discriminating no-fabrication test: with no client signals, the
        client's own strengths/weaknesses must be INSUFFICIENT_DATA, never made up."""
        f = grounded_swot()
        assert f["strengths"] == {"status": "insufficient_data", "note": "no grounded source for strengths (client position)"}
        assert f["weaknesses"]["status"] == "insufficient_data"

    def test_opportunities_include_grounded_openings(self):
        f = grounded_swot()
        assert isinstance(f["opportunities"], list) and f["opportunities"]
        # e.g. Rostec/Almaz-Antey "Sanctioned — replacement opportunity", Denel "acquisition opportunity"
        joined = " ".join(o["evidence"].lower() for o in f["opportunities"])
        assert any(k in joined for k in ("opportunity", "sanctioned", "acquisition", "partner"))
        for o in f["opportunities"]:
            assert o["source"] in ("competitors.COMPETITORS", "signal_correlator")

    def test_market_filter_narrows_the_set(self):
        full = grounded_swot()
        one = grounded_swot("Norinco")  # matches a single competitor by name
        assert one["competitors_considered"] == 1
        assert one["competitors_considered"] < full["competitors_considered"]

    def test_signal_backed_strength_is_grounded_not_invented(self):
        sig = [{"insight_type": "strength", "country": "Angola",
                "recommendation": "warm MoD contacts + prior delivery track record"}]
        f = grounded_swot(signals=sig)
        assert isinstance(f["strengths"], list) and f["strengths"]
        assert f["strengths"][0]["source"] == "signal_correlator"
        assert f["strengths"][0]["evidence"] == "warm MoD contacts + prior delivery track record"

    def test_no_filled_cell_lacks_a_source(self):
        """Grounding invariant: every filled threat/opportunity item has a source."""
        f = grounded_swot(signals=[{"insight_type": "opportunity", "country": "Kenya",
                                    "recommendation": "budget increase signal"}])
        for cell in ("threats", "opportunities"):
            items = f[cell]
            if isinstance(items, list):
                assert all(i.get("source") for i in items), f"{cell} has an ungrounded item"
