"""Cross-sweep verification accumulator (A angle (a)).

Closes the 2026-04-25 training-pipeline starvation residual: even with
the well_formed tier (angle b), responses whose claims are 1-source on
first appearance get bucketed below `grounded` and stay there forever
because the audit entry's verification_status is captured once at
recording time and never re-evaluated. As later sweeps add corroborating
sources to verified_intel, claims that were 1-source today become
2+-source tomorrow — but no mechanism upgrades the audit entry's
status retroactively.

This module fills that gap with a SIDECAR QUEUE — never mutating the
HMAC-signed audit log entry itself (which would break chain integrity).
The flow:

  1. _verify_and_record_chat enqueues (response_hash, claims, status)
     for any audit entry recorded as `well_formed` or `unverified`
     that has at least one extracted claim.

  2. A periodic reconcile() task re-runs claim matching against the
     CURRENT verified_intel:facts snapshot. If grounded_rate now
     ≥ 0.40, the queue entry's `current_status` is updated to
     `grounded` and an `upgraded_at` timestamp recorded.

  3. training_export._collect_chat_turns checks get_status() for each
     audit entry. When the queue says `grounded` (upgraded post-hoc),
     the entry is accepted for training as if it had originally
     graded grounded.

  4. Entries older than 14 days that haven't upgraded are dropped from
     the queue — past that point, the verification window is closed
     and the entry's `well_formed` or `unverified` tier is final.

Storage
-------
crucix:verification:accumulator:{response_hash}  →  JSON entry
crucix:verification:accumulator:state             →  reconciler diagnostics

The response_hash is the chat_audit_log entry's `response_hash` field
(SHA-256 prefix of the response text). Stable, unique per response,
already part of the audit row — no new ID scheme to maintain.
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("aria.verification_accumulator")

_KEY_PREFIX = "crucix:verification:accumulator:"
_STATE_KEY = "crucix:verification:accumulator:state"
_QUEUE_TTL_S = 14 * 86400          # entries dropped after 14 days
_DEFAULT_RECONCILE_BATCH = 200
_GROUNDED_RATE_THRESHOLD = 0.40    # matches engine + training filter


async def enqueue_for_reconcile(
    *,
    response_hash: str,
    claims: list[str],
    original_status: str,
    audit_timestamp: str,
) -> None:
    """Queue a chat audit entry for cross-sweep re-verification.

    Called from _verify_and_record_chat when the recorded status is
    `well_formed` or `unverified` AND at least one tier-marked claim
    was extracted. No-op for `grounded` (already verified) or
    `no_claims` (nothing to re-check) or empty claims list.
    """
    if not response_hash or not claims:
        return
    if original_status not in ("well_formed", "unverified"):
        return
    try:
        from . import redis_store as rs
        # Truncate claim text aggressively — re-evaluation is keyword-
        # overlap based, so short forms preserve matching and bound
        # storage. 400 chars × up to 15 claims = ~6KB max per entry.
        trimmed = [c.strip()[:400] for c in claims[:15] if c and len(c.strip()) >= 15]
        if not trimmed:
            return
        now = time.time()
        entry = {
            "response_hash": response_hash,
            "audit_timestamp": audit_timestamp,
            "claims": trimmed,
            "original_status": original_status,
            "current_status": original_status,
            "queued_at": now,
            "last_check": 0.0,
            "checks_run": 0,
            "upgraded_at": None,
        }
        await rs.set_json(_KEY_PREFIX + response_hash, entry, ex=_QUEUE_TTL_S)
    except Exception as exc:
        # Queue persistence must never block chat — the audit row is
        # already written by the time we get here.
        logger.debug("verification_accumulator.enqueue failed (non-fatal): %s", exc)


async def get_status(response_hash: str) -> dict | None:
    """Look up an entry's current (possibly upgraded) status. Returns
    None if not in queue (entry was either never enqueued — already
    grounded or no_claims — or aged out past the 14-day window)."""
    if not response_hash:
        return None
    try:
        from . import redis_store as rs
        entry = await rs.get_json(_KEY_PREFIX + response_hash)
        return entry if isinstance(entry, dict) else None
    except Exception:

    # R-F1001 - wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="verification_accumulator",
        summary="Get Status",
        source_id="verification_accumulator:R-F1001",
    )

        return None


async def reconcile(batch_size: int = _DEFAULT_RECONCILE_BATCH) -> dict:
    """Re-evaluate up to `batch_size` pending entries against current
    verified_intel:facts. Promote `grounded` if grounded_rate now meets
    threshold. Returns {scanned, upgraded, still_pending, errors}."""
    from . import redis_store as rs
    diag = {"scanned": 0, "upgraded": 0, "still_pending": 0, "errors": 0,
            "started_at": time.time(), "finished_at": None}
    try:
        keys = await rs.scan_keys(_KEY_PREFIX + "*", count=max(batch_size * 2, 400))
        # Filter out the state key itself (it shares the prefix).
        keys = [k for k in keys if k != _STATE_KEY][:batch_size]
        if not keys:
            diag["finished_at"] = time.time()
            await _persist_state(diag)
            return diag

        # Snapshot verified_intel facts ONCE for the whole batch — the
        # corpus is shared across all claims, no need to re-fetch.
        facts_raw = await rs.get_json("crucix:verified_intel:facts") or []
        if not isinstance(facts_raw, list):
            facts_raw = []

        for k in keys:
            try:
                entry = await rs.get_json(k)
                if not isinstance(entry, dict):
                    continue
                diag["scanned"] += 1
                if entry.get("current_status") == "grounded":
                    # Already upgraded by an earlier pass — leave it
                    # in the queue until it ages out so training_export
                    # can still consult it.
                    continue
                claims = entry.get("claims") or []
                if not claims:
                    continue
                rate = _grounded_rate(claims, facts_raw)
                entry["last_check"] = time.time()
                entry["checks_run"] = (entry.get("checks_run") or 0) + 1
                if rate >= _GROUNDED_RATE_THRESHOLD:
                    entry["current_status"] = "grounded"
                    entry["upgraded_at"] = time.time()
                    entry["upgraded_grounded_rate"] = round(rate, 3)
                    diag["upgraded"] += 1
                else:
                    diag["still_pending"] += 1
                # Preserve TTL alignment with original queue_at: re-set
                # with the time remaining rather than refreshing the
                # full 14-day TTL on every reconcile (which would never
                # let entries age out).
                age = time.time() - (entry.get("queued_at") or time.time())
                remaining = max(60, int(_QUEUE_TTL_S - age))
                await rs.set_json(k, entry, ex=remaining)
            except Exception as exc:
                diag["errors"] += 1
                logger.debug("verification_accumulator reconcile entry failed: %s", exc)
    except Exception as exc:
        diag["errors"] += 1
        logger.warning("verification_accumulator.reconcile failed: %s", exc)
    diag["finished_at"] = time.time()
    await _persist_state(diag)
    return diag


def _grounded_rate(claims: list[str], facts_raw: list[dict]) -> float:
    """Local re-implementation of response_verifier's claim-matching
    logic. Counts how many of `claims` have ≥2 verified-intel matches
    in the current corpus. Returns 0.0 when corpus is empty.

    Kept in-module so reconcile doesn't introduce a circular import on
    response_verifier (which imports from this module's neighbours).
    """
    if not claims or not facts_raw:
        return 0.0
    verified = 0
    for claim in claims:
        terms = {t for t in (claim or "").lower().split() if len(t) > 3}
        if not terms:
            continue
        match_count = 0
        for fact in facts_raw:
            combined = (
                f"{(fact.get('claim') or '').lower()} "
                f"{(fact.get('entity_name') or '').lower()}"
            )
            overlap = sum(1 for t in terms if t in combined)
            if overlap >= 2:
                match_count += 1
                if match_count >= 2:
                    verified += 1
                    break
    return verified / max(1, len(claims))


async def stats() -> dict:
    """Queue depth + reconcile diagnostics for /api/aria/brain/stats."""
    from . import redis_store as rs
    try:
        keys = await rs.scan_keys(_KEY_PREFIX + "*", count=500)
        keys = [k for k in keys if k != _STATE_KEY]
        pending = upgraded = 0
        for k in keys:
            entry = await rs.get_json(k)
            if not isinstance(entry, dict):
                continue
            if entry.get("current_status") == "grounded":
                upgraded += 1
            else:
                pending += 1
        state = await rs.get_json(_STATE_KEY) or {}
    except Exception:
        pending = upgraded = 0
        state = {}
    return {
        "pending": pending,
        "upgraded": upgraded,
        "last_reconcile": state,
    }


async def _persist_state(diag: dict) -> None:
    try:
        from . import redis_store as rs
        await rs.set_json(_STATE_KEY, diag, ex=_QUEUE_TTL_S)
    except Exception:
        pass
