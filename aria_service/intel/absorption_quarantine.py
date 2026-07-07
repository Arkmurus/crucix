"""Absorption quarantine queue — defence-in-depth for brain_hook.

Background
----------
The 2026-04-25 OpenClaw incident proved that pay-once-remember-forever
absorption is a memory-poisoning vector when the underlying answer is a
hallucination. The current absorption gate (brain_hook.py) hard-rejects
two specific cases — known-fabrication tokens and search-derived self-
infra content — but every other absorption flows directly into mem0 /
knowledge facts / RAG with no review step.

This module provides the third option: opt-in quarantine for absorption
attempts, gated by env var ARIA_ABSORPTION_QUARANTINE_MODULES (a comma-
separated list of module names). When a listed module calls
brain_hook.absorb(), the absorption is enqueued here instead of being
written directly to permanent memory. Operator inspects via the
/api/aria/admin/absorption-quarantine/* endpoints and either:
  - promotes (calls _real_absorb_after_review() — content lands in
    permanent memory as if the gate never fired)
  - rejects (drops the entry, optionally adding the offending tokens
    to a watchlist so future attempts hard-reject)

Default OFF — empty env var means production behaviour is unchanged.
This is a primitive operator can flip on instantly when a new poison
vector is suspected, without a redeploy or code change.

Storage shape
-------------
Each pending entry: crucix:aria:absorption_quarantine:<id> -> JSON
  {
    "id": str,              # uuid
    "enqueued_at": float,   # unix seconds
    "module": str,          # absorbing module name
    "entity_name": str,
    "summary": str,
    "detail": str,
    "topic": str | None,    # operator-provided tag for grouping
    "status": "pending" | "promoted" | "rejected",
    "decision_at": float | None,
    "decision_reason": str | None,
  }

Pending entries TTL 30 days. Once decided they're rewritten with a
shorter TTL (7 days) so the audit trail survives one or two operator
reviews without growing unbounded.
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import logging
import os
import time
import uuid
from typing import Any

logger = logging.getLogger("aria.absorption_quarantine")

_KEY_PREFIX = "crucix:aria:absorption_quarantine:"
_PENDING_TTL_S = 30 * 86400
_DECIDED_TTL_S = 7 * 86400

_ENV_VAR = "ARIA_ABSORPTION_QUARANTINE_MODULES"


def quarantined_modules() -> set[str]:
    """Set of module names whose absorptions should be quarantined.

    Read fresh on every call so an env-var flip via fly.io secrets takes
    effect on the next absorb() — no restart required.
    """
    raw = os.environ.get(_ENV_VAR, "") or ""
    return {m.strip() for m in raw.split(",") if m.strip()}


def is_quarantine_enabled_for(module: str) -> bool:
    return module in quarantined_modules()


async def enqueue(
    *,
    module: str,
    entity_name: str | None,
    summary: str | None,
    detail: str | None,
    topic: str | None,
) -> str:
    """Add an absorption attempt to the quarantine queue. Returns the entry id."""
    from . import redis_store as rs
    qid = uuid.uuid4().hex
    entry = {
        "id": qid,
        "enqueued_at": time.time(),
        "module": module,
        "entity_name": entity_name or "",
        "summary": (summary or "")[:4000],
        "detail": (detail or "")[:8000],
        "topic": topic,
        "status": "pending",
        "decision_at": None,
        "decision_reason": None,
    }
    await rs.set_json(_KEY_PREFIX + qid, entry, ex=_PENDING_TTL_S)
    logger.info("absorption_quarantine: enqueued %s (module=%s entity=%s)", qid, module, entity_name)
    return qid


async def list_pending(limit: int = 100) -> list[dict[str, Any]]:
    """Return pending entries, newest-first (capped at `limit`)."""
    from . import redis_store as rs
    keys = await rs.scan_keys(_KEY_PREFIX + "*", count=max(limit * 4, 200))
    entries: list[dict[str, Any]] = []
    for k in keys:
        e = await rs.get_json(k)
        if isinstance(e, dict) and e.get("status") == "pending":
            entries.append(e)
    entries.sort(key=lambda x: x.get("enqueued_at") or 0, reverse=True)
    return entries[:limit]


async def get(entry_id: str) -> dict[str, Any] | None:
    from . import redis_store as rs
    e = await rs.get_json(_KEY_PREFIX + entry_id)
    return e if isinstance(e, dict) else None


async def _decide(entry_id: str, decision: str, reason: str | None) -> dict[str, Any] | None:
    from . import redis_store as rs
    entry = await get(entry_id)
    if not entry or entry.get("status") != "pending":
        return None
    entry["status"] = decision
    entry["decision_at"] = time.time()
    entry["decision_reason"] = reason or ""
    await rs.set_json(_KEY_PREFIX + entry_id, entry, ex=_DECIDED_TTL_S)
    return entry


async def reject(entry_id: str, reason: str | None = None) -> dict[str, Any] | None:
    """Drop a pending entry. The audit row stays for 7 days under the
    same key, status=rejected, so operator can review the decision trail."""
    return await _decide(entry_id, "rejected", reason)


async def promote(entry_id: str, reason: str | None = None) -> dict[str, Any] | None:
    """Mark an entry promoted. The CALLER is responsible for actually
    writing the content into permanent memory -- typically the
    `/admin/absorption-quarantine/promote` route, which re-runs
    brain_hook.absorb() with the source module temporarily removed from
    the quarantine list so the absorption goes through the gate but not
    back into the queue.

    (Earlier comments referenced `brain_hook.absorb_after_review()` --
    that function never existed; the route at routes/aria.py:6292 is
    the actual re-trigger path.)
    """
    return await _decide(entry_id, "promoted", reason)


async def stats() -> dict[str, Any]:
    """Queue depth + decided counts for /api/aria/brain/stats surface."""
    from . import redis_store as rs
    # R-F1885 — one batched query (scan_json) instead of scan_keys + up to 500
    # sequential get_json round-trips. This loop dominated /api/aria/health cost
    # (the round-trips, not the scan — R-F1871 already fixed the scan). Same 500
    # cap + counting semantics; only the fetch is batched.
    items = await rs.scan_json(_KEY_PREFIX + "*", count=500)
    pending = promoted = rejected = 0
    oldest_pending_at: float | None = None
    for _k, e in items:
        if not isinstance(e, dict):
            continue
        s = e.get("status")
        if s == "pending":
            pending += 1
            ts = e.get("enqueued_at")
            if isinstance(ts, (int, float)):
                oldest_pending_at = ts if oldest_pending_at is None else min(oldest_pending_at, ts)
        elif s == "promoted":
            promoted += 1
        elif s == "rejected":
            rejected += 1
    age_h = round((time.time() - oldest_pending_at) / 3600, 1) if oldest_pending_at else None
    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="absorption_quarantine",
                     summary="absorption_quarantine module active",
                     source_id="absorption_quarantine:init")
    except Exception:
        try:
            wire_failure(module="absorption_quarantine", detail="operation failed", gap_type="engine_failure", source="absorption_quarantine")
        except Exception:
            pass
        pass

    return {
        "enabled": bool(quarantined_modules()),
        "quarantined_modules": sorted(quarantined_modules()),
        "pending": pending,
        "promoted_recent": promoted,
        "rejected_recent": rejected,
        "oldest_pending_age_h": age_h,
    }
