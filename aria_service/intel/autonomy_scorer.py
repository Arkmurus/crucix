"""Composite autonomy scorer — replaces single-confidence autonomy level.

Combines four independent quality signals into one composite score,
then maps to five autonomy tiers:

  ≥ 0.85  FULL    — full delivery, all channels
  0.70–85 HIGH    — full delivery, flagged in weekly review
  0.55–70 MEDIUM  — tasks execute, no delivery, human approves output
  0.35–55 LOW     — research only, no mutations
  < 0.35  NONE    — engine paused, only self-assessment tasks run

Signal weights:
  mastery          30%  — student.get_mastery_report().overall_mastery
  verification     35%  — source_verifier grounded rate (the REAL grounding)
  predictor_gate   20%  — 1.0 - (blocks_24h / 10), clamped [0,1]
  honesty_rate     15%  — honesty_judge recent-window score (R-F906: was
                          mislabeled "grounded_rate"; renamed because it
                          reads the honesty judge, not source grounding)

HARD OVERRIDE: predictor BLOCK overrides all signals — if predictor
has blocked >5 tasks in 24h, tier drops to NONE regardless of
composite score.

Also includes 80/20 self-improvement scorer with compliance_risk_reduction
weight (20%) for prioritizing compliance gaps over general accuracy.

Redis key:
  crucix:autonomy:composite     — latest composite score
  crucix:autonomy:history       — 30-day rolling history
  crucix:autonomy:baseline      — Week 4 baseline snapshot
"""
from __future__ import annotations

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

# Signal weights
W_MASTERY = 0.30
W_VERIFICATION = 0.35
W_PREDICTOR = 0.20
W_HONESTY = 0.15  # R-F906: renamed from W_GROUNDED (signal reads honesty judge)

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


async def compute_composite() -> dict:
    """Compute the composite autonomy score from all four signals."""
    signals: dict[str, float | None] = {
        "mastery": None,
        "verification": None,
        "predictor_gate": None,
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
        sample = stats.get("rate_sample_size") or 0
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
        signals["verification"] = val
        details["verification_source"] = source
        details["verification_samples"] = sample
    except Exception as e:
        logger.debug("autonomy scorer: verification failed: %s", e)
        details["verification_source"] = "error"

    # 3. Predictor gate health (20%)
    try:
        from . import redis_store as rs
        blocks_24h = int(await rs.get("crucix:predictor:blocks:24h") or 0)
        # Score: 1.0 at 0 blocks, 0.0 at 10+ blocks
        signals["predictor_gate"] = max(0.0, 1.0 - (blocks_24h / 10.0))
        details["predictor_blocks_24h"] = blocks_24h
    except Exception as e:
        logger.debug("autonomy scorer: predictor failed: %s", e)

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
        signals["honesty_rate"] = val
        details["honesty_rate_source"] = source
        details["honesty_rate_samples"] = sample
    except Exception as e:
        logger.debug("autonomy scorer: honesty failed: %s", e)
        details["honesty_rate_source"] = "error"

    # Compute weighted composite (use 0.5 default for missing signals)
    weighted_sum = 0.0
    total_weight = 0.0
    for signal, weight in [
        ("mastery", W_MASTERY),
        ("verification", W_VERIFICATION),
        ("predictor_gate", W_PREDICTOR),
        ("honesty_rate", W_HONESTY),
    ]:
        val = signals[signal]
        if val is not None:
            weighted_sum += val * weight
            total_weight += weight
        else:
            # Use neutral default for missing signals
            weighted_sum += 0.5 * weight
            total_weight += weight

    composite = round(weighted_sum / total_weight, 4) if total_weight > 0 else 0.5

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
                    "predictor_gate": W_PREDICTOR, "honesty_rate": W_HONESTY},
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
        from .engine_wiring import wire_success as _ws
        _ws(
            module="autonomy_scorer",
            summary=f"Composite score: {composite:.3f} (tier={tier.name})",
            detail=f"mastery={signals['mastery']} verification={signals['verification']} "
                   f"predictor={signals['predictor_gate']} honesty={signals['honesty_rate']} "
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
    return round(
        impact * 0.25
        + frequency * 0.20
        + user_facing * 0.15
        + ease * 0.20
        + compliance_risk * 0.20,
        4,
    )
