"""Composite autonomy scorer — replaces single-confidence autonomy level.

Combines four independent quality signals into one composite score,
then maps to five autonomy tiers:

  ≥ 0.85  FULL    — full delivery, all channels
  0.70–85 HIGH    — full delivery, flagged in weekly review
  0.55–70 MEDIUM  — tasks execute, no delivery, human approves output
  0.35–55 LOW     — research only, no mutations
  < 0.35  NONE    — engine paused, only self-assessment tasks run

Signal weights (R-F1350 — HONEST composite):
  mastery          30%  — capability (headline_mastery)
  verification     45%  — source_verifier grounded rate (the REAL grounding)
  honesty_rate     25%  — honesty_judge recent-window score

R-F1350: predictor_gate was REMOVED from the weighted composite. It was
`1.0 - blocks_24h/10` at 20% weight, but `blocks_24h` is written ONLY by the
autonomous task-block loop (tasks.py) — chat/DD never touch it — so in normal
operation it is a near-permanent 1.0 = a +0.20 constant unrelated to honesty
or grounding. It inflated Phase A gate #1's "composite >=71%" by ~0.20 and
double-counted (predictor is ALSO the hard override below). It now serves ONLY
its legitimate purpose: the hard safety override. The freed 20% was given to
the two signals that actually measure honesty — verification (grounding, +10)
and honesty_rate (+10) — so every weighted component now measures real
honesty/grounding/capability.

CONFIDENCE: the composite renormalises over the signals that have REAL data
(no silent 0.5 padding). `confidence` = fraction of weight backed by data;
`low_confidence` flags a score built mostly on defaults — so the number is
honest about how much it actually knows.

HARD OVERRIDE: if the predictor has blocked >5 tasks in 24h, tier drops to
NONE regardless of composite score.

Also includes 80/20 self-improvement scorer with compliance_risk_reduction
weight (20%) for prioritizing compliance gaps over general accuracy.

Redis key:
  crucix:autonomy:composite     — latest composite score
  crucix:autonomy:history       — 30-day rolling history
  crucix:autonomy:baseline      — Week 4 baseline snapshot
"""
from __future__ import annotations
from .engine_wiring import wire_failure, wire_success

import logging
import time
from enum import IntEnum
from typing import Any

logger = logging.getLogger("aria.autonomy_scorer")

_K_COMPOSITE = "crucix:autonomy:composite"
_K_HISTORY = "crucix:autonomy:history"
_K_BASELINE = "crucix:autonomy:baseline"


class AutonomyTier(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    FULL = 4


TIER_LABELS = {
    AutonomyTier.NONE: "NONE — engine paused",
    AutonomyTier.LOW: "LOW — research only",
    AutonomyTier.MEDIUM: "MEDIUM — execute, human approves",
    AutonomyTier.HIGH: "HIGH — full delivery, weekly flag",
    AutonomyTier.FULL: "FULL — all channels, autonomous",
}

# Signal weights (R-F1350: predictor removed from weights → override-only;
# its 20% redistributed to the real honesty signals).
W_MASTERY = 0.30
W_VERIFICATION = 0.45  # R-F1350: 0.35 + 0.10 (was predictor's)
W_HONESTY = 0.25       # R-F1350: 0.15 + 0.10 (was predictor's)
# Below which `confidence` (fraction of weight backed by real data) the
# composite is flagged low_confidence rather than presented as a hard number.
MIN_CONFIDENCE = 0.60

# R-F1907 — a signal scored from FEWER than this many recent samples is too
# noisy to trust as a hard number. Live 2026-06-25: a SINGLE honesty_judge
# sample of 0.0 (scored_sample_size=1) drove honesty_rate=0.0 and deflated the
# composite from ~0.804 to 0.6028 — despite 91% all-time honesty (167 ok / 16
# judge_failed). R-F1350 already excludes a None signal + renormalises ("no
# padding"); this extends that: an under-sampled signal is treated as no_data
# (None) so it is excluded + renormalised (confidence flagged) rather than letting
# n=1 noise determine 25% of the gate. NEVER inflates — only stops noise from
# spuriously deflating. Verification's avg already carries enough samples; the
# guard future-proofs it too.
_MIN_SIGNAL_SAMPLES = 5

# Tier thresholds
TIER_THRESHOLDS = [
    (0.85, AutonomyTier.FULL),
    (0.70, AutonomyTier.HIGH),
    (0.55, AutonomyTier.MEDIUM),
    (0.35, AutonomyTier.LOW),
    (0.00, AutonomyTier.NONE),
]

# Hard override: predictor blocks > this → NONE regardless
PREDICTOR_BLOCK_OVERRIDE = 5


def _prefer_better_sampled(val, sample, source, *, lifetime_val, lifetime_n):
    """R-F4381 (C-326) — a thin window must not DISCARD better lifetime evidence.

    Two rules composed badly and made the outcome NON-MONOTONIC: two
    observations were strictly worse than zero. R-F590's fallback in
    `source_verifier` fires only when the 24h window is EMPTY, while R-F1907's
    guard here discards anything below `_MIN_SIGNAL_SAMPLES`. With exactly 2
    samples the window is non-empty (so no fallback) and then fails the guard —
    throwing away a 100-sample lifetime rate. At ZERO samples the fallback
    would have fired and the gate would have been decidable. Measured live
    2026-08-27: gate #1 confidence 0.30, verification and honesty both dark,
    against `lifetime_sample_size` 100 and 52.

    R-F3696 fixed the adjacent half — it aligned the sample COUNT with the
    WINDOW the rate came from — but left the trigger keyed on ABSENT rather
    than INSUFFICIENT, so the gate stayed dark for a new reason.

    This MEASURES MORE, not less (§1). The guard is untouched when there is no
    better evidence: the fallback is taken only to a sample that is BOTH above
    the floor AND strictly larger than the one being rejected, so it can never
    manufacture a signal, never lower the evidentiary bar, and never pass a
    gate on thin data. A missing lifetime rate rescues nothing.

    The label is preserved so a consumer can always see which window served —
    a silent substitution would be the same unreadable-provenance defect this
    repo has recorded against three Phase A gates.
    """
    sample = int(sample or 0)
    if val is not None and sample >= _MIN_SIGNAL_SAMPLES:
        return val, sample, source
    lt_n = int(lifetime_n or 0)
    if lifetime_val is not None and lt_n >= _MIN_SIGNAL_SAMPLES and lt_n > sample:
        return lifetime_val, lt_n, f"lifetime_fallback_rf4381:{source}"
    if val is None:
        return None, sample, source
    return None, sample, f"insufficient_samples_n{sample}"


async def compute_composite() -> dict:
    """Compute the composite autonomy score from all four signals."""
    # R-F1350: predictor_gate is no longer a weighted signal (override-only).
    signals: dict[str, float | None] = {
        "mastery": None,
        "verification": None,
        "honesty_rate": None,
    }
    details: dict[str, Any] = {}

    # 1. Mastery (30%) — use headline_mastery (min of weighted-overall and
    # core-mastery mean) so the autonomy gate cannot be pulled up by
    # high-sample easy topics while the 9 load-bearing capability cells
    # are starved. Falls back to overall_mastery if the key is missing
    # (e.g. older report payloads in transit).
    try:
        from . import student
        report = await student.get_mastery_report()
        signals["mastery"] = report.get(
            "headline_mastery", report.get("overall_mastery", 0.5)
        )
        details["mastery_topics"] = len(report.get("topics", {}))
        details["weak_topics"] = report.get("weak_topics", [])
        details["core_mastery"] = report.get("core_mastery")
        details["core_weak_topics"] = report.get("core_weak_topics", [])
    except Exception as e:
        logger.debug("autonomy scorer: mastery failed: %s", e)

    # 2. Verification / grounded rate (35%) — R-F576 honesty fix.
    #
    # Pre-R-F576 this read `avg_grounded_rate` only. That field is None
    # when verification entries lack a numeric grounded_rate (the field
    # gets populated by a follow-up RAG-grounding pass that doesn't
    # always run on every entry). Result: composite fell back to the
    # 0.5 neutral prior even when there were 23 real verdicts in the
    # last 24h (verified/unverified/contradicted). Dashboard showed
    # "50%* (default — no data yet)" while /verification/stats showed
    # "verification rate 39%".
    #
    # Fix: if `avg_grounded_rate` is None but verdicts exist, compute a
    # verdict-ratio proxy (verified / total_with_decision). Mark the
    # signal source so the dashboard can render the honest origin.
    try:
        from . import source_verifier
        stats = await source_verifier.get_verification_stats()
        val = stats.get("avg_grounded_rate")
        source = "avg_grounded_rate"
        # R-F3696 — the sample size must describe the WINDOW `val` came from.
        # `rate_sample_size` is 24h-only, but `avg_grounded_rate` falls back to
        # the LIFETIME average when the 24h window is quiet (R-F590). Reading
        # the 24h count against a lifetime value made the R-F1907 guard below
        # discard a well-sampled signal as `insufficient_samples_n0` — which
        # zeroed 45% of the composite and pinned gate #1 at confidence 0.30.
        # `effective_sample_size` is co-computed with the rate in
        # source_verifier, so the two can no longer describe different windows.
        # `.get(... , None)` then falling back keeps this working against an
        # older stats dict that predates the field.
        sample = stats.get("effective_sample_size")
        if sample is None:
            sample = stats.get("rate_sample_size") or 0
        sample = int(sample or 0)
        if stats.get("data_source"):
            source = f"avg_grounded_rate:{stats['data_source']}"
        if val is None:
            by_verdict = stats.get("by_verdict") or {}
            verified = int(by_verdict.get("verified") or 0)
            unverified = int(by_verdict.get("unverified") or 0)
            contradicted = int(by_verdict.get("contradicted") or 0)
            total = verified + unverified + contradicted
            if total > 0:
                val = round(verified / total, 4)
                source = "verdict_ratio_rf576"
                sample = total
            else:
                source = "no_data_neutral_prior"
        # R-F1907 — same min-sample guard as honesty: don't let an under-sampled
        # grounded-rate determine 45% of the composite.
        # R-F4381 (C-326) — ...but prefer a better-sampled lifetime rate over
        # discarding outright, so 2 samples can never be worse than 0.
        val, sample, source = _prefer_better_sampled(
            val, sample, source,
            lifetime_val=stats.get("lifetime_grounded_rate"),
            lifetime_n=stats.get("lifetime_sample_size"),
        )
        signals["verification"] = val
        details["verification_source"] = source
        details["verification_samples"] = sample
    except Exception as e:
        logger.debug("autonomy scorer: verification failed: %s", e)
        details["verification_source"] = "error"

    # 3. Predictor blocks — read for the HARD OVERRIDE only (R-F1350: no
    # longer a weighted signal; blocks_24h is written only by the autonomous
    # task-block loop, so as a weight it was a near-constant +0.20).
    try:
        from . import redis_store as rs
        details["predictor_blocks_24h"] = int(
            await rs.get("crucix:predictor:blocks:24h") or 0
        )
    except Exception as e:
        logger.debug("autonomy scorer: predictor blocks read failed: %s", e)
        details["predictor_blocks_24h"] = 0

    # 4. Honesty rate from judge (15%) — R-F906.
    #
    # NOTE ON NAMING (R-F906): this signal was historically keyed
    # "grounded_rate", but it reads the HONESTY JUDGE, not source grounding.
    # Source grounding is the "verification" signal above
    # (source_verifier.avg_grounded_rate). The mislabel made the dashboard
    # show a "grounded_rate" row that was actually an honesty score — and a
    # structurally depressed one (see below). Renamed to "honesty_rate".
    #
    # `avg_honesty_score` is the 24h rolling average; it is None when no
    # in-window "ok" judgment carries a numeric honesty_score. The PRE-R-F906
    # fallback then used the ALL-TIME `by_status` ok/total ratio — a lifetime
    # number that never recovers from historical suspicious/failed verdicts,
    # which pinned this signal at ~17% and dragged the composite into a
    # permanent DEGRADED read. R-F906 uses a fair RECENT-WINDOW status ratio
    # (by_status_24h) to match the 24h shape of every other signal; with no
    # recent data it returns the neutral prior + an honest source label
    # rather than a misleading depressed number.
    try:
        from . import honesty_judge
        h_stats = await honesty_judge.get_honesty_stats()
        val = h_stats.get("avg_honesty_score")  # 24h rolling avg
        source = "avg_honesty_score"
        sample = h_stats.get("scored_sample_size") or 0
        if val is None:
            by_status_24h = h_stats.get("by_status_24h") or {}
            ok = int(by_status_24h.get("ok") or 0)
            suspicious = int(by_status_24h.get("suspicious") or 0)
            failed = int(by_status_24h.get("failed") or 0)
            contradicted = int(by_status_24h.get("contradicted") or 0)
            total = ok + suspicious + failed + contradicted
            if total > 0:
                val = round(ok / total, 4)
                source = "status_ratio_24h_rf906"
                sample = total
            else:
                source = "no_data_neutral_prior"
        # R-F1907 — min-sample guard: a value scored from < _MIN_SIGNAL_SAMPLES
        # recent judgments is too noisy to weight (a single 0.0 sample deflated
        # the composite ~0.80->0.60 despite 91% all-time honesty). Exclude it
        # (None -> renormalised + confidence flagged), never inflate.
        # R-F4381 (C-326) — same shape, twenty lines apart. Live, honesty had 52
        # lifetime judgments and used NONE of them because the 24h window held 1.
        # Fixing only the member that was measured is how an allow-list rots.
        val, sample, source = _prefer_better_sampled(
            val, sample, source,
            lifetime_val=h_stats.get("lifetime_honesty_score"),
            lifetime_n=h_stats.get("lifetime_sample_size"),
        )
        signals["honesty_rate"] = val
        details["honesty_rate_source"] = source
        details["honesty_rate_samples"] = sample
    except Exception as e:
        logger.debug("autonomy scorer: honesty failed: %s", e)
        details["honesty_rate_source"] = "error"

    # R-F1350: compute over the signals that have REAL data and renormalise —
    # no silent 0.5 padding (padding flattered a data-starved score toward
    # neutral). `confidence` = fraction of total weight backed by real data;
    # a low-confidence score is flagged, not dressed up as a hard number.
    _WEIGHTS = [
        ("mastery", W_MASTERY),
        ("verification", W_VERIFICATION),
        ("honesty_rate", W_HONESTY),
    ]
    _total_weight = sum(w for _, w in _WEIGHTS)
    measured_sum = 0.0
    measured_weight = 0.0
    for signal, weight in _WEIGHTS:
        val = signals[signal]
        if val is not None:
            measured_sum += val * weight
            measured_weight += weight

    if measured_weight > 0:
        composite = round(measured_sum / measured_weight, 4)
    else:
        composite = 0.5  # genuinely no data — neutral prior, flagged below
    confidence = round(measured_weight / _total_weight, 4) if _total_weight else 0.0
    low_confidence = confidence < MIN_CONFIDENCE
    details["confidence"] = confidence
    details["low_confidence"] = low_confidence
    details["signals_measured"] = [s for s, _ in _WEIGHTS if signals[s] is not None]

    # Determine tier
    tier = AutonomyTier.NONE
    for threshold, t in TIER_THRESHOLDS:
        if composite >= threshold:
            tier = t
            break

    # HARD OVERRIDE: predictor block count
    blocks = details.get("predictor_blocks_24h", 0)
    override = None
    if blocks >= PREDICTOR_BLOCK_OVERRIDE:
        override = f"predictor blocked {blocks} tasks (>={PREDICTOR_BLOCK_OVERRIDE})"
        tier = AutonomyTier.NONE

    result = {
        "composite_score": composite,
        "tier": tier.value,
        "tier_name": tier.name,
        "tier_label": TIER_LABELS[tier],
        "signals": {k: round(v, 4) if v is not None else None for k, v in signals.items()},
        "weights": {"mastery": W_MASTERY, "verification": W_VERIFICATION,
                    "honesty_rate": W_HONESTY},  # R-F1350: predictor is override-only
        "confidence": confidence,          # R-F1350: fraction of weight with real data
        "low_confidence": low_confidence,  # R-F1350: True when mostly defaults
        "override": override,
        "computed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "details": details,
    }

    # Persist
    try:
        from . import redis_store as rs
        await rs.set_json(_K_COMPOSITE, result, ex=86400)
        history = await rs.get_json(_K_HISTORY) or []
        history.insert(0, {
            "score": composite, "tier": tier.name,
            "at": result["computed_at"],
            "override": override,
        })
        await rs.set_json(_K_HISTORY, history[:720], ex=30 * 86400)  # 30 days hourly
    except Exception:
        pass

    # R-F1059 — wire composite score to brain
    try:
        from .engine_wiring import wire_success as _ws, wire_failure
        _ws(
            module="autonomy_scorer",
            summary=f"Composite score: {composite:.3f} (tier={tier.name}, conf={confidence})",
            detail=f"mastery={signals['mastery']} verification={signals['verification']} "
                   f"honesty={signals['honesty_rate']} confidence={confidence} "
                   f"low_conf={low_confidence} blocks={details.get('predictor_blocks_24h')} "
                   f"override={override}",
            source_id="autonomy_scorer",
        )
    except Exception:
        pass

    return result


async def get_latest() -> dict | None:
    """Return the latest composite score."""
    try:
        from . import redis_store as rs
        return await rs.get_json(_K_COMPOSITE)
    except Exception:
        pass


        return None


async def get_history(limit: int = 168) -> list[dict]:
    """Return composite score history (default: 7 days hourly)."""
    try:
        from . import redis_store as rs
        history = await rs.get_json(_K_HISTORY) or []
        return history[:limit]
    except Exception:
        pass
        return []


async def save_baseline() -> dict:
    """Snapshot the current composite as the Week 4 baseline.
    Every future score is compared against this."""
    result = await compute_composite()
    try:
        from . import redis_store as rs
        baseline = {
            "snapshot": result,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "description": "Week 4 baseline — first calibrated measurement",
        }
        await rs.set_json(_K_BASELINE, baseline, ex=365 * 86400)
    except Exception:
        pass
        pass
    return result


async def get_baseline() -> dict | None:
    """Return the saved baseline."""
    try:
        from . import redis_store as rs
        return await rs.get_json(_K_BASELINE)
    except Exception:
        pass
        return None


# ── 80/20 Self-improvement scorer ─────────────────────────────────────────

def score_improvement(
    *,
    impact: float = 0.0,          # 0-1: how much accuracy/coverage improves
    frequency: float = 0.0,       # 0-1: how often the gap is hit
    user_facing: float = 0.0,     # 0-1: visible to users/clients?
    ease: float = 0.0,            # 0-1: how easy to implement
    compliance_risk: float = 0.0, # 0-1: does it reduce compliance risk?
) -> float:
    """Score a self-improvement candidate with compliance weighting.

    Weights: impact(25%) + frequency(20%) + user_facing(15%) +
    ease(20%) + compliance_risk_reduction(20%).

    Compliance gaps score higher than general accuracy improvements.
    """
    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="autonomy_scorer",
                     summary="autonomy_scorer module active",
                     source_id="autonomy_scorer:init")
    except Exception:
        try:
            wire_failure(module="autonomy_scorer", detail="module init failed",
                        gap_type="engine_failure", source="autonomy_scorer:init")
        except Exception:
            pass

    return round(
        impact * 0.25
        + frequency * 0.20
        + user_facing * 0.15
        + ease * 0.20
        + compliance_risk * 0.20,
        4,
    )
