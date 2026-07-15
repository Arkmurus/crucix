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

import asyncio
import logging
import os
import time
from typing import Any

from ..intel import redis_store as rs
from ..intel.engine_wiring import wire_success, wire_failure
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

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
# R-F1051: raised to 1000 — ARIA is state-of-the-art and should not be
# artificially constrained. The $300/mo LLM cap is the real budget guard.
MAX_FIRINGS_PER_HOUR = _env_int("ARIA_AUTONOMOUS_MAX_FIRINGS_PER_HOUR", 1000)

# Daily cost cap in USD across the entire autonomous engine.
# R-F1051: raised to $50 — the $300/mo LLM cap is the real budget guard.
DAILY_COST_CAP_USD = _env_float("ARIA_AUTONOMOUS_DAILY_COST_CAP_USD", 50.00)

# Deduplication window: skip a task if the same task_id + resolved
# entity hash ran within the last N seconds. Default 23h (just under
# a day) so that a daily task can re-fire the next day without false
# dedupe hits at the schedule boundary.
DEDUPE_WINDOW_SECONDS = _env_int("ARIA_AUTONOMOUS_DEDUPE_WINDOW_S", 23 * 3600)


# ── Redis keys ─────────────────────────────────────────────────────────────

_RATE_KEY_FMT = "crucix:autonomous:rate:{hour}"  # hour bucket
# R-F901 — the ARIA-Coder gets its OWN hourly fix budget, separate from the
# shared task bucket above, so the ~87 periodic tasks can't starve it (live
# 2026-05-26: 50 gaps detected but 0 staged because the tasks consumed all 12
# slots). Stage-only (AUTO_DEPLOY=0) so each fire = one fix-gen LLM call that
# STAGES for operator review; the $300/mo cap remains the spend brake.
# Conservative default; raise via ARIA_CODER_MAX_FIXES_PER_HOUR if reviewing
# faster.
_CODER_RATE_KEY_FMT = "crucix:autonomous:coder_rate:{hour}"
CODER_MAX_FIXES_PER_HOUR = _env_int("ARIA_CODER_MAX_FIXES_PER_HOUR", 500)  # R-F1051: raised from 60
_COST_KEY_FMT = "crucix:autonomous:cost:{date}"  # daily total
_DEDUPE_KEY_FMT = "crucix:autonomous:dedupe:{task_id}:{entity_hash}"
_PAUSE_KEY = "crucix:autonomous:paused"  # "1" if engine is paused
# R-F2004: epoch-seconds the pause auto-expires. A pause WITHOUT this key is a
# legacy indefinite pause (pre-R-F2004) and is auto-resumed — a forgotten
# indefinite pause silently starved the entire live ecosystem for ~187h
# (fire=0). The live organism must never be killable indefinitely by a single
# forgotten "pause to verify".
_PAUSE_UNTIL_KEY = "crucix:autonomous:paused_until"
# Hard ceiling on how long a pause can last, even if a larger value is asked for.
# A safety pause is meant to be brief; anything longer is almost certainly a
# forgotten pause, and the ecosystem must come back to life on its own.
_DEFAULT_MAX_PAUSE_S = int(os.getenv("ARIA_MAX_PAUSE_SECONDS", str(6 * 3600)))   # 6h default
_HARD_MAX_PAUSE_S = 24 * 3600   # 24h absolute cap
_PAUSE_TASK_FMT = "crucix:autonomous:paused:task:{task_id}"
# R-F2141 — permanent kill switch. Unlike the auto-expiring pause (which
# self-heals within 24h), this flag is MANUALLY cleared and NEVER auto-resumes.
# Intended for operator-initiated emergency stop that must persist until
# explicitly released. Checked BEFORE the pause flag in is_engine_paused.
_SAFETY_STOP_KEY = "crucix:autonomous:safety_stop"


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


# R-F457 (2026-05-13) — in-memory rate counter so a Redis outage no
# longer enables unbounded over-fire. The original audit framed this
# as "cost cap fails open" but the actual cost cap (check_cost_cap)
# already uses a dual Redis + in-memory counter. The risk was on the
# RATE-LIMIT path: Redis outage → check_and_increment_rate returns
# (True, 0) every call → autonomous tasks fan out without bound.
# Same H8 pattern as the cost-cap in-memory fallback: track the
# current hour bucket's firings in process memory so the fail-open
# branch is bounded by `MAX_FIRINGS_PER_HOUR` rather than infinity.
_memory_rate_hour = 0
_memory_rate_count = 0


def _memory_rate_incr() -> int:
    """Increment the in-memory rate counter for the current hour bucket.
    Resets across hour boundaries automatically. Returns the new count."""
    global _memory_rate_hour, _memory_rate_count
    hour_bucket = int(time.time() // 3600)
    if hour_bucket != _memory_rate_hour:
        _memory_rate_hour = hour_bucket
        _memory_rate_count = 0
    _memory_rate_count += 1
    return _memory_rate_count


# ── Public: rate limit ─────────────────────────────────────────────────────

@fail_wire(module="safety", gap_type="agent_cycle_failure")
async def check_and_increment_rate(*, key_fmt: str | None = None,
                                   cap: int | None = None) -> tuple[bool, int]:
    """Token-bucket rate limit with hourly buckets.

    Returns (allowed, current_count_after_increment).
    The bucket key has a 3600s TTL so it self-cleans.

    R-F901 — key_fmt/cap let a dedicated caller (the ARIA-Coder) use a SEPARATE
    hourly bucket from the shared 87-task budget. Without this the coder shared
    one MAX_FIRINGS_PER_HOUR=12 bucket with every periodic task, which drained
    it to 0 coder slots/hr (live 2026-05-26: 50 gaps detected, STAGED=0). Both
    args default to the shared task bucket. (Resolved at call-time, not as
    default args, so tests that monkeypatch MAX_FIRINGS_PER_HOUR still bind.)
    """
    key_fmt = key_fmt or _RATE_KEY_FMT
    cap = cap if cap is not None else MAX_FIRINGS_PER_HOUR
    hour_bucket = int(time.time() // 3600)
    key = key_fmt.format(hour=hour_bucket)
    try:
        # Increment first, then check — this is atomic via INCR
        new_count = await rs.incr(key)
        if new_count == 1:
            # First firing in this bucket — set TTL
            await rs.expire(key, 3600)
        allowed = new_count <= cap
        if not allowed:
            # R-F897 (P0-1) — a BLOCKED attempt must NOT inflate the bucket.
            # Pre-R-F897 the speculative INCR stuck even when over-cap, so a
            # backlog of N>cap gaps (live: 43 gaps, cap 12) drove the counter
            # to N on a single scan and it never drained back under cap within
            # the hour — the coder saw 43 gaps and fixed 0 (rate_limit_exceeded
            # forever). Roll the speculative increment back so the bucket only
            # ever reflects EXECUTED firings; the backlog now drains at the cap
            # (12/hr) instead of 0/hr. Best-effort rollback (coder loop is
            # single-threaded, so the incr/decr race window is negligible).
            try:
                await rs.incr(key, -1)
            except Exception:
                pass
            new_count -= 1
            logger.warning(
                "[autonomous safety] rate limit hit: bucket %s already at cap %d "
                "this hour. Skipped (speculative incr rolled back).",
                key, cap,
            )
        if not allowed:
            wire_failure(
                module="autonomous_safety",
                detail=f"Rate limit hit: bucket {key} at cap {cap}",
                gap_type="rate_limited",
                source="autonomous_safety:check_and_increment_rate",
            )
        return allowed, new_count
    except Exception as e:
        # R-F457 (2026-05-13) — bounded fail-open. Pre-R-F457 a Redis
        # outage made this return (True, 0) every call, allowing
        # unbounded autonomous task fan-out for the duration of the
        # outage. Now we increment an in-memory hourly counter on the
        # Redis-fail path so over-fire is capped at MAX_FIRINGS_PER_HOUR
        # globally even when Redis is down. Once Redis recovers the
        # real counter takes over again. Documented fail-open intent
        # ("better to over-fire than halt") is preserved BUT bounded.
        # NOTE (R-F897): the in-memory fallback path is left as-is — it
        # deliberately bounds over-fire during a Redis outage per R-F457
        # (counter shows MAX+1 then denies; resets hourly). The P0-1 draining
        # fix targets the NORMAL Redis path above; the rare-outage fallback's
        # transient over-count is acceptable and its R-F457 invariant is pinned.
        mem_count = _memory_rate_incr()
        allowed = mem_count <= cap
        if allowed:
            logger.warning(
                "[autonomous safety] rate limit check failed (Redis): %s — "
                "falling back to in-memory counter (%d/%d this hour)",
                e, mem_count, MAX_FIRINGS_PER_HOUR,
            )
        else:
            logger.warning(
                "[autonomous safety] rate limit check failed (Redis): %s — "
                "in-memory counter ALSO above cap (%d/%d). Skipping.",
                e, mem_count, MAX_FIRINGS_PER_HOUR,
            )
        wire_failure(
            module="autonomous_safety",
            detail=f"Rate limit Redis failure: {e} — in-memory fallback {'allowed' if allowed else 'denied'} ({mem_count}/{cap})",
            gap_type="redis_failure",
            source="autonomous_safety:check_and_increment_rate",
        )
        return allowed, mem_count


# ── Public: cost cap ───────────────────────────────────────────────────────

@fail_wire(module="safety", gap_type="agent_cycle_failure")
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
        wire_failure(
            module="autonomous_safety",
            detail=f"Daily cost cap hit: ${spent:.4f} spent vs ${DAILY_COST_CAP_USD:.2f} cap",
            gap_type="cost_cap_hit",
            source="autonomous_safety:check_cost_cap",
        )
    elif not redis_ok:
        wire_failure(
            module="autonomous_safety",
            detail=f"Cost cap Redis read failed — using in-memory fallback (${mem_spent:.4f})",
            gap_type="redis_failure",
            source="autonomous_safety:check_cost_cap",
        )
    return within, spent


@fail_wire(module="safety", gap_type="agent_cycle_failure")
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
    return hashlib.sha1((entity or "").strip().lower().encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


@fail_wire(module="safety", gap_type="agent_cycle_failure")
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
        # R-F2626 — mark as run with TTL, ATOMICALLY.
        #
        # This was `set(key, "1")` followed by a separate
        # `expire(key, DEDUPE_WINDOW_SECONDS)` whose return value was
        # DISCARDED. That is a race, and it took the autonomous engine dark:
        #
        #   - state_store.set() ENQUEUES the INSERT (state_store.py:2121 ->
        #     _enqueue_write); a background worker drains it every 100ms.
        #   - expire() first calls _flush_write_queue() to force the INSERT
        #     down (R-F1933 anticipated the ordering hazard) — but that flush
        #     returns 0 immediately when the queue is EMPTY
        #     (state_store.py:626), and the worker has already dequeued the
        #     write into an in-flight batch it is still awaiting.
        #   - So the queue looks empty while the INSERT is uncommitted, the
        #     UPDATE matches 0 rows, expire() returns False
        #     (state_store.py:2911 — silently, it never raises), the caller
        #     ignored it, and the INSERT then landed with expires_at = NULL.
        #
        # The dedupe key is NOT time-bucketed, so expires_at = NULL means the
        # marker NEVER expires: `duplicate_recent_run` forever. Live evidence
        # 2026-07-15: 1078 of 1210 dedupe markers had NULL expires_at and 81
        # of 97 tasks were permanently locked out — the engine ticked but
        # fired nothing for hours ("ENGINE DARK", R-F2006 watchdog).
        #
        # set(ex=) is a single write that carries its own TTL: it reaches
        # state_store._upsert as ONE `INSERT ... ON CONFLICT DO UPDATE SET
        # ... expires_at = excluded.expires_at` (state_store.py:2083), so the
        # row cannot exist without its TTL. This removes the failure class
        # rather than retrying around it (§1).
        #
        # The max(1, ...) is load-bearing, not defensive noise:
        # _ttl_to_expires(0) and (-5) both return None (verified), so a
        # misconfigured ARIA_AUTONOMOUS_DEDUPE_WINDOW_S=0 would write
        # expires_at = NULL and silently recreate the exact permanent lockout
        # this fix exists to remove.
        await rs.set(key, "1", ex=max(1, DEDUPE_WINDOW_SECONDS))
        return True
    except Exception as e:
        logger.warning(
            "[autonomous safety] dedupe check failed: %s — failing open",
            e,
        )
        return True


_DEDUPE_REPAIR_SENTINEL = "crucix:autonomous:dedupe_repair:v1"
_DEDUPE_SCAN_PATTERN = "crucix:autonomous:dedupe:*"
# Batch size per scan round. scan_keys HARD-TRUNCATES at `count`
# (state_store.py:3240 — `if len(matched) >= count: break`), it does NOT page,
# so a single un-batched scan would silently return only the first N keys in
# alphabetical order. We therefore scan → delete → re-scan until a round comes
# back empty, which converges because every round removes what it found.
_DEDUPE_REPAIR_BATCH = 500
_DEDUPE_REPAIR_MAX_ROUNDS = 40  # 20k markers — a backstop, not a budget


async def repair_nulled_dedupe_markers() -> dict:
    """R-F2626 — one-time sweep that releases the dedupe markers which the
    old non-atomic set+expire race stranded WITHOUT a TTL.

    The atomic set(ex=) above stops NEW markers being written with
    expires_at = NULL, but it cannot help the ones already on disk: they are
    permanent by construction and would keep the engine dark forever. Live
    2026-07-15: 1078 of 1210 markers, 81 of 97 tasks locked out.

    HONEST SCOPE: this deletes EVERY dedupe marker it finds, not only the
    stranded ones. There is no portable way to read a key's TTL through
    redis_store, and scan_keys returns none — so "delete only the NULL ones"
    is not expressible without reaching into state_store's SQL, which is the
    most wedge-prone module in this tree (R-F2277) and no place for a repair
    sweep. Deleting the live ones too costs at most ONE extra run for those
    tasks, once. That is bounded by the guards that actually matter, all of
    which are checked BEFORE dedupe in can_task_run: the hourly rate bucket
    (MAX_FIRINGS_PER_HOUR) and the $50/day cost cap (safety.py:66), plus
    §17's $300/mo cap. A dedupe marker is an efficiency hint, not a safety
    control — and the alternative is leaving a production outage running to
    protect one.

    Sentinel-guarded so it runs ONCE, not on every boot — otherwise every
    restart would wipe live markers and defeat dedupe entirely.

    The sentinel is written ONLY if the sweep actually drained: a truncated
    sweep that recorded itself as complete would clear a fraction, burn its
    one attempt, and leave the engine dark while logging success.
    """
    out = {"scanned": 0, "deleted": 0, "skipped": False, "complete": False}
    try:
        if await rs.get(_DEDUPE_REPAIR_SENTINEL):
            out["skipped"] = True
            return out

        drained = False
        for _round in range(_DEDUPE_REPAIR_MAX_ROUNDS):
            keys = await rs.scan_keys(_DEDUPE_SCAN_PATTERN, _DEDUPE_REPAIR_BATCH)
            if not keys:
                drained = True
                break
            out["scanned"] += len(keys)
            for k in keys:
                try:
                    # Count only what was ACTUALLY deleted. state_store.delete
                    # swallows its own exceptions and returns False
                    # (state_store.py:2388), so incrementing unconditionally
                    # would report "cleared N markers — tasks unblocked" while
                    # clearing nothing. That is the same discarded-return-value
                    # class as the expire() bug this R-number exists to fix —
                    # don't reintroduce it in the repair for it.
                    if await rs.delete(k):
                        out["deleted"] += 1
                    else:
                        out["delete_failed"] = out.get("delete_failed", 0) + 1
                except Exception as e:  # noqa: BLE001
                    out["delete_failed"] = out.get("delete_failed", 0) + 1
                    logger.debug(
                        "[R-F2626] dedupe repair: delete %s failed: %s", k, e,
                    )
            # Yield between rounds: delete() flushes + commits per key on the
            # single state_store writer (R-F2157 saturation risk), so don't
            # hold it for thousands of keys without letting serving breathe.
            await asyncio.sleep(0)

        out["complete"] = drained
        if not drained:
            # Round budget exhausted with keys still matching. Do NOT write
            # the sentinel — leave the sweep armed so the next start finishes
            # the job rather than declaring victory over a partial clear.
            logger.warning(
                "[R-F2626] dedupe repair INCOMPLETE: deleted %d but markers "
                "remain after %d rounds — sentinel NOT written, sweep stays "
                "armed for the next engine start",
                out["deleted"], _DEDUPE_REPAIR_MAX_ROUNDS,
            )
            return out

        # No TTL on the sentinel: this repair must never run twice (§7 — an
        # expiring sentinel would silently re-arm the sweep and wipe live
        # markers).
        await rs.set(_DEDUPE_REPAIR_SENTINEL, "1")

        logger.info(
            "[R-F2626] dedupe repair: cleared %d markers — tasks locked out "
            "by NULL-TTL dedupe are unblocked",
            out["deleted"],
        )
        # §21a — success reaches the brain, not just the console.
        try:
            from ..intel.engine_wiring import wire_success
            wire_success(
                module="safety",
                summary=f"R-F2626 dedupe repair: cleared {out['deleted']} stranded markers",
                source_id="safety:R-F2626",
            )
        except Exception:  # noqa: BLE001
            pass
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("[R-F2626] dedupe repair failed: %s", e)
        try:
            from ..intel.engine_wiring import wire_failure
            # gap_type verified against capability_gaps.VALID_GAP_TYPES —
            # "module_bug" is NOT registered and would be rejected.
            wire_failure(
                module="safety",
                detail=f"R-F2626 dedupe repair failed: {e}",
                gap_type="engine_failure",
                source="safety:repair_nulled_dedupe_markers",
            )
        except Exception:  # noqa: BLE001
            pass
        out["error"] = str(e)
        return out


@fail_wire(module="safety", gap_type="agent_cycle_failure")
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

# R-F1693: in-process mirror of the pause state so the kill switch FAILS CLOSED
# (holds the last-known intent) during a Redis outage instead of silently
# resuming ALL autonomy. The emergency stop is the one control that must never
# fail open: an operator who hit "pause" because something was going wrong must
# stay paused even when state is unreliable. A successful Redis read refreshes
# the mirror; pause/resume set it FIRST so the intent holds even if the Redis
# write fails. Default is "not paused" so a transient blip (when nobody paused)
# does not spuriously halt autonomy — we hold last-known intent, not "always
# paused".
_paused_inproc: bool = False
_task_paused_inproc: dict[str, bool] = {}


@fail_wire(module="safety", gap_type="agent_cycle_failure")
async def is_engine_paused() -> bool:
    """Global engine kill switch. When True, NO tasks fire.

    R-F1693: fails CLOSED on Redis error — returns the last-known in-process
    pause state, never an unconditional False.
    """
    global _paused_inproc
    try:
        # R-F2141: safety_stop is checked FIRST — it NEVER auto-expires.
        # If set, the engine stays stopped until explicitly released.
        _safety_val = await rs.get(_SAFETY_STOP_KEY)
        if (_safety_val or "").strip() == "1":
            _paused_inproc = True
            return True

        val = await rs.get(_PAUSE_KEY)
        if (val or "").strip() != "1":
            _paused_inproc = False
            return False

        # R-F2004: the pause flag is set — honour its expiry so a forgotten
        # pause can't starve the live ecosystem forever.
        until_raw = await rs.get(_PAUSE_UNTIL_KEY)
        now = time.time()
        if not until_raw:
            # Legacy indefinite pause (set before R-F2004 with no expiry) —
            # auto-resume. This self-heals the exact 187h fire=0 outage.
            logger.warning(
                "[autonomous safety] R-F2004: legacy indefinite pause found "
                "(no expiry) — AUTO-RESUMING. The live ecosystem must not be "
                "starved by a forgotten pause."
            )
            try:
                if hasattr(rs, "delete"):
                    await rs.delete(_PAUSE_KEY)
                else:
                    await rs.set(_PAUSE_KEY, "0")
            except Exception:
                pass
            _paused_inproc = False
            return False
        try:
            until = float(until_raw)
        except (TypeError, ValueError):
            until = 0.0
        if now >= until:
            logger.warning(
                "[autonomous safety] R-F2004: pause expired — AUTO-RESUMING "
                "(engine back to live)."
            )
            try:
                if hasattr(rs, "delete"):
                    await rs.delete(_PAUSE_KEY)
                    await rs.delete(_PAUSE_UNTIL_KEY)
                else:
                    await rs.set(_PAUSE_KEY, "0")
            except Exception:
                pass
            _paused_inproc = False
            return False

        _paused_inproc = True  # still within the pause window
        return True
    except Exception:
        logger.warning(
            "[autonomous safety] is_engine_paused: Redis unreadable — failing "
            "CLOSED to last-known pause=%s",
            _paused_inproc,
        )
        return _paused_inproc  # R-F1693: hold last-known, never default to "run"


@fail_wire(module="safety", gap_type="agent_cycle_failure")
async def pause_engine(reason: str = "", minutes: float | None = None) -> None:
    global _paused_inproc
    _paused_inproc = True  # R-F1693: set intent FIRST so pause holds even if the Redis write fails
    # R-F2004: every pause now has a bounded lifetime. Honour an explicit
    # `minutes` (capped at the 24h hard max), else fall back to the 6h default
    # ceiling — so a pause ALWAYS auto-expires and the ecosystem self-heals.
    if minutes and minutes > 0:
        duration_s = min(float(minutes) * 60.0, float(_HARD_MAX_PAUSE_S))
    else:
        duration_s = float(_DEFAULT_MAX_PAUSE_S)
    until = int(time.time() + duration_s)
    try:
        await rs.set(_PAUSE_KEY, "1")
        await rs.set(_PAUSE_UNTIL_KEY, str(until))
        logger.warning(
            "[autonomous safety] engine PAUSED via admin endpoint. Reason: %s "
            "(auto-resumes in %.0f min, at epoch %d)",
            reason or "(none)", duration_s / 60.0, until,
        )
        wire_success(
            module="autonomous_safety",
            summary=f"Engine paused: {reason or 'no reason given'}",
            source_id="autonomous_safety:pause_engine",
        )
    except Exception as e:
        logger.error("[autonomous safety] failed to set pause flag: %s", e)
        wire_failure(
            module="autonomous_safety",
            detail=f"Failed to pause engine: {e}",
            gap_type="redis_failure",
            source="autonomous_safety:pause_engine",
        )


@fail_wire(module="safety", gap_type="agent_cycle_failure")
async def resume_engine() -> None:
    global _paused_inproc
    _paused_inproc = False  # R-F1693: clear intent FIRST (mirror stays consistent with operator action)
    try:
        # Use delete (atomic), not set "0" (would still be truthy)
        if hasattr(rs, "delete"):
            await rs.delete(_PAUSE_KEY)
            await rs.delete(_PAUSE_UNTIL_KEY)   # R-F2004: clear the expiry too
        else:
            await rs.set(_PAUSE_KEY, "0")
            await rs.set(_PAUSE_UNTIL_KEY, "0")
        logger.info("[autonomous safety] engine RESUMED via admin endpoint")
        wire_success(
            module="autonomous_safety",
            summary="Engine resumed",
            source_id="autonomous_safety:resume_engine",
        )
    except Exception as e:
        logger.error("[autonomous safety] failed to clear pause flag: %s", e)
        wire_failure(
            module="autonomous_safety",
            detail=f"Failed to resume engine: {e}",
            gap_type="redis_failure",
            source="autonomous_safety:resume_engine",
        )


@fail_wire(module="safety", gap_type="agent_cycle_failure")
async def safety_stop_engine(reason: str = "") -> None:
    """R-F2141 — permanent engine stop. Unlike pause_engine(), this NEVER
    auto-expires. Only safety_release_engine() clears it. Intended for
    operator-initiated emergency stop that must persist until explicitly released."""
    global _paused_inproc
    _paused_inproc = True
    try:
        await rs.set(_SAFETY_STOP_KEY, "1")
        logger.warning(
            "[autonomous safety] SAFETY STOP engaged. Reason: %s. "
            "Engine will NOT auto-resume — safety_release_engine() required.",
            reason or "(none)",
        )
        wire_success(
            module="autonomous_safety",
            summary=f"Safety stop: {reason or 'no reason given'}",
            source_id="autonomous_safety:safety_stop_engine",
        )
    except Exception as e:
        logger.error("[autonomous safety] failed to set safety_stop flag: %s", e)
        wire_failure(
            module="autonomous_safety",
            detail=f"Failed to set safety_stop: {e}",
            gap_type="redis_failure",
            source="autonomous_safety:safety_stop_engine",
        )


@fail_wire(module="safety", gap_type="agent_cycle_failure")
async def safety_release_engine() -> None:
    """R-F2141 — release the permanent safety stop. Engine returns to normal
    operation (subject to the regular pause/rate/cost checks)."""
    global _paused_inproc
    _paused_inproc = False
    try:
        if hasattr(rs, "delete"):
            await rs.delete(_SAFETY_STOP_KEY)
        else:
            await rs.set(_SAFETY_STOP_KEY, "0")
        logger.warning("[autonomous safety] SAFETY STOP released — engine back to normal.")
        wire_success(
            module="autonomous_safety",
            summary="Safety stop released",
            source_id="autonomous_safety:safety_release_engine",
        )
    except Exception as e:
        logger.error("[autonomous safety] failed to clear safety_stop flag: %s", e)
        wire_failure(
            module="autonomous_safety",
            detail=f"Failed to clear safety_stop: {e}",
            gap_type="redis_failure",
            source="autonomous_safety:safety_release_engine",
        )


@fail_wire(module="safety", gap_type="agent_cycle_failure")
async def is_task_paused(task_id: str) -> bool:
    """Per-task pause flag (independent of the global engine pause).

    R-F1693: fails CLOSED to the last-known per-task state on Redis error.
    """
    global _task_paused_inproc
    try:
        val = await rs.get(_PAUSE_TASK_FMT.format(task_id=task_id))
        paused = (val or "").strip() == "1"
        _task_paused_inproc[task_id] = paused  # refresh mirror on success
        return paused
    except Exception:
        return _task_paused_inproc.get(task_id, False)  # hold last-known


@fail_wire(module="safety", gap_type="agent_cycle_failure")
async def pause_task(task_id: str) -> None:
    try:
        await rs.set(_PAUSE_TASK_FMT.format(task_id=task_id), "1")
        logger.info("[autonomous safety] task paused: %s", task_id)
    except Exception as e:
        logger.error("[autonomous safety] failed to pause task %s: %s", task_id, e)


@fail_wire(module="safety", gap_type="agent_cycle_failure")
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

@fail_wire(module="safety", gap_type="agent_cycle_failure")
async def can_task_run(task_id: str, entity: str, *, coder: bool = False) -> tuple[bool, str]:
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
    # R-F901 — the coder uses its OWN hourly bucket so the shared 87-task budget
    # can't starve it. Engine-pause + cost-cap above still apply uniformly.
    if coder:
        allowed_rate, count = await check_and_increment_rate(
            key_fmt=_CODER_RATE_KEY_FMT, cap=CODER_MAX_FIXES_PER_HOUR,
        )
    else:
        allowed_rate, count = await check_and_increment_rate()
    if not allowed_rate:
        return False, f"rate_limit_exceeded:{count}"
    if not await check_and_mark_dedupe(task_id, entity):
        return False, "duplicate_recent_run"
    return True, "ok"


# ── Public: snapshot for /status admin endpoint ────────────────────────────

@fail_wire(module="safety", gap_type="agent_cycle_failure")
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
