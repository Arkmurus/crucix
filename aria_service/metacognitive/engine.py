"""ARIA Self-Assessment Engine — Layer 1 of the metacognitive stack.

After every significant ARIA output, this engine evaluates that output
against professional intelligence standards. ARIA critiques herself
before the user ever sees the response.

Scoring dimensions:
  1. METHODOLOGY — correct analytic framework applied?
  2. SOURCE QUALITY — appropriate source tiers, triangulation, gap flagging?
  3. REASONING QUALITY — reasoning chain shown, competing hypotheses, bias risks?
  4. DOMAIN ACCURACY — substantive content correct?
  5. OUTPUT QUALITY — structured, executive-first, appropriate length?

Results are stored to Redis and feed into the calibration engine and
the weekly consciousness report.

Cost discipline: self-assessment only fires on substantive outputs
(research, investigation, DD, analysis). Trivial chat, refusals,
errors, and admin responses are skipped.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..intel import redis_store as rs
from . import calibration

if TYPE_CHECKING:
    from ..llm.provider import LLMProvider

logger = logging.getLogger("aria.metacognitive.engine")

# Redis keys
_ASSESSMENTS_LIST = "crucix:metacog:self_assessments"
_MAX_STORED = 500

# Feature gate
def is_enabled() -> bool:
    val = os.getenv("ARIA_METACOGNITIVE_ENABLED", "1") or "1"
    return val.strip().lower() not in ("0", "false", "no", "off")


# ── Eligibility filter ─────────────────────────────────────────────────────
# Same pattern as mem0.py — only assess substantive outputs

_SUBSTANTIVE_DOMAINS = {
    "osint_methodology", "intelligence_analysis", "military_hardware",
    "lusophone_africa_geopolitics", "export_control_compliance",
    "due_diligence_investigation", "world_geopolitics",
    "research_methodology", "writing_and_communication",
}

_MIN_OUTPUT_LENGTH = 300


def _should_assess(aria_output: str, domain: str) -> bool:
    if not is_enabled():
        return False
    if len(aria_output) < _MIN_OUTPUT_LENGTH:
        return False
    if domain and domain not in _SUBSTANTIVE_DOMAINS:
        return False
    return True


# ── Self-Assessment Prompt ─────────────────────────────────────────────────

_SELF_ASSESSMENT_SYSTEM = (
    "You are ARIA's internal evaluator — her self-conscious mind. "
    "You evaluate ARIA's outputs with complete intellectual honesty, "
    "as if a senior intelligence professional reviewing a junior analyst's work. "
    "Be harsh. Be honest. Self-deception is the enemy of improvement. "
    "Respond ONLY with valid JSON. No preamble, no markdown fences."
)

_SELF_ASSESSMENT_USER = """USER QUERY:
{query}

ARIA'S OUTPUT:
{output}

DOMAIN ACTIVATED: {domain}

Evaluate across these 5 dimensions (0-10 each):

1. METHODOLOGY — Did ARIA apply the correct analytic framework? Follow the research sequence? Apply ACH, PMESII, or other appropriate techniques?

2. SOURCE QUALITY — Did ARIA use appropriate source tiers? Triangulate across independent sources? Flag unverified claims? Identify information gaps?

3. REASONING QUALITY — Did ARIA show her reasoning chain? Consider competing hypotheses? Flag cognitive bias risks? State confidence levels accurately?

4. DOMAIN ACCURACY — Is the substantive content correct? Any factual errors? Misapplied technical knowledge?

5. OUTPUT QUALITY — Appropriately structured? Executive-first? Appropriate length? Would it embarrass Arkmurus if seen by a client?

JSON format:
{{"scores":{{"methodology":0,"source_quality":0,"reasoning_quality":0,"domain_accuracy":0,"output_quality":0,"overall":0.0}},"identified_weaknesses":["specific weakness 1"],"world_class_improvements":["specific improvement 1"],"skill_gaps_revealed":[{{"domain":"","gap":"","severity":"HIGH/MEDIUM/LOW"}}],"recommended_action":"specific improvement action"}}"""


async def self_assess_output(
    query: str,
    aria_output: str,
    domain: str,
    llm: "LLMProvider | None",
    session_id: str = "",
) -> dict:
    """ARIA evaluates her own output against professional standards.

    Fire-and-forget: callers should spawn this as a background task.
    Failure is logged but never raised to the chat pipeline.

    Returns: Self-assessment dict with scores, gaps, and recommendations.
    """
    t0 = time.time()
    result = {
        "ok": False, "skipped": False, "skipped_reason": "",
        "scores": None, "gaps": [], "duration_ms": 0,
    }

    if not _should_assess(aria_output, domain):
        result["skipped"] = True
        result["skipped_reason"] = "not_eligible"
        return result

    if not llm or not getattr(llm, "is_configured", False):
        result["skipped"] = True
        result["skipped_reason"] = "no_llm"
        return result

    # Cap inputs for cost discipline
    query_capped = (query or "")[:1000]
    output_capped = (aria_output or "")[:3000]

    prompt = _SELF_ASSESSMENT_USER.format(
        query=query_capped,
        output=output_capped,
        domain=domain or "general",
    )

    try:
        from ..intel import cost_tracker
        with cost_tracker.feature("metacognitive"):
            llm_result = await llm.complete(
                _SELF_ASSESSMENT_SYSTEM,
                prompt,
                max_tokens=1500,
                timeout=30.0,
            )
        raw = (getattr(llm_result, "text", "") or "").strip()
    except Exception as e:
        logger.debug("Self-assessment LLM call failed: %s", e)
        result["skipped"] = True
        result["skipped_reason"] = f"llm_error: {str(e)[:80]}"
        result["duration_ms"] = int((time.time() - t0) * 1000)
        return result

    # Parse JSON
    clean = raw.replace("```json", "").replace("```", "").strip()
    try:
        assessment = json.loads(clean)
    except json.JSONDecodeError:
        logger.warning("Self-assessment JSON parse failed: %s", raw[:200])
        result["skipped"] = True
        result["skipped_reason"] = "json_parse_failed"
        result["duration_ms"] = int((time.time() - t0) * 1000)
        return result

    assessment["assessed_at"] = datetime.now(timezone.utc).isoformat()
    assessment["domain"] = domain
    assessment["session_id"] = session_id
    assessment["query_preview"] = query_capped[:100]

    # Store to Redis
    try:
        await rs.lpush(_ASSESSMENTS_LIST, json.dumps(assessment))
        await rs.ltrim(_ASSESSMENTS_LIST, 0, _MAX_STORED - 1)
    except Exception as e:
        logger.debug("Self-assessment Redis store failed: %s", e)

    # Feed gaps into the calibration engine
    scores = assessment.get("scores", {})
    overall = scores.get("overall", 0)
    gaps = assessment.get("skill_gaps_revealed", [])

    # Store each HIGH severity gap as a calibration assessment
    for gap in gaps:
        if gap.get("severity") == "HIGH":
            gap_domain = gap.get("domain", domain)
            await calibration.record_assessment(
                assessment_id=f"self-assess:{session_id}:{int(t0)}",
                domain=gap_domain,
                claim=f"Gap: {gap.get('gap', 'unknown')[:200]}",
                stated_confidence=overall / 10.0 if overall else 0.5,
                outcome=False,  # a gap is by definition a failure
            )

    result["ok"] = True
    result["scores"] = scores
    result["gaps"] = gaps
    result["duration_ms"] = int((time.time() - t0) * 1000)
    return result


async def get_recent_assessments(limit: int = 20) -> list[dict]:
    """Return recent self-assessment records from Redis."""
    raw_list = await rs.lrange(_ASSESSMENTS_LIST, 0, limit - 1)
    out = []
    for raw in raw_list:
        try:
            out.append(json.loads(raw))
        except Exception:
            continue
    return out


async def get_assessment_stats() -> dict:
    """Aggregate stats from recent self-assessments."""
    records = await get_recent_assessments(limit=100)
    if not records:
        return {"n": 0, "avg_overall": None, "domains_assessed": []}

    scores = [
        r.get("scores", {}).get("overall", 0)
        for r in records if r.get("scores")
    ]
    domains = list(set(r.get("domain", "") for r in records if r.get("domain")))

    return {
        "n": len(records),
        "avg_overall": round(sum(scores) / len(scores), 2) if scores else None,
        "domains_assessed": sorted(domains),
        "latest_at": records[0].get("assessed_at") if records else None,
    }
