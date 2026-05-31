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
from ..intel.engine_wiring import wire_success, wire_failure
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


# ── R-F357 (2026-05-12): Truncated-JSON Salvage ───────────────────────────
#
# When the LLM hits max_tokens mid-output, the raw string is a prefix of a
# valid JSON object. Naively json.loads(prefix) raises and the assessment is
# dropped. We attempt two progressive repairs:
#   Strategy 1 — close any unterminated string + then walk the bracket stack
#                and append the closing brackets in reverse order. Works when
#                truncation happened inside a string or array.
#   Strategy 2 — regex-extract just the `"scores": {...}` sub-object. Even a
#                fully-trashed response usually contains a complete scores
#                block (it's emitted first per the prompt schema), which is
#                the highest-value field downstream (calibration uses it).
#
# A None return means both strategies failed; caller logs WARNING + skips.
# A dict return is treated as a real assessment; caller logs INFO that
# salvage ran. Either way the calling path stays untouched.

import re as _re_repair


def _repair_truncated_assessment(s: str) -> dict | None:
    """Salvage a truncated self-assessment JSON string. None on failure."""
    if not s or not s.startswith("{"):
        return None

    # Strategy 1: close unterminated string, then close pending brackets.
    in_string = False
    escape = False
    stack: list[str] = []
    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()

    repaired = s
    if in_string:
        # Drop the trailing incomplete value (often `"some half-text`).
        # Walk back to the last comma/[/{ that wasn't inside the unterminated
        # string and trim from there.
        last_open = max(repaired.rfind(","), repaired.rfind("["), repaired.rfind("{"))
        if last_open > 0:
            repaired = repaired[:last_open]
            # Recompute pending brackets after the trim — re-walk briefly.
            stack = []
            in_string = False
            escape = False
            for ch in repaired:
                if escape:
                    escape = False
                    continue
                if ch == "\\" and in_string:
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch in "{[":
                    stack.append(ch)
                elif ch == "}" and stack and stack[-1] == "{":
                    stack.pop()
                elif ch == "]" and stack and stack[-1] == "[":
                    stack.pop()

    # Append closing brackets in reverse to match what's still open.
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"

    try:
        obj = json.loads(repaired)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Strategy 2: extract just the scores sub-object via brace-balanced scan.
    m = _re_repair.search(r'"scores"\s*:\s*\{', s)
    if m:
        start = m.end() - 1  # points at the `{`
        depth = 0
        in_str = False
        esc = False
        end = -1
        for i in range(start, len(s)):
            ch = s[i]
            if esc:
                esc = False
                continue
            if ch == "\\" and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end > start:
            try:
                scores = json.loads(s[start:end + 1])
                if isinstance(scores, dict):
                    return {"scores": scores, "_salvaged": "scores_only"}
            except json.JSONDecodeError:
                pass

    return None


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
        "repaired": False,
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
                # R-F357 (2026-05-12): bumped 1500 → 2500. The 5-dimension
                # JSON schema with identified_weaknesses / world_class_
                # improvements / skill_gaps_revealed (each multi-entry) hits
                # 1500 tokens on detailed outputs and the response truncates
                # mid-string. Live evidence 2026-05-12 10:39:35Z: output cut
                # off inside `"identified_weaknesses": [ "` → json.loads
                # raised → assessment dropped. 2500 covers the worst-case
                # schema; cost delta is marginal (this fires only on
                # substantive outputs).
                max_tokens=2500,
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
        # R-F357 (2026-05-12): truncated JSON salvage path. When max_tokens
        # is exhausted mid-array (or the LLM appends trailing prose despite
        # the system prompt), we try to recover the scores + as many arrays
        # as completed cleanly. Only fall through to skipped if repair also
        # fails. Mirrors the BD-Brain progressive-repair pattern (R-F355
        # Fix 6/7) at a smaller scope.
        assessment = _repair_truncated_assessment(clean)
        if assessment is None:
            logger.warning("Self-assessment JSON parse failed: %s", raw[:200])
            result["skipped"] = True
            result["skipped_reason"] = "json_parse_failed"
            result["duration_ms"] = int((time.time() - t0) * 1000)
            return result
        # Stamp the salvaged dict so downstream observers can distinguish a
        # full assessment from a partial one. Strategy 2 sets `_salvaged`
        # itself; Strategy 1 success path stamps it here.
        assessment.setdefault("_salvaged", "structure_closed")
        logger.info(
            "Self-assessment JSON repaired (truncated response salvaged, mode=%s, %d chars)",
            assessment.get("_salvaged"), len(clean),
        )
        result["repaired"] = True

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

    # R-F1219: wire to brain
    wire_success(
        module="metacognitive_engine",
        summary=f"Self-assessment: overall={overall}, {len(gaps)} gaps, {result['duration_ms']}ms",
        source_id=f"metacognitive:assess:{session_id or 'anon'}",
    )
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
        return {"n": 0, "total": 0, "avg_overall": None, "domains_assessed": []}

    scores = [
        r.get("scores", {}).get("overall", 0)
        for r in records if r.get("scores")
    ]
    domains = list(set(r.get("domain", "") for r in records if r.get("domain")))

    return {
        "n": len(records),
        "total": len(records),
        "avg_overall": round(sum(scores) / len(scores), 2) if scores else None,
        "domains_assessed": sorted(domains),
        "latest_at": records[0].get("assessed_at") if records else None,
    }


# ── Periodic metacognitive routines ──────────────────────────────────────────
# Called by autonomous tasks (tasks.py) via hasattr() guards.


async def run_daily_check() -> dict:
    """Daily metacognitive check — assess today's output quality.
    Reviews recent assessments and flags any quality drift."""
    try:
        stats = await get_assessment_stats()
        recent = await get_recent_assessments(limit=10)

        # Check for quality drift
        if recent:
            avg_score = sum(a.get("score", 0.5) for a in recent) / len(recent)
            drift = "stable" if avg_score >= 0.6 else "declining"
        else:
            avg_score = 0.0
            drift = "no_data"

        result = {
            "check": "daily",
            "total_assessments": stats.get("total", 0),
            "recent_avg_score": round(avg_score, 2),
            "quality_drift": drift,
            "recent_count": len(recent),
        }
        wire_success(
            module="metacognitive_engine",
            summary=f"Daily check: avg_score={avg_score:.2f}, drift={drift}, {len(recent)} recent",
            source_id="metacognitive:daily_check",
        )
        return result
    except Exception as e:
        wire_failure(
            module="metacognitive_engine",
            detail=f"Daily check failed: {e}",
            gap_type="metacognitive_failure",
            source="metacognitive:daily_check",
        )
        return {"check": "daily", "error": str(e)}


async def run_weekly_review(llm=None) -> dict:
    """Weekly metacognitive review — deeper analysis of output patterns."""
    try:
        stats = await get_assessment_stats()
        recent = await get_recent_assessments(limit=50)

        # Aggregate by category
        by_category = {}
        for a in recent:
            cat = a.get("category", "general")
            by_category.setdefault(cat, []).append(a.get("score", 0.5))

        category_avgs = {k: round(sum(v)/len(v), 2) for k, v in by_category.items()}
        weak_areas = [k for k, v in category_avgs.items() if v < 0.5]

        result = {
            "review": "weekly",
            "total_assessments": stats.get("total", 0),
            "category_averages": category_avgs,
            "weak_areas": weak_areas,
            "recommendations": [f"Focus on improving {a}" for a in weak_areas[:3]],
        }
        wire_success(
            module="metacognitive_engine",
            summary=f"Weekly review: {len(weak_areas)} weak areas, {stats.get('total', 0)} total assessments",
            source_id="metacognitive:weekly_review",
        )
        return result
    except Exception as e:
        wire_failure(
            module="metacognitive_engine",
            detail=f"Weekly review failed: {e}",
            gap_type="metacognitive_failure",
            source="metacognitive:weekly_review",
        )
        return {"review": "weekly", "error": str(e)}


async def run_monthly_sprint(llm=None) -> dict:
    """Monthly metacognitive sprint — comprehensive capability assessment."""
    try:
        stats = await get_assessment_stats()
        return {
            "sprint": "monthly",
            "total_assessments": stats.get("total", 0),
            "status": "completed",
        }
    except Exception as e:
        return {"sprint": "monthly", "error": str(e)}
