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
# R-F2635 — TTL for SLOT-KEYED dedupe markers. A slot-keyed marker only has to
# outlive the window in which the SAME scheduled minute could be attempted
# twice: the tick, a catch-up for that slot, and any restart in between. It
# must therefore exceed engine._CATCH_UP_MAX_AGE_S (7200s / 2h) so a catch-up
# cannot re-fire a slot whose marker has already expired; 3h gives headroom.
# It must NOT be 23h — that flat window is what capped every task at one fire
# per day regardless of its cron (the bug this fixes). Uniqueness comes from
# the slot in the key, not from the length of the TTL.
DEDUPE_SLOT_WINDOW_SECONDS = _env_int("ARIA_AUTONOMOUS_DEDUPE_SLOT_WINDOW_S", 3 * 3600)

# R-F2635 — the invariant above is env-tunable on BOTH sides
# (ARIA_AUTONOMOUS_DEDUPE_SLOT_WINDOW_S here, ARIA_ENGINE_CATCHUP_MAX_AGE_S in
# engine.py), so a well-meaning tune can silently re-open a same-slot
# double-fire. A test pins it, but a test does not run in production — say it
# out loud at import, in the process that will actually suffer.
def _warn_if_slot_window_too_short() -> None:
    try:
        _lookback = float(os.getenv("ARIA_ENGINE_CATCHUP_MAX_AGE_S", "7200"))
    except (TypeError, ValueError):
        return
    if DEDUPE_SLOT_WINDOW_SECONDS <= _lookback:
        logger.warning(
            "[autonomous safety] R-F2635 INVARIANT BROKEN: dedupe slot window "
            "(%ss) <= catch-up lookback (%ss). A catch-up can re-fire a slot "
            "whose marker already expired => the SAME scheduled slot can fire "
            "twice. Raise ARIA_AUTONOMOUS_DEDUPE_SLOT_WINDOW_S above %ss.",
            DEDUPE_SLOT_WINDOW_SECONDS, _lookback, _lookback,
        )


_warn_if_slot_window_too_short()


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
    hour_bucket = current_hour_bucket()
    if hour_bucket != _memory_rate_hour:
        _memory_rate_hour = hour_bucket
        _memory_rate_count = 0
    _memory_rate_count += 1
    return _memory_rate_count


# ── Public: rate limit ─────────────────────────────────────────────────────

def current_hour_bucket() -> int:
    """The hour index every rate-bucket key is derived from.

    R-F3940 — ONE derivation. `int(time.time() // 3600)` was open-coded in five
    places, and the moment two of them ran at different TIMES they silently
    referred to different buckets. That is not a style point: it is exactly how
    R-F3919's refund came to credit a bucket it had never charged.
    """
    return int(time.time() // 3600)


def rate_bucket_key(*, coder: bool = False, key_fmt: str | None = None,
                    hour_bucket: int | None = None) -> str:
    """THE hourly rate-bucket key. Pass `hour_bucket` to name a SPECIFIC hour.

    R-F3940 — every caller that must refer to the bucket it already charged has
    to be able to NAME it, rather than re-deriving "now" and hoping the clock has
    not moved. `can_task_run`/`check_and_increment_rate` take the same
    `hour_bucket` for exactly that reason: charge and refund then provably address
    one bucket, with no window between them for the hour to tick.
    """
    fmt = key_fmt or (_CODER_RATE_KEY_FMT if coder else _RATE_KEY_FMT)
    hb = current_hour_bucket() if hour_bucket is None else hour_bucket
    return fmt.format(hour=hb)


def _wire_refund_outcome(*, landed: bool, detail: str) -> None:
    """Report a refund outcome to the brain WITHOUT being able to change it.

    R-F3940 — these are fire-and-forget signals and must never alter the result
    they describe. Called bare inside `release_rate_slot`'s try/except, a wiring
    error (a wrong signature, a sink outage) is caught by the broad handler and
    turns a refund that ACTUALLY LANDED into a reported failure — an instrument
    corrupting the measurement it exists to report, which is the class this whole
    module is written against. Caught here so it cannot.
    """
    try:
        if landed:
            wire_success(module="safety", summary="rate slot refunded", detail=detail)
        else:
            wire_failure(module="safety", detail=detail,
                         gap_type="agent_cycle_failure")
    except Exception as e:      # pragma: no cover - signalling is best-effort
        logger.debug("[R-F3940] refund signal failed: %s", e)


@fail_wire(module="safety", gap_type="agent_cycle_failure")
async def release_rate_slot(*, coder: bool = False,
                            bucket_key: str | None = None) -> bool:
    """R-F3919 — refund a slot an attempt consumed and then did NO WORK with.

    THE INVARIANT THIS RESTORES is stated in `can_task_run`'s own docstring:
    "rate limit is the LAST check that increments state". R-F897 enforced it for
    over-cap attempts ("the bucket only ever reflects EXECUTED firings") and
    R-F3823 enforced it for duplicates, moving the dedupe READ above the limiter
    because "an attempt that was about to be discarded had already consumed a fix
    slot".

    THE SAME INVARIANT IS BROKEN AGAIN, one level up, by a gate that cannot move
    above the limiter. `self_coder.fix_gap` acquires a slot and THEN runs the
    R-F1460 reproduce-symptom gate, which exists to discard gaps whose symptom
    cannot be reproduced — i.e. FALSE POSITIVES, work that never happened.

    Measured live 2026-08-12 over 15 monitoring cycles, cap=6/hour:
        7x  stage=reproducing_symptom
        4x  "not fixed: Reproduce-symptom gate"     <- 4 of 6 slots, on non-gaps
        10x "not fixed: Safety guardrail: rate_limit_exceeded:6"
    while gap_detector went 105 -> 110 -> 127 actionable gaps. The §21c P0
    verbatim: "it can see gaps but can't act".

    WHY REFUND RATHER THAN MOVE THE GATE UP (the R-F3823 remedy). The dedupe check
    is a cheap READ, so it could sit above the limiter. The reproduce gate RUNS A
    TEST — moving it above `can_task_run` would execute work before the engine-pause
    and cost-cap checks, which is exactly backwards. So the slot is taken first and
    given back when, and only when, the attempt turns out to be a no-op.

    DELIBERATELY NARROW. This is not a general "undo" for any failure: an attempt
    that reached the LLM, or failed validation, DID consume the budget it was
    metering and must keep its slot. Only a gap discarded as not-real refunds.

    Never raises, never drives the bucket below zero, and returns whether the
    refund actually landed so a caller can log the truth rather than assume it.

    R-F3940 — REFUND THE BUCKET THAT WAS CHARGED. This used to re-derive the key
    from `time.time()` AT REFUND TIME, but the slot was charged back at
    `can_task_run`, and the thing in between is the R-F1460 reproduce gate, which
    RUNS A TEST. Straddle an hour boundary and the refund credited the NEW hour:
    the charged hour stayed over-counted (the budget loss R-F3919 exists to stop)
    while the new hour was handed a slot nobody paid for — the same
    manufactured-budget failure the zero-guard below was written to prevent,
    arriving through the key instead of the count. Callers now pass the
    `bucket_key` that `check_and_increment_rate` returned.
    """
    key = bucket_key or rate_bucket_key(coder=coder)
    try:
        current = await rs.get(key)
        # A bucket that is absent or already at zero has nothing to refund —
        # decrementing it would manufacture budget, which is the opposite failure.
        if current is None or int(current) <= 0:
            # §21a — a refund that does NOT land means a slot was permanently
            # lost, which is the P0 R-F3919 was written to end. Say so; a silent
            # False here is how the original leak stayed invisible for so long.
            _wire_refund_outcome(
                landed=False,
                detail=(f"rate slot refund did not land for {key} — bucket "
                        f"absent or already zero; one slot is permanently lost"),
            )
            return False
        await rs.incr(key, -1)
        _wire_refund_outcome(landed=True, detail=f"rate slot refunded to {key}")
        return True
    except Exception as e:      # pragma: no cover - refund is best-effort
        logger.debug("[R-F3919] rate slot refund failed for %s: %s", key, e)
        return False


# R-F3928 — RESTORED. R-F3919 inserted `release_rate_slot` immediately above this
# function and, because the edit anchored on the `async def` line rather than the
# decorator above it, `check_and_increment_rate` SILENTLY LOST its @fail_wire to the
# new neighbour. Gate A caught it: "safety.py:254 public async function
# 'check_and_increment_rate()' has no @fail_wire and is not in HARD_EXEMPT".
#
# This is the exact defect §16 already records — "R-F3842: three wiring-gate failures
# caused by my own stolen-decorator defect" — reproduced verbatim, which is why the
# gate that catches it must never be muted or baselined. A decorator theft is
# invisible in review (both functions look decorated) and silently un-wires a path
# that was wired, so the module keeps reporting health it no longer measures.
#
# WHEN INSERTING A FUNCTION ABOVE ANOTHER, ANCHOR ON THE DECORATOR, NOT THE `def`.
@fail_wire(module="safety", gap_type="agent_cycle_failure")
async def remaining_fix_budget(*, coder: bool = True,
                               hour_bucket: int | None = None) -> int | None:
    """How many fix slots are left in this hour. A READ — never a charge.

    R-F3975 (C-64) — the coder claimed `MAX_GAPS_PER_CYCLE` (20) gaps every
    cycle and only discovered the `ARIA_CODER_MAX_FIXES_PER_HOUR` limit (live: 6)
    once it was already inside `fix_gap`. Every refused gap had already been
    `mark_attempted` and scoreboard-`claimed`, which is how the live board reads
    claimed 19,097 / blocked 19,129 / fixed 0. Knowing the budget BEFORE claiming
    lets the loop attempt only what it can finish — and since `actionable` is
    sorted by severity descending, the slots then go to the most severe gaps
    instead of to whatever arrived first.

    Reads the SAME bucket `check_and_increment_rate` charges, via the same
    `rate_bucket_key`, so the two can never address different keys and make the
    budget a fiction.

    Returns None for "could not measure", which is NOT zero: an unreadable store
    must not silently stop the autonomous loop. §21c calls a loop that can see
    gaps but cannot act a P0, so this fails OPEN and leaves `fix_gap`'s own
    limiter as the authority — exactly as before this function existed.
    """
    cap = CODER_MAX_FIXES_PER_HOUR if coder else MAX_FIRINGS_PER_HOUR
    key = rate_bucket_key(coder=coder, hour_bucket=hour_bucket)
    try:
        raw = await rs.get(key)
    except Exception as e:      # pragma: no cover - store outage
        logger.debug("[R-F3975] budget read failed (%s) — unknown, not zero", e)
        return None
    try:
        spent = int(raw or 0)
    except (TypeError, ValueError):
        return None
    return max(0, cap - spent)


@fail_wire(module="safety", gap_type="agent_cycle_failure")
async def check_and_increment_rate(*, key_fmt: str | None = None,
                                   cap: int | None = None,
                                   hour_bucket: int | None = None) -> tuple[bool, int]:
    """Token-bucket rate limit with hourly buckets.

    Returns (allowed, current_count_after_increment).
    The bucket key has a 3600s TTL so it self-cleans.

    R-F3940 — `hour_bucket` lets a caller that may LATER refund name the bucket
    it is charging, instead of both sides independently asking what time it is.
    Defaults to now, so every existing caller is unchanged.

    R-F901 — key_fmt/cap let a dedicated caller (the ARIA-Coder) use a SEPARATE
    hourly bucket from the shared 87-task budget. Without this the coder shared
    one MAX_FIRINGS_PER_HOUR=12 bucket with every periodic task, which drained
    it to 0 coder slots/hr (live 2026-05-26: 50 gaps detected, STAGED=0). Both
    args default to the shared task bucket. (Resolved at call-time, not as
    default args, so tests that monkeypatch MAX_FIRINGS_PER_HOUR still bind.)
    """
    key_fmt = key_fmt or _RATE_KEY_FMT
    cap = cap if cap is not None else MAX_FIRINGS_PER_HOUR
    if hour_bucket is None:
        hour_bucket = current_hour_bucket()
    key = rate_bucket_key(key_fmt=key_fmt, hour_bucket=hour_bucket)
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
async def is_recent_duplicate(
    task_id: str, entity: str, *, slot: int | None = None,
) -> bool:
    """READ-ONLY: would `check_and_mark_dedupe` reject this as a recent duplicate?

    R-F3823 — exists so `can_task_run` can refuse a duplicate WITHOUT first spending
    a rate-bucket slot on it. It writes nothing; marking stays
    `check_and_mark_dedupe`'s job, and must happen only once the slot is secured.

    Keying is identical to `check_and_mark_dedupe` by construction (same
    `_dedupe_key`), because two dedupe key formats that drifted apart would be worse
    than the bug this fixes — the pre-check would clear work the marker then blocks.
    """
    if not task_id:
        return False
    key, _window = _dedupe_key(task_id, entity, slot)
    try:
        return bool(await rs.get(key))
    except Exception as e:      # pragma: no cover - a store blip must not block work
        logger.debug("[autonomous safety] dedupe pre-check failed for %s: %s",
                     task_id, e)
        return False


def _dedupe_key(task_id: str, entity: str, slot: int | None) -> tuple[str, int]:
    """The dedupe key + TTL for a task+entity(+slot). ONE definition, two callers."""
    key = _DEDUPE_KEY_FMT.format(
        task_id=task_id, entity_hash=_entity_hash(entity),
    )
    window = DEDUPE_WINDOW_SECONDS
    if slot is not None:
        key = f"{key}:{slot}"
        window = DEDUPE_SLOT_WINDOW_SECONDS
    return key, window


@fail_wire(module="safety", gap_type="agent_cycle_failure")
async def check_and_mark_dedupe(
    task_id: str, entity: str, *, slot: int | None = None,
) -> bool:
    """Return True if this task+entity is allowed to run, False if it
    duplicates a recent run.

    `slot` (R-F2635) — the SCHEDULED CRON SLOT this fire belongs to, as a UTC
    minute (`epoch // 60`). Pass it for anything cron-driven; omit it for
    work that has no schedule (the coder's per-gap attempts, manual runs).

    WHY THE SLOT EXISTS. Without it the marker is keyed on task+entity alone
    and held for a FLAT DEDUPE_WINDOW_SECONDS (23h), which has nothing to do
    with the task's cadence — so EVERY task fired at most once per 23h no
    matter what its cron said. Live 2026-07-15: `DRAIN-COLLAB-BRIDGE`
    (cron `*/2` = 720 slots/day) got ONE fire/day and logged
    `blocked: duplicate_recent_run` for the other 719; across tasks.yaml the
    ~964 cron-implied fires/24h collapsed to a ~59/day ceiling. The docstring
    here used to say "so a DAILY task can re-fire the next day" — that
    assumption silently became false as sub-hourly crons were added.
    Bucketing the key to the slot ties dedupe to the SCHEDULE, which is what
    it was always meant to express: "don't run this task twice for the same
    scheduled moment" — not "once a day, whatever you asked for".
    (Per §1 this is the root fix; lowering the 23h constant is the band-aid —
    the failure class is "window unrelated to schedule".)

    It also retires a latent bug rather than patching it: tasks.py:1790 calls
    `clear_dedupe(task.id, "")` after a failed run so the slot isn't burned,
    but that hashes entity="" while the marker was written with
    `entity or task_id` — the keys can NEVER match (verified), so it has
    never worked. With slot-keyed markers a failed run cannot burn a future
    slot at all: the next slot is a different key.
    """
    if not task_id:
        return True
    # R-F3823 — one key definition, shared with `is_recent_duplicate`. Two formats
    # that drifted apart would be worse than the bug this fixes: the pre-check would
    # clear work that the marker then blocks.
    #   Same task+entity, same scheduled minute => same key => deduped.
    #   Next scheduled minute => different key => allowed.
    key, window = _dedupe_key(task_id, entity, slot)
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
        await rs.set(key, "1", ex=max(1, window))
        return True
    except Exception as e:
        logger.warning(
            "[autonomous safety] dedupe check failed: %s — failing open",
            e,
        )
        return True


_DEDUPE_SCAN_PATTERN = "crucix:autonomous:dedupe:*"
_DEDUPE_REPAIR_BATCH = 500
_DEDUPE_REPAIR_MAX_ROUNDS = 40  # 20k markers/pass — a backstop, not a budget

# R-F2629 retired _DEDUPE_REPAIR_SENTINEL ("crucix:autonomous:dedupe_repair:v1").
# Any stale copy of that key in prod is now an inert orphan — do NOT reintroduce
# a sentinel here; see the docstring below for why it was the defect.


@fail_wire(module="safety", gap_type="agent_cycle_failure")
async def repair_nulled_dedupe_markers() -> dict:
    """R-F2626/R-F2629 — release dedupe markers stranded WITHOUT a TTL by the
    old non-atomic set+expire race.

    The atomic set(ex=) above stops NEW markers being written with
    expires_at = NULL, but cannot help the ones already on disk: they are
    permanent by construction and keep the engine dark forever. Live
    2026-07-15: 1078 of 1210 markers, 81 of 97 tasks locked out.

    R-F2629 — WHY THIS IS NO LONGER ONE-SHOT, AND NO LONGER SENTINEL-GUARDED.

    R-F2626's sweep deleted EVERY marker under the prefix (it could not see
    TTLs), so it HAD to be one-shot — otherwise each restart would wipe live
    markers and defeat dedupe. That one-shot guard is what killed it: it
    declared itself complete when a scan returned [], and
    `state_store.scan_keys` returns [] on FAILURE too (no read conn, or any
    exception → `logger.warning("SCAN failed"); return []`). Under the
    state_store saturation we were independently observing, a scan failed,
    the sweep read [] as "drained", wrote its sentinel, and burned its only
    attempt. Live result: sentinel set, 926 NULL markers still present, the
    engine still dark — while the code logged success.

    That is the exact failure this R-number family exists to delete: absence
    of evidence read as evidence of absence (cf. R-F2622, and the discarded
    expire() return that caused R-F2626 itself).

    The fix is to remove the fragility rather than guard it better. The sweep
    now targets ONLY rows with expires_at IS NULL (rs.scan_keys_null_ttl),
    which makes it PRECISE — a live, correctly-TTL'd marker can never be
    touched. Precise makes it IDEMPOTENT, and idempotent makes the sentinel
    unnecessary: it runs on every engine start, and a failed scan simply
    means "nothing done this pass, retry next start" instead of a permanent
    false completion. There is no longer a one-shot to burn.

    After the atomic set(ex=), no NEW NULL-TTL marker can be created, so
    anything this finds is by definition broken and safe to drop. Cost: at
    most one extra run for an affected task, once — bounded by the guards
    checked BEFORE dedupe in can_task_run (hourly rate bucket, $50/day cap,
    §17's $300/mo cap). A dedupe marker is an efficiency hint, not a safety
    control.
    """
    out = {"scanned": 0, "deleted": 0, "delete_failed": 0, "rounds": 0}
    try:
        for _round in range(_DEDUPE_REPAIR_MAX_ROUNDS):
            keys = await rs.scan_keys_null_ttl(
                _DEDUPE_SCAN_PATTERN, _DEDUPE_REPAIR_BATCH,
            )
            if not keys:
                # [] means "nothing found THIS pass" — which may be a clean
                # keyspace OR a failed read. We deliberately do NOT decide
                # which: the sweep is idempotent, so the next engine start
                # retries. Never record completion from this signal.
                break
            out["rounds"] += 1
            out["scanned"] += len(keys)
            for k in keys:
                try:
                    # Count only real deletions — rs.delete swallows failures
                    # and returns False (state_store.py delete()).
                    if await rs.delete(k):
                        out["deleted"] += 1
                    else:
                        out["delete_failed"] += 1
                except Exception as e:  # noqa: BLE001
                    out["delete_failed"] += 1
                    logger.debug(
                        "[R-F2629] dedupe repair: delete %s failed: %s", k, e,
                    )
            # Yield between rounds: delete() flushes + commits per key on the
            # single state_store writer (R-F2157 saturation risk).
            await asyncio.sleep(0)

        if out["deleted"]:
            logger.info(
                "[R-F2629] dedupe repair: released %d TTL-less markers "
                "(%d delete-failures) — tasks locked out by the R-F2626 race "
                "are unblocked", out["deleted"], out["delete_failed"],
            )
            try:
                from ..intel.engine_wiring import wire_success
                wire_success(
                    module="safety",
                    summary=f"R-F2629 dedupe repair released {out['deleted']} TTL-less markers",
                    source_id="safety:R-F2629",
                )
            except Exception:  # noqa: BLE001
                pass
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("[R-F2629] dedupe repair failed: %s", e)
        try:
            from ..intel.engine_wiring import wire_failure
            # gap_type verified against capability_gaps.VALID_GAP_TYPES.
            wire_failure(
                module="safety",
                detail=f"R-F2629 dedupe repair failed: {e}",
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


# ── R-F3638 — the coder-lane switch, resolved in ONE place ────────────────
# `ARIA_CODER_ENABLED` is the operator's consent for the AUTONOMOUS coder lane.
# It does NOT stop the coder LOOP: coder_entrypoint.start_aria_coder() has had
# no env gate since R-F996, so run_forever() and the 30s heartbeat ticker run
# whenever ARIA_INTERNAL_TOKEN is set. R-F3064 put the real gate at the one
# chokepoint every fix funnels through — self_coder.fix_gap.
#
# Two readers must never disagree about this value: the GATE (fix_gap, which
# refuses the work) and the PROBE (self_introspect_guard, which tells ARIA what
# to say about herself). Before R-F3638 the probe could not see the gate at all
# — it read heartbeat freshness only, so a paused lane rendered as
# "running: True ... it detects gaps, plans fixes, writes code, and stages
# improvements", and ARIA reported active self-improvement in an operator
# briefing while every gap was being refused `coder_disabled`. Both callers now
# resolve the switch HERE so the probe cannot drift from the gate again.
CODER_LANE_VAR = "ARIA_CODER_ENABLED"
_CODER_LANE_TRUTHY = ("1", "true", "yes")


# R-F3767 — §21a. A BOOL gate on the coder lane: a failure reads as "the lane is
# disabled", which is safe but SILENT — the autonomous coder would simply stop
# doing anything with nothing explaining why (the R-F897 "sees gaps but cannot
# act" P0). Failing closed is right; failing closed quietly is not.
@fail_wire(module="safety", gap_type="agent_cycle_failure")
def is_coder_lane_enabled() -> bool:
    """True when the operator has consented to the autonomous coder lane.

    Sync and env-only by design: the introspection probe calls this on a chat
    turn and must not await a store read. The truthy set is R-F3064's gate
    verbatim — change it HERE, never at a call site.
    """
    return (os.getenv(CODER_LANE_VAR, "") or "").strip().lower() in _CODER_LANE_TRUTHY


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
async def can_task_run(
    task_id: str, entity: str, *, coder: bool = False, slot: int | None = None,
    hour_bucket: int | None = None,
) -> tuple[bool, str]:
    """Run all five guardrails. Returns (allowed, reason_if_blocked).

    R-F3940 — pass `hour_bucket` (from `current_hour_bucket()`) when the caller
    may later refund the slot, so the charge and the refund name the SAME bucket
    even if the hour ticks over during the work in between.

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
    # R-F3823 — REFUSE A DUPLICATE BEFORE SPENDING A SLOT ON IT.
    #
    # This check used to live below the rate limiter, so an attempt that was about to
    # be discarded as a duplicate had already consumed a fix slot. The docstring above
    # states the invariant it broke — "rate limit is the LAST check that increments
    # state" — dedupe was simply on the wrong side of it.
    #
    # Survivable at the 500/hour default, fatal at the live one. Measured 2026-08-09:
    # ARIA_CODER_MAX_FIXES_PER_HOUR=6, dedupe window 23h, coder re-attempting the same
    # gaps every 15-minute scan. Four no-op duplicates ate four of six slots, the rest
    # hit `rate_limit_exceeded:6`, and the loop fixed ZERO of 96-100 detected gaps —
    # the §21c P0 ("sees gaps but cannot act").
    #
    # This is a READ. The authoritative check-and-MARK stays below, after the slot is
    # secured, because marking here would lock out a task the rate limiter then
    # refuses — 23h of `duplicate_recent_run` for work that never ran.
    if await is_recent_duplicate(task_id, entity, slot=slot):
        return False, "duplicate_recent_run"
    # R-F901 — the coder uses its OWN hourly bucket so the shared 87-task budget
    # can't starve it. Engine-pause + cost-cap above still apply uniformly.
    if coder:
        allowed_rate, count = await check_and_increment_rate(
            key_fmt=_CODER_RATE_KEY_FMT, cap=CODER_MAX_FIXES_PER_HOUR,
            hour_bucket=hour_bucket,
        )
    else:
        allowed_rate, count = await check_and_increment_rate(hour_bucket=hour_bucket)
    if not allowed_rate:
        return False, f"rate_limit_exceeded:{count}"
    # R-F2635 — `slot` ties dedupe to the SCHEDULE. Cron-driven callers pass
    # the scheduled UTC minute; unscheduled work (coder gaps, manual runs)
    # omits it and keeps the flat 23h window.
    if not await check_and_mark_dedupe(task_id, entity, slot=slot):
        return False, "duplicate_recent_run"
    return True, "ok"


# ── Public: snapshot for /status admin endpoint ────────────────────────────

@fail_wire(module="safety", gap_type="agent_cycle_failure")
async def get_safety_state() -> dict[str, Any]:
    """One-shot view of every safety counter for the admin /status endpoint."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    hour_bucket = current_hour_bucket()
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
        rate_count = await rs.get(rate_bucket_key(hour_bucket=hour_bucket))
        out["current_hour_firings"] = int(rate_count) if rate_count else 0
    except Exception as e:
        out["current_hour_firings_error"] = str(e)[:200]
    try:
        spent = await rs.get(_COST_KEY_FMT.format(date=today))
        out["daily_spent_usd"] = float(spent) if spent else 0.0
    except Exception as e:
        out["daily_spent_usd_error"] = str(e)[:200]
    return out
