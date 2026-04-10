"""ARIA Skill Calibration Engine — Brier scoring with Redis persistence.

Tracks ARIA's prediction accuracy over time using the same methodology
that measures superforecasters (IARPA Good Judgment Project).

  Brier score of 0.00 = perfect calibration
  Brier score of 0.25 = random chance (useless)
  Brier score >0.25  = worse than random (miscalibrated)

Every assessment ARIA makes is recorded with her stated confidence. When
outcomes are later resolved, the Brier contribution is computed and
stored. Domain-level aggregates drive the confidence recalibration
addenda in the system prompt.

All state in Redis (crucix:metacog:calibration:*). Survives redeploys.
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Optional

from ..intel import redis_store as rs
from . import domains as dom

logger = logging.getLogger("aria.metacognitive.calibration")

# Redis key patterns
_ASSESSMENTS_KEY = "crucix:metacog:calibration:assessments"  # list of JSON
_DOMAIN_BRIER_KEY = "crucix:metacog:calibration:brier:{domain}"  # JSON aggregate
_GLOBAL_STATS_KEY = "crucix:metacog:calibration:global_stats"
_MAX_ASSESSMENTS = 2000  # rolling window


async def record_assessment(
    assessment_id: str,
    domain: str,
    claim: str,
    stated_confidence: float,
    outcome: bool | None = None,
) -> dict:
    """Record an ARIA assessment for later Brier scoring.

    Args:
        assessment_id: Unique ID (e.g. ARK-ASSESS-2026-0047)
        domain: Which capability domain
        claim: What ARIA claimed or predicted
        stated_confidence: How confident she said she was (0.0-1.0)
        outcome: True if correct, False if wrong, None if unresolved
    """
    record = {
        "id": assessment_id,
        "domain": domain,
        "claim": claim[:500],
        "stated_confidence": max(0.0, min(1.0, stated_confidence)),
        "outcome": outcome,
        "brier_contribution": None,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }

    if outcome is not None:
        outcome_int = 1 if outcome else 0
        record["brier_contribution"] = (stated_confidence - outcome_int) ** 2

    await rs.lpush(_ASSESSMENTS_KEY, json.dumps(record))
    await rs.ltrim(_ASSESSMENTS_KEY, 0, _MAX_ASSESSMENTS - 1)

    # Update domain aggregate if we have an outcome
    if outcome is not None:
        await _update_domain_aggregate(domain, record["brier_contribution"])

    return record


_RESOLVED_KEY = "crucix:metacog:calibration:resolved"
_MAX_RESOLVED = 1000


async def resolve_assessment(
    assessment_id: str,
    outcome: bool,
) -> dict | None:
    """Resolve a previously unresolved assessment with its outcome.

    Scans recent assessments for the ID, computes Brier contribution,
    updates the domain aggregate. Stores the resolution in a separate
    resolved list to avoid duplicating the original record.
    """
    records = await rs.lrange(_ASSESSMENTS_KEY, 0, _MAX_ASSESSMENTS - 1)
    for i, raw in enumerate(records):
        try:
            rec = json.loads(raw)
        except Exception:
            continue
        if rec.get("id") != assessment_id:
            continue
        if rec.get("outcome") is not None:
            return rec  # already resolved

        conf = rec["stated_confidence"]
        outcome_int = 1 if outcome else 0
        brier = (conf - outcome_int) ** 2

        rec["outcome"] = outcome
        rec["brier_contribution"] = brier
        rec["resolved_at"] = datetime.now(timezone.utc).isoformat()

        # Store resolution in a separate list to avoid duplicating in
        # the main assessments list (can't atomically update a list
        # element in Redis without Lua).
        await rs.lpush(_RESOLVED_KEY, json.dumps(rec))
        await rs.ltrim(_RESOLVED_KEY, 0, _MAX_RESOLVED - 1)
        await _update_domain_aggregate(rec["domain"], brier)
        return rec

    return None


async def _update_domain_aggregate(domain: str, brier_contribution: float) -> None:
    """Incrementally update the running Brier score for a domain."""
    key = _DOMAIN_BRIER_KEY.format(domain=domain)
    agg = await rs.get_json(key) or {"total_brier": 0.0, "n": 0, "last_updated": ""}
    agg["total_brier"] += brier_contribution
    agg["n"] += 1
    agg["last_updated"] = datetime.now(timezone.utc).isoformat()
    await rs.set_json(key, agg)


async def get_domain_brier(domain: str) -> dict:
    """Compute ARIA's current Brier score for a domain."""
    key = _DOMAIN_BRIER_KEY.format(domain=domain)
    agg = await rs.get_json(key) or {"total_brier": 0.0, "n": 0}

    if agg["n"] == 0:
        return {
            "domain": domain,
            "brier_score": None,
            "n_assessments": 0,
            "status": "insufficient_data",
            "calibration": "unknown",
            "recommendation": "Not enough resolved assessments to calibrate.",
        }

    avg = agg["total_brier"] / agg["n"]

    if avg < 0.05:
        calibration = "EXCELLENT"
        rec = f"Well calibrated in {domain}. Maintain current confidence discipline."
    elif avg < 0.10:
        calibration = "GOOD"
        rec = f"Good calibration in {domain}. Continue monitoring."
    elif avg < 0.15:
        calibration = "ACCEPTABLE"
        rec = f"Minor miscalibration in {domain}. Review recent overconfident outputs."
    elif avg < 0.20:
        calibration = "POOR"
        rec = (
            f"Overconfident in {domain}. Reduce stated confidence by 15% "
            f"and trigger a gap detection session."
        )
    else:
        calibration = "CRITICAL"
        rec = (
            f"Severely miscalibrated in {domain} (Brier {avg:.3f}). "
            f"Reduce stated confidence by 30% immediately. "
            f"Urgent gap detection review required."
        )

    return {
        "domain": domain,
        "brier_score": round(avg, 4),
        "n_assessments": agg["n"],
        "calibration": calibration,
        "recommendation": rec,
        "last_updated": agg.get("last_updated", ""),
    }


async def get_full_calibration_report() -> dict:
    """Generate a complete calibration report across all domains.

    This is ARIA's self-report on her own accuracy — used by the
    weekly consciousness review and the confidence-recalibration
    system prompt addendum.
    """
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "domain_scores": {},
        "overall_brier_score": None,
        "strongest_domains": [],
        "weakest_domains": [],
        "recommended_priority_improvements": [],
    }

    scored: list[tuple[str, float]] = []
    for domain_name in dom.get_domain_names():
        score = await get_domain_brier(domain_name)
        report["domain_scores"][domain_name] = score
        if score["brier_score"] is not None:
            scored.append((domain_name, score["brier_score"]))

    if scored:
        scored.sort(key=lambda x: x[1])
        total = sum(s for _, s in scored)
        report["overall_brier_score"] = round(total / len(scored), 4)
        report["strongest_domains"] = [d for d, _ in scored[:3]]
        report["weakest_domains"] = [d for d, _ in scored[-3:]]
        report["recommended_priority_improvements"] = [
            f"Focus on: {d} (Brier: {s:.4f})"
            for d, s in scored[-3:]
            if s > 0.10
        ]

    # Persist global stats for the admin endpoint
    await rs.set_json(_GLOBAL_STATS_KEY, report)
    return report


async def get_recent_assessments(limit: int = 20) -> list[dict]:
    """Return the most recent assessment records."""
    raw_list = await rs.lrange(_ASSESSMENTS_KEY, 0, limit - 1)
    out = []
    for raw in raw_list:
        try:
            out.append(json.loads(raw))
        except Exception:
            continue
    return out


async def get_calibration_addendum() -> str:
    """Build a system-prompt addendum that adjusts ARIA's confidence
    based on her calibration data. Returns empty string if no data.

    This is the bridge between metacognitive tracking and real-time
    behaviour — ARIA's confidence statements are tuned by her own
    accuracy record.
    """
    lines = []
    any_data = False

    for domain_name in dom.get_domain_names():
        score = await get_domain_brier(domain_name)
        if score["brier_score"] is None:
            continue
        any_data = True
        brier = score["brier_score"]

        if brier > 0.20:
            lines.append(
                f"  CRITICAL: You are severely miscalibrated in {domain_name} "
                f"(Brier {brier:.3f}). REDUCE stated confidence by 30% in this domain."
            )
        elif brier > 0.15:
            lines.append(
                f"  WARNING: You are overconfident in {domain_name} "
                f"(Brier {brier:.3f}). Reduce stated confidence by 15%."
            )

    if not any_data or not lines:
        return ""

    header = (
        "\n[METACOGNITIVE CALIBRATION — your own accuracy data says:]\n"
    )
    return header + "\n".join(lines)
