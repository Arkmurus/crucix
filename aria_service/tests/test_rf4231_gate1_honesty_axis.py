"""R-F4231 / C-211 — Phase A gate #1 can certify with the HONESTY axis unmeasured.

MEASURED LIVE 2026-08-22 on aria-intel, `GET /health/composite`:

    signals : {"mastery": 0.838, "verification": 0.594, "honesty_rate": null}
    weights : {"mastery": 0.30,  "verification": 0.45,  "honesty_rate": 0.25}
    details : honesty_rate_source = "no_data_neutral_prior",
              honesty_rate_samples = 0,
              confidence = 0.75, low_confidence = false
    composite_score : 0.6916          -> gate #1 reports pass: false

`GET /api/aria/honesty/stats` (through the RUNNING server, §17) explains the zero:
**55 honesty judgments in the platform's entire lifetime**, 41 of them scored,
`by_status_24h: {}`. The judge fires only when a chat turn ran a tool AND the
response carries confidence tags, so the axis is effectively unpopulated. That is
a separate problem; THIS defect is what the gate does about it.

## The guard's stated contract is stronger than its mechanism

R-F2665's own comment in `phase_gates.py` says gate #1 closes only when the
composite is measured at real confidence — *"(both honesty signals present with
real samples)"*. What it enforces is `not low_confidence`, i.e.
`confidence >= MIN_CONFIDENCE (0.60)`, where `confidence` is the **fraction of
total WEIGHT** backed by data. Honesty carries 0.25. So mastery + verification
alone give 0.75, the flag stays False, and **the honesty axis can be entirely
absent while the gate certifies**. R-F2665 was calibrated against the
mastery-ONLY case (confidence 0.30); once verification started reporting, its
guard went inert — the R-F3791/R-F3858 shape of a guard that stops being able to
fail rather than failing.

This is Phase A, whose name is *Honesty foundation*, and this is its exit gate.

## And the number is not comparable to the target in EITHER direction

`compute_composite` renormalises over measured signals
(`measured_sum / measured_weight`) but gate #1 compares that to `GATE_1_TARGET`,
a threshold defined over the FULL weight set. With honesty missing, the true
full-weight composite for the live signals is:

    honesty 0.00 -> 0.5187   honesty 0.50 -> 0.6437   honesty 1.00 -> 0.7687

It straddles 0.71. So the live `pass: false` is not a measurement either — a
sufficiently honest ARIA would already have closed this gate. The renormalised
score can falsely FAIL as well as falsely PASS, and today it is doing the former
while being one signal-tick away from the latter.

## The fix, and what it is NOT

`pass` becomes tri-state, which is the contract §1/R-F2639 already binds every
gate to: `True`/`False` = measured, **`None` = COULD NOT MEASURE**, rendered
`unknown`, never `open`. An unmeasured weighted axis makes the comparison
undefined, so the honest verdict is `unknown` — with the score and the missing
axes still published, so no information is lost.

This MEASURES MORE, it does not clamp (§1's anti-clamp rule): it refuses to
convert an absence into a verdict. It cannot help Phase A exit — `unknown` is not
a pass, and §1 needs all seven. What it does is make the honesty axis
load-bearing again, so the only way to close gate #1 is to actually produce
honesty judgments.

**Do NOT "fix" a future `unknown` by lowering MIN_CONFIDENCE or by special-casing
honesty back out.** That reintroduces exactly this defect.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import autonomy_scorer
from aria_service.intel import phase_gates


def _run(coro):
    # Loop-safe — mirrors test_rf2665_gate1_confidence so sibling suites that use
    # the legacy get_event_loop idiom are not poisoned.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


def _gate1(monkeypatch, score, confidence, low_confidence, signals):
    async def _c():
        return {"composite_score": score, "confidence": confidence,
                "low_confidence": low_confidence, "signals": signals}
    monkeypatch.setattr(autonomy_scorer, "compute_composite", _c)
    return _run(phase_gates.compute_phase_gates())["gates"]["gate_1_composite"]


# The EXACT live signals measured on aria-intel, 2026-08-22.
LIVE_SIGNALS_HONESTY_DARK = {
    "mastery": 0.838, "verification": 0.594, "honesty_rate": None,
}
ALL_MEASURED = {"mastery": 0.838, "verification": 0.594, "honesty_rate": 0.9}


class TestUnmeasuredAxisIsUnknownNotAVerdict:

    def test_the_live_reading_is_unknown_not_a_measured_failure(self, monkeypatch):
        g = _gate1(monkeypatch, 0.6916, 0.75, False, LIVE_SIGNALS_HONESTY_DARK)
        assert g["pass"] is None, (
            "with honesty unmeasured the renormalised 0.6916 is not comparable to "
            "the 0.71 target — the honest verdict is unknown, not 'measured and "
            "failed'")
        assert g["measurable"] is False

    def test_the_latent_false_certification(self, monkeypatch):
        """THE defect. Today this returns True with ZERO honesty samples.

        Mastery or verification ticking up ~3 points is all it takes; nothing
        about honesty has to change. This is the assertion that fails against the
        pre-fix tree.
        """
        g = _gate1(monkeypatch, 0.75, 0.75, False, LIVE_SIGNALS_HONESTY_DARK)
        assert g["pass"] is not True, (
            "gate #1 must NEVER certify Phase A — the HONESTY foundation — while "
            "the honesty axis has no data. confidence 0.75 clears MIN_CONFIDENCE "
            "0.60 because honesty is only 25% of the weight, which is exactly why "
            "a weight-fraction guard cannot express this.")
        assert g["pass"] is None

    def test_it_names_the_missing_axis(self, monkeypatch):
        g = _gate1(monkeypatch, 0.6916, 0.75, False, LIVE_SIGNALS_HONESTY_DARK)
        assert "honesty_rate" in (g.get("unmeasured_signals") or []), (
            "a reader must be able to see WHICH axis is dark; 'unknown' alone "
            "sends them to read the scorer")

    def test_no_information_is_lost(self, monkeypatch):
        """`unknown` must not hide the score — that would be measuring LESS."""
        g = _gate1(monkeypatch, 0.6916, 0.75, False, LIVE_SIGNALS_HONESTY_DARK)
        assert g["value"] == 0.692          # _gate rounds to 3dp
        assert g["target"] == phase_gates.GATE_1_TARGET
        assert g["confidence"] == 0.75


class TestTheGateStillWorksWhenFullyMeasured:

    def test_fully_measured_above_target_passes(self, monkeypatch):
        g = _gate1(monkeypatch, 0.75, 1.0, False, ALL_MEASURED)
        assert g["pass"] is True
        assert g["measurable"] is True
        assert not (g.get("unmeasured_signals") or [])

    def test_fully_measured_below_target_fails(self, monkeypatch):
        """A real, comparable miss is still a measured FALSE — not softened."""
        g = _gate1(monkeypatch, 0.60, 1.0, False, ALL_MEASURED)
        assert g["pass"] is False
        assert g["measurable"] is True

    def test_rf2665_low_confidence_guard_survives(self, monkeypatch):
        """R-F2665's contract is kept, not replaced: thin evidence never passes."""
        g = _gate1(monkeypatch, 0.75, 0.30, True, {
            "mastery": 0.75, "verification": None, "honesty_rate": None})
        assert g["pass"] is not True


class TestTheGuardCannotGoBlind:

    def test_a_payload_without_signals_is_unknown_not_assumed_fine(self, monkeypatch):
        """Absence of the field must not read as 'nothing is missing'.

        The `{}` -> `[] unmeasured` -> 'all measured' collapse is how this repo's
        three fabricated Phase A gates were certified (§1). If the scorer ever
        stops publishing `signals`, the gate must say so rather than silently
        fall back to the weight-fraction guard this defect is about.
        """
        g = _gate1(monkeypatch, 0.99, 1.0, False, None)
        assert g["pass"] is None
        assert "signals_unavailable" in (g.get("unmeasured_signals") or [])

    def test_the_real_scorer_publishes_what_this_gate_reads(self):
        """Pin the CONTRACT, so the stubs above cannot drift from reality.

        C-208's lesson: a fixture that no longer matches the code turns every
        guard built on it into noise. `signals` must exist and must carry exactly
        the weighted axis names.
        """
        payload = _run(autonomy_scorer.compute_composite())
        assert "signals" in payload
        assert set(payload["signals"]) == set(payload["weights"]), (
            "every weighted axis must appear in `signals`, or an axis could be "
            "weighted while being invisible to the gate")
        assert "honesty_rate" in payload["weights"]
