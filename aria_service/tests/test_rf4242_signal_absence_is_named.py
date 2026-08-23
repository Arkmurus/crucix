"""R-F4242 — a small recording run can REMOVE a well-sampled signal. Say why.

Measured live 2026-08-23, and it is genuinely counter-intuitive.

Before a targeted 6-entry honesty seed:

    verification 0.594   source avg_grounded_rate:lifetime_fallback   samples 83
    confidence 0.75

After it:

    verification None    source insufficient_samples_n4               samples 4
    confidence 0.30      gate_1.unmeasured_signals ['honesty_rate', 'verification']

Writing SIX verification records made the composite measure LESS. Nothing is
broken: `avg_grounded_rate` falls back to the lifetime average only while the 24h
window is quiet (R-F590), the seed woke that window, and R-F3696 made
`effective_sample_size` co-computed with the rate so the two always describe the
SAME window. The window honestly holds 4, which is below
`_MIN_SIGNAL_SAMPLES`, so R-F1907 excludes it. It reverts once the window goes
quiet again.

R-F3696's own comment records this exact symptom — *"zeroed 45% of the composite
and pinned gate #1 at confidence 0.30"* — so the failure mode is known and the
current behaviour is the FIX working, not the bug returning.

## What this test protects

The only reason that was diagnosable in a single probe is that the scorer NAMES
why a signal is absent (`insufficient_samples_n4`) instead of reporting a bare
`None`. A future reader who sees confidence collapse after a recording run and
CANNOT see the reason is one step from "fixing" it by deleting the R-F1907
min-sample guard — which exists because a single 0.0 sample once deflated the
composite from ~0.804 to 0.6028.

So: an absent signal must always carry its reason, and the gate must name it.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import autonomy_scorer


def _run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def _stub_verification(monkeypatch, *, rate, samples):
    from aria_service.intel import source_verifier

    async def _stats():
        return {"avg_grounded_rate": rate,
                "effective_sample_size": samples,
                "rate_sample_size": samples,
                "data_source": "recent_24h",
                "by_verdict": {}}

    monkeypatch.setattr(source_verifier, "get_verification_stats", _stats,
                        raising=True)


class TestAnAbsentSignalCarriesItsReason:

    def test_an_under_sampled_verification_names_the_count(self, monkeypatch):
        """The exact live reading after the seed: n=4, excluded, and SAID SO."""
        _stub_verification(monkeypatch, rate=0.9, samples=4)
        out = _run(autonomy_scorer.compute_composite())
        assert out["signals"]["verification"] is None
        assert out["details"]["verification_source"] == "insufficient_samples_n4", (
            "an excluded signal must name WHY — a bare None sends the reader to "
            "delete the R-F1907 guard that exists to stop n=1 noise deciding 45% "
            "of the gate")
        assert out["details"]["verification_samples"] == 4

    def test_a_well_sampled_verification_is_used(self, monkeypatch):
        """NEGATIVE CONTROL — the guard must not swallow a real signal."""
        _stub_verification(monkeypatch, rate=0.9, samples=40)
        out = _run(autonomy_scorer.compute_composite())
        assert out["signals"]["verification"] == pytest.approx(0.9)
        assert "insufficient" not in out["details"]["verification_source"]

    def test_the_value_and_the_sample_describe_the_same_window(self, monkeypatch):
        """R-F3696's invariant, restated so it cannot silently regress.

        Reading a 24h count against a LIFETIME value is what made the guard
        discard a well-sampled signal as `insufficient_samples_n0`.
        """
        from ._source_probe import function_source

        src = function_source(autonomy_scorer, "compute_composite")
        assert "effective_sample_size" in src, (
            "the sample size must be the one co-computed with the rate "
            "(R-F3696), not a separately-windowed count")


class TestTheGateNamesTheMissingAxis:

    def test_gate_1_lists_every_unmeasured_signal(self, monkeypatch):
        """Both axes absent must BOTH be named, not just the first."""
        from aria_service.intel import phase_gates

        async def _c():
            return {"composite_score": 0.831, "confidence": 0.30,
                    "low_confidence": True,
                    "signals": {"mastery": 0.831, "verification": None,
                                "honesty_rate": None}}

        monkeypatch.setattr(autonomy_scorer, "compute_composite", _c)
        g = _run(phase_gates.compute_phase_gates())["gates"]["gate_1_composite"]
        assert g["pass"] is None
        assert sorted(g["unmeasured_signals"]) == ["honesty_rate", "verification"], (
            "the operator must see BOTH dark axes; naming one hides the other")
