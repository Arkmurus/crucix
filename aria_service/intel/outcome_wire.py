"""
Reusable outcome-wire primitive — ARIA proprioception for every limb.

Every surface (WA, web, TG, email, Coder CLI, API) reports delivery outcomes
to this module. The brain records success/failure, writes to a health ledger,
and triggers a capability gap on non-success so the self-heal/coder loop can act.

Usage (any surface):
    from aria_service.intel.outcome_wire import record_outcome, OutcomeRecord

    await record_outcome(OutcomeRecord(
        surface="wa",
        request_id=msg.key.id,
        intended_result="send_reply",
        actual_outcome="delivered_real_answer",  # or timeout_fallback/error/send_failed
        latency_ms=1234,
        detail="",
    ))

R-F1411 — T0★ reusable outcome primitive.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Literal

logger = logging.getLogger("aria.intel.outcome_wire")

# Redis key prefix for per-surface outcome lists
_OUTCOME_KEY_PREFIX = "crucix:aria:outcomes:"
# Max outcomes stored per surface (oldest trimmed)
_MAX_OUTCOMES_PER_SURFACE = 10000
# Default time window for health queries (hours)
_DEFAULT_HEALTH_WINDOW_H = 24

OutcomeType = Literal[
    "delivered_real_answer",
    "timeout_fallback",
    "error",
    "send_failed",
]


@dataclass
class OutcomeRecord:
    """One delivery outcome for a single request.

    Attributes:
        surface:      Channel name (wa, web, tg, email, cli, api, ...).
        request_id:   Unique request identifier (e.g. WA msg.key.id).
        intended_result: What was supposed to happen (e.g. "send_reply").
        actual_outcome: What actually happened.
        latency_ms:   Wall-clock time from request to outcome (ms).
        detail:       Human-readable detail (error message, etc.).
        timestamp:    ISO-8601 timestamp (auto-set on record).
    """
    surface: str
    request_id: str
    intended_result: str
    actual_outcome: OutcomeType
    latency_ms: int
    detail: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _outcome_key(surface: str) -> str:
    return f"{_OUTCOME_KEY_PREFIX}{surface}"


def _dedupe_key(surface: str, request_id: str) -> str:
    return f"{_OUTCOME_KEY_PREFIX}{surface}:dedupe:{request_id}"


async def record_outcome(record: OutcomeRecord) -> dict:
    """Record a delivery outcome to the brain + health ledger + gap store.

    On non-success outcomes, records a capability gap so the self-heal/coder
    loop can act. Dedupes by (surface, request_id) — last outcome wins.

    Args:
        record: The outcome to record.

    Returns:
        dict with keys: recorded (bool), gap_recorded (bool), deduped (bool).
    """
    from . import redis_store as _rs
    from . import brain_hook as _bh
    from . import capability_gaps as _cg

    result: dict = {"recorded": False, "gap_recorded": False, "deduped": False}

    # ── Dedupe by (surface, request_id) ──────────────────────────────────
    # The dedupe key prevents double gap recording from retries (R-F1393).
    # The outcome list keeps all entries (retries are informative for health).
    dedupe_key = _dedupe_key(record.surface, record.request_id)
    existing = await _rs.get(dedupe_key)
    if existing:
        result["deduped"] = True

    # ── Store the outcome ────────────────────────────────────────────────
    entry = asdict(record)
    raw = json.dumps(entry, sort_keys=True, default=str)
    try:
        await _rs.lpush(_outcome_key(record.surface), raw)
        # Set a TTL on the dedupe key (24h — after that, same request_id is new)
        await _rs.set(dedupe_key, raw)
        await _rs.expire(dedupe_key, 86400)
        # Cap the list to prevent unbounded growth
        await _rs.ltrim(_outcome_key(record.surface), 0, _MAX_OUTCOMES_PER_SURFACE - 1)
        result["recorded"] = True
    except Exception as e:
        logger.error("[outcome_wire] Failed to store outcome: %s", e)
        return result

    # ── Wire to brain_hook ───────────────────────────────────────────────
    is_success = record.actual_outcome == "delivered_real_answer"
    try:
        await _bh.absorb(
            module=f"outcome_wire:{record.surface}",
            summary=(
                f"{'OK' if is_success else 'FAIL'} {record.intended_result} "
                f"on {record.surface} ({record.latency_ms}ms)"
            ),
            detail=record.detail or record.actual_outcome,
            success=is_success,
            gap_type=None if is_success else "delivery_failure",
            gap_detail=(
                None if is_success
                else f"{record.surface}:{record.intended_result} "
                     f"→ {record.actual_outcome} ({record.detail})"
            ),
            source_id=record.request_id,
            confidence="CONFIRMED",
        )
    except Exception as e:
        logger.warning("[outcome_wire] brain_hook.absorb failed: %s", e)

    # ── Record capability gap on non-success ─────────────────────────────
    if not is_success:
        try:
            gap_result = await _cg.record_gap(
                gap_type="delivery_failure",
                detail=(
                    f"{record.surface}:{record.intended_result} "
                    f"→ {record.actual_outcome} "
                    f"({record.detail or 'no detail'}) "
                    f"[{record.request_id}]"
                ),
                source=record.surface,
            )
            result["gap_recorded"] = not gap_result.get("deduped", False)
        except Exception as e:
            logger.warning("[outcome_wire] record_gap failed: %s", e)

    return result


async def get_outcomes(surface: str, hours: int = _DEFAULT_HEALTH_WINDOW_H) -> list[OutcomeRecord]:
    """Retrieve outcomes for a surface within a time window.

    Args:
        surface: Channel name.
        hours:   Lookback window in hours.

    Returns:
        List of OutcomeRecord, newest first.
    """
    from . import redis_store as _rs

    key = _outcome_key(surface)
    try:
        raw_list = await _rs.lrange(key, 0, -1)
    except Exception as e:
        logger.error("[outcome_wire] Failed to read outcomes: %s", e)
        return []

    cutoff = time.time() - (hours * 3600)
    results: list[OutcomeRecord] = []
    for raw in raw_list:
        try:
            entry = json.loads(raw)
            ts = entry.get("timestamp", "")
            # Parse ISO timestamp to epoch for comparison
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    if dt.timestamp() < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass  # keep entries with unparseable timestamps
            results.append(OutcomeRecord(**entry))
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            logger.debug("[outcome_wire] Skipping malformed entry: %s", e)
            continue

    return results


async def get_surface_health(surface: str, hours: int = _DEFAULT_HEALTH_WINDOW_H) -> dict:
    """Get delivery health stats for a surface.

    Args:
        surface: Channel name.
        hours:   Lookback window in hours.

    Returns:
        dict with keys: surface, hours, total, success_count, success_rate,
                        by_outcome (dict of outcome→count), recent_failures (list).
    """
    outcomes = await get_outcomes(surface, hours)
    total = len(outcomes)
    if total == 0:
        return {
            "surface": surface,
            "hours": hours,
            "total": 0,
            "success_count": 0,
            "success_rate": 0.0,
            "by_outcome": {},
            "recent_failures": [],
        }

    by_outcome: dict[str, int] = {}
    success_count = 0
    recent_failures: list[dict] = []

    for o in outcomes:
        by_outcome[o.actual_outcome] = by_outcome.get(o.actual_outcome, 0) + 1
        if o.actual_outcome == "delivered_real_answer":
            success_count += 1
        else:
            if len(recent_failures) < 20:
                recent_failures.append({
                    "request_id": o.request_id,
                    "intended_result": o.intended_result,
                    "actual_outcome": o.actual_outcome,
                    "latency_ms": o.latency_ms,
                    "detail": o.detail,
                    "timestamp": o.timestamp,
                })

    return {
        "surface": surface,
        "hours": hours,
        "total": total,
        "success_count": success_count,
        "success_rate": round(success_count / total, 4) if total > 0 else 0.0,
        "by_outcome": by_outcome,
        "recent_failures": recent_failures,
    }
