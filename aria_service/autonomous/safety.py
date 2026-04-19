"""ARIA Layer 3 — autonomous engine safety prerequisites.

This module is the **mandatory** first line of defence for the autonomous
engine. Every task spawn passes through these checks BEFORE any LLM call,
HTTP request, or delivery action runs. The whole point is to make
runaway behaviour impossible — a buggy task cannot drain the cost
budget, a stuck task cannot fire forever, a duplicate run cannot
double-spend tokens.

There are five guardrails:

  1. Rate limit       — token bucket on task firings per hour
  2. Daily cost cap   — circuit breaker on total daily LLM cost
  3. Deduplication    — skip if same task+entity ran in last 24h
  4. Per-task timeout — wrap the tool chain in asyncio.wait_for
  5. Engine pause     — global kill switch reachable from admin

All counters live in Redis with short TTLs so they survive restart but
self-clean. None of them rely on Python in-process state — the engine
can crash and restart without losing safety state.

The defaults are intentionally conservative. Bump them via env vars
once the engine has proven itself.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from ..intel import redis_store as rs

logger = logging.getLogger("aria.autonomous.safety")


# ── Configurable thresholds (env-var overridable) ──────────────────────────

def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "") or ""
    try:
        return float(raw) if raw else default
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r — using default %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "") or ""
    try:
        return int(raw) if raw else default
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r — using default %s", name, raw, default)
        return default


# Maximum task firings per rolling hour, across all tasks.
# Default is conservative (12/hour = average one every 5 minutes) so a
# bug that triggers a task in a tight loop cannot pile up.
MAX_FIRINGS_PER_HOUR = _env_int("ARIA_AUTONOMOUS_MAX_FIRINGS_PER_HOUR", 12)

# Daily cost cap in USD across the entire autonomous engine. Tasks
# whose cost would exceed this are rejected at spawn time. Default $1
# is intentionally tight for the first week of validation; bump via
# ARIA_AUTONOMOUS_DAILY_COST_CAP_USD once the engine has proven itself.
DAILY_COST_CAP_USD = _env_float("ARIA_AUTONOMOUS_DAILY_COST_CAP_USD", 1.00)

# Deduplication window: skip a task if the same task_id + resolved
# entity hash ran within the last N seconds. Default 23h (just under
# a day) so that a daily task can re-fire the next day without false
# dedupe hits at the schedule boundary.
DEDUPE_WINDOW_SECONDS = _env_int("ARIA_AUTONOMOUS_DEDUPE_WINDOW_S", 23 * 3600)


# ── Redis keys ─────────────────────────────────────────────────────────────

_RATE_KEY_FMT = "crucix:autonomous:rate:{hour}"  # hour bucket
_COST_KEY_FMT = "crucix:autonomous:cost:{date}"  # daily total
_DEDUPE_KEY_FMT = "crucix:autonomous:dedupe:{task_id}:{entity_hash}"
_PAUSE_KEY = "crucix:autonomous:paused"  # "1" if engine is paused
_PAUSE_TASK_FMT = "crucix:autonomous:paused:task:{task_id}"


# ── In-memory cost circuit breaker (H8) ───────────────────────────────────
# Redis is the authoritative cost counter, but it fails open on read.
# A Redis outage used to mean unlimited spend until it recovered. We now
# also track spend in-process with a UTC-day reset, and enforce the cap
# whichever counter is higher. If Redis is down, the in-memory counter
# still stops the engine at ARIA_AUTONOMOUS_DAILY_COST_CAP_USD.
_memory_cost_lock = None  # lazily created asyncio.Lock
_memory_cost_day = ""
_memory_cost_spent = 0.0


def _today_utc() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _memory_cost_get() -> float:
    global _memory_cost_day, _memory_cost_spent
    today = _today_utc()
    if today != _memory_cost_day:
        _memory_cost_day = today
        _memory_cost_spent = 0.0
    return _memory_cost_spent


def _memory_cost_add(usd: float) -> float:
    global _memory_cost_day, _memory_cost_spent
    today = _today_utc()
    if today != _memory_cost_day:
        _memory_cost_day = today
        _memory_cost_spent = 0.0
    if usd > 0:
        _memory_cost_spent += usd
    return _memory_cost_spent


# ── Public: rate limit ─────────────────────────────────────────────────────

async def check_and_increment_rate() -> tuple[bool, int]:
    """Token-bucket rate limit with hourly buckets.

    Returns (allowed, current_count_after_increment).
    The bucket key has a 3600s TTL so it self-cleans.
    """
    hour_bucket = int(time.time() // 3600)
    key = _RATE_KEY_FMT.format(hour=hour_bucket)
    try:
        # Increment first, then check — this is atomic via INCR
        new_count = await rs.incr(key)
        if new_count == 1:
            # First firing in this bucket — set TTL
            await rs.expire(key, 3600)
        allowed = new_count <= MAX_FIRINGS_PER_HOUR
        if not allowed:
            logger.warning(
                "[autonomous safety] rate limit hit: %d firings in current hour bucket "
                "(cap %d). Task will be skipped.",
                new_count, MAX_FIRINGS_PER_HOUR,
            )
        return allowed, new_count
    except Exception as e:
        # Fail OPEN on Redis errors — better to occasionally over-fire
        # than to grind the engine to a halt because Redis blipped.
        # Operators see the warning and can investigate.
        logger.warning(
            "[autonomous safety] rate limit check failed (Redis): %s — failing open",
            e,
        )
        return True, 0


# ── Public: cost cap ───────────────────────────────────────────────────────

async def check_cost_cap() -> tuple[bool, float]:
    """Circuit breaker on daily LLM cost.

    Returns (within_budget, current_daily_spent_usd).

    H8: uses BOTH a Redis counter (authoritative across processes) and
    an in-memory counter (survives Redis outages). The higher of the two
    is compared against the cap — so a Redis outage no longer opens the
    floodgates. Previously Redis failure failed open with unlimited spend.
    """
    today = _today_utc()
    key = _COST_KEY_FMT.format(date=today)
    mem_spent = _memory_cost_get()
    redis_spent = 0.0
    redis_ok = True
    try:
        spent_str = await rs.get(key) or "0"
        redis_spent = float(spent_str)
    except Exception as e:
        redis_ok = False
        logger.warning(
            "[autonomous safety] cost cap Redis read failed: %s — falling back to in-memory counter ($%.4f)",
            e, mem_spent,
        )

    spent = max(mem_spent, redis_spent)
    within = spent < DAILY_COST_CAP_USD
    if not within:
        logger.warning(
            "[autonomous safety] daily cost cap hit: $%.4f spent vs cap $%.2f "
            "(redis=$%.4f mem=$%.4f redis_ok=%s). Tasks skipped until %s 00:00 UTC.",
            spent, DAILY_COST_CAP_USD, redis_spent, mem_spent, redis_ok, today,
        )
    return within, spent


async def record_task_cost(usd: float) -> None:
    """Record the cost of a single task run against today's budget.

    Writes to both the in-memory counter (H8 fallback) and Redis. The
    in-memory counter is authoritative when Redis is offline.
    """
    if usd <= 0:
        return
    _memory_cost_add(usd)
    today = _today_utc()
    key = _COST_KEY_FMT.format(date=today)
    try:
        # Most Redis libraries expose incrbyfloat directly. The intel
        # redis_store wrapper exposes it as `incrbyfloat`. If it doesn't,
        # fall back to a get+set (racy but acceptable for the cost meter).
        if hasattr(rs, "incrbyfloat"):
            new_total = await rs.incrbyfloat(key, usd)
        else:
            existing = float(await rs.get(key) or "0")
            new_total = existing + usd
            await rs.set(key, f"{new_total:.6f}")
        # Set TTL on first write so the key auto-expires after 48h
        # (longer than 24h to handle UTC-day boundary edge cases)
        await rs.expire(key, 48 * 3600)
        logger.debug(
            "[autonomous safety] recorded $%.4f task cost; daily total now $%.4f",
            usd, new_total,
        )
    except Exception as e:
        logger.warning(
            "[autonomous safety] cost record failed: %s",
            e,
        )


# ── Public: deduplication ──────────────────────────────────────────────────

def _entity_hash(entity: str) -> str:
    """Stable short hash of an entity string for dedup keys."""
    import hashlib
    return hashlib.sha1((entity or "").strip().lower().encode("utf-8")).hexdigest()[:12]


async def check_and_mark_dedupe(task_id: str, entity: str) -> bool:
    """Return True if this task+entity is allowed to run, False if it
    duplicates a recent run.

    The marker is set with TTL = DEDUPE_WINDOW_SECONDS so a daily task
    can re-fire the next day cleanly.
    """
    if not task_id:
        return True
    key = _DEDUPE_KEY_FMT.format(
        task_id=task_id, entity_hash=_entity_hash(entity),
    )
    try:
        existing = await rs.get(key)
        if existing:
            logger.info(
                "[autonomous safety] dedupe hit for %s entity=%r — skipping",
                task_id, (entity or "")[:60],
            )
            return False
        # Mark as run with TTL
        await rs.set(key, "1")
        await rs.expire(key, DEDUPE_WINDOW_SECONDS)
        return True
    except Exception as e:
        logger.warning(
            "[autonomous safety] dedupe check failed: %s — failing open",
            e,
        )
        return True


async def clear_dedupe(task_id: str, entity: str = "") -> None:
    """Drop the dedupe marker for a task+entity. Called after a failed
    or invalid run so the next scheduled fire can retry — without this
    a transient outage (LLM cooldown, dispatch error) would lock the
    task out for the full DEDUPE_WINDOW_SECONDS (currently 23h).

    Discovered live 2026-04-19: today's adversarial + constitution +
    every dispatch-bug task burned their daily slot on a failed run.
    Manual run-now also blocked. Without this clear, the only recourse
    was waiting for the TTL or flushing redis.
    """
    if not task_id:
        return
    key = _DEDUPE_KEY_FMT.format(
        task_id=task_id, entity_hash=_entity_hash(entity),
    )
    try:
        await rs.delete(key)
    except Exception as e:
        logger.debug("[autonomous safety] clear_dedupe failed (non-fatal): %s", e)


# ── Public: pause / resume ─────────────────────────────────────────────────

async def is_engine_paused() -> bool:
    """Global engine kill switch. When True, NO tasks fire."""
    try:
        val = await rs.get(_PAUSE_KEY)
        return (val or "").strip() == "1"
    except Exception:
        return False  # Fail open — pause must be deliberate, not by accident


async def pause_engine(reason: str = "") -> None:
    try:
        await rs.set(_PAUSE_KEY, "1")
        logger.warning(
            "[autonomous safety] engine PAUSED via admin endpoint. Reason: %s",
            reason or "(none)",
        )
    except Exception as e:
        logger.error("[autonomous safety] failed to set pause flag: %s", e)


async def resume_engine() -> None:
    try:
        # Use delete (atomic), not set "0" (would still be truthy)
        if hasattr(rs, "delete"):
            await rs.delete(_PAUSE_KEY)
        else:
            await rs.set(_PAUSE_KEY, "0")
        logger.info("[autonomous safety] engine RESUMED via admin endpoint")
    except Exception as e:
        logger.error("[autonomous safety] failed to clear pause flag: %s", e)


async def is_task_paused(task_id: str) -> bool:
    """Per-task pause flag (independent of the global engine pause)."""
    try:
        val = await rs.get(_PAUSE_TASK_FMT.format(task_id=task_id))
        return (val or "").strip() == "1"
    except Exception:
        return False


async def pause_task(task_id: str) -> None:
    try:
        await rs.set(_PAUSE_TASK_FMT.format(task_id=task_id), "1")
        logger.info("[autonomous safety] task paused: %s", task_id)
    except Exception as e:
        logger.error("[autonomous safety] failed to pause task %s: %s", task_id, e)


async def resume_task(task_id: str) -> None:
    try:
        if hasattr(rs, "delete"):
            await rs.delete(_PAUSE_TASK_FMT.format(task_id=task_id))
        else:
            await rs.set(_PAUSE_TASK_FMT.format(task_id=task_id), "0")
        logger.info("[autonomous safety] task resumed: %s", task_id)
    except Exception as e:
        logger.error("[autonomous safety] failed to resume task %s: %s", task_id, e)


# ── Public: composite check (one call) ─────────────────────────────────────

async def can_task_run(task_id: str, entity: str) -> tuple[bool, str]:
    """Run all five guardrails. Returns (allowed, reason_if_blocked).

    Use this at the top of every task execution path. The five checks
    are ordered cheapest-first so we exit early on the most likely
    block conditions:

      1. Engine paused?           (1 Redis read)
      2. Task paused?             (1 Redis read)
      3. Daily cost cap exceeded? (1 Redis read)
      4. Hourly rate limit hit?   (1 Redis incr + maybe expire)
      5. Duplicate of recent run? (1 Redis get + maybe set+expire)

    Important: rate limit is the LAST check that increments state, so
    a task blocked by the cost cap or pause does NOT consume a rate
    bucket slot. This means a paused engine can be resumed without
    losing rate budget.
    """
    if await is_engine_paused():
        return False, "engine_paused"
    if await is_task_paused(task_id):
        return False, "task_paused"
    within_budget, spent = await check_cost_cap()
    if not within_budget:
        return False, f"daily_cost_cap_exceeded:{spent:.4f}"
    allowed_rate, count = await check_and_increment_rate()
    if not allowed_rate:
        return False, f"rate_limit_exceeded:{count}"
    if not await check_and_mark_dedupe(task_id, entity):
        return False, "duplicate_recent_run"
    return True, "ok"


# ── Public: snapshot for /status admin endpoint ────────────────────────────

async def get_safety_state() -> dict[str, Any]:
    """One-shot view of every safety counter for the admin /status endpoint."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    hour_bucket = int(time.time() // 3600)
    out: dict[str, Any] = {
        "thresholds": {
            "max_firings_per_hour": MAX_FIRINGS_PER_HOUR,
            "daily_cost_cap_usd": DAILY_COST_CAP_USD,
            "dedupe_window_seconds": DEDUPE_WINDOW_SECONDS,
        },
    }
    try:
        out["engine_paused"] = await is_engine_paused()
    except Exception as e:
        out["engine_paused_error"] = str(e)[:200]
    try:
        rate_count = await rs.get(_RATE_KEY_FMT.format(hour=hour_bucket))
        out["current_hour_firings"] = int(rate_count) if rate_count else 0
    except Exception as e:
        out["current_hour_firings_error"] = str(e)[:200]
    try:
        spent = await rs.get(_COST_KEY_FMT.format(date=today))
        out["daily_spent_usd"] = float(spent) if spent else 0.0
    except Exception as e:
        out["daily_spent_usd_error"] = str(e)[:200]
    return out
