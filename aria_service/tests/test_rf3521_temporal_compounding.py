"""R-F3521 — the correlator treated time as a binary filter, so nothing compounded.

``correlate_signals`` did this and nothing else with time (signal_correlator.py:130):

    if ts < cutoff:
        continue

A signal 13 days old counted exactly as much as one from this morning, and
everything older than 14 days was discarded outright. The intel ledger retains
~100 years by design (§7 — no TTL on knowledge; RETENTION_DAYS = 36500, live
2026-07-30: 72,729 signals), so the correlator was throwing away almost all of
ARIA's own memory every time it ran.

The capability that costs: COMPOUNDING. Whether a country's activity is
accelerating, merely sustained, or decaying cannot be seen when the only question
asked is "did this land inside a fortnight". Two countries with identical 14-day
scores are indistinguishable even when one has been building for three months and
the other appeared on Tuesday — and that difference is most of the judgement an
analyst is being paid for.

chain_correlator.py does not cover this. It models 12–18 month causal chains from
STRUCTURAL shifts (coup, sanctions change, budget announcement) behind a
MIN_SEVERITY 0.35 gate. Ordinary signal tempo between 14 and 90 days was seen by
neither module.

THE PROPERTY THAT MATTERS MOST, and the reason most of this file tests a
NEGATIVE: widening a correlation window is the textbook way to make an engine
look smarter while making it wrong more often. Every score rises, more insights
fire, and nothing new was learnt — "a grade that improves without new evidence IS
the false clean". So the fix annotates insights that have already been generated
and cannot create one, suppress one, or move a score by any amount. The first
test class asserts exactly that, by diffing full engine output with the
historical band empty against the same run with 90 days of history behind it.

The second discipline carried over is R-F3487's: a trajectory is a claim about
the world, so it is computed from independent ORIGINS rather than signal volume.
Otherwise syndication drives the trend line and the fabrication this product
exists to prevent simply reappears on the time axis.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from aria_service.intel import signal_correlator as sc


NOW = datetime.now(timezone.utc)


def _ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _sig(days_ago, country="angola", text="Defence budget increase approved",
         url="https://janes.com/a", source="janes", stype="news"):
    return {"ts": _ts(days_ago), "countries": [country], "text": text,
            "url": url, "source": source, "type": stype}


def _fresh_cluster(country="angola"):
    """Signals that clear the existing score/type gate on their own merit."""
    return [
        _sig(1, country, "Defence budget increase approved by parliament",
             "https://janes.com/budget", "janes"),
        _sig(2, country, "SIMPORTEX tender issued for armoured vehicles",
             "https://defensenews.com/tender", "defensenews"),
    ]


class _FakeLedger:
    def __init__(self, signals):
        self._signals = signals

    async def _load(self):
        return {"signals": self._signals, "version": 1}


@pytest.fixture(autouse=True)
def _no_leaked_cache():
    """R-F3521 caches the historical band per country at module level.

    Reset around EVERY test. A leaked module-level cache is precisely the
    mechanism behind the 15 order-dependent failures closed by R-F3449 — the
    second test silently observes the first one's answer and the suite passes or
    fails depending on collection order.
    """
    sc._reset_trajectory_cache()
    yield
    sc._reset_trajectory_cache()


@pytest.fixture
def _isolate(monkeypatch):
    """Silence the sibling sources so the ledger is the only variable."""
    from aria_service.intel import deal_pipeline, contact_intelligence, brain_hook

    async def _no_leads():
        return []

    async def _no_contacts():
        return []

    async def _no_absorb(**kw):
        return None

    monkeypatch.setattr(deal_pipeline, "get_pipeline", _no_leads)
    monkeypatch.setattr(contact_intelligence, "get_contacts", _no_contacts)
    monkeypatch.setattr(brain_hook, "absorb", _no_absorb)


async def _run(monkeypatch, signals):
    """One full engine run against a given ledger.

    The cache reset is load-bearing and is NOT papering over a defect. The
    historical band is cached per COUNTRY, which is correct in production: the
    ledger only grows, appends land in the ACTIVE band, and signals age into the
    historical band over days — so a 5-minute TTL cannot serve a materially stale
    answer. These tests instead compare two HYPOTHETICAL WORLDS ("the same
    country, but with and without 90 days of history"), which is something a
    country-keyed cache legitimately cannot distinguish. Two scenarios, not two
    moments on one timeline.
    """
    from aria_service.intel import intel_ledger
    sc._reset_trajectory_cache()
    fake = _FakeLedger(signals)
    monkeypatch.setattr(intel_ledger, "_load", fake._load)
    return await sc.correlate_signals()


def _core(insights):
    """Everything an operator acts on. Trajectory fields are deliberately absent
    — those are what this change is allowed to add."""
    return sorted(
        (i["country"], i["insight_type"], i["score"], i["signal_count"],
         i["independent_origins"], i["independently_corroborated"])
        for i in insights
    )


class TestHistoryCannotInflate:
    """The anti-inflation property, asserted by DIFF rather than by inspection.

    PARAMETRISED OVER HISTORY DEPTH ON PURPOSE. The first version of this test
    used one fixture with 40 historical origins, which yields a DECAYING
    trajectory — so when an inflating implementation was injected deliberately
    (score *= 1.2 whenever ACCELERATING), the test still passed. It never
    exercised the branch it was written to defend.

    _fresh_cluster gives 2 active origins, so with the historical band running
    76 days the ratio is 10.86/H: H<=5 accelerates, 6..21 is sustained, H>=22
    decays, H=0 emerges. The depths below hit all four.
    """

    @pytest.mark.parametrize("hist_depth,expected", [
        (0, "EMERGING"),
        (3, "ACCELERATING"),
        (12, "SUSTAINED"),
        (40, "DECAYING"),
    ])
    @pytest.mark.asyncio
    async def test_history_changes_no_score_and_no_insight(
            self, monkeypatch, _isolate, hist_depth, expected):
        fresh = _fresh_cluster()
        history = [
            _sig(20 + i, "angola", f"Angola defence procurement update {i}",
                 f"https://pub{i}.com/x", f"pub{i}")
            for i in range(hist_depth)
        ]

        without = await _run(monkeypatch, copy.deepcopy(fresh))
        with_hist = await _run(monkeypatch, copy.deepcopy(fresh + history))

        assert _core(without) == _core(with_hist), (
            f"adding {hist_depth} historical origins changed which insights fired "
            f"or what they scored. Historical evidence must annotate, never "
            f"participate"
        )
        # Non-vacuity: prove this run actually reached the trajectory branch the
        # parametrisation is aiming at. Without this the assertion above could be
        # trivially true because nothing was ever measured.
        angola = [i for i in with_hist if i["country"].lower() == "angola"]
        assert angola, "the fixture cluster no longer fires — test is vacuous"
        assert angola[0]["trajectory"] == expected, (
            f"expected the {expected} branch to be exercised, got "
            f"{angola[0]['trajectory']} ({angola[0].get('trajectory_basis')})"
        )

    @pytest.mark.asyncio
    async def test_annotation_only_ADDS_fields_it_never_edits_one(
            self, monkeypatch, _isolate):
        """The property stated ABSOLUTELY rather than differentially.

        The parametrised test above compares "with history" against "without",
        which a UNIFORM inflation defeats: multiply every score by 1.2 on all four
        trajectory labels and both sides move together, so the diff stays equal.
        That variant was injected deliberately and slipped through, which is what
        prompted this test.

        So: snapshot every insight at the moment annotation begins, let the real
        annotator run, and require that nothing which existed before was changed.
        Only new keys may appear.
        """
        captured = {}
        real = sc._annotate_trajectories

        async def _spy(insights):
            captured["before"] = copy.deepcopy(insights)
            await real(insights)
            captured["after"] = copy.deepcopy(insights)

        monkeypatch.setattr(sc, "_annotate_trajectories", _spy)

        history = [_sig(20 + i, "angola", f"Angola history {i}",
                        f"https://pub{i}.com/x", f"pub{i}") for i in range(3)]
        await _run(monkeypatch, _fresh_cluster() + history)

        # The ONLY keys annotation is permitted to write. Listed explicitly rather
        # than pattern-matched on a "trajectory*" prefix, so a future field that
        # quietly starts being rewritten here fails this test instead of slipping
        # under a wildcard.
        WRITABLE = {"trajectory", "trajectory_basis",
                    "historical_signal_count", "historical_independent_origins"}

        before, after = captured.get("before"), captured.get("after")
        assert before, "annotation never ran — the test proves nothing"
        assert len(before) == len(after), "annotation added or removed an insight"
        for b, a in zip(before, after):
            for key, old_value in b.items():
                if key in WRITABLE:
                    continue
                assert a[key] == old_value, (
                    f"annotation MODIFIED {key!r}: {old_value!r} -> {a[key]!r}. "
                    f"It may only write {sorted(WRITABLE)}"
                )
        assert after[0]["trajectory"] != sc.TRAJECTORY_UNKNOWN, (
            "annotation ran but measured nothing — the test would pass vacuously"
        )

    @pytest.mark.asyncio
    async def test_history_alone_cannot_create_an_insight(self, monkeypatch, _isolate):
        """A country with a rich past and a silent present stays silent."""
        history = [
            _sig(30 + i, "kenya", f"Kenya procurement note {i}",
                 f"https://pub{i}.com/k", f"pub{i}")
            for i in range(50)
        ]
        insights = await _run(monkeypatch, history)
        assert not any(i["country"].lower() == "kenya" for i in insights), (
            "history conjured an insight for a country with no current signals"
        )

    @pytest.mark.asyncio
    async def test_history_cannot_reorder_the_operators_list(self, monkeypatch, _isolate):
        """Sorting stays by score. An ACCELERATING label must not jump the queue."""
        sigs = _fresh_cluster("angola") + [
            _sig(1, "nigeria", "Nigeria defence budget increase approved",
                 "https://janes.com/ng", "janes"),
            _sig(1, "nigeria", "Nigeria tender issued for patrol craft",
                 "https://defensenews.com/ng", "defensenews"),
            _sig(2, "nigeria", "Nigeria new defence minister appointed",
                 "https://reuters.com/ng", "reuters"),
        ] + [
            # Deep history for angola only.
            _sig(20 + i, "angola", f"Angola background item {i}",
                 f"https://pub{i}.com/a", f"pub{i}") for i in range(30)
        ]
        insights = await _run(monkeypatch, sigs)
        scores = [i["score"] for i in insights]
        assert scores == sorted(scores, reverse=True), (
            "insights are no longer ordered by score alone"
        )


class TestTrajectoryIsMeasuredFromOrigins:
    """R-F3487's discipline, carried onto the time axis."""

    def test_syndicated_history_does_not_read_as_a_sustained_trend(self):
        """Twelve copies of one wire report is one witness, in any time band."""
        hist_syndicated = {"signals": 12, "origins": 1}
        out = sc._trajectory(active_origins=4, hist=hist_syndicated)
        assert out["trajectory"] == sc.TRAJECTORY_ACCELERATING, out
        assert "1" in out["basis"], (
            "the basis must expose the origin count that drove the verdict"
        )

    def test_a_genuinely_broad_history_is_not_called_accelerating(self):
        out = sc._trajectory(active_origins=2, hist={"signals": 60, "origins": 40})
        assert out["trajectory"] == sc.TRAJECTORY_DECAYING, out

    def test_comparable_rates_read_as_sustained(self):
        # 14d band vs 76d band: 2 origins in 14d ~= 11 origins in 76d.
        out = sc._trajectory(active_origins=2, hist={"signals": 30, "origins": 11})
        assert out["trajectory"] == sc.TRAJECTORY_SUSTAINED, out

    def test_no_prior_coverage_is_emerging_not_accelerating(self):
        """A different claim: new activity, not growth from a measured base."""
        out = sc._trajectory(active_origins=3, hist={"signals": 0, "origins": 0})
        assert out["trajectory"] == sc.TRAJECTORY_EMERGING, out

    def test_thin_evidence_is_unknown_not_a_guess(self):
        out = sc._trajectory(active_origins=1, hist={"signals": 0, "origins": 0})
        assert out["trajectory"] == sc.TRAJECTORY_UNKNOWN, out
        assert out["basis"], "a refusal must say why"

    def test_rates_are_compared_not_raw_counts(self):
        """The bands are 14d and 76d. Comparing totals would call almost
        everything DECAYING purely because the older band is five times longer."""
        # Equal RATES: 2/14 vs 11/76 are within ~5%.
        assert sc._trajectory(2, {"signals": 11, "origins": 11})["trajectory"] == \
            sc.TRAJECTORY_SUSTAINED


class TestBandsDoNotOverlap:

    def test_a_signal_is_never_counted_in_both_bands(self):
        """Overlapping bands would put the same evidence on both sides of the
        comparison, flattening every trajectory toward SUSTAINED."""
        signals = [_sig(d, "angola", f"item {d}", f"https://p{d}.com/x", f"p{d}")
                   for d in (1, 5, 13, 13.9, 20, 60, 89, 120)]
        out = sc._historical_origins_by_country(signals, {"angola"}, NOW)
        # 20, 60, 89 are historical. 1/5/13/13.9 are active; 120 is beyond 90d.
        assert out["angola"]["signals"] == 3, out

    def test_signals_beyond_the_historical_window_are_excluded(self):
        signals = [_sig(200, "angola", "ancient", "https://old.com/x", "old")]
        out = sc._historical_origins_by_country(signals, {"angola"}, NOW)
        assert out["angola"]["signals"] == 0

    def test_only_requested_countries_are_walked(self):
        """The pass runs on the chat request path over ~72k signals, so it must
        not build descriptors for countries no insight asked about."""
        signals = [_sig(30, "kenya", "kenya item", "https://k.com/x", "k"),
                   _sig(30, "angola", "angola item", "https://a.com/x", "a")]
        out = sc._historical_origins_by_country(signals, {"angola"}, NOW)
        assert set(out) == {"angola"}
        assert out["angola"]["signals"] == 1


class TestTheHistoricalBandIsCached:
    """The band is 15..90-day-old data; recomputing it per chat message is the
    defect, not the scan speed. Measured at live scale (72,729 signals) the walk
    is 39ms for one country and 98ms for three."""

    def test_a_second_call_does_not_rewalk_the_ledger(self):
        signals = [_sig(30, "angola", "item", "https://p.com/x", "p")]

        class _CountingList(list):
            def __init__(self, items):
                super().__init__(items)
                self.walks = 0

            def __iter__(self):
                self.walks += 1
                return super().__iter__()

        counting = _CountingList(signals)
        sc._historical_origins_by_country(counting, {"angola"}, NOW)
        sc._historical_origins_by_country(counting, {"angola"}, NOW)
        assert counting.walks == 1, (
            f"the ledger was walked {counting.walks} times for the same country "
            f"inside the cache TTL"
        )

    def test_an_uncached_country_still_gets_measured(self):
        """A partial hit must not silently drop the countries it lacks."""
        signals = [_sig(30, "angola", "a", "https://a.com/x", "a"),
                   _sig(30, "kenya", "k", "https://k.com/x", "k")]
        sc._historical_origins_by_country(signals, {"angola"}, NOW)
        out = sc._historical_origins_by_country(signals, {"angola", "kenya"}, NOW)
        assert set(out) == {"angola", "kenya"}
        assert out["kenya"]["signals"] == 1, (
            "the cached angola entry masked the uncached kenya scan"
        )

    def test_a_failed_origin_count_is_not_cached(self, monkeypatch):
        """Caching a failure would pin a wrong answer for the whole TTL and make
        it indistinguishable from a real measurement."""
        from aria_service.intel import dd_independent_verifier as div

        def _boom(_sources):
            raise RuntimeError("counter down")

        monkeypatch.setattr(div, "count_independent_origins", _boom)
        signals = [_sig(30, "angola", "a", "https://a.com/x", "a")]
        first = sc._historical_origins_by_country(signals, {"angola"}, NOW)
        assert first["angola"]["origins"] == 0

        monkeypatch.undo()
        second = sc._historical_origins_by_country(signals, {"angola"}, NOW)
        assert second["angola"]["origins"] == 1, (
            "a failed count was cached, so the recovered counter was never used"
        )


class TestTheAnnotationIsHonestWhenItFails:

    @pytest.mark.asyncio
    async def test_a_ledger_failure_leaves_insights_intact_and_unknown(
            self, monkeypatch, _isolate):
        """A failure must not remove insights, and must not invent a direction."""
        insights = [{"country": "Angola", "score": 7.0, "independent_origins": 2}]

        from aria_service.intel import intel_ledger

        async def _boom():
            raise RuntimeError("ledger unavailable")

        monkeypatch.setattr(intel_ledger, "_load", _boom)
        await sc._annotate_trajectories(insights)

        assert len(insights) == 1, "a failed annotation dropped an insight"
        assert insights[0]["score"] == 7.0, "a failed annotation changed a score"
        assert insights[0].get("trajectory", sc.TRAJECTORY_UNKNOWN) == \
            sc.TRAJECTORY_UNKNOWN

    @pytest.mark.asyncio
    async def test_empty_insights_is_a_no_op(self):
        await sc._annotate_trajectories([])   # must not raise


class TestEveryInsightCarriesTheField:
    """No consumer should have to test for the key's existence."""

    def test_generate_insight_defaults_to_unknown_not_absent(self):
        sigs = [{"type": "budget_increase", "text": "budget up", "source": "janes",
                 "url": "https://janes.com/a", "story": "s1", "ts": _ts(1),
                 "weight": 3.0},
                {"type": "active_tender", "text": "tender out", "source": "dn",
                 "url": "https://defensenews.com/b", "story": "s2", "ts": _ts(2),
                 "weight": 4.0}]
        out = sc._generate_insight("angola", sigs,
                                   {"budget_increase", "active_tender"}, 7.0)
        assert out["trajectory"] == sc.TRAJECTORY_UNKNOWN, (
            "a directly-generated insight has no historical band to reason from, "
            "so it must say UNKNOWN rather than imply a flat trend"
        )
        assert out["trajectory_basis"]

    @pytest.mark.asyncio
    async def test_the_full_chain_populates_the_trajectory(self, monkeypatch, _isolate):
        sigs = _fresh_cluster("angola") + [
            _sig(20 + i, "angola", f"Angola history {i}",
                 f"https://pub{i}.com/a", f"pub{i}") for i in range(20)
        ]
        insights = await _run(monkeypatch, sigs)
        angola = [i for i in insights if i["country"].lower() == "angola"]
        assert angola, "the fixture cluster no longer fires — test is vacuous"
        a = angola[0]
        assert a["trajectory"] != sc.TRAJECTORY_UNKNOWN, (
            f"trajectory was never measured: {a.get('trajectory_basis')}"
        )
        assert a["historical_independent_origins"] > 0
        assert a["trajectory_basis"]
