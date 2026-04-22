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
  verification     35%  — source_verifier grounded rate
  predictor_gate   20%  — 1.0 - (blocks_24h / 10), clamped [0,1]
  grounded_rate    15%  — honesty_judge average score

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
W_GROUNDED = 0.15

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
        "grounded_rate": None,
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

    # 2. Verification / grounded rate (35%)
    try:
        from . import source_verifier
        stats = await source_verifier.get_verification_stats()
        signals["verification"] = stats.get("avg_grounded_rate")
    except Exception as e:
        logger.debug("autonomy scorer: verification failed: %s", e)

    # 3. Predictor gate health (20%)
    try:
        from . import redis_store as rs
        blocks_24h = int(await rs.get("crucix:predictor:blocks:24h") or 0)
        # Score: 1.0 at 0 blocks, 0.0 at 10+ blocks
        signals["predictor_gate"] = max(0.0, 1.0 - (blocks_24h / 10.0))
        details["predictor_blocks_24h"] = blocks_24h
    except Exception as e:
        logger.debug("autonomy scorer: predictor failed: %s", e)

    # 4. Honesty / grounded rate from judge (15%)
    try:
        from . import honesty_judge
        h_stats = await honesty_judge.get_honesty_stats()
        signals["grounded_rate"] = h_stats.get("avg_honesty_score")
    except Exception as e:
        logger.debug("autonomy scorer: honesty failed: %s", e)

    # Compute weighted composite (use 0.5 default for missing signals)
    weighted_sum = 0.0
    total_weight = 0.0
    for signal, weight in [
        ("mastery", W_MASTERY),
        ("verification", W_VERIFICATION),
        ("predictor_gate", W_PREDICTOR),
        ("grounded_rate", W_GROUNDED),
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
                    "predictor_gate": W_PREDICTOR, "grounded_rate": W_GROUNDED},
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

    return result


async def get_latest() -> dict | None:
    """Return the latest composite score."""
    try:
        from . import redis_store as rs
        return await rs.get_json(_K_COMPOSITE)
    except Exception:
        return None


async def get_history(limit: int = 168) -> list[dict]:
    """Return composite score history (default: 7 days hourly)."""
    try:
        from . import redis_store as rs
        history = await rs.get_json(_K_HISTORY) or []
        return history[:limit]
    except Exception:
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
    return result


async def get_baseline() -> dict | None:
    """Return the saved baseline."""
    try:
        from . import redis_store as rs
        return await rs.get_json(_K_BASELINE)
    except Exception:
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
