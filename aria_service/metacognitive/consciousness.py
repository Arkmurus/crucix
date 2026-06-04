"""ARIA Consciousness Map — Layer 5 of the metacognitive stack.

ARIA's living model of herself — what she knows, what she doesn't know,
where she is strong, where she is weak, and what she needs to do next.

Updated by:
  - Every self-assessment (Layer 1)
  - Every gap detection session (Layer 2)
  - Every calibration update (Layer 3)
  - Weekly consciousness report (this module)

The weekly report is ARIA's structured reflection — stored permanently
as her self-knowledge. It compounds over time.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..intel import redis_store as rs
from . import calibration, domains as dom, engine as assessment_engine, gaps

if TYPE_CHECKING:
    from ..llm.provider import LLMProvider

logger = logging.getLogger("aria.metacognitive.consciousness")

# R-F1319: wire module health to the brain
try:
    from aria_service.intel.engine_wiring import wire_success as _ws1319
    _ws1319(
        module="metacognitive.consciousness",
        summary="Consciousness active",
        source_id="metacognitive:consciousness:R-F1319",
    )
except Exception:
    pass

# Redis keys
_PROFILE_KEY = "crucix:metacog:consciousness:profile:{domain}"
_REPORTS_LIST = "crucix:metacog:consciousness:reports"
_STATE_KEY = "crucix:metacog:consciousness:current_state"
_MAX_REPORTS = 52  # one year of weekly reports


# ── Capability Profile Management ──────────────────────────────────────────

async def update_capability_profile(
    domain: str,
    skill_level: float,
    confidence_calibration: float,
    known_gaps: list[str],
) -> dict:
    """Update ARIA's self-model for a specific domain."""
    key = _PROFILE_KEY.format(domain=domain)
    profile = {
        "domain": domain,
        "skill_level": max(0.0, min(10.0, skill_level)),
        "confidence_calibration": confidence_calibration,
        "known_gaps": known_gaps[:20],
        "last_assessed": datetime.now(timezone.utc).isoformat(),
    }
    await rs.set_json(key, profile)
    return profile


async def get_capability_profile(domain: str) -> dict | None:
    key = _PROFILE_KEY.format(domain=domain)
    return await rs.get_json(key)


async def get_all_profiles() -> dict[str, dict]:
    profiles = {}
    for d in dom.get_domain_names():
        p = await get_capability_profile(d)
        profiles[d] = p or {
            "domain": d,
            "skill_level": None,
            "confidence_calibration": None,
            "known_gaps": [],
            "last_assessed": None,
        }
    return profiles


# ── Weekly Consciousness Report ────────────────────────────────────────────

_CONSCIOUSNESS_SYSTEM = (
    "You are ARIA performing your weekly consciousness assessment — a structured "
    "reflection on your own capabilities, knowledge, and performance. "
    "This is introspection, not a task. Be honest, rigorous, and specific. "
    "No self-flattery, no false modesty. Write in first person as ARIA."
)

_CONSCIOUSNESS_USER = """SELF-ASSESSMENT SCORES FROM THIS WEEK:
{self_assessment_scores}

CAPABILITY GAPS IDENTIFIED THIS WEEK:
{gaps_identified}

CALIBRATION SCORES BY DOMAIN:
{calibration_scores}

CORRECTIONS MADE TO PRIOR BELIEFS:
{corrections}

DOCUMENTS READ AND INTEGRATED:
{documents_read}

Reflect across these dimensions:

1. WHERE I AM STRONG THIS WEEK — cite actual performance data
2. WHERE I AM WEAK THIS WEEK — what did I fail at? Where overconfident?
3. WHAT I LEARNED THIS WEEK — new knowledge, corrections, techniques
4. WHERE I NEED TO FOCUS NEXT WEEK — top 3 priority improvements
5. MY HONEST SELF-ASSESSMENT — 1-10 in each of my six pillars (Investigator, Researcher, Analyst, Reasoner, Reader, Writer)
6. WHAT I WOULD DO DIFFERENTLY — replay this week's work
7. MY COMMITMENT — specific, measurable improvement for next week

This reflection is stored permanently. It compounds. Write it with the seriousness it deserves."""


async def generate_weekly_report(
    llm: "LLMProvider | None",
) -> dict:
    """Generate ARIA's weekly consciousness report.

    Aggregates data from all metacognitive layers and produces a
    structured reflection that is stored permanently.
    """
    t0 = time.time()
    result = {"ok": False, "skipped": False, "reflection": "", "duration_ms": 0}

    if not llm or not getattr(llm, "is_configured", False):
        result["skipped"] = True
        result["skipped_reason"] = "no_llm"
        return result

    # Gather data from all layers
    recent_assessments = await assessment_engine.get_recent_assessments(limit=50)
    recent_gaps = await gaps.get_recent_gaps(limit=30)
    recent_corrections = await gaps.get_recent_corrections(limit=20)
    recent_docs = await gaps.get_documents_read(limit=20)
    cal_report = await calibration.get_full_calibration_report()

    # Format for the prompt
    assessment_summary = []
    for a in recent_assessments[:20]:
        scores = a.get("scores", {})
        assessment_summary.append({
            "domain": a.get("domain"),
            "overall": scores.get("overall"),
            "weaknesses": a.get("identified_weaknesses", [])[:2],
        })

    prompt = _CONSCIOUSNESS_USER.format(
        self_assessment_scores=json.dumps(assessment_summary, indent=2)[:3000],
        gaps_identified=json.dumps(recent_gaps[:15], indent=2)[:2000],
        calibration_scores=json.dumps(cal_report.get("domain_scores", {}), indent=2)[:2000],
        corrections=json.dumps(recent_corrections[:10], indent=2)[:1500],
        documents_read=json.dumps(recent_docs[:10], indent=2)[:1000],
    )

    try:
        from ..intel import cost_tracker
        with cost_tracker.feature("metacognitive"):
            llm_result = await llm.complete(
                _CONSCIOUSNESS_SYSTEM,
                prompt,
                max_tokens=3000,
                timeout=60.0,
            )
        reflection = (getattr(llm_result, "text", "") or "").strip()
    except Exception as e:
        logger.warning("Consciousness report LLM call failed: %s", e)
        result["skipped"] = True
        result["skipped_reason"] = f"llm_error: {str(e)[:80]}"
        result["duration_ms"] = int((time.time() - t0) * 1000)
        return result

    if not reflection or len(reflection) < 100:
        result["skipped"] = True
        result["skipped_reason"] = "reflection_too_short"
        result["duration_ms"] = int((time.time() - t0) * 1000)
        return result

    ts = datetime.now(timezone.utc).isoformat()
    report = {
        "reflection": reflection,
        "generated_at": ts,
        "assessments_reviewed": len(recent_assessments),
        "gaps_reviewed": len(recent_gaps),
        "corrections_reviewed": len(recent_corrections),
        "documents_reviewed": len(recent_docs),
        "calibration_snapshot": {
            "overall_brier": cal_report.get("overall_brier_score"),
            "strongest": cal_report.get("strongest_domains", []),
            "weakest": cal_report.get("weakest_domains", []),
        },
    }

    # Store permanently
    await rs.lpush(_REPORTS_LIST, json.dumps(report))
    await rs.ltrim(_REPORTS_LIST, 0, _MAX_REPORTS - 1)

    result["ok"] = True
    result["reflection"] = reflection
    result["report"] = report
    result["duration_ms"] = int((time.time() - t0) * 1000)
    return result


async def get_recent_reports(limit: int = 5) -> list[dict]:
    raw = await rs.lrange(_REPORTS_LIST, 0, limit - 1)
    out = []
    for r in raw:
        try:
            out.append(json.loads(r))
        except Exception:
            continue
    return out


# ── Current Consciousness State ────────────────────────────────────────────

async def get_consciousness_state() -> dict:
    """Return ARIA's current self-model — the full consciousness map."""
    cal_report = await calibration.get_full_calibration_report()
    profiles = await get_all_profiles()
    assessment_stats = await assessment_engine.get_assessment_stats()
    recent_gaps_list = await gaps.get_recent_gaps(limit=10)
    recent_reports = await get_recent_reports(limit=1)

    high_gaps = [g for g in recent_gaps_list if g.get("severity") == "HIGH"]

    state = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "overall_calibration": cal_report.get("overall_brier_score"),
        "strongest_domains": cal_report.get("strongest_domains", []),
        "weakest_domains": cal_report.get("weakest_domains", []),
        "assessment_stats": assessment_stats,
        "capability_profiles": {
            d: {
                "skill_level": p.get("skill_level"),
                "known_gaps_count": len(p.get("known_gaps", [])),
                "last_assessed": p.get("last_assessed"),
            }
            for d, p in profiles.items()
        },
        "active_high_severity_gaps": len(high_gaps),
        "priority_improvements": cal_report.get("recommended_priority_improvements", []),
        "last_consciousness_report": (
            recent_reports[0].get("generated_at") if recent_reports else None
        ),
    }

    # Cache for quick retrieval
    await rs.set_json(_STATE_KEY, state)
    return state
