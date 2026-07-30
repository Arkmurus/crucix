"""R-F3487 — "market heating" counted articles, not independent sources.

signal_correlator emitted MARKET_HEATING on ``len(signals) >= 4`` alone
(signal_correlator.py:253). It requires >=2 signal TYPES and weight >=5 to form a
cluster, but nowhere does it ask whether those signals came from INDEPENDENT
publishers. Combined with URL-only dedup upstream (sha256(url)[:16]), four
syndicated copies of one wire report — same story, four domains — presented as a
heating market.

That is the fabrication class this product exists to prevent. From memory, both
binding:

  golden_intel_and_grade_a_sources: "don't wire RSS -> distribution_ready
      (single-source = fabrication)"
  usp_verdict_layer_defects:        "one false positive destroys the USP"

Reporting VOLUME is not evidence of real-world CHANGE. A customer acting on
"Poland's market is heating" because Reuters was republished four times is
exactly the harm.

THE FIX IS REUSE, NOT NEW MACHINERY. dd_independent_verifier already solves this
and is hardened by live evals:

  count_independent_origins()  union-find over publisher UNION story:
      same story, different publishers -> wire syndication -> ONE origin
      same publisher, different stories -> one editorial voice -> ONE origin
      "conservative by construction ... can only REDUCE the count, never inflate"
  R-F3388 records the governing rule: the false-positive rate on independence
      MUST be 0, while a conservative undercount is acceptable.

Deliberately NOT used: intel/corroboration.py. It has zero production callers —
dead code, and memory/corroboration_engine_rf2638 records that its fixtures were
green while it scored 0/20 on real data. Building the USP gate on an unproven
engine would be the same mistake twice.
"""
from __future__ import annotations

import pytest

from aria_service.intel import signal_correlator as sc


def _sig(stype, source, url, ts="2026-07-30T09:00:00+00:00", weight=2.0):
    return {"type": stype, "text": "Poland signs tank deal", "source": source,
            "url": url, "ts": ts, "weight": weight}



def _gen(sigs):
    """Drive the real production entry point with its real signature."""
    types = {s["type"] for s in sigs}
    score = sum(s.get("weight", 0.5) for s in sigs)
    return sc._generate_insight("poland", sigs, types, score)

class TestIndependenceIsCounted:

    def test_syndicated_copies_count_as_one_origin(self):
        """Four domains carrying the SAME story is one witness, not four."""
        sigs = [
            _sig("procurement", "Reuters", "https://reuters.com/a", ),
            _sig("procurement", "Yahoo News", "https://news.yahoo.com/a"),
            _sig("procurement", "MSN", "https://msn.com/a"),
            _sig("procurement", "AOL", "https://aol.com/a"),
        ]
        for s in sigs:
            s["story"] = "story-hash-identical"
        assert sc._independent_origins(sigs) == 1, (
            "syndicated copies of one wire report counted as independent sources"
        )

    def test_one_publisher_many_stories_is_one_origin(self):
        sigs = [_sig("procurement", "Janes", f"https://janes.com/{i}") for i in range(4)]
        for i, s in enumerate(sigs):
            s["story"] = f"distinct-{i}"
        assert sc._independent_origins(sigs) == 1, (
            "one publisher is not four independent witnesses"
        )

    def test_genuinely_distinct_publishers_count_separately(self):
        sigs = [
            _sig("procurement", "Janes", "https://janes.com/a"),
            _sig("budget", "Defense News", "https://defensenews.com/b"),
        ]
        for i, s in enumerate(sigs):
            s["story"] = f"distinct-{i}"
        assert sc._independent_origins(sigs) >= 2

    def test_missing_provenance_never_inflates_the_count(self):
        """Unclassifiable sources must collapse to ONE origin, not many.

        R-F3388 fixed exactly this in the DD path: an unusable family table gave
        every source its own origin, so count rose and corroboration said True.
        """
        sigs = [_sig("procurement", "", "") for _ in range(5)]
        assert sc._independent_origins(sigs) <= 1


class TestMarketHeatingRequiresIndependence:

    def test_four_syndicated_signals_do_NOT_heat_the_market(self):
        sigs = [
            _sig("procurement", "Reuters", "https://reuters.com/a"),
            _sig("procurement", "Yahoo News", "https://news.yahoo.com/a"),
            _sig("budget", "MSN", "https://msn.com/a"),
            _sig("budget", "AOL", "https://aol.com/a"),
        ]
        for s in sigs:
            s["story"] = "one-wire-report"
        insight = _gen(sigs)
        if insight is not None:
            assert insight["insight_type"] != "MARKET_HEATING", (
                "four copies of one wire report were reported as a heating market"
            )

    def test_four_independent_signals_still_heat_the_market(self):
        """The gate must not suppress genuine corroboration."""
        sigs = [
            _sig("procurement", "Janes", "https://janes.com/a"),
            _sig("budget", "Defense News", "https://defensenews.com/b"),
            _sig("competitor", "Shephard Media", "https://shephardmedia.com/c"),
            _sig("tender", "Breaking Defense", "https://breakingdefense.com/d"),
        ]
        for i, s in enumerate(sigs):
            s["story"] = f"distinct-{i}"
        insight = _gen(sigs)
        assert insight is not None
        assert insight["insight_type"] == "MARKET_HEATING", insight["insight_type"]

    def test_every_insight_reports_its_independence_count(self):
        """Auditable by construction — a reader can see WHY it was emitted."""
        sigs = [
            _sig("procurement", "Janes", "https://janes.com/a"),
            _sig("budget", "Defense News", "https://defensenews.com/b"),
        ]
        for i, s in enumerate(sigs):
            s["story"] = f"distinct-{i}"
        insight = _gen(sigs)
        assert insight is not None
        assert "independent_origins" in insight
        assert insight["independent_origins"] >= 2
        assert "signal_count" in insight

    def test_recommendation_does_not_claim_volume_it_cannot_support(self):
        """The text must not say 'N signals' when those N are one story."""
        sigs = [_sig("procurement", "Reuters", f"https://r{i}.com/a") for i in range(5)]
        for s in sigs:
            s["story"] = "one-wire-report"
        insight = _gen(sigs)
        if insight is not None:
            assert "5 signals" not in insight.get("recommendation", ""), (
                "recommendation advertises 5 signals from a single origin"
            )
