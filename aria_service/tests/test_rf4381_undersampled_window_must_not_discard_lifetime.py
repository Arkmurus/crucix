"""R-F4381 (C-326) — a thin window must not DISCARD a well-sampled lifetime signal.

THE DEFECT, and it is non-monotonic: two observations were strictly WORSE than
zero. Measured live 2026-08-27 on aria-intel:

    verify/stats: rate_sample_size 2, effective_sample_size 2,
                  avg_grounded_rate 0.75, data_source "24h_window",
                  lifetime_grounded_rate 0.598, lifetime_sample_size 100
    phase/gates : gate_1 confidence 0.30, unmeasured ["honesty_rate","verification"]

Two rules compose badly. R-F590's fallback in `source_verifier` fires only when
the 24h window is EMPTY (`rolling_rate_24h is None`), while R-F1907's guard in
`autonomy_scorer` discards any value backed by fewer than `_MIN_SIGNAL_SAMPLES`.
With exactly 2 samples the window is non-empty — so the fallback does not fire —
and then fails the guard, throwing away a 100-sample lifetime rate. Had the
window held ZERO samples the fallback WOULD have fired and the gate would have
been decidable.

R-F3696 fixed the adjacent half of this (it aligned the sample COUNT with the
WINDOW the rate came from) and its own docstring calls that "exactly what kept
Phase A gate #1 unpassable" — but it left the trigger keyed on ABSENT rather
than INSUFFICIENT, so the gate stayed dark for a new reason.

This MEASURES MORE, not less (§1): the R-F1907 guard is untouched when there is
genuinely no better evidence, and the fallback is only ever taken to a sample
that is both above the floor AND larger than the one being rejected.

BOTH signals are fixed, not just the one that was caught. The verification and
honesty blocks are the same shape twenty lines apart; fixing only the member
that was measured is how an allow-list rots.

Run: python -m pytest aria_service/tests/test_rf4381_undersampled_window_must_not_discard_lifetime.py -v
"""
from __future__ import annotations

import asyncio

import pytest


def _verif(rate, n24, lifetime_rate, lifetime_n):
    """Shape of source_verifier.get_verification_stats().

    FAITHFUL to the producer: R-F590 substitutes the LIFETIME rate when the 24h
    window holds no scored entry, and R-F3696 then reports the LIFETIME count as
    `effective_sample_size`. Modelling the empty case as `(None, 0)` would make
    the monotonicity test below vacuous — the empty arm would be dark too, so
    the comparison would never fire.
    """
    if n24 == 0:
        return {
            "avg_grounded_rate": lifetime_rate,
            "rolling_grounded_rate": lifetime_rate,
            "rate_sample_size": 0,
            "effective_sample_size": lifetime_n,
            "data_source": "lifetime_fallback",
            "lifetime_grounded_rate": lifetime_rate,
            "lifetime_sample_size": lifetime_n,
            "by_verdict": {},
        }
    return {
        "avg_grounded_rate": rate,
        "rolling_grounded_rate": rate,
        "rate_sample_size": n24,
        "effective_sample_size": n24,
        "data_source": "24h_window",
        "lifetime_grounded_rate": lifetime_rate,
        "lifetime_sample_size": lifetime_n,
        "by_verdict": {},
    }


def _honesty(score, n24, lifetime_score, lifetime_n):
    """Shape of honesty_judge.get_honesty_stats()."""
    return {
        "avg_honesty_score": score,
        "rolling_honesty_score": score,
        "scored_sample_size": n24,
        "lifetime_honesty_score": lifetime_score,
        "lifetime_sample_size": lifetime_n,
        "by_status_24h": {},
    }


def _run(monkeypatch, verif, honesty):
    from aria_service.intel import autonomy_scorer, source_verifier, honesty_judge

    async def _fv():
        return verif

    async def _fh():
        return honesty

    monkeypatch.setattr(source_verifier, "get_verification_stats", _fv)
    monkeypatch.setattr(honesty_judge, "get_honesty_stats", _fh)
    out = asyncio.run(autonomy_scorer.compute_composite())
    return out, (out.get("details") or {})


# ══════════════════════════════════════════════════════════════════════════
# The core defect — the exact live numbers
# ══════════════════════════════════════════════════════════════════════════

def test_the_live_shape_is_measured_not_discarded(monkeypatch):
    """n24=2 with 100 lifetime samples must NOT go dark."""
    out, d = _run(
        monkeypatch,
        _verif(0.75, 2, 0.598, 100),
        _honesty(0.5, 1, 0.259, 52),
    )
    assert out["signals"]["verification"] is not None, (
        "a 100-sample lifetime grounded rate was discarded because the 24h "
        f"window happened to hold 2 samples; source={d.get('verification_source')}"
    )
    assert out["signals"]["honesty_rate"] is not None, (
        "a 52-sample lifetime honesty score was discarded because the 24h "
        f"window held 1 sample; source={d.get('honesty_rate_source')}"
    )
    assert out["confidence"] >= 0.99, (
        f"all three axes are now backed by real samples, so confidence must be "
        f"full; got {out['confidence']}"
    )


@pytest.mark.parametrize("n24", [1, 2, 3, 4])
def test_a_thin_window_is_never_worse_than_an_empty_one(monkeypatch, n24):
    """THE PROPERTY: outcome must be monotonic in sample count.

    Adding observations can never take a measured signal dark. This is the
    invariant the fix exists to restore, and it is asserted as a property
    across the whole below-floor band rather than at the single value that
    happened to be live.
    """
    empty, _ = _run(
        monkeypatch,
        _verif(None, 0, 0.598, 100),
        _honesty(None, 0, 0.259, 52),
    )
    thin, d = _run(
        monkeypatch,
        _verif(0.75, n24, 0.598, 100),
        _honesty(0.5, n24, 0.259, 52),
    )
    for sig in ("verification", "honesty_rate"):
        if empty["signals"][sig] is not None:
            assert thin["signals"][sig] is not None, (
                f"{sig}: {n24} sample(s) produced a DARK signal while ZERO "
                f"samples produced a measured one — adding evidence made the "
                f"gate less decidable. source={d.get(sig + '_source')}"
            )
    assert thin["confidence"] >= empty["confidence"], (
        f"confidence fell from {empty['confidence']} (n=0) to "
        f"{thin['confidence']} (n={n24}) — more evidence, less certainty"
    )


# ══════════════════════════════════════════════════════════════════════════
# The guard must still be able to fail — this measures more, not less
# ══════════════════════════════════════════════════════════════════════════

def test_no_better_evidence_still_discards(monkeypatch):
    """R-F1907's guard stays: thin window AND thin lifetime → still dark."""
    out, d = _run(
        monkeypatch,
        _verif(0.9, 2, 0.9, 2),
        _honesty(0.9, 2, 0.9, 2),
    )
    assert out["signals"]["verification"] is None, (
        "2 samples with no better lifetime evidence is genuinely under-sampled "
        "and must still be rejected — this fix must not become a way to pass "
        f"a gate on thin data; source={d.get('verification_source')}"
    )
    assert out["signals"]["honesty_rate"] is None
    assert "insufficient_samples" in str(d.get("verification_source"))


def test_the_fallback_never_invents_a_signal(monkeypatch):
    """A missing lifetime rate cannot rescue a thin window."""
    out, d = _run(
        monkeypatch,
        _verif(0.9, 2, None, 0),
        _honesty(0.9, 2, None, 0),
    )
    assert out["signals"]["verification"] is None
    assert out["signals"]["honesty_rate"] is None


def test_the_fallback_is_labelled_not_silent(monkeypatch):
    """A consumer must be able to see WHICH window the number came from."""
    _, d = _run(
        monkeypatch,
        _verif(0.75, 2, 0.598, 100),
        _honesty(0.5, 1, 0.259, 52),
    )
    assert "lifetime" in str(d.get("verification_source")).lower(), (
        f"the source tag must name the fallback; got {d.get('verification_source')}"
    )
    assert d.get("verification_samples") == 100, (
        "the reported sample size must describe the window the value came "
        f"from (R-F3696); got {d.get('verification_samples')}"
    )
    assert "lifetime" in str(d.get("honesty_rate_source")).lower()
    assert d.get("honesty_rate_samples") == 52


# ══════════════════════════════════════════════════════════════════════════
# The two safety conditions on the fallback itself.
#
# Added after MUTATION TESTING: removing either condition left every test
# above green, so neither was actually guarded. Red-before-green proves a
# test exercises the path; only mutation proves it constrains it.
# ══════════════════════════════════════════════════════════════════════════

def test_the_fallback_respects_the_same_floor(monkeypatch):
    """A lifetime sample below the floor is not better evidence — stay dark.

    Kills the mutation that drops `lt_n >= _MIN_SIGNAL_SAMPLES`: a 3-sample
    lifetime is larger than a 2-sample window but is still under-sampled, and
    substituting it would lower the evidentiary bar the guard exists to hold.
    """
    out, d = _run(
        monkeypatch,
        _verif(0.9, 2, 0.9, 3),
        _honesty(0.9, 2, 0.9, 3),
    )
    assert out["signals"]["verification"] is None, (
        "fell back to a 3-sample lifetime rate — below _MIN_SIGNAL_SAMPLES. "
        f"source={d.get('verification_source')}"
    )
    assert out["signals"]["honesty_rate"] is None
    assert "insufficient_samples" in str(d.get("verification_source"))


def test_the_fallback_never_substitutes_weaker_evidence():
    """Only STRICTLY better-sampled evidence may replace what is rejected.

    Kills the mutation that drops `lt_n > sample`. Exercised on the helper
    directly: the contract belongs to the helper, and it must hold for any
    caller — not only for the two stat shapes that happen to exist today.
    """
    from aria_service.intel import autonomy_scorer as a

    val, sample, source = a._prefer_better_sampled(
        None, 9, "no_data_neutral_prior",
        lifetime_val=0.4, lifetime_n=5,
    )
    assert val is None, (
        f"substituted a 5-sample lifetime for a 9-sample window — the fallback "
        f"must only ever move to MORE evidence, not less (got {val} n={sample} "
        f"source={source})"
    )

    # ...and the mirror case still works, so this is a constraint, not a block.
    val, sample, source = a._prefer_better_sampled(
        None, 0, "no_data_neutral_prior",
        lifetime_val=0.4, lifetime_n=5,
    )
    assert val == 0.4 and sample == 5, (
        "a genuinely better-sampled lifetime must still be adopted"
    )
