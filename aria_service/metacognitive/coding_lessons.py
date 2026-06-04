"""ARIA Coding Lessons — pattern library that grows with every fix shipped.

When ARIA deploys a code fix and observes its outcome, the lesson is stored
here. Over time she accumulates a library of proven patterns specific to
her own architecture — what worked, what failed, and why.

This is the coding memory that makes ARIA progressively better at fixing
herself. Each lesson has:
  - What gap was closed
  - What approach was tried
  - What worked and what didn't
  - The pattern to remember for similar future fixes

All state in Redis. The library is consulted by the self-improvement
codegen before generating new fixes — ARIA checks "have I seen this
before?" and applies the lesson if so.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from ..intel import redis_store as rs

logger = logging.getLogger("aria.metacognitive.coding_lessons")

# R-F1319: wire module health to the brain
try:
    from aria_service.intel.engine_wiring import wire_success as _ws1319
    _ws1319(
        module="metacognitive.coding_lessons",
        summary="Coding Lessons active",
        source_id="metacognitive:coding_lessons:R-F1319",
    )
except Exception:
    pass

# Redis keys
_LESSONS_LIST = "crucix:metacog:coding_lessons:all"
_PATTERNS_KEY = "crucix:metacog:coding_lessons:patterns"
_MAX_LESSONS = 200
_MAX_PATTERNS = 100


async def record_lesson(
    reference: str,
    outcome: str,
    what_worked: str,
    what_failed: str,
    gap_type: str = "",
    domain: str = "",
    file_changed: str = "",
    pattern_name: str = "",
) -> dict:
    """Store the outcome of a deployed fix as a coding lesson.

    ARIA learns from what worked and what did not — her coding
    pattern library grows with every fix she ships.

    Args:
        reference: Fix reference ID (e.g. ARK-CODEFIX-20260410120000)
        outcome: SUCCESS / PARTIAL / FAILED / ROLLED_BACK
        what_worked: Specific description of what succeeded
        what_failed: Specific description of what didn't work
        gap_type: Original gap signal type
        domain: Capability domain this fix addressed
        file_changed: Which file was modified
        pattern_name: If this fix established a reusable pattern, name it
    """
    ts = datetime.now(timezone.utc).isoformat()

    lesson = {
        "reference": reference,
        "outcome": outcome,
        "what_worked": what_worked[:500],
        "what_failed": what_failed[:500],
        "gap_type": gap_type,
        "domain": domain,
        "file_changed": file_changed,
        "pattern_name": pattern_name,
        "recorded_at": ts,
    }

    await rs.lpush(_LESSONS_LIST, json.dumps(lesson))
    await rs.ltrim(_LESSONS_LIST, 0, _MAX_LESSONS - 1)

    # If the fix succeeded and established a pattern, store it
    if outcome == "SUCCESS" and pattern_name:
        await _store_pattern(pattern_name, what_worked, file_changed, domain)

    logger.info(
        "[coding lesson] %s outcome=%s pattern=%s",
        reference, outcome, pattern_name or "none",
    )

    return {"ok": True, "lesson": lesson}


async def _store_pattern(
    name: str,
    description: str,
    file_context: str,
    domain: str,
) -> None:
    """Add a proven pattern to the library. Deduplicates by name."""
    patterns = await rs.get_json(_PATTERNS_KEY) or []

    # Check for existing pattern with same name
    existing_names = {p["name"] for p in patterns}
    if name in existing_names:
        # Update existing pattern with latest evidence
        for p in patterns:
            if p["name"] == name:
                p["last_confirmed"] = datetime.now(timezone.utc).isoformat()
                p["confirmation_count"] = p.get("confirmation_count", 1) + 1
                break
    else:
        patterns.append({
            "name": name,
            "description": description[:300],
            "file_context": file_context,
            "domain": domain,
            "learned_at": datetime.now(timezone.utc).isoformat(),
            "last_confirmed": datetime.now(timezone.utc).isoformat(),
            "confirmation_count": 1,
        })

    # Cap size
    patterns = patterns[-_MAX_PATTERNS:]
    await rs.set_json(_PATTERNS_KEY, patterns)


async def get_recent_lessons(limit: int = 20) -> list[dict]:
    """Return recent coding lessons."""
    raw = await rs.lrange(_LESSONS_LIST, 0, limit - 1)
    out = []
    for r in raw:
        try:
            out.append(json.loads(r))
        except Exception:
            continue
    return out


async def get_patterns() -> list[dict]:
    """Return the full pattern library — proven solutions from past fixes."""
    return await rs.get_json(_PATTERNS_KEY) or []


async def search_lessons(query: str, limit: int = 5) -> list[dict]:
    """Search lessons by keyword overlap for relevant prior experience.

    Used by the codegen before generating a new fix — "have I seen this
    problem before?" If so, the lesson shapes the new approach.
    """
    all_lessons = await get_recent_lessons(limit=100)
    if not all_lessons or not query:
        return []

    words = {w.lower() for w in query.split() if len(w) > 3}
    if not words:
        return all_lessons[:limit]

    scored: list[tuple[int, dict]] = []
    for lesson in all_lessons:
        text = (
            f"{lesson.get('what_worked', '')} "
            f"{lesson.get('what_failed', '')} "
            f"{lesson.get('gap_type', '')} "
            f"{lesson.get('domain', '')} "
            f"{lesson.get('file_changed', '')} "
            f"{lesson.get('pattern_name', '')}"
        ).lower()
        score = sum(1 for w in words if w in text)
        if score > 0:
            scored.append((score, lesson))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [lesson for _, lesson in scored[:limit]]


async def get_lesson_stats() -> dict:
    """Aggregate stats from the coding lessons library."""
    lessons = await get_recent_lessons(limit=200)
    patterns = await get_patterns()

    by_outcome: dict[str, int] = {}
    by_gap_type: dict[str, int] = {}
    for l in lessons:
        o = l.get("outcome", "UNKNOWN")
        by_outcome[o] = by_outcome.get(o, 0) + 1
        g = l.get("gap_type", "")
        if g:
            by_gap_type[g] = by_gap_type.get(g, 0) + 1

    success_count = by_outcome.get("SUCCESS", 0)
    total = len(lessons)
    success_rate = (success_count / total * 100) if total > 0 else 0

    return {
        "total_lessons": total,
        "by_outcome": by_outcome,
        "by_gap_type": by_gap_type,
        "success_rate": round(success_rate, 1),
        "proven_patterns": len(patterns),
        "most_confirmed_pattern": (
            max(patterns, key=lambda p: p.get("confirmation_count", 0))["name"]
            if patterns else None
        ),
    }


async def lessons_addendum_for_codegen(gap_description: str) -> str:
    """Build a context block for the codegen prompt with relevant prior lessons.

    If ARIA has fixed similar gaps before, this injects that experience
    into the prompt so she doesn't repeat past mistakes.
    """
    relevant = await search_lessons(gap_description, limit=3)
    if not relevant:
        return ""

    lines = ["\n[PRIOR CODING LESSONS — what worked and failed on similar gaps]"]
    for l in relevant:
        outcome = l.get("outcome", "?")
        worked = l.get("what_worked", "")[:150]
        failed = l.get("what_failed", "")[:150]
        pattern = l.get("pattern_name", "")
        line = f"- [{outcome}] Worked: {worked}"
        if failed:
            line += f" | Failed: {failed}"
        if pattern:
            line += f" | Pattern: {pattern}"
        lines.append(line)

    lines.append(
        "(Apply lessons from past fixes. Do not repeat approaches that failed.)"
    )
    return "\n".join(lines)
