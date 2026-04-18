"""Defective-run quarantine.

Extracted from the F3 incident 2026-04-17 21:30: a DD ran with a
malformed entity name ("https" from URL-parser bug), produced a
5-match "sanctions hit" that was all noise, and that output was
ingested into mem0 and cited in later conversations as confirmed
evidence. When the user challenged the YES answer, ARIA had to run
primary-source verification to discover the original run was
defective.

This module prevents the pattern recurring:
  - quarantine_run(run_id, reason): mark a run's output as unsafe
    for downstream citation. Persisted in Redis.
  - is_quarantined(run_id): O(1) check for citation guards
  - filter_citations(text): scrub [from dd_orchestrate:XXX] markers
    pointing at quarantined runs (used when rendering prior
    conversations or pulling mem0 context)

Seeded with the two known-bad run_ids from tonight's F3 incident so
those assessments can no longer poison future chat turns.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.intel.run_quarantine")


_KEY = "crucix:aria:quarantined_runs"

# ═══════════════════════════════════════════════════════════════════════
# Seeded quarantine — known-defective runs from 2026-04-17 F3 incident
# ═══════════════════════════════════════════════════════════════════════
#
# Both of these ran on entity name "https" because the DD intent regex
# stripped the URL at the first `/`. Their outputs should never be
# cited as evidence.

_SEEDED: dict[str, dict[str, str]] = {
    "dd_30477701e537": {
        "reason": "Ran on malformed entity 'https' — URL parser bug (fixed in d4aa0c1). "
                  "5-match sanctions hit is noise against the substring 'https' in list aliases.",
        "quarantined_at": "2026-04-17T21:45:00+00:00",
        "entity_was":     "https",
        "real_entity":    "f3ir.com / F3 International Resources LLC",
    },
    "dd_adc7c7f87e4a": {
        "reason": "Same malformed-entity bug as dd_30477701e537. 5-match sanctions "
                  "result is a repeat of the same noise hit — both runs are defective.",
        "quarantined_at": "2026-04-17T21:45:00+00:00",
        "entity_was":     "https",
        "real_entity":    "f3ir.com / F3 International Resources LLC",
    },
    "dd_07f45b072b9f": {
        "reason": "Earlier instance of the same bug — ran before the d4aa0c1 URL-parser fix. "
                  "Findings that reference this run_id should be re-verified against primary sources.",
        "quarantined_at": "2026-04-17T21:45:00+00:00",
        "entity_was":     "https / URL fragment",
        "real_entity":    "f3ir.com / F3 International Resources LLC",
    },
}


_RUN_ID_RE = re.compile(r"(dd_[a-f0-9]{10,16})", re.IGNORECASE)


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

async def _load() -> dict[str, dict[str, str]]:
    """Load quarantine list from Redis, merging in the seeded entries."""
    merged: dict[str, dict[str, str]] = dict(_SEEDED)
    try:
        from . import redis_store as rs
        data = await rs.get_json(_KEY)
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict):
                    merged[k] = v
    except Exception as exc:
        logger.debug("run_quarantine: Redis load failed (using seed only): %s", exc)
    return merged


async def _save(entries: dict[str, dict[str, str]]) -> None:
    """Persist quarantine entries to Redis (excluding seeded ones)."""
    try:
        from . import redis_store as rs
        # Do not persist the seeds — they are code-resident so the
        # quarantine survives a Redis wipe.
        dynamic = {k: v for k, v in entries.items() if k not in _SEEDED}
        await rs.set_json(_KEY, dynamic)
    except Exception as exc:
        logger.debug("run_quarantine: Redis save failed: %s", exc)


async def quarantine_run(
    run_id: str,
    reason: str,
    entity_was: str = "",
    real_entity: str = "",
) -> dict[str, Any]:
    """Mark a DD run as defective. Entries are additive — a second call
    with the same run_id updates the reason."""
    if not run_id or not run_id.strip():
        return {"ok": False, "error": "empty run_id"}
    run_id = run_id.strip()
    entries = await _load()
    entries[run_id] = {
        "reason": reason or "(no reason given)",
        "quarantined_at": datetime.now(timezone.utc).isoformat(),
        "entity_was": entity_was,
        "real_entity": real_entity,
    }
    await _save(entries)
    logger.warning("Run %s quarantined: %s", run_id, reason[:120])

    # Brain signal — quarantine = "this run was defective and must not be
    # cited". Feed it as a HIGH-severity capability gap so the predictor
    # can warn on similar future runs.
    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="run_quarantine",
            summary=f"Quarantined run {run_id}: {reason[:120]}",
            detail=f"entity_was={entity_was!r} real_entity={real_entity!r} reason={reason}",
            entity_name=real_entity or entity_was or "",
            success=True,  # quarantining is a successful integrity action
            gap_type="defective_dd_run",
            gap_detail=f"run {run_id} blocked from being cited: {reason[:200]}",
            confidence="CONFIRMED",
        )
    except Exception:
        pass

    return {"ok": True, "run_id": run_id, "total_quarantined": len(entries)}


async def is_quarantined(run_id: str) -> bool:
    """Fast check — returns True if this run's output should NOT be
    cited as evidence."""
    if not run_id:
        return False
    entries = await _load()
    return run_id.strip() in entries


async def get_quarantine_entry(run_id: str) -> dict[str, str] | None:
    """Full metadata for a quarantined run, or None if clean."""
    entries = await _load()
    return entries.get((run_id or "").strip())


async def list_quarantined() -> list[dict[str, Any]]:
    """Complete list for the /api/aria/dd/quarantine endpoint."""
    entries = await _load()
    return [
        {"run_id": k, **v}
        for k, v in sorted(entries.items(), key=lambda kv: kv[1].get("quarantined_at", ""))
    ]


async def filter_citations(text: str) -> tuple[str, list[str]]:
    """Remove quarantined-run citations from a block of text.

    Replaces `[from dd_orchestrate:dd_XXX]` markers pointing at
    quarantined runs with `[CITATION-QUARANTINED — original run defective]`
    so the reader knows the claim was based on a poisoned run and needs
    re-verification.

    Returns (cleaned_text, list_of_quarantined_run_ids_found).
    """
    if not text:
        return text, []
    entries = await _load()
    found: list[str] = []
    quarantined_set = set(entries.keys())

    def _replace(match: re.Match) -> str:
        rid = match.group(1).lower()
        if rid in quarantined_set:
            found.append(rid)
            return "[CITATION-QUARANTINED — prior run defective; re-verify]"
        return match.group(0)

    cleaned = re.sub(
        r"\[from\s+dd_orchestrate:(dd_[a-f0-9]{10,16})\]",
        _replace,
        text,
        flags=re.IGNORECASE,
    )
    # Also handle the bare "from dd_orchestrate:dd_XXX" shape
    cleaned = re.sub(
        r"dd_orchestrate:(dd_[a-f0-9]{10,16})",
        _replace,
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned, found


async def extract_run_ids(text: str) -> list[str]:
    """Pull every dd_XXX identifier out of a block of text. Used by
    the chat pipeline to check prior-turn citations for quarantine."""
    if not text:
        return []
    return [m.lower() for m in _RUN_ID_RE.findall(text)]


def summary() -> dict[str, Any]:
    """Capability-manifest summary."""
    return {
        "seeded_quarantined": len(_SEEDED),
        "seed_run_ids": list(_SEEDED.keys()),
    }


# ═══════════════════════════════════════════════════════════════════════
# Sync helpers — for sync callers (mem0 retrieve, knowledge search)
# ═══════════════════════════════════════════════════════════════════════
#
# These operate on the in-memory seed list only, skipping the Redis
# lookup. They're the fast path used from sync code (context-building
# layer functions). Async callers use the async helpers above which
# merge in the Redis-backed dynamic quarantines.

def is_quarantined_sync(run_id: str) -> bool:
    """O(1) sync check against seeded quarantines only."""
    return bool(run_id) and run_id.strip() in _SEEDED


def filter_citations_sync(text: str) -> tuple[str, list[str]]:
    """Sync version of filter_citations — seeded quarantines only."""
    if not text:
        return text, []
    found: list[str] = []

    def _replace(match: re.Match) -> str:
        rid = match.group(1).lower()
        if rid in _SEEDED:
            found.append(rid)
            return "[CITATION-QUARANTINED — prior run defective; re-verify]"
        return match.group(0)

    cleaned = re.sub(
        r"\[from\s+dd_orchestrate:(dd_[a-f0-9]{10,16})\]",
        _replace,
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"dd_orchestrate:(dd_[a-f0-9]{10,16})",
        _replace,
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned, found
