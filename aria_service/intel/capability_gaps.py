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
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.intel.capability_gaps")

KEY = "crucix:aria:capability_gaps"
MAX_GAPS = 500

# F66 fix 2026-04-28: same (gap_type, detail) within this window is
# treated as a duplicate and not re-recorded. calibration_review fires
# `[adversarial_critical_failure] Mastery scores overconfident by 23%`
# on every diagnostic cycle (~5 min) — without dedupe that single signal
# wrote 288 identical entries per day and would crowd MAX_GAPS=500 in
# under 2 days, hiding real gaps from the cycle's analysis. 1h is the
# trade-off: aggressive enough to suppress diagnostic spam, lax enough
# that a recurring real gap (e.g. mastery dropping further at noon)
# still re-surfaces hourly.
_DEDUPE_WINDOW_SECONDS = 3600
_DEDUPE_KEY_PREFIX = "crucix:aria:capability_gaps:dedupe:"


def _gap_fingerprint(gap_type: str, detail: str) -> str:
    return hashlib.md5(f"{gap_type}|{detail[:200]}".encode("utf-8")).hexdigest()

VALID_GAP_TYPES = frozenset({
    "file_parse",
    "registry_lookup",
    "api_missing",
    "knowledge_gap",
    "timeout",
    "format_unsupported",
    "embedder_failure",                # R-F895: semantic_search encode failed
                                       # (EncodeLockTimeout under load = wedge
                                       # precursor, or model error)
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
    # F74 batch (2026-04-28) — types that production code legitimately
    # emits but were not registered. Each was generating an "Unknown
    # gap type" WARNING at first emit per process, which the
    # error_log_handler then mirrored into the error ledger as noise.
    # Grepped from `gap_type=` callers in aria_service/. Grouped by
    # source module:
    "compliance_gate",                 # regional_bright_lines.check_text — UAE_HOUTHI / DRC / Libya etc.
    "counterparty_risk_materialised",  # autonomous DD — RED outcome on a target
    "defective_dd_run",                # dd_orchestrator — run aborted before report
    "domain_ownership",                # virtual_office_registry — domain RDAP ambiguity
    "euc_critical_clauses_missing",    # euc_library — required clause absent from contract
    "ghost_entity",                    # ghost_detector — shell / no-substance counterparty
    "pdf_parse_failure",               # ocr / pdf_ingest — non-recoverable parse error
    "provider_disagreement",           # ensemble — providers gave contradictory signals
    "rescreen_errors",                 # dd_orchestrator.rescreen_watchlist — non-zero error count
    "sanctions_hit",                   # sanctions — confirmed designated party
    "stalled_cell",                    # heatmap — region/topic with no movement over window
    "unverified_citation",             # cited_artifact_verifier — claim could not be grounded
    "format_unsupported",              # already in set above (kept for clarity, fine if dup)
    # F94 batch (2026-04-30) — circuit-breaker failure reasons. Before
    # this, every tripped breaker recorded gap_type="timeout" regardless
    # of whether the upstream said 402 (Brave billing), 429 (Semantic
    # Scholar rate limit), or 401/403 (auth). Triage was reading those
    # as transient network issues when the actual fix is operator-side
    # (key rotation, plan upgrade, caller-side rate limiting).
    "billing_required",                # 402 / credit exhausted
    "rate_limited",                    # 429 / quota exceeded
    "auth_failure",                    # 401 / 403
    # R-F708 (2026-05-18) — vendor health dropped below 50% live.
    # Emitted by vendor_registry.all_vendor_statuses() via brain_hook
    # when live_pct < 0.5. Was logging "Unknown gap type" on every
    # dashboard poll while >50% of vendors were dark (acled +
    # worldbank_debarred + worldbank_documents in current state).
    "vendor_outage",
})


async def record_gap(
    gap_type: str,
    detail: str,
    message_context: str = "",
    source: str = "",
    user_id: str = "",
    sector: str = "",
) -> dict:
    """Record a capability gap to Redis.

    Args:
        gap_type: one of VALID_GAP_TYPES
        detail: human-readable description of the gap
        message_context: optional snippet from the user message that triggered it
        source: optional identifier for where the gap was detected
        user_id: R-F56 — authenticated user id when the gap surfaced
                 from a chat / DD turn. Empty for sweep / autonomous.
        sector:  R-F56 — persona sector (broker / oem_export / compliance /
                 banking_insurance / journalist / government_acquisition).
                 Carried on every gap entry so per-sector reports can
                 surface "compliance officers have hit X gap N times".

    Returns:
        The stored gap entry dict.
    """
    if gap_type not in VALID_GAP_TYPES:
        logger.warning("Unknown gap type %r — recording anyway", gap_type)

    # F66 dedupe (2026-04-28): same (gap_type, detail) within window = no-op.
    fingerprint = _gap_fingerprint(gap_type, detail)
    dedupe_key = _DEDUPE_KEY_PREFIX + fingerprint
    if await rs.get(dedupe_key):
        logger.debug(
            "Capability gap deduped within %ds window: [%s] %s",
            _DEDUPE_WINDOW_SECONDS, gap_type, detail[:80],
        )
        return {
            "deduped": True,
            "type": gap_type,
            "detail": detail,
            "fingerprint": fingerprint,
        }

    entry: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "type": gap_type,
        "detail": detail,
        "source": source,
        "message_context": message_context[:500] if message_context else "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "resolved": False,
        "resolution": None,
        # R-F56: per-customer telemetry tags. Empty strings on legacy /
        # sweep-driven gaps preserve the existing shape.
        "user_id": (user_id or "").strip()[:64],
        "sector": (sector or "").strip()[:64],
    }

    await rs.lpush(KEY, json.dumps(entry, default=str))
    await rs.ltrim(KEY, 0, MAX_GAPS - 1)
    # Set the dedupe sentinel AFTER the write so a failed lpush doesn't
    # silently suppress the next try.
    await rs.set(dedupe_key, "1", ex=_DEDUPE_WINDOW_SECONDS)

    # Strip newlines/control chars so multi-line gap_detail (e.g. rlaif's
    # query echo) doesn't split the log entry across lines and confuse
    # downstream log parsers / fly-log search. Live evidence
    # 2026-05-01 07:30:17: a digest-generation prompt with an embedded
    # `\n` rendered as a stray "Inclu..." next-line fragment in fly logs.
    _safe = " ".join((detail or "")[:120].split())
    logger.info("Capability gap recorded: [%s] %s", gap_type, _safe)
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
