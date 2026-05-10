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
        "quarantined_at":         "2026-04-17T21:45:00+00:00",
        "entity_was":             "https",
        "real_entity":            "f3ir.com / F3 International Resources LLC",
        # Closure metadata (R-F147, 2026-05-10) — Phase A gate #4 closer
        "investigation_status":   "closed",
        "closed_at":              "2026-05-10T22:30:00+00:00",
        "verified_fix_in_commit": "d4aa0c1b82482adc1d1f5d6a3cba4202cdc3a4b3",
        "regression_test_path":   "aria_service/tests/test_f3_cascade_fixes.py::test_validator_rejects_url_scheme_fragments",
        "closure_rationale":      "Root cause _DD_ENTITY_CAPTURE_RE bare '/' in terminator class — fixed (current code: aria.py:2693). URL-as-entity bridge using urllib.parse.urlparse is in _detect_dd_intent (current code: aria.py:2845, 2877-2879). Regression test actively rejects 'https'/'http'/'ftp' as entity names. The quarantine seed is permanent code-resident protection so any future references to this run_id are scrubbed by filter_citations(). No further action needed.",
    },
    "dd_adc7c7f87e4a": {
        "reason": "Same malformed-entity bug as dd_30477701e537. 5-match sanctions "
                  "result is a repeat of the same noise hit — both runs are defective.",
        "quarantined_at":         "2026-04-17T21:45:00+00:00",
        "entity_was":             "https",
        "real_entity":            "f3ir.com / F3 International Resources LLC",
        "investigation_status":   "closed",
        "closed_at":              "2026-05-10T22:30:00+00:00",
        "verified_fix_in_commit": "d4aa0c1b82482adc1d1f5d6a3cba4202cdc3a4b3",
        "regression_test_path":   "aria_service/tests/test_f3_cascade_fixes.py::test_validator_rejects_url_scheme_fragments",
        "closure_rationale":      "Same root cause as dd_30477701e537 — URL-parser bug in _DD_ENTITY_CAPTURE_RE. Same fix (d4aa0c1) covers this run. Quarantine seed remains active to scrub any residual citations. Real entity (f3ir.com / F3 International Resources LLC) can be re-DD'd cleanly via POST /api/aria/dd/orchestrate when the operator needs a current assessment.",
    },
    "dd_07f45b072b9f": {
        "reason": "Earlier instance of the same bug — ran before the d4aa0c1 URL-parser fix. "
                  "Findings that reference this run_id should be re-verified against primary sources.",
        "quarantined_at":         "2026-04-17T21:45:00+00:00",
        "entity_was":             "https / URL fragment",
        "real_entity":            "f3ir.com / F3 International Resources LLC",
        "investigation_status":   "closed",
        "closed_at":              "2026-05-10T22:30:00+00:00",
        "verified_fix_in_commit": "d4aa0c1b82482adc1d1f5d6a3cba4202cdc3a4b3",
        "regression_test_path":   "aria_service/tests/test_f3_cascade_fixes.py::test_validator_rejects_url_scheme_fragments",
        "closure_rationale":      "Earliest of the three F3-cascade defective runs, all sharing the same URL-parser bug root cause. Bug fix d4aa0c1 retrospectively makes this run impossible to reproduce. Filter_citations() scrubs any prior-conversation reference to this run_id. No live ledger signals or mem0 entries should still cite this run; any that do are flagged at chat-time by run_quarantine.is_quarantined().",
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
    """Complete list for the /api/aria/dd/quarantine endpoint.

    Each entry carries `investigation_status` ("closed" | "open") so the
    operator can see at a glance whether all quarantines have been
    investigated + closed (Phase A gate #4 criterion).
    """
    entries = await _load()
    items = [
        {"run_id": k, **v}
        for k, v in sorted(entries.items(), key=lambda kv: kv[1].get("quarantined_at", ""))
    ]
    # Default investigation_status for entries that lack the field (legacy
    # dynamic quarantines from before R-F147 closure tracking landed).
    for it in items:
        it.setdefault("investigation_status", "open")
    return items


async def closure_summary() -> dict[str, Any]:
    """Closed vs open quarantine counts. Phase A gate #4 closer surface.

    Returns:
      total: int                — all quarantined runs (seeded + dynamic)
      closed: int               — investigated + documented
      open: int                 — outstanding investigation work
      gate_passes: bool         — True iff all quarantines are closed
      open_run_ids: list[str]   — what's left to investigate
    """
    items = await list_quarantined()
    closed = sum(1 for it in items if it.get("investigation_status") == "closed")
    open_items = [it for it in items if it.get("investigation_status") != "closed"]
    return {
        "total": len(items),
        "closed": closed,
        "open": len(open_items),
        "gate_passes": len(open_items) == 0 and len(items) > 0,
        "open_run_ids": [it["run_id"] for it in open_items],
    }


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
