"""
Capability Gap Tracker — structured logging of ARIA's known limitations.

When ARIA encounters something she cannot handle (unknown file type,
unsupported registry, missing API, parse failure), this module records
it as a structured gap.  Gaps live in a capped Redis list and can be
marked resolved once a fix ships.

Phase 3 of the ARIA learning infrastructure.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.intel.capability_gaps")

KEY = "crucix:aria:capability_gaps"
MAX_GAPS = 500

VALID_GAP_TYPES = frozenset({
    "file_parse",
    "registry_lookup",
    "api_missing",
    "knowledge_gap",
    "timeout",
    "format_unsupported",
    # Core Self-Development Loop (Clauses 17/18/19) — shipped 2026-04-15
    "verified_contradiction",          # verified_intel: sources disagree
    "source_seeding_suspected",        # search_doctrine: uniformity cluster
    "insufficient_public_intel",       # search_doctrine: 3-attempt exhaustion
    "source_validator_rejected",       # source_validator: quality gate fail
    "source_auto_suspended",           # web_atlas: reliability EMA < 0.40
    "paraphrase_violation",            # chat post-processor: verbatim ≥200
    "adversarial_critical_failure",    # adversarial_challenge: CRITICAL fail
    "mistake",                         # generic bridge from mistake_ledger
    # Operational-signal bridges from metacognitive/gaps.py (2026-04-22).
    # Before this bridge, ecosystem_reassess read only capability_gaps.recent()
    # and never saw live memory-miss / research-failure signals from the
    # streaming chat path — the 24/7 learning loop was effectively blind
    # to production WhatsApp traffic.
    "operational:memory_miss",
    "operational:research_failure",
    "operational:confidence_failure",
    "operational:output_rejection",
    # Layer 5c commercial coherence (2026-04-22) — structural deal flags
    # surfaced by commercial_coherence.assess_commercial_coherence().
    "commercial_coherence_elevated",
    # Symbolic reasoner miss (2026-04-24) — no handler matched a question
    # that cleared the doc-review / workflow-command / multiparty guards.
    # Emitted by symbolic_reasoner.reason() via brain_hook. Registering
    # the type silences the "Unknown gap type" warning that fired on
    # every miss.
    "no_symbolic_rule",
    # Web search exhaustion (2026-04-27) — all backends returned 0 results
    # for a research-cycle query. Emitted by researcher._web_search when
    # every configured backend (crossref/openalex/semantic-scholar/brave)
    # comes back empty. Was generating "Unknown gap type" warnings on
    # every weak-cell search.
    "search_zero_results",
})


async def record_gap(
    gap_type: str,
    detail: str,
    message_context: str = "",
    source: str = "",
) -> dict:
    """Record a capability gap to Redis.

    Args:
        gap_type: one of VALID_GAP_TYPES
        detail: human-readable description of the gap
        message_context: optional snippet from the user message that triggered it
        source: optional identifier for where the gap was detected

    Returns:
        The stored gap entry dict.
    """
    if gap_type not in VALID_GAP_TYPES:
        logger.warning("Unknown gap type %r — recording anyway", gap_type)

    entry: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "type": gap_type,
        "detail": detail,
        "source": source,
        "message_context": message_context[:500] if message_context else "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "resolved": False,
        "resolution": None,
    }

    await rs.lpush(KEY, json.dumps(entry, default=str))
    await rs.ltrim(KEY, 0, MAX_GAPS - 1)

    logger.info("Capability gap recorded: [%s] %s", gap_type, detail[:120])
    return entry


_RESOLVE_LOCK = None


def _get_resolve_lock():
    global _RESOLVE_LOCK
    if _RESOLVE_LOCK is None:
        _RESOLVE_LOCK = asyncio.Lock()
    return _RESOLVE_LOCK


async def resolve_gap(gap_id: str, resolution: str) -> dict:
    """Mark a gap as resolved with the fix description.

    Scans the list, patches the matching entry, and rewrites.

    Returns:
        The updated gap entry, or ``{"error": ...}`` if not found.
    """
    async with _get_resolve_lock():
        return await _resolve_gap_inner(gap_id, resolution)


async def _resolve_gap_inner(gap_id: str, resolution: str) -> dict:
    raw_entries = await rs.lrange(KEY, 0, MAX_GAPS - 1)
    entries = [json.loads(r) for r in raw_entries]

    for i, entry in enumerate(entries):
        if entry.get("id") == gap_id:
            entry["resolved"] = True
            entry["resolution"] = resolution
            entry["resolved_at"] = datetime.now(timezone.utc).isoformat()
            # Rewrite the whole list (gaps list is small, this is fine)
            await _rewrite_list(entries)
            logger.info("Gap %s resolved: %s", gap_id, resolution[:120])
            return entry

    return {"error": f"Gap {gap_id} not found"}


async def purge_resolved_type(gap_type: str) -> dict:
    """Bulk-resolve all gaps of a given type. Used after fixing the
    root cause (e.g., mastery scores reset after broken EWMA).

    Returns dict with ``purged`` count and ``gap_type``.
    """
    async with _get_resolve_lock():
        raw_entries = await rs.lrange(KEY, 0, MAX_GAPS - 1)
        entries = [json.loads(r) for r in raw_entries]

        count = 0
        now_iso = datetime.now(timezone.utc).isoformat()
        for entry in entries:
            if entry.get("type") == gap_type and not entry.get("resolved", False):
                entry["resolved"] = True
                entry["resolution"] = "bulk_purged_after_fix"
                entry["resolved_at"] = now_iso
                count += 1

        if count:
            await _rewrite_list(entries)

        logger.info("Bulk-purged %d gaps of type '%s'", count, gap_type)
        return {"purged": count, "gap_type": gap_type}


async def get_gaps(resolved: bool = False, limit: int = 50) -> list[dict]:
    """Retrieve gaps, filtered by resolved status.

    Args:
        resolved: if True, return resolved gaps; if False, unresolved.
        limit: max entries to return.
    """
    raw_entries = await rs.lrange(KEY, 0, MAX_GAPS - 1)
    entries = [json.loads(r) for r in raw_entries]
    filtered = [e for e in entries if e.get("resolved", False) == resolved]
    return filtered[:limit]


async def recent_gaps(limit: int = 50) -> list[dict]:
    """Thin alias for `get_gaps(resolved=False, limit=limit)` —
    metacognitive_journal gates on `hasattr(cg, "recent_gaps")` and
    silently skipped this seed source for weeks (added 2026-04-20).
    """
    return await get_gaps(resolved=False, limit=limit)


async def recent(limit: int = 50) -> list[dict]:
    """Same as `recent_gaps` — ecosystem_reassess uses the shorter
    name. Added 2026-04-20 to close the same silent-skip gap."""
    return await get_gaps(resolved=False, limit=limit)


async def get_gap_summary() -> dict:
    """Return counts by type, most common unresolved, and latest gaps."""
    raw_entries = await rs.lrange(KEY, 0, MAX_GAPS - 1)
    entries = [json.loads(r) for r in raw_entries]

    total = len(entries)
    unresolved = [e for e in entries if not e.get("resolved", False)]
    resolved = [e for e in entries if e.get("resolved", False)]

    # Counts by type (unresolved only)
    by_type: dict[str, int] = {}
    for e in unresolved:
        t = e.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    # Most common unresolved type
    most_common = max(by_type, key=by_type.get) if by_type else None  # type: ignore[arg-type]

    return {
        "total": total,
        "unresolved": len(unresolved),
        "resolved": len(resolved),
        "by_type": by_type,
        "most_common_unresolved": most_common,
        "latest_unresolved": unresolved[:5],
        "recently_resolved": resolved[:5],
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _rewrite_list(entries: list[dict]) -> None:
    """Delete and re-push the entire gap list (for in-place mutation)."""
    from . import redis_store as _rs
    # Delete old list
    await _rs.delete(KEY)
    # Re-push in reverse so the newest is at the head
    for entry in reversed(entries):
        await _rs.lpush(KEY, json.dumps(entry, default=str))
