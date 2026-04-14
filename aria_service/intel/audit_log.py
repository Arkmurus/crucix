"""ARIA Audit Log — hash-chained, append-only compliance record.

Every compliance-relevant decision ARIA makes — sanctions screens, dual-use
assessments, EUC validations, watchlist changes, deal stage transitions,
export assessments, compliance case state changes — produces an audit entry.

Design properties
─────────────────
  - Append-only: stored via Redis LPUSH; never deleted, never edited.
  - Hash-chained: each entry contains prev_hash (SHA-256 of the prior entry's
    serialised form) + entry_hash (SHA-256 of this entry's serialised form
    including prev_hash). Tampering with any entry breaks the chain forward.
  - Indexed: secondary indexes by deal_id and by entity_hash for fast queries
    without scanning the whole log.
  - Brain-fed: every recorded entry also calls brain_hook.absorb so ARIA
    learns the rate and shape of compliance work over time, and can answer
    "what did I do on Deal X last week?" from her own memory.
  - Compliance-grade only: routine LLM thoughts and RAG queries do NOT log
    here. The threshold is "would a regulator ask about this?". Everything
    else stays in brain_hook / mastery / debug logs.

What to record
──────────────
  YES: sanctions_screen, dual_use_assessment, euc_check, watchlist_add,
       watchlist_remove, watchlist_alert, deal_stage_change, export_assessment,
       compliance_case_create, compliance_case_state_change, manual_override,
       registry_lookup_decisive (i.e. one that fed a compliance decision).

  NO:  RAG retrieval, internal LLM steps, tender crawls, briefings, chat
       turns, contact lookups, knowledge searches.

Public API
──────────
  await record(action, ...) -> dict           # returns the recorded entry
  await get_chain(start, count) -> list[dict] # walk the chain
  await get_by_deal(deal_id) -> list[dict]    # entries for a deal
  await get_by_entity(name) -> list[dict]     # entries about an entity
  await get_entry(entry_hash) -> dict | None  # single entry lookup
  await verify_chain(start, count) -> dict    # integrity check
  await stats() -> dict                       # totals + last entry
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("aria.audit_log")

# ── Redis keys ──────────────────────────────────────────────────────────────
_KEY_LOG = "crucix:audit:log"                     # main append-only chain
_KEY_HEAD_HASH = "crucix:audit:head_hash"         # latest entry_hash (for chain continuity)
_KEY_BY_DEAL = "crucix:audit:by_deal:{deal_id}"   # secondary index
_KEY_BY_ENTITY = "crucix:audit:by_entity:{ehash}" # secondary index
_KEY_BY_HASH = "crucix:audit:by_hash:{hash}"      # direct lookup

_GENESIS_HASH = "0" * 64

# Allow-list of action types — anything else is rejected to keep noise out.
RECORDED_ACTIONS = {
    "sanctions_screen",
    "dual_use_assessment",
    "euc_check",
    "watchlist_add",
    "watchlist_remove",
    "watchlist_alert",
    "deal_stage_change",
    "deal_create",
    "deal_close",
    "export_assessment",
    "compliance_case_create",
    "compliance_case_state_change",
    "registry_lookup_decisive",
    "manual_override",
}


def _entity_hash(entity_name: str) -> str:
    """Stable 16-char hash for entity-name indexing (case-insensitive, trimmed)."""
    norm = (entity_name or "").strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _serialise_for_hash(entry: dict) -> bytes:
    """Canonical JSON serialisation for hashing — sorted keys, no whitespace."""
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


async def _read_head_hash() -> str:
    """Read the current head hash, or return genesis if log is empty."""
    from . import redis_store as rs
    try:
        h = await rs.get(_KEY_HEAD_HASH)
        return h if h else _GENESIS_HASH
    except Exception:
        return _GENESIS_HASH


async def record(
    action: str,
    *,
    actor: str = "aria",
    entity_name: str = "",
    deal_id: str = "",
    case_id: str = "",
    inputs: Optional[dict] = None,
    outputs: Optional[dict] = None,
    decision: str = "",
    confidence: str = "",
    sources: Optional[list[str]] = None,
    notes: str = "",
    feed_brain: bool = True,
) -> dict:
    """Record a compliance-grade decision. Returns the entry as written.

    The returned dict carries `entry_hash` — keep it as the citation token
    for any downstream output that should be traceable back to this decision.
    """
    if action not in RECORDED_ACTIONS:
        # Refuse unrecognised actions so the log doesn't fill with noise.
        # Caller should either use brain_hook.absorb (for learning-only) or
        # extend RECORDED_ACTIONS with explicit justification.
        raise ValueError(
            f"audit_log.record: action '{action}' not in RECORDED_ACTIONS. "
            f"Either add it (with justification) or use brain_hook.absorb instead."
        )

    from . import redis_store as rs

    prev_hash = await _read_head_hash()
    timestamp = datetime.now(timezone.utc).isoformat()
    monotonic_seq = int(time.time() * 1000)  # sortable seq for tie-breaking

    # The body is everything that goes into the hash. The hash itself is
    # added after computation so it doesn't recurse.
    body = {
        "ts": timestamp,
        "seq": monotonic_seq,
        "action": action,
        "actor": actor,
        "entity_name": entity_name,
        "deal_id": deal_id,
        "case_id": case_id,
        "inputs": inputs or {},
        "outputs": outputs or {},
        "decision": decision,
        "confidence": confidence,
        "sources": sources or [],
        "notes": notes,
        "prev_hash": prev_hash,
    }
    entry_hash = hashlib.sha256(_serialise_for_hash(body)).hexdigest()
    entry = {**body, "entry_hash": entry_hash}

    # Persist: main chain + indexes + direct-lookup. Each is a separate Redis
    # call; if any fails we still have the main chain. Logging on best-effort.
    try:
        await rs.lpush(_KEY_LOG, json.dumps(entry, default=str))
        await rs.set(_KEY_HEAD_HASH, entry_hash)
        await rs.set(_KEY_BY_HASH.format(hash=entry_hash), json.dumps(entry, default=str))
        if deal_id:
            await rs.lpush(_KEY_BY_DEAL.format(deal_id=deal_id), entry_hash)
        if entity_name:
            await rs.lpush(_KEY_BY_ENTITY.format(ehash=_entity_hash(entity_name)), entry_hash)
    except Exception as e:
        # Refuse to silently swallow audit failures — but don't crash the
        # caller either. Log loud and return the entry so the caller knows
        # what would have been recorded.
        logger.error("AUDIT WRITE FAILED for action=%s entity=%s: %s", action, entity_name, e)
        entry["_persisted"] = False
        return entry

    logger.info(
        "[audit] %s | %s | entity=%s | deal=%s | hash=%s | decision=%s",
        action, actor, entity_name[:40], deal_id, entry_hash[:12], decision[:40],
    )

    # ── Brain hook: ARIA learns from her own audit trail. She should be
    # able to answer "what compliance work did I do on Deal X?" without
    # querying the audit log directly — the brain absorbs the same info.
    if feed_brain:
        try:
            from . import brain_hook
            summary = (
                f"Audit: {action} on {entity_name or deal_id or 'system'} "
                f"by {actor} → {decision or 'recorded'}"
            )
            await brain_hook.absorb(
                module="audit_log",
                summary=summary,
                entity_name=entity_name or deal_id,
                source_id=entry_hash,
                success=True,
                confidence=confidence or "ASSESSED",
            )
        except Exception as e:
            logger.debug("audit→brain absorb failed (non-fatal): %s", e)

    return entry


async def get_entry(entry_hash: str) -> Optional[dict]:
    """Look up a single entry by its hash."""
    from . import redis_store as rs
    raw = await rs.get(_KEY_BY_HASH.format(hash=entry_hash))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def get_chain(start: int = 0, count: int = 50) -> list[dict]:
    """Walk the main chain, newest first. start=0, count=50 returns the
    50 most-recent entries."""
    from . import redis_store as rs
    raw_list = await rs.lrange(_KEY_LOG, start, start + count - 1)
    out: list[dict] = []
    for raw in raw_list:
        try:
            out.append(json.loads(raw) if isinstance(raw, str) else raw)
        except Exception:
            continue
    return out


async def get_by_deal(deal_id: str, limit: int = 200) -> list[dict]:
    """All audit entries linked to a deal, newest first."""
    from . import redis_store as rs
    if not deal_id:
        return []
    hashes = await rs.lrange(_KEY_BY_DEAL.format(deal_id=deal_id), 0, limit - 1)
    entries: list[dict] = []
    for h in hashes:
        e = await get_entry(h.strip('"') if isinstance(h, str) else h)
        if e:
            entries.append(e)
    return entries


async def get_by_entity(entity_name: str, limit: int = 200) -> list[dict]:
    """All audit entries about an entity (case-insensitive), newest first."""
    from . import redis_store as rs
    if not entity_name:
        return []
    hashes = await rs.lrange(_KEY_BY_ENTITY.format(ehash=_entity_hash(entity_name)), 0, limit - 1)
    entries: list[dict] = []
    for h in hashes:
        e = await get_entry(h.strip('"') if isinstance(h, str) else h)
        if e:
            entries.append(e)
    return entries


async def verify_chain(start: int = 0, count: int = 100) -> dict:
    """Walk the chain and verify that each entry's prev_hash matches the
    actual hash of the prior entry, AND that each entry_hash matches the
    SHA-256 of its own body. Returns a structured integrity report."""
    entries = await get_chain(start, count)
    # We walk chronologically (oldest → newest), so reverse the newest-first list.
    entries_chrono = list(reversed(entries))
    if not entries_chrono:
        return {"verified": True, "checked": 0, "broken_at": None, "detail": "empty log"}

    broken: list[dict] = []
    expected_prev = entries_chrono[0].get("prev_hash") or _GENESIS_HASH
    for i, e in enumerate(entries_chrono):
        # Recompute the hash from the body
        body = {k: v for k, v in e.items() if k != "entry_hash"}
        recomputed = hashlib.sha256(_serialise_for_hash(body)).hexdigest()
        actual = e.get("entry_hash", "")
        if recomputed != actual:
            broken.append({
                "index": i,
                "ts": e.get("ts"),
                "action": e.get("action"),
                "stored_hash": actual,
                "recomputed_hash": recomputed,
                "reason": "entry_hash does not match recomputed body hash",
            })
        if e.get("prev_hash") != expected_prev:
            broken.append({
                "index": i,
                "ts": e.get("ts"),
                "action": e.get("action"),
                "stored_prev": e.get("prev_hash"),
                "expected_prev": expected_prev,
                "reason": "prev_hash does not match prior entry's entry_hash",
            })
        expected_prev = actual

    return {
        "verified": not broken,
        "checked": len(entries_chrono),
        "broken_count": len(broken),
        "broken": broken[:20],  # cap report size
    }


async def stats() -> dict:
    """Quick stats: total entries, head hash, last entry summary."""
    from . import redis_store as rs
    chain = await get_chain(0, 1)
    last = chain[0] if chain else None
    # Approximate total via LRANGE up to high cap (Redis LLEN would be cleaner;
    # we don't have it wrapped — use a high-bound LRANGE for a safe upper count).
    sample = await rs.lrange(_KEY_LOG, 0, 9999)
    total = len(sample)
    head = await _read_head_hash()
    return {
        "total_entries": total,
        "head_hash": head,
        "last_entry": {
            "ts": last.get("ts") if last else None,
            "action": last.get("action") if last else None,
            "actor": last.get("actor") if last else None,
            "entity_name": last.get("entity_name") if last else None,
            "deal_id": last.get("deal_id") if last else None,
            "decision": last.get("decision") if last else None,
            "entry_hash": last.get("entry_hash") if last else None,
        } if last else None,
    }
