"""ARIA Metacognitive Cycle — the operating rhythm of self-improvement.

  Daily:   Quick self-assessment of the day's outputs (22:00 UTC)
  Weekly:  Full consciousness report + calibration review (Sunday 20:00 UTC)
  Monthly: Deep gap detection review + code generation for top gaps (1st Sunday)

Each cycle function is designed to be called from the autonomous engine
or from an admin endpoint for manual trigger. All are async, fail-safe,
and cost-tracked under the 'metacognitive' feature.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..intel import redis_store as rs
from . import (
    calibration,
    consciousness,
    engine as assessment_engine,
    gaps,
    self_improvement_codegen as codegen,
)

if TYPE_CHECKING:
    from ..llm.provider import LLMProvider

logger = logging.getLogger("aria.metacognitive.cycle")

# Redis keys for cycle tracking
_DAILY_LAST_RUN = "crucix:metacog:cycle:daily_last_run"
_WEEKLY_LAST_RUN = "crucix:metacog:cycle:weekly_last_run"
_MONTHLY_LAST_RUN = "crucix:metacog:cycle:monthly_last_run"


async def daily_self_check(llm: "LLMProvider | None") -> dict:
    """Daily quick review of ARIA's outputs.

    Pulls the day's self-assessment records and computes aggregate
    stats. Light-weight — no LLM call, just aggregation.
    """
    t0 = time.time()
    stats = await assessment_engine.get_assessment_stats()
    recent_gaps = await gaps.get_recent_gaps(limit=10)

    high_gaps = [g for g in recent_gaps if g.get("severity") == "HIGH"]

    summary = {
        "cycle": "daily",
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "assessments_today": stats.get("n", 0),
        "avg_score": stats.get("avg_overall"),
        "domains_active": stats.get("domains_assessed", []),
        "high_severity_gaps": len(high_gaps),
        "duration_ms": int((time.time() - t0) * 1000),
    }

    await rs.set(_DAILY_LAST_RUN, json.dumps(summary))
    logger.info(
        "[metacog daily] assessments=%d avg_score=%s high_gaps=%d",
        summary["assessments_today"],
        summary["avg_score"],
        summary["high_severity_gaps"],
    )
    return summary


async def weekly_consciousness_review(llm: "LLMProvider | None") -> dict:
    """Full weekly consciousness report + calibration review.

    This is the deep one — fires an LLM call for the reflection,
    generates the full calibration report, and stores everything
    permanently. Runs Sunday 20:00 UTC.
    """
    t0 = time.time()

    # Generate the consciousness report (LLM call)
    report_result = await consciousness.generate_weekly_report(llm)

    # Generate full calibration report
    cal_report = await calibration.get_full_calibration_report()

    # Get the consciousness state
    state = await consciousness.get_consciousness_state()

    summary = {
        "cycle": "weekly",
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "consciousness_report_ok": report_result.get("ok", False),
        "overall_brier": cal_report.get("overall_brier_score"),
        "strongest_domains": cal_report.get("strongest_domains", []),
        "weakest_domains": cal_report.get("weakest_domains", []),
        "high_severity_gaps": state.get("active_high_severity_gaps", 0),
        "duration_ms": int((time.time() - t0) * 1000),
    }

    if report_result.get("ok"):
        summary["reflection_preview"] = report_result.get("reflection", "")[:500]

    await rs.set(_WEEKLY_LAST_RUN, json.dumps(summary))
    logger.info(
        "[metacog weekly] ok=%s brier=%s strongest=%s weakest=%s",
        summary["consciousness_report_ok"],
        summary["overall_brier"],
        summary["strongest_domains"],
        summary["weakest_domains"],
    )
    return summary


async def monthly_gap_closure_sprint(
    llm: "LLMProvider | None",
    top_n_gaps: int = 3,
) -> dict:
    """Monthly deep improvement cycle.

    Reviews the top N persistent HIGH-severity gaps and generates
    code proposals to close them. All proposals are PENDING_REVIEW.
    """
    t0 = time.time()

    high_gaps = await gaps.get_high_severity_gaps(limit=top_n_gaps)

    improvements = []
    for gap in high_gaps:
        gap_desc = f"Domain: {gap.get('domain', 'unknown')} | Gap: {gap.get('gap', 'unknown')}"
        result = await codegen.generate_improvement_code(
            gap_description=gap_desc,
            requirements=(
                "Must integrate with ARIA's Fly.io Python stack. "
                "Must store results to Redis. "
                "Must be deployable without manual configuration changes."
            ),
            domain=gap.get("domain", "unknown"),
            llm=llm,
        )
        improvements.append({
            "gap": gap.get("gap", ""),
            "domain": gap.get("domain", ""),
            "code_generated": result.get("ok", False),
        })

    summary = {
        "cycle": "monthly",
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "gaps_reviewed": len(high_gaps),
        "code_proposals_generated": sum(1 for i in improvements if i["code_generated"]),
        "improvements": improvements,
        "duration_ms": int((time.time() - t0) * 1000),
    }

    await rs.set(_MONTHLY_LAST_RUN, json.dumps(summary))
    logger.info(
        "[metacog monthly] gaps=%d proposals=%d",
        summary["gaps_reviewed"],
        summary["code_proposals_generated"],
    )
    return summary


async def get_cycle_status() -> dict:
    """Return the last-run status of each cycle."""
    return {
        "daily": await rs.get_json(_DAILY_LAST_RUN),
        "weekly": await rs.get_json(_WEEKLY_LAST_RUN),
        "monthly": await rs.get_json(_MONTHLY_LAST_RUN),
    }
