"""Pending-actions queue — ARIA's "I owe you one" ledger.

Context
───────
ARIA frequently promises work that cannot fire inline (too slow, no URL to
hand, missing env var, no tool binding for the intent). Before this module,
those promises disappeared: she said "I will now crawl Baykar" and then
the chat turn ended with no tool call, no background task, no nudge back
to the operator. Silent failure — the user thinks the work is running.

This queue makes those promises visible and actionable:

  1. Any module that detects an un-actionable claim calls `record()` with
     the promise text, the reason it can't run inline, and (if known) the
     resolver — either an autonomous-task name to fire, or an operator
     prompt ("please set ARIA_AUTONOMOUS_ENABLED=1 on fly.io").
  2. The daily 05:45 UTC briefing reads the queue and surfaces open items
     at the top — the operator sees "4 pending actions, 2 need your input".
  3. WA channel can proactively nudge on HIGH severity items when it's
     been >6 hours since the promise was made.
  4. When the resolver completes (task fires, env var is set), the item
     is marked satisfied so the queue stays honest.

Not a substitute for
────────────────────
  - mistake_ledger: that logs what went wrong. This logs what was promised.
  - approval_queue (if/when added): that logs items awaiting human sign-off.
    Pending actions are ARIA's own TODO list — some resolve themselves
    (background tasks), some need the operator.
  - capability_gaps: that logs "I don't support X". This logs "I claimed
    I WOULD do X and here's the state of that promise."

Public API
──────────
  await record(promise, reason, resolver_kind, resolver_ref, ...) -> dict
  await list_open(limit=50, severity=None) -> list[dict]
  await mark_satisfied(action_id, note="") -> dict
  await mark_cancelled(action_id, note="") -> dict
  await stats() -> dict
  await nudge_candidates(min_age_hours=6) -> list[dict]
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.pending_actions")

# Severity levels govern whether the operator gets a proactive WA nudge:
#   LOW       — informational, surfaced only in daily briefing
#   MEDIUM    — surfaced in briefing + highlighted
#   HIGH      — proactive nudge after 6h unresolved
#   CRITICAL  — proactive nudge immediately (e.g. autonomous engine OFF)
SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

# Resolver kinds tell the briefing how to frame the item:
#   autonomous_task  — will fire on the next matching cron tick
#   operator_action  — needs the human to do something (env var, approval)
#   background_job   — enqueued job, no human action required
#   pure_promise     — ARIA made the claim without a resolver path (worst
#                      case — flagged so we can wire the missing tool)
RESOLVER_KINDS = {"autonomous_task", "operator_action", "background_job", "pure_promise"}

_KEY_LIST = "crucix:pending_actions:list"          # list of action_ids, newest first
_KEY_BY_ID = "crucix:pending_actions:by_id:{aid}"  # per-item payload
_KEY_STATS = "crucix:pending_actions:stats"        # counters


async def record(
    promise: str,
    reason: str,
    *,
    resolver_kind: str = "pure_promise",
    resolver_ref: str = "",
    severity: str = "MEDIUM",
    user_id: str = "",
    chat_id: str = "",
    source: str = "chat",
    operator_prompt: str = "",
    metadata: dict | None = None,
) -> dict:
    """Record a pending action. Returns the stored entry.

    Args:
        promise: The claim ARIA made, verbatim or summarised.
        reason: Why it cannot complete inline (e.g. "no URL in prompt",
            "ARIA_AUTONOMOUS_ENABLED is off").
        resolver_kind: How this should eventually resolve. See RESOLVER_KINDS.
        resolver_ref: Pointer to the resolver — autonomous task name,
            operator env var, job ID. Free-text but kept stable.
        severity: LOW | MEDIUM | HIGH | CRITICAL.
        user_id / chat_id: Who asked and where, so we can nudge back.
        source: Which subsystem recorded this (chat, autonomous, briefing).
        operator_prompt: Optional verbatim message to send back to the
            operator. Use imperative: "Set ARIA_AUTONOMOUS_ENABLED=1 on fly".
        metadata: Free-form dict — intent, entity list, etc.
    """
    if severity not in SEVERITIES:
        severity = "MEDIUM"
    if resolver_kind not in RESOLVER_KINDS:
        resolver_kind = "pure_promise"

    from . import redis_store as rs

    ts = datetime.now(timezone.utc).isoformat()
    seq = int(time.time() * 1000)
    aid = hashlib.sha256(
        f"{seq}|{resolver_kind}|{resolver_ref}|{promise[:120]}".encode("utf-8")
    ).hexdigest()[:16]

    entry = {
        "action_id": aid,
        "ts": ts,
        "seq": seq,
        "promise": (promise or "").strip()[:600],
        "reason": (reason or "").strip()[:300],
        "resolver_kind": resolver_kind,
        "resolver_ref": (resolver_ref or "").strip()[:200],
        "severity": severity,
        "user_id": user_id[:80],
        "chat_id": chat_id[:80],
        "source": source[:40],
        "operator_prompt": (operator_prompt or "").strip()[:400],
        "metadata": metadata or {},
        "status": "open",
        "nudged_at": None,
        "satisfied_at": None,
        "satisfied_note": "",
    }

    try:
        await rs.set_json(_KEY_BY_ID.format(aid=aid), entry)
        await rs.lpush(_KEY_LIST, aid)
        await rs.ltrim(_KEY_LIST, 0, 499)  # keep last 500
        await rs.incr(_KEY_STATS + ":recorded")
        await rs.incr(_KEY_STATS + f":severity:{severity}")
        logger.info(
            "[pending_actions] recorded %s (%s / %s) — %s",
            aid, severity, resolver_kind, entry["promise"][:80],
        )
    except Exception as e:
        logger.warning("[pending_actions] record failed: %s", e)

    # Airtable mirror — operator-visible Task Register. Fire-and-forget;
    # silent no-op if AIRTABLE_PAT isn't set (local dev, or the table
    # hasn't been created yet). See integrations/airtable_sync.py docstring.
    #
    # 2026-04-21: loglevel raised debug→warning for real failures. Ops
    # flagged "fix the airtable" because silent drops at logger.debug
    # made it impossible to see why rows were missing. sync_record
    # returns a dict with ok/reason; surface the reason explicitly when
    # it's a real failure (not "disabled" / "no_action_id" which are
    # expected no-op returns). The bare-except fallback stays debug
    # since it would only fire on a codebase bug, not an ops issue.
    try:
        from ..integrations import airtable_sync as _as
        _at_result = await _as.sync_record(entry)
        if isinstance(_at_result, dict) and not _at_result.get("ok"):
            _reason = _at_result.get("reason") or "unknown"
            if not str(_reason).startswith(("disabled:", "no_action_id", "empty_fields")):
                logger.warning(
                    "airtable sync DROPPED action=%s reason=%s http=%s",
                    (entry or {}).get("action_id", "?"),
                    _reason,
                    _at_result.get("http_status") or "-",
                )
                # R-F529 (2026-05-15) — buffer for retry instead of
                # silently dropping. Live 2026-05-15 morning:
                # "airtable sync DROPPED action=4a19afaeb076c6cb
                # reason=net:ConnectTimeout" was a HIGH/operator_action
                # circuit-trip notification — the single most
                # operationally important signal that turn — and we
                # lost it forever to a transient network blip.
                # buffer.enqueue() persists to SQLite; a periodic drain
                # retries against Airtable on the next sync cycle.
                try:
                    from ..integrations import airtable_buffer as _atb
                    _atb.enqueue(entry, reason=str(_reason)[:200])
                except Exception as _buf_err:
                    logger.warning(
                        "[airtable_buffer] R-F529 enqueue raised: %s "
                        "(this is bad — both sync and buffer failed for "
                        "action=%s; check airtable_buffer.db permissions)",
                        _buf_err, (entry or {}).get("action_id", "?"),
                    )
    except Exception as e:
        logger.warning("pending_actions airtable sync raised: %s", e)
        # R-F529 — even when the sync_record call itself raises (not
        # just returns ok=False), buffer the entry. The action_id
        # was already minted; the operator-visible record must not
        # be lost.
        try:
            from ..integrations import airtable_buffer as _atb
            _atb.enqueue(entry, reason=f"raised: {type(e).__name__}")
        except Exception:
            pass

    # Brain signal — every recorded promise is a self-honesty event.
    # CRITICAL/HIGH severity feeds capability_gap so the predictor
    # can warn on similar future promises (autonomy_off etc.).
    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="pending_actions",
            summary=f"Recorded {severity} promise via {resolver_kind} ({source}): {entry['promise'][:120]}",
            detail=entry.get("operator_prompt", "") or entry["reason"],
            success=True,
            gap_type=("pure_promise_no_resolver"
                      if resolver_kind == "pure_promise" else None),
            gap_detail=(f"Promise has no resolver path: {entry['promise'][:120]}"
                        if resolver_kind == "pure_promise" else None),
            confidence="CONFIRMED",
        )
    except Exception:
        pass

    # R-F1001 - wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="pending_actions",
        summary="Record",
        source_id="pending_actions:R-F1001",
    )

    return entry


async def list_open(
    limit: int = 50,
    severity: str | None = None,
) -> list[dict]:
    """Return open items newest-first. Closed/cancelled items are filtered."""
    from . import redis_store as rs
    try:
        ids = await rs.lrange(_KEY_LIST, 0, max(limit * 3, 50) - 1)
    except Exception as e:
        logger.warning("[pending_actions] list_open LRANGE failed: %s", e)
        return []

    out: list[dict] = []
    for aid in ids:
        try:
            entry = await rs.get_json(_KEY_BY_ID.format(aid=aid))
        except Exception:
            continue
        if not entry or entry.get("status") != "open":
            continue
        if severity and entry.get("severity") != severity:
            continue
        out.append(entry)
        if len(out) >= limit:
            break
    return out


async def mark_satisfied(action_id: str, note: str = "") -> dict:
    """Mark an item closed — resolver completed."""
    return await _mark_status(action_id, "satisfied", note)


async def mark_cancelled(action_id: str, note: str = "") -> dict:
    """Mark an item closed — no longer relevant (operator overrode, etc.)."""
    return await _mark_status(action_id, "cancelled", note)


async def _mark_status(action_id: str, new_status: str, note: str) -> dict:
    from . import redis_store as rs
    key = _KEY_BY_ID.format(aid=action_id)
    entry = await rs.get_json(key)
    if not entry:
        return {"ok": False, "error": "not_found", "action_id": action_id}
    entry["status"] = new_status
    entry["satisfied_at"] = datetime.now(timezone.utc).isoformat()
    entry["satisfied_note"] = (note or "")[:300]
    await rs.set_json(key, entry)
    await rs.incr(_KEY_STATS + f":{new_status}")
    logger.info("[pending_actions] %s → %s (%s)", action_id, new_status, note[:60])
    # Mirror the status change to Airtable — pass the FULL entry so that
    # compact-mode (What/Notes Housekeeping target) still has the promise
    # text available for the What field. Full-mode treats it as a normal
    # upsert on Action ID.
    # 2026-04-21: loglevel lifted from debug to warning for real drops.
    try:
        from ..integrations import airtable_sync as _as
        _at_result = await _as.sync_record(entry)
        if isinstance(_at_result, dict) and not _at_result.get("ok"):
            _reason = _at_result.get("reason") or "unknown"
            if not str(_reason).startswith(("disabled:", "no_action_id", "empty_fields")):
                logger.warning(
                    "airtable status-change sync DROPPED action=%s reason=%s http=%s",
                    action_id, _reason, _at_result.get("http_status") or "-",
                )
                # R-F529 — buffer status-change drops too. Status
                # transitions (satisfied/cancelled) are operationally
                # important: silently losing one leaves the Airtable
                # view stuck on "open" indefinitely.
                try:
                    from ..integrations import airtable_buffer as _atb
                    _atb.enqueue(entry, reason=str(_reason)[:200])
                except Exception as _buf_err:
                    logger.warning(
                        "[airtable_buffer] R-F529 enqueue raised: %s",
                        _buf_err,
                    )
    except Exception as e:
        logger.warning("pending_actions airtable status-change raised: %s", e)
        try:
            from ..integrations import airtable_buffer as _atb
            _atb.enqueue(entry, reason=f"raised: {type(e).__name__}")
        except Exception:
            pass
    return {"ok": True, "entry": entry}


async def nudge_candidates(min_age_hours: int = 6) -> list[dict]:
    """Return open HIGH/CRITICAL items old enough to re-nudge.

    CRITICAL items are nudged immediately (ignores min_age_hours). HIGH
    items are nudged only after the age gate to avoid spam. The caller
    (WA nudger or daily briefing) is responsible for sending the actual
    message and recording the nudge by calling `mark_nudged()`.
    """
    from . import redis_store as rs
    cutoff_seq = int((time.time() - min_age_hours * 3600) * 1000)

    out: list[dict] = []
    for entry in await list_open(limit=200):
        sev = entry.get("severity")
        if sev not in ("HIGH", "CRITICAL"):
            continue
        # Never re-nudge within 6h of the last nudge
        nudged_at_raw = entry.get("nudged_at")
        if nudged_at_raw:
            try:
                last = datetime.fromisoformat(nudged_at_raw).timestamp()
                if (time.time() - last) < 6 * 3600:
                    continue
            except Exception:
                pass
        if sev == "CRITICAL":
            out.append(entry)
            continue
        # HIGH — only after min_age_hours since creation
        if entry.get("seq", 0) <= cutoff_seq:
            out.append(entry)
    return out


async def mark_nudged(action_id: str) -> None:
    """Record that a nudge was sent so we don't spam the operator."""
    from . import redis_store as rs
    key = _KEY_BY_ID.format(aid=action_id)
    entry = await rs.get_json(key)
    if not entry:
        return
    entry["nudged_at"] = datetime.now(timezone.utc).isoformat()
    await rs.set_json(key, entry)


async def stats() -> dict:
    """One-shot counters for the dashboard."""
    from . import redis_store as rs
    open_items = await list_open(limit=500)
    counts_by_sev: dict[str, int] = {s: 0 for s in SEVERITIES}
    counts_by_kind: dict[str, int] = {k: 0 for k in RESOLVER_KINDS}
    for e in open_items:
        counts_by_sev[e.get("severity", "MEDIUM")] = counts_by_sev.get(e.get("severity", "MEDIUM"), 0) + 1
        counts_by_kind[e.get("resolver_kind", "pure_promise")] = counts_by_kind.get(e.get("resolver_kind", "pure_promise"), 0) + 1

    recorded = int((await rs.get(_KEY_STATS + ":recorded")) or 0)
    satisfied = int((await rs.get(_KEY_STATS + ":satisfied")) or 0)
    cancelled = int((await rs.get(_KEY_STATS + ":cancelled")) or 0)

    return {
        "open_total": len(open_items),
        "by_severity": counts_by_sev,
        "by_resolver_kind": counts_by_kind,
        "lifetime": {
            "recorded": recorded,
            "satisfied": satisfied,
            "cancelled": cancelled,
        },
    }


def briefing_summary(open_items: list[dict]) -> str:
    """Render open items as a block for the 05:45 daily briefing.

    Returns an empty string if the list is empty — callers can append
    this directly to the briefing without a conditional.
    """
    if not open_items:
        return ""

    needs_operator = [e for e in open_items if e.get("resolver_kind") == "operator_action"]
    will_resolve = [e for e in open_items if e.get("resolver_kind") in ("autonomous_task", "background_job")]
    stuck = [e for e in open_items if e.get("resolver_kind") == "pure_promise"]

    lines = ["📌 PENDING ACTIONS"]
    if needs_operator:
        lines.append(f"Needs your input ({len(needs_operator)}):")
        for e in needs_operator[:5]:
            prompt = e.get("operator_prompt") or e.get("promise")
            lines.append(f"  • [{e.get('severity')}] {prompt[:160]}")
    if will_resolve:
        lines.append(f"Will resolve on next tick ({len(will_resolve)}):")
        for e in will_resolve[:5]:
            lines.append(f"  • {e.get('promise', '')[:160]} ← {e.get('resolver_ref', '(no ref)')}")
    if stuck:
        lines.append(f"No resolver — I need code to action these ({len(stuck)}):")
        for e in stuck[:5]:
            lines.append(f"  • {e.get('promise', '')[:160]}  (reason: {e.get('reason', '')[:80]})")
    return "\n".join(lines)
