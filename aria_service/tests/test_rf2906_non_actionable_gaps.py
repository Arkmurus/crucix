"""R-F2906 — simulated and degenerate gaps never reach a queue.

Five weeks of autonomous coder output (data/aria_training/coder_verifiable_gold.jsonl)
was 52 attempts across THREE distinct instructions:
    35x  "Fix gap: End-to-end test gap / A simulated bug for pipeline testing"
    16x  "Fix gap: t\\nd"
     1x  a real failing test  <- the only gold ever produced
51 of 52 billable attempts were spent on a self-declared simulation and a
two-character string, retried indefinitely because nothing filtered them.

The filter runs inside scan(), BEFORE dedupe, so it protects LATEST_KEY, the
coder lane, and the R-F2904 operator review queue in one place. These tests drive
scan() itself, not the predicate in isolation.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.autonomous.gap_detector import (
    Gap,
    GapDetector,
    GapSeverity,
    GapType,
    _is_actionable_gap,
)


def _run(coro):
    return asyncio.run(coro)


def _gap(gap_id: str, title: str, description: str,
         severity: GapSeverity = GapSeverity.HIGH) -> Gap:
    return Gap(
        gap_id=gap_id, gap_type=GapType.MODULE_BUG, severity=severity,
        title=title, description=description, module="aria_service.intel.example",
    )


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value
        return True


class _Extractor:
    def __init__(self, gaps):
        self._gaps = gaps

    async def extract(self, since):
        return list(self._gaps)


# The two shapes that actually burned the lane, verbatim from the live file.
SIMULATED = _gap("sim", "End-to-end test gap", "A simulated bug for pipeline testing")
DEGENERATE = _gap("junk", "t", "d")
REAL = _gap(
    "real",
    "DD layer 3 returned no findings",
    "dd_orchestrator layer 3 produced an empty result for a known-good entity",
)


class TestScanFiltersNonWork:
    def _detector(self, gaps):
        d = GapDetector(_FakeRedis())
        d.extractors = [_Extractor(gaps)]
        return d

    def test_simulated_gap_is_dropped(self):
        out = _run(self._detector([SIMULATED]).scan())
        assert [g.gap_id for g in out] == []

    def test_degenerate_gap_is_dropped(self):
        out = _run(self._detector([DEGENERATE]).scan())
        assert [g.gap_id for g in out] == []

    def test_real_gap_survives(self):
        """The filter must not cost a real signal — that is worse than noise."""
        out = _run(self._detector([REAL]).scan())
        assert [g.gap_id for g in out] == ["real"]

    def test_mixed_batch_keeps_only_the_real_one(self):
        """The live ratio: 2 junk shapes alongside 1 real gap."""
        out = _run(self._detector([SIMULATED, DEGENERATE, REAL]).scan())
        assert [g.gap_id for g in out] == ["real"]


class TestPredicatePrecision:
    """False positives here cost a lost production signal, so the markers are
    multi-word and the content floor is low."""

    @pytest.mark.parametrize("title,desc", [
        ("End-to-end test gap", "A simulated bug for pipeline testing"),
        ("x", "synthetic gap, ignore"),
        ("t", "d"),
        ("", ""),
        ("   ", "  \n "),
    ])
    def test_rejects_non_work(self, title, desc):
        assert _is_actionable_gap(_gap("g", title, desc)) is False

    @pytest.mark.parametrize("title,desc", [
        ("DD layer 3 returned no findings", "empty result for a known-good entity"),
        ("Source RSS 500", "janes.com feed returned HTTP 500 six times"),
        # 'synthetic'/'dummy' appear in REAL gaps — a bare-word filter would eat these.
        ("Synthetic data loader fails", "raises KeyError on an empty batch"),
        ("Dummy credentials rejected", "portal login refuses the placeholder account"),
        ("Test runner rate limited", "coder test runner hit its hourly bucket"),
    ])
    def test_keeps_real_work(self, title, desc):
        assert _is_actionable_gap(_gap("g", title, desc)) is True


class TestOrdering:
    def test_filter_runs_before_dedupe_and_publish(self):
        """If it ran after publish_latest, junk would still reach LATEST_KEY and
        the review queue — the whole point is that no queue ever sees it."""
        r = _FakeRedis()
        d = GapDetector(r)
        d.extractors = [_Extractor([SIMULATED, DEGENERATE])]
        gaps = _run(d.scan())
        _run(d.publish_latest(gaps))
        import json
        published = json.loads(r.store[d.LATEST_KEY])
        assert published == [], f"junk reached LATEST_KEY: {published}"
