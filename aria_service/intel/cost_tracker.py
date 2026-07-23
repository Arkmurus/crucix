"""
ARIA Cost Tracker — token + USD attribution per LLM call, per feature.

Why this exists
═══════════════
ARIA fires a lot of LLM calls from many surfaces:
  - chat replies (one per WhatsApp mention)
  - research_tasks (5-20 per multi-entity batch)
  - autonomous research cycle (every 30 min)
  - self-improve cycle (every 2h)
  - student quiz / reading (every 3h)
  - proactive anomaly checks
  - hypothesis validation

You currently have NO idea what each of these costs in tokens or USD,
or which one is the bloated outlier wasting budget. Token cost is also
the cleanest proxy for "is this prompt bloated" — if a feature is using
8k tokens to answer a question that needs 800, the prompt is probably
the problem.

How it works
════════════
A wrapper provider (llm/metered.py) intercepts every llm.complete() call,
reads input_tokens + output_tokens off the LLMResult, looks up the
per-1M-token price for the model, and writes a record here. The record
is attributed to whichever "feature" the caller set on the contextvar
before invoking the LLM. Async-safe because contextvars propagate
through asyncio tasks.

Attribution pattern
═══════════════════
    from ..intel import cost_tracker

    with cost_tracker.feature("research_task"):
        result = await llm.complete(...)

If no feature is set, calls are bucketed as "uncategorized" so we
always know how much un-attributed traffic we have (and where to add
instrumentation next).

Pricing
═══════
Hardcoded per known model. Uses cache-miss / standard rates — these
are upper bounds. Unknown models default to a conservative deepseek
estimate so we never silently over-report cost. Edit PRICING when
provider rates change; runtime prices change ~quarterly.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from . import redis_store as rs
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.cost")


class MonthlyCostCapExceeded(RuntimeError):
    """Raised when a metered LLM call would exceed the monthly USD cap."""

    def __init__(self, spent: float, cap: float, month: str):
        self.spent = spent
        self.cap = cap
        self.month = month
        super().__init__(
            f"Monthly LLM cost cap ${cap:.2f} exceeded for {month} "
            f"(spent ${spent:.4f}). Raise ARIA_MONTHLY_CAP_USD or set "
            f"ARIA_MONTHLY_CAP_WARN_ONLY=1 to continue."
        )


class DailyCostCapExceeded(RuntimeError):
    """R-F2888 — raised when a metered LLM call would exceed the DAILY USD cap.

    A RuntimeError subclass for the same reason MonthlyCostCapExceeded is one:
    chat/stream endpoints already surface RuntimeError from the cap path with a
    useful message, so the daily ceiling needs no new handling at the edges.
    """

    def __init__(self, spent: float, cap: float, day: str):
        self.spent = spent
        self.cap = cap
        self.day = day
        super().__init__(
            f"Daily LLM cost cap ${cap:.2f} reached for {day} "
            f"(spent ${spent:.4f}). Raise ARIA_DAILY_CAP_USD or set "
            f"ARIA_DAILY_CAP_WARN_ONLY=1 to continue."
        )

# ── Pricing table (USD per 1M tokens) ──────────────────────────────────────
# Standard rates as of late 2025 — update when providers change them.
# Tuple is (input_per_1m, output_per_1m).
PRICING: dict[str, tuple[float, float]] = {
    # DeepSeek — primary for ARIA, dirt cheap
    "deepseek-chat":      (0.27, 1.10),
    "deepseek-reasoner":  (0.55, 2.19),
    "deepseek-v3":        (0.27, 1.10),

    # Anthropic — R-F2759: current-generation rows added ahead of the DeepSeek->Claude
    # switch. WITHOUT these, claude-opus-4-8 / claude-sonnet-5 fall through _get_price to
    # DEFAULT_PRICING (DeepSeek rates), so Claude spend would be under-counted ~5-25x AND
    # the $300/mo cap (assert_monthly_cap reads these same numbers) would go blind. Prices
    # are the standard published per-1M rates (input, output). Sonnet 5 has an intro rate
    # of $2/$10 through 2026-08-31 — deliberately priced here at the STANDARD $3/$15 so the
    # cap OVER-estimates during the intro window (over-estimate is safe; under-estimate is
    # the failure this module exists to prevent — see DEFAULT_PRICING note below).
    "claude-opus-4-8":    (5.00, 25.00),
    "claude-opus-4-7":    (5.00, 25.00),
    "claude-sonnet-5":    (3.00, 15.00),
    "claude-sonnet-4-6":  (3.00, 15.00),
    "claude-opus-4-6":    (15.00, 75.00),
    "claude-haiku-4-5":   (1.00, 5.00),
    "claude-3-5-sonnet":  (3.00, 15.00),

    # OpenAI
    "gpt-4o":             (2.50, 10.00),
    "gpt-4o-mini":        (0.15, 0.60),
    "gpt-4":              (30.00, 60.00),
    "gpt-4-turbo":        (10.00, 30.00),
    "gpt-3.5-turbo":      (0.50, 1.50),

    # Google
    "gemini-2.5-pro":     (1.25, 10.00),
    "gemini-2.5-flash":   (0.075, 0.30),
    "gemini-3.1-pro":     (1.25, 10.00),

    # Mistral
    "mistral-large-latest": (2.00, 6.00),
    "mistral-small-latest": (0.20, 0.60),

    # Ollama / local — zero (no API cost)
    "ollama":             (0.0, 0.0),
    "llama3.1:8b":        (0.0, 0.0),
    "llama3.1:70b":       (0.0, 0.0),
}

# Conservative default for unknown models. R-F2766: raised from deepseek-chat
# rates (0.27/1.10) to Claude Sonnet rates ahead of the DeepSeek->Claude switch.
# The primary provider is becoming Claude, so an unrecognised model ID is now far
# more likely to be a Claude variant than a DeepSeek one — defaulting to DeepSeek
# rates would SILENTLY UNDER-COUNT it (and the $300/mo cap reads these numbers, so
# it would go blind). Over-estimating a genuinely-cheap unknown model only trips
# the cap slightly early (safe); under-estimating Claude is the failure this
# guards against. All current Claude model IDs are listed explicitly above, so
# this default rarely fires. Same module philosophy: easier to fix an
# over-estimate than a silent under-estimate.
DEFAULT_PRICING = (3.00, 15.00)

COST_INDEX_KEY = "crucix:aria:cost:index"
COST_RECORD_PREFIX = "crucix:aria:cost:record:"
COST_AGG_KEY = "crucix:aria:cost:aggregate"
COST_MONTH_PREFIX = "crucix:aria:cost:month:"   # + YYYY-MM
COST_TTL = 90 * 86400  # 90 days of per-call detail
COST_MONTH_TTL = 400 * 86400  # keep ~13 months of monthly rollups

_INDEX_CAP = 1000  # last N call summaries kept in the index

# ── R-F2172: write-coalescing for the hot per-call aggregates ───────────────
# record_call used to do THREE read-modify-write round-trips on shared hot keys
# (COST_INDEX_KEY, COST_AGG_KEY, COST_MONTH_PREFIX+month) on EVERY LLM call.
# Under autonomous load that saturated state_store's single connection — the
# residual of the R-F2157 self-DOS (live 2026-06-30: cost:* keys timing out at
# 5s). Same fix: accumulate raw records in-process (zero I/O — atomic between
# awaits in single-threaded asyncio) and replay the merges in ONE read+write per
# hot key at most once per _COST_FLUSH_INTERVAL_S. N calls → 3 writes/interval,
# not 3N. The $300 cap is UNAFFECTED: assert_monthly_cap reserves atomically via
# INCRBYFLOAT on a separate reserve key (kept inline), so coalescing the
# REPORTING aggregates can never let spend slip past the ceiling.
_COST_FLUSH_INTERVAL_S = float(os.getenv("ARIA_COST_FLUSH_INTERVAL_S", "15"))
_cost_flush_lock = asyncio.Lock()
_cost_last_flush: float = 0.0
_pending_cost_records: list[dict] = []   # raw records since the last flush

# R-F2483 — the SAME coalescing for the EXTERNAL call path (record_external_call).
# A DD fires dozens of external searches, each previously doing ~7 state_store ops
# (unique record + INDEX/AGG/MONTH read-modify-write) → the dominant single-writer
# amplifier during a DD. A SEPARATE buffer + lock so the $300-cap LLM path
# (assert_monthly_cap's atomic reserve) is completely untouched. External cost feeds
# observability + the composite month rollup only, so the ~15s window is cap-safe.
_pending_external_records: list[dict] = []
_ext_flush_lock = asyncio.Lock()
_ext_last_flush: float = 0.0

# ── Monthly cap config ─────────────────────────────────────────────────────
# Hard ceiling on monthly LLM spend across ALL providers. Set at import
# time from env; callers re-read env on every check so a runtime override
# (operator flipping an env var) takes effect without a restart.
DEFAULT_MONTHLY_CAP_USD = 300.0


def _monthly_cap_usd() -> float:
    try:
        return float(os.getenv("ARIA_MONTHLY_CAP_USD", str(DEFAULT_MONTHLY_CAP_USD)))
    except (TypeError, ValueError):
        return DEFAULT_MONTHLY_CAP_USD


def _warn_only() -> bool:
    return os.getenv("ARIA_MONTHLY_CAP_WARN_ONLY", "0").strip().lower() in ("1", "true", "yes")


def _current_month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


# ── R-F2888: DAILY cap ──────────────────────────────────────────────────────
# Why this exists: the $300 monthly cap was the ONLY live brake on LLM spend.
# `autonomous/cost_monitor.py` implements a daily cap + 80% warning + circuit
# breaker, but it is reachable ONLY from read-only report routes
# (routes/aria.py `_get_cost_monitor`) and its `track_cost` decorator is applied
# to no LLM call site — so nothing ever fed it and it could not trip. A runaway
# could therefore burn the whole month inside a day and only be stopped at the
# $300 wall. This adds a REAL daily ceiling on the same metered path.
#
# Storage is deliberately a single INCRBYFLOAT counter, NOT a rollup: the month
# rollup path is write-coalesced (R-F2172) and read-guarded (R-F2854), and a day
# ceiling must be exact and immediate. One atomic increment per call is cheaper
# than the read-modify-write a rollup would need, and it cannot be reset to zero
# by a failed read. Reporting still comes from the month rollup.
COST_DAY_PREFIX = "crucix:aria:cost:day:"   # + YYYY-MM-DD
COST_DAY_TTL = 2 * 86400  # 48h — long enough to survive a UTC rollover + restart
DEFAULT_DAILY_CAP_USD = 25.0


def _daily_cap_usd() -> float:
    try:
        return float(os.getenv("ARIA_DAILY_CAP_USD", str(DEFAULT_DAILY_CAP_USD)))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_CAP_USD


def _daily_warn_only() -> bool:
    return os.getenv("ARIA_DAILY_CAP_WARN_ONLY", "0").strip().lower() in ("1", "true", "yes")


def _current_day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def get_day_spend() -> dict:
    """Today's LLM spend + daily cap utilisation (UTC day)."""
    day = _current_day_key()
    try:
        raw = await rs.get(f"{COST_DAY_PREFIX}{day}")
        spent = float(raw or 0.0)
    except Exception:
        spent = 0.0
    cap = _daily_cap_usd()
    return {
        "day": day,
        "spent_usd": round(spent, 6),
        "cap_usd": cap,
        "remaining_usd": round(max(0.0, cap - spent), 6),
        "utilisation_pct": round((spent / cap * 100) if cap > 0 else 0.0, 2),
        "warn_only": _daily_warn_only(),
    }


@fail_wire(module="cost_tracker", gap_type="engine_failure",
           control_flow_exempt=("DailyCostCapExceeded",))
async def assert_daily_cap(estimated_cost_usd: float = 0.02) -> None:
    """Raise DailyCostCapExceeded if today's spend is at/over ARIA_DAILY_CAP_USD.

    Same concurrent-overshoot protection as assert_monthly_cap (R-F2111): the
    estimate is RESERVED atomically before the call so N concurrent requests
    cannot all pass the same sub-cap read. record_call reconciles the reserve
    once the actual cost is known; an unreconciled reserve (crash mid-call)
    expires with the 5-minute TTL.

    Fails OPEN when the store is unreachable: a cost ceiling must never be the
    reason a request dies when we cannot even read the counter. The monthly cap
    remains the backstop in that window.
    """
    cap = _daily_cap_usd()
    if cap <= 0:
        return
    day = _current_day_key()
    day_key = f"{COST_DAY_PREFIX}{day}"
    reserve_key = f"{day_key}:reserve"
    est = max(0.001, float(estimated_cost_usd))

    reserved = False
    try:
        new_reserve = await rs.incrbyfloat(reserve_key, est)
        await rs.expire(reserve_key, 300)  # 5min — a crashed reserve auto-expires
        reserved = True
        spent = float(await rs.get(day_key) or 0.0)
        new_total = spent + new_reserve
    except Exception:
        return  # store unreachable — fail open, monthly cap still applies

    if new_total >= cap:
        if reserved:
            try:
                await rs.incrbyfloat(reserve_key, -est)
            except Exception:
                pass
        if _daily_warn_only():
            logger.error(
                "ARIA daily LLM cap $%.2f would block call — allowed because "
                "ARIA_DAILY_CAP_WARN_ONLY=1 (total would be $%.4f on %s)",
                cap, new_total, day,
            )
            return
        _emit_threshold_warnings(new_total, cap, day, scope="daily")
        raise DailyCostCapExceeded(spent=new_total, cap=cap, day=day)


# In-process mirror of the month-to-date total so pre-call checks don't
# require a Redis round-trip. Refreshed from Redis when stale or the
# calendar month rolls over.
_month_cache: dict[str, Any] = {"month": "", "total": 0.0, "loaded_at": 0.0}
_MONTH_CACHE_TTL_S = 30.0  # re-sync from Redis at least this often
# Warning threshold latches so we emit each alert at most once per month.
_warned_thresholds: set[str] = set()


# ── R-F1954: per-USER monthly sub-cap ───────────────────────────────────────
# The global cap above is a SHARED $300 ceiling — one heavy user can drain it
# and hard-fail the whole team (the cliff). This adds a second, per-user monthly
# budget so no single user can monopolise the shared pool. It is wired off the
# SAME record_call path that already knows each call's USD (user_quota.record_cost
# was dead code — never invoked — so per-user cost was previously unenforced).
# Attribution is via a contextvar set by the chat handler, mirroring _current_feature.
COST_USER_MONTH_PREFIX = "crucix:aria:cost:usermonth:"   # + <user>:<YYYY-MM>
DEFAULT_USER_MONTHLY_CAP_USD = 20.0
# user → (month, spent_usd) in-process mirror (authoritative on this process;
# Redis is the cross-process mirror, same pattern as the global _month_cache).
_user_month_mem: dict[str, tuple[str, float]] = {}
_current_user: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aria_cost_user", default="",
)


class UserMonthlyCostCapExceeded(RuntimeError):
    """Raised/surfaced when a single user's monthly sub-cap is hit."""

    def __init__(self, user: str, spent: float, cap: float, month: str):
        self.user = user
        self.spent = spent
        self.cap = cap
        self.month = month
        super().__init__(
            f"Per-user monthly cost cap ${cap:.2f} reached for {month} "
            f"(user spent ${spent:.4f})."
        )


def _user_monthly_cap_usd() -> float:
    try:
        return float(os.getenv("ARIA_USER_MONTHLY_COST_USD_CAP",
                               str(DEFAULT_USER_MONTHLY_CAP_USD)))
    except (TypeError, ValueError):
        return DEFAULT_USER_MONTHLY_CAP_USD


def set_user(name: str) -> contextvars.Token:
    """Attribute subsequent LLM calls (incl. tool-internal ones spawned as
    child tasks — contextvars propagate) to this user for per-user budgeting."""
    return _current_user.set((name or "").strip())


def get_current_user() -> str:
    return _current_user.get()


# ── R-F2767: per-TIER attribution ───────────────────────────────────────────
# The global + per-user caps bound spend, but the operator could not see COST
# PER SUBSCRIPTION TIER (free vs Essentials vs Pro Intel) — the brain never
# received the caller's tier. This contextvar carries it (set by the chat / DD
# handlers from the tier the Node proxy forwards), so record_call buckets spend
# by_tier. Precision monitoring of Claude spend per tier + margin, exactly what
# the switch-to-Claude cost model needs. Same contextvar mechanics as set_user
# so it propagates through the parallel LLM calls a DD run fires.
_current_tier: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aria_cost_tier", default="",
)


def set_tier(name: str) -> contextvars.Token:
    """Attribute subsequent LLM calls to this subscription tier (free / pro /
    proIntel). Propagates through child asyncio tasks like set_user/set_feature."""
    return _current_tier.set((name or "").strip())


def get_current_tier() -> str:
    return _current_tier.get()


def _user_month_key(user: str, month: str | None = None) -> str:
    return f"{COST_USER_MONTH_PREFIX}{user}:{month or _current_month_key()}"


async def _record_user_month_spend(user: str, cost_usd: float) -> None:
    """Add one call's USD to the user's month-to-date bucket (mem + Redis).
    Best-effort: a Redis failure never blocks the LLM call (caller wraps)."""
    if not user or cost_usd <= 0:
        return
    month = _current_month_key()
    prev = _user_month_mem.get(user)
    base = prev[1] if (prev and prev[0] == month) else 0.0
    _user_month_mem[user] = (month, base + cost_usd)
    try:
        key = _user_month_key(user, month)
        if hasattr(rs, "incrbyfloat"):
            await rs.incrbyfloat(key, cost_usd)
        else:
            existing = float(await rs.get(key) or "0")
            await rs.set(key, f"{existing + cost_usd:.6f}")
        if hasattr(rs, "expire"):
            await rs.expire(key, COST_MONTH_TTL)
    except Exception as e:
        logger.debug("cost_tracker per-user month record failed (non-fatal): %s", e)


async def get_user_month_spend(user: str) -> float:
    """User's month-to-date USD spend (max of in-process + Redis mirror)."""
    if not user:
        return 0.0
    month = _current_month_key()
    prev = _user_month_mem.get(user)
    mem = prev[1] if (prev and prev[0] == month) else 0.0
    redis_val = 0.0
    try:
        redis_val = float(await rs.get(_user_month_key(user, month)) or "0")
    except Exception:
        pass
    return max(mem, redis_val)


async def user_month_cap_exceeded(user: str) -> tuple[bool, float, float]:
    """Return (exceeded, spent, cap). Operators on the unlimited allow-list and
    an unset/zero cap are never capped."""
    cap = _user_monthly_cap_usd()
    if cap <= 0 or not user:
        return (False, 0.0, cap)
    try:
        from . import user_quota
        if user_quota.is_unlimited(user):
            return (False, 0.0, cap)
    except Exception:
        pass
    spent = await get_user_month_spend(user)
    return (spent >= cap, spent, cap)


# ── Feature attribution via contextvar ─────────────────────────────────────
# contextvar (not threading.local) so it propagates correctly through
# asyncio tasks — research_tasks fires multiple parallel LLM calls and
# each needs the right feature label.
_current_feature: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aria_cost_feature", default="uncategorized",
)


@fail_wire(module="cost_tracker", gap_type="engine_failure")
def get_current_feature() -> str:
    # R-F1598: removed wire_success call — this is a high-frequency getter
    # called on every LLM call via record_call(). Each call triggered full
    # brain_hook.absorb with expensive background tiers, contributing to
    # the circuit breaker trips. The feature name is a context var lookup,
    # not a meaningful brain event.
    return _current_feature.get()


@fail_wire(module="cost_tracker", gap_type="engine_failure")
def set_feature(name: str) -> contextvars.Token:
    """Set the active feature attribution. Returns a token the caller can
    use to reset later. Prefer the `feature()` context manager for paired
    set/reset semantics."""
    return _current_feature.set(name or "uncategorized")


@fail_wire(module="cost_tracker", gap_type="engine_failure")
def reset_feature(token: contextvars.Token) -> None:
    try:
        _current_feature.reset(token)
    except Exception:
        pass


@contextmanager
def feature(name: str):
    """Context manager: scope LLM calls inside `with feature("..."):` to
    that feature label. Async-safe — survives across `await` boundaries
    because contextvars are part of the async context."""
    token = _current_feature.set(name or "uncategorized")
    try:
        yield
    finally:
        try:
            _current_feature.reset(token)
        except Exception:
            pass


# ── Pricing lookup ─────────────────────────────────────────────────────────

def _get_price(model: str) -> tuple[float, float]:
    """Best-effort match against PRICING — exact match first, then
    case-insensitive prefix match, then default."""
    if not model:
        return DEFAULT_PRICING
    if model in PRICING:
        return PRICING[model]
    m = model.lower().strip()
    for k, v in PRICING.items():
        if k.lower() == m:
            return v
    # Prefix match: "claude-sonnet-4-6-20251201" → "claude-sonnet-4-6"
    for k, v in PRICING.items():
        if m.startswith(k.lower()) or k.lower().startswith(m):
            return v
    logger.debug("cost_tracker: unknown model %s — using default pricing", model)
    return DEFAULT_PRICING


@fail_wire(module="cost_tracker", gap_type="engine_failure")
def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = _get_price(model)
    return round(
        (input_tokens / 1_000_000) * in_rate
        + (output_tokens / 1_000_000) * out_rate,
        6,
    )


# ── Recording ──────────────────────────────────────────────────────────────

# R-F139 (2026-05-10) — external (non-LLM) service cost tracking.
# Brave Search API and Upstash Redis are both paid services that don't
# go through the LLM MeteredProvider — we need a parallel recording
# path so the dashboard cost panel surfaces total cost-of-operation,
# not just LLM spend. Same Redis index + monthly rollup as LLM calls
# but with a "kind" discriminator so they don't pollute LLM stats.
EXTERNAL_RECORD_PREFIX = "crucix:cost:ext:"
EXTERNAL_INDEX_KEY = "crucix:cost:ext:index"
EXTERNAL_AGG_KEY = "crucix:cost:ext:agg"

# Public Brave Search API price as of 2026-05-10: $5 per 1,000 queries
# on the Pro plan, $9 per 1,000 on Pro AI. Free tier allows 2,000/month.
# We assume Pro pricing as the conservative ceiling — operator can
# override via `BRAVE_COST_PER_CALL_USD` env var if on a different plan.
_BRAVE_DEFAULT_COST_PER_CALL = 0.005  # $5 / 1,000 queries


@fail_wire(module="cost_tracker", gap_type="engine_failure")
async def record_external_call(
    *,
    service: str,                  # "brave" | "upstash" | etc.
    operation: str = "",           # "search" | "match" | "snapshot" | etc.
    cost_usd: float = 0.0,
    feature_name: str | None = None,
    success: bool = True,
    latency_ms: int = 0,
    metadata: dict | None = None,
    error: str = "",
) -> dict:
    """Persist one external-service call (Brave / Upstash / etc).

    Same Redis-index + monthly-rollup pattern as record_call() but
    keyed under EXTERNAL_RECORD_PREFIX so the LLM cost panel doesn't
    conflate them. Aggregates are keyed by service so /cost/external
    can return per-service totals.

    Fail-open — never blocks the calling feature on persist errors.
    """
    feat = feature_name or get_current_feature() or "uncategorized"
    call_id = f"ext_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    record = {
        "id": call_id,
        "kind": "external",
        "service": service or "unknown",
        "operation": operation or "",
        "ts": time.time(),
        "feature": feat,
        "cost_usd": round(float(cost_usd or 0.0), 6),
        "latency_ms": int(latency_ms or 0),
        "success": success,
        "error": (error or "")[:300],
        "metadata": metadata or {},
    }
    # R-F2483 — coalesce: accumulate the record and let the batched flush update the
    # hot EXTERNAL index / per-service aggregate / composite month-rollup keys in ONE
    # read+write per interval, instead of ~7 state_store ops PER external call (the
    # writer amplifier during a DD's search fan-out). Fail-open — the caller is never
    # blocked; on flush failure the batch is folded back so no cost data is lost.
    _pending_external_records.append(record)
    try:
        await _flush_external_pending()
    except Exception as e:
        logger.warning(
            "cost_tracker.record_external_call flush failed (%s/%s): %s",
            service, operation, e,
        )
    return record


@fail_wire(module="cost_tracker", gap_type="engine_failure")
async def record_brave_call(
    *,
    operation: str = "search",
    feature_name: str | None = None,
    success: bool = True,
    latency_ms: int = 0,
    cost_per_call_usd: float | None = None,
) -> dict:
    """Convenience wrapper around record_external_call for Brave.
    Operator can override per-call cost via BRAVE_COST_PER_CALL_USD."""
    rate = cost_per_call_usd
    if rate is None:
        env = (os.getenv("BRAVE_COST_PER_CALL_USD") or "").strip()
        try:
            rate = float(env) if env else _BRAVE_DEFAULT_COST_PER_CALL
        except Exception:
            rate = _BRAVE_DEFAULT_COST_PER_CALL
    return await record_external_call(
        service="brave",
        operation=operation,
        cost_usd=rate,
        feature_name=feature_name,
        success=success,
        latency_ms=latency_ms,
    )


@fail_wire(module="cost_tracker", gap_type="engine_failure")
async def get_external_summary(month: str | None = None) -> dict:
    """Return per-service external spend summary for the dashboard."""
    await _flush_external_pending()  # R-F2483 — surface pending coalesced records
    agg = await rs.get_json(EXTERNAL_AGG_KEY) or {}
    return {
        "by_service": agg,
        "total_calls": sum(int(v.get("calls", 0)) for v in agg.values()),
        "total_cost_usd": round(
            sum(float(v.get("cost_usd", 0.0)) for v in agg.values()),
            6,
        ),
    }


@fail_wire(module="cost_tracker", gap_type="engine_failure")
async def record_call(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int = 0,
    feature_name: str | None = None,
    provider_name: str = "",
    success: bool = True,
    error: str = "",
) -> dict:
    """Persist one LLM call's cost record. Called by the metered provider
    wrapper — should not be invoked directly by feature code.

    Failure to persist must NEVER affect the LLM call itself, so wrap
    every Redis op defensively.
    """
    feat = feature_name or get_current_feature() or "uncategorized"
    cost_usd = estimate_cost_usd(model, input_tokens, output_tokens)
    call_id = f"call_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    record = {
        "id": call_id,
        "ts": time.time(),
        "model": model or "",
        "provider": provider_name or "",
        "feature": feat,
        "tier": get_current_tier() or "unattributed",  # R-F2767 — per-tier cost
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "total_tokens": int((input_tokens or 0) + (output_tokens or 0)),
        "cost_usd": cost_usd,
        "latency_ms": int(latency_ms or 0),
        "success": success,
        "error": (error or "")[:300],
    }
    try:
        await rs.set_json(f"{COST_RECORD_PREFIX}{call_id}", record, ex=COST_TTL)
        # Self-attach to the active trace if there is one. Lazy import
        # to avoid an unconditional dependency at module load.
        try:
            from . import trace_stream
            await trace_stream.attach_call_to_current_trace(record)
        except Exception as e:
            logger.debug("trace attach failed: %s", e)
        # R-F2172: accumulate this record and let the coalesced flush update the
        # hot index / per-feature aggregate / month-rollup keys in ONE batched
        # read+write per interval. This replaces the 3 read-modify-write
        # round-trips per LLM call that saturated state_store's single
        # connection (the residual cost:* self-DOS after R-F2157).
        _pending_cost_records.append(record)
        await _flush_cost_pending()
        # R-F2111: reconcile the atomic reserve made in assert_monthly_cap.
        # Deduct the estimated reserve and let the actual cost stand in the
        # rollup. If no reserve exists (e.g. Redis was down during assert),
        # this is a no-op. Best-effort, never blocks.
        try:
            _reserve_key = f"{COST_MONTH_PREFIX}{_current_month_key()}:reserve"
            _reserved = await rs.get(_reserve_key)
            if _reserved:
                _reserved_f = float(_reserved)
                if _reserved_f > 0:
                    # Deduct the reserve by the actual cost (or the full reserve
                    # if actual cost is unknown). This prevents the reserve from
                    # permanently inflating the month total.
                    _deduct = min(_reserved_f, max(0.001, cost_usd))
                    await rs.incrbyfloat(_reserve_key, -_deduct)
        except Exception:
            pass
        # R-F2888 — DAILY ledger: commit the actual cost to today's counter and
        # reconcile the reserve assert_daily_cap made. This counter IS the daily
        # cap's source of truth (see COST_DAY_PREFIX note), so it is written on
        # every call rather than coalesced. Best-effort: a failure here loses one
        # day-metric, never the call.
        try:
            _day_key = f"{COST_DAY_PREFIX}{_current_day_key()}"
            await rs.incrbyfloat(_day_key, max(0.0, cost_usd))
            await rs.expire(_day_key, COST_DAY_TTL)
            _day_reserve = await rs.get(f"{_day_key}:reserve")
            if _day_reserve:
                _dr = float(_day_reserve)
                if _dr > 0:
                    await rs.incrbyfloat(
                        f"{_day_key}:reserve", -min(_dr, max(0.001, cost_usd)))
            _emit_threshold_warnings(
                float(await rs.get(_day_key) or 0.0), _daily_cap_usd(),
                _current_day_key(), scope="daily",
            )
        except Exception:
            pass
        # R-F1954 — per-user monthly sub-cap accounting (best-effort).
        await _record_user_month_spend(get_current_user(), cost_usd)
        # R-F2103 (2026-06-28, ARIA brain DD) — wire the DEAD per-user DAILY cost
        # cap. user_quota.record_cost was defined but NEVER called, so the
        # ARIA_USER_DAILY_COST_USD_CAP ($5/user/day) never fired — only the global
        # $300/mo bounded spend, so one heavy user could drain the budget. record
        # it here on the SAME path that already knows the per-call USD + user (the
        # cost_tracker.py:164 comment pointed here). Best-effort, never blocks.
        try:
            _u = get_current_user()
            if _u and cost_usd > 0:
                from . import user_quota as _uq
                await _uq.record_cost(_u, cost_usd)
        except Exception as _uqe:
            logger.debug("cost_tracker: user_quota.record_cost failed (non-fatal): %s", _uqe)
    except Exception as e:
        logger.warning("cost_tracker.record_call persist failed: %s", e)
    return record


# ── Monthly rollup + cap enforcement ───────────────────────────────────────

def _new_rollup(month: str, ts: float) -> dict:
    return {
        "month": month,
        "total_cost_usd": 0.0,
        "total_calls": 0,
        "total_tokens": 0,
        "first_ts": ts,
        "last_ts": ts,
        "by_provider": {},
        "by_feature": {},
        "by_model": {},
        "by_tier": {},  # R-F2767 — per-subscription-tier spend
        "top_calls": [],
    }


def _merge_record_into_rollup(roll: dict, record: dict) -> None:
    """Pure, in-place merge of one call record into a month rollup dict.
    Extracted (R-F2172) so BOTH the single-update path (external calls) and the
    batched coalescing flush apply identical math."""
    cost = float(record.get("cost_usd") or 0.0)
    roll["total_cost_usd"] = round(roll.get("total_cost_usd", 0.0) + cost, 6)
    roll["total_calls"] = roll.get("total_calls", 0) + 1
    roll["total_tokens"] = roll.get("total_tokens", 0) + int(record.get("total_tokens") or 0)
    roll["last_ts"] = record["ts"]
    for bucket_key, val in (
        ("by_provider", record.get("provider") or "unknown"),
        ("by_feature", record.get("feature") or "uncategorized"),
        ("by_model", record.get("model") or "unknown"),
        ("by_tier", record.get("tier") or "unattributed"),  # R-F2767
    ):
        bucket = roll.setdefault(bucket_key, {})
        cell = bucket.get(val) or {"calls": 0, "tokens": 0, "cost_usd": 0.0}
        cell["calls"] += 1
        cell["tokens"] += int(record.get("total_tokens") or 0)
        cell["cost_usd"] = round(cell["cost_usd"] + cost, 6)
        bucket[val] = cell
    top = roll.setdefault("top_calls", [])
    top.append({
        "id": record["id"],
        "ts": record["ts"],
        "model": record.get("model", ""),
        "provider": record.get("provider", ""),
        "feature": record.get("feature", ""),
        "total_tokens": record.get("total_tokens", 0),
        "cost_usd": cost,
    })
    top.sort(key=lambda r: r.get("cost_usd", 0.0), reverse=True)
    roll["top_calls"] = top[:20]


async def _load_rollup_for_update(key: str, month: str, ts: Any) -> dict | None:
    """Strict read of a month rollup for a read-modify-write.

    Returns ``None`` when the store could not be read — the caller MUST skip the
    write.

    R-F2854 (2026-07-22): every rollup update was
    ``roll = await rs.get_json(key) or _new_rollup(...)``. Non-strict get_json
    swallows a store-layer StoreReadError to None, and None is indistinguishable
    from "no spend recorded this month" — so a 5s state_store timeout (observed
    live on aria-intel 2026-07-22) produced a ZERO rollup, merged the current
    record into it, and wrote it back, RESETTING the month's accumulated spend.

    That is a safety-control failure, not just lost telemetry:
      * the §17 $300/mo cap is evaluated against the rollup, so a reset grants
        up to a full extra cap of spend;
      * ``_emit_threshold_warnings`` then evaluates the reset total, so no
        warning fires;
      * ``/api/aria/cost/monthly/status`` — the surface the operator checks
        daily — reports the reset figure. A false clean on spend.

    Fail closed: on a failed read we skip the write entirely. The individual
    cost records are still persisted under COST_RECORD_PREFIX, so the rollup
    remains rebuildable; under-counting one batch is recoverable, wiping the
    month's total is not.
    """
    try:
        existing = await rs.get_json_strict(key)
    except Exception as e:
        logger.warning(
            "[R-F2854] month rollup %s NOT updated — store read failed (%s); "
            "skipping the write so the accumulated total is not reset to zero",
            key, e,
        )
        return None
    if isinstance(existing, dict) and existing:
        return existing
    return _new_rollup(month, ts)


async def _update_month_rollup(record: dict) -> None:
    """Add one call to the current month's aggregate + refresh the in-process
    cache. Safe to call inside the record_call try/except — its failure must
    never prevent the rest of the record from persisting.

    Used by record_external_call (low volume). The high-volume record_call path
    coalesces via _flush_cost_pending (R-F2172) instead of calling this per-call.
    """
    month = _current_month_key()
    key = f"{COST_MONTH_PREFIX}{month}"
    try:
        roll = await _load_rollup_for_update(key, month, record["ts"])
        if roll is None:
            return  # R-F2854: never write a total derived from a failed read
        _merge_record_into_rollup(roll, record)
        await rs.set_json(key, roll, ex=COST_MONTH_TTL)

        # Refresh in-process cache + evaluate warning thresholds.
        _month_cache["month"] = month
        _month_cache["total"] = roll["total_cost_usd"]
        _month_cache["loaded_at"] = time.time()
        _emit_threshold_warnings(roll["total_cost_usd"], _monthly_cap_usd(), month)
    except Exception as e:
        logger.warning("cost_tracker._update_month_rollup failed: %s", e)


def _index_summary(record: dict) -> dict:
    """The compact per-call entry kept in COST_INDEX_KEY."""
    return {
        "id": record["id"],
        "ts": record["ts"],
        "model": record.get("model", ""),
        "feature": record.get("feature", "uncategorized"),
        "total_tokens": record.get("total_tokens", 0),
        "cost_usd": record.get("cost_usd", 0.0),
        "success": record.get("success", True),
    }


async def _flush_cost_pending(force: bool = False) -> bool:
    """R-F2172 — coalesce all pending per-call records into ONE read+write per
    hot key (index, aggregate, month rollup). Time-gated to once per
    _COST_FLUSH_INTERVAL_S unless `force`; single in-flight flush (lock). On DB
    failure the batch is folded back so no cost data is lost. Returns True if a
    flush actually wrote."""
    global _cost_last_flush, _pending_cost_records
    now = time.time()
    if not force and (now - _cost_last_flush) < _COST_FLUSH_INTERVAL_S:
        return False
    if not _pending_cost_records:
        _cost_last_flush = now
        return False
    if _cost_flush_lock.locked():
        return False
    async with _cost_flush_lock:
        now = time.time()
        if not force and (now - _cost_last_flush) < _COST_FLUSH_INTERVAL_S:
            return False
        batch = _pending_cost_records
        _pending_cost_records = []
        try:
            # 1. Index — prepend newest-first, cap at _INDEX_CAP. batch is in
            # chronological order; insert(0) each in order so the LAST (newest)
            # ends up at position 0 (matches the original per-call insert(0)).
            index = await rs.get_json(COST_INDEX_KEY) or []
            for rec in batch:
                index.insert(0, _index_summary(rec))
            await rs.set_json(COST_INDEX_KEY, index[:_INDEX_CAP], ex=COST_TTL)

            # 2. Per-feature cumulative aggregate.
            agg = await rs.get_json(COST_AGG_KEY) or {}
            for rec in batch:
                feat = rec.get("feature") or "uncategorized"
                fa = agg.get(feat) or {
                    "calls": 0, "input_tokens": 0, "output_tokens": 0,
                    "total_tokens": 0, "cost_usd": 0.0, "errors": 0,
                }
                fa["calls"] += 1
                fa["input_tokens"] += int(rec.get("input_tokens") or 0)
                fa["output_tokens"] += int(rec.get("output_tokens") or 0)
                fa["total_tokens"] += int(rec.get("total_tokens") or 0)
                fa["cost_usd"] = round(fa["cost_usd"] + float(rec.get("cost_usd") or 0.0), 6)
                if not rec.get("success", True):
                    fa["errors"] += 1
                agg[feat] = fa
            await rs.set_json(COST_AGG_KEY, agg, ex=COST_TTL)

            # 3. Month rollup(s) — group by the record's calendar month.
            from collections import defaultdict
            by_month: dict[str, list[dict]] = defaultdict(list)
            for rec in batch:
                m = datetime.fromtimestamp(
                    float(rec.get("ts") or now), tz=timezone.utc).strftime("%Y-%m")
                by_month[m].append(rec)
            for m, recs in by_month.items():
                key = f"{COST_MONTH_PREFIX}{m}"
                roll = await _load_rollup_for_update(key, m, recs[0]["ts"])
                if roll is None:
                    continue  # R-F2854: skip rather than reset the month total
                for rec in recs:
                    _merge_record_into_rollup(roll, rec)
                await rs.set_json(key, roll, ex=COST_MONTH_TTL)
                # Refresh in-process cache + thresholds for the CURRENT month.
                if m == _current_month_key():
                    _month_cache["month"] = m
                    _month_cache["total"] = roll["total_cost_usd"]
                    _month_cache["loaded_at"] = time.time()
                    _emit_threshold_warnings(roll["total_cost_usd"], _monthly_cap_usd(), m)

            _cost_last_flush = now
            return True
        except Exception as e:
            # Fold the batch back so the next flush retries it — no cost lost.
            _pending_cost_records = batch + _pending_cost_records
            logger.debug("cost_tracker flush failed (folded back): %s", e)
            return False


async def _flush_external_pending(force: bool = False) -> bool:
    """R-F2483 — coalesce pending EXTERNAL call records into ONE read+write per hot
    key (EXTERNAL_INDEX, EXTERNAL_AGG, composite month rollup) per interval. Mirrors
    _flush_cost_pending. External cost is observability + the composite month total,
    NOT the $300 LLM cap (assert_monthly_cap reserves atomically), so the ~15s window
    is cap-safe. Time-gated + single in-flight; folds the batch back on DB failure so
    no cost data is lost. Returns True if a flush actually wrote."""
    global _ext_last_flush, _pending_external_records
    now = time.time()
    if not force and (now - _ext_last_flush) < _COST_FLUSH_INTERVAL_S:
        return False
    if not _pending_external_records:
        _ext_last_flush = now
        return False
    if _ext_flush_lock.locked():
        return False
    async with _ext_flush_lock:
        now = time.time()
        if not force and (now - _ext_last_flush) < _COST_FLUSH_INTERVAL_S:
            return False
        batch = _pending_external_records
        _pending_external_records = []
        try:
            # 1. Per-call detail blobs (unique keys — no RMW; batched to cut the
            #    per-call write count).
            for rec in batch:
                await rs.set_json(f"{EXTERNAL_RECORD_PREFIX}{rec['id']}", rec, ex=COST_TTL)
            # 2. Index — newest-first, capped (chronological batch → insert(0) each
            #    so the newest lands at position 0, matching the old per-call insert).
            index = await rs.get_json(EXTERNAL_INDEX_KEY) or []
            for rec in batch:
                index.insert(0, {
                    "id": rec["id"], "ts": rec["ts"], "service": rec["service"],
                    "operation": rec["operation"], "feature": rec["feature"],
                    "cost_usd": rec["cost_usd"], "success": rec["success"],
                })
            await rs.set_json(EXTERNAL_INDEX_KEY, index[:_INDEX_CAP], ex=COST_TTL)
            # 3. Per-service cumulative aggregate.
            agg = await rs.get_json(EXTERNAL_AGG_KEY) or {}
            for rec in batch:
                svc = rec["service"]
                sa = agg.get(svc) or {"calls": 0, "cost_usd": 0.0, "errors": 0}
                sa["calls"] += 1
                sa["cost_usd"] = round(sa["cost_usd"] + float(rec.get("cost_usd") or 0.0), 6)
                if not rec.get("success", True):
                    sa["errors"] += 1
                agg[svc] = sa
            await rs.set_json(EXTERNAL_AGG_KEY, agg, ex=COST_TTL)
            # 4. Composite month rollup(s) — identical math to the LLM path
            #    (_merge_record_into_rollup is external-record-safe by design).
            from collections import defaultdict
            by_month: dict[str, list[dict]] = defaultdict(list)
            for rec in batch:
                m = datetime.fromtimestamp(
                    float(rec.get("ts") or now), tz=timezone.utc).strftime("%Y-%m")
                by_month[m].append(rec)
            for m, recs in by_month.items():
                key = f"{COST_MONTH_PREFIX}{m}"
                roll = await _load_rollup_for_update(key, m, recs[0]["ts"])
                if roll is None:
                    continue  # R-F2854: skip rather than reset the month total
                for rec in recs:
                    _merge_record_into_rollup(roll, rec)
                await rs.set_json(key, roll, ex=COST_MONTH_TTL)
                if m == _current_month_key():
                    _month_cache["month"] = m
                    _month_cache["total"] = roll["total_cost_usd"]
                    _month_cache["loaded_at"] = time.time()
            _ext_last_flush = now
            return True
        except Exception as e:
            _pending_external_records = batch + _pending_external_records
            logger.debug("cost_tracker external flush failed (folded back): %s", e)
            return False


def _emit_threshold_warnings(
    spent: float, cap: float, period: str, scope: str = "monthly",
) -> None:
    """Warn as spend approaches a cap. Latched per (scope, period, threshold)
    so the operator is not spammed; month latches reset on calendar rollover
    (see _refresh_month_cache), day latches are pruned when the UTC day turns.

    R-F2889: this used to be LOG-ONLY at 50/80% — the warning went to the fly
    logs and nowhere else, so the operator only learned about spend by going and
    looking at the dashboard. That is precisely the §19e failure mode (a blocker
    the operator has to discover himself). From 80% up, the warning is now PUSHED
    to the operator-visible pending-actions queue and to the brain (§21), and the
    ladder gains 95% and 100% steps so the wall is never the first notification.

    ``scope`` is "monthly" or "daily"; ``period`` is the YYYY-MM or YYYY-MM-DD
    the spend belongs to. Sync by design (it is called from inside the cost
    write path); the operator push is dispatched as a task and never blocks.
    """
    if cap <= 0:
        return
    util = spent / cap

    # Keep the day latches from accumulating in a long-lived process.
    if scope == "daily":
        _stale = {t for t in _warned_thresholds
                  if t.startswith("daily:") and not t.startswith(f"daily:{period}:")}
        _warned_thresholds.difference_update(_stale)

    ladder = ((1.00, "100"), (0.95, "95"), (0.80, "80"), (0.50, "50"))
    for idx, (pct, label) in enumerate(ladder):
        tag = f"{scope}:{period}:{label}"
        if util < pct or tag in _warned_thresholds:
            continue
        # Latch EVERY lower rung too. Spend can jump straight past a rung (0 →
        # 85% in one call), and without this the next call would fire a "50%"
        # alert *after* the 80% one — alarming backwards and training the
        # operator to ignore the channel.
        for _, lower in ladder[idx:]:
            _warned_thresholds.add(f"{scope}:{period}:{lower}")
        logger.warning(
            "ARIA %s LLM spend at %.0f%% of cap — spent $%.4f of $%.2f (%s)",
            scope, util * 100, spent, cap, period,
        )
        if pct >= 0.80:
            _push_spend_alert(scope, period, spent, cap, util, label)
        return  # only fire the highest-matching threshold per call


def _push_spend_alert(
    scope: str, period: str, spent: float, cap: float, util: float, label: str,
) -> None:
    """R-F2889 — surface a spend threshold to the OPERATOR + the brain.

    Fire-and-forget: cost alerting must never delay or fail an LLM call. Needs a
    running loop (every caller is inside one); if there is none, the logger.warning
    already emitted by the caller stands as the record.
    """
    severity = "CRITICAL" if util >= 1.0 else "HIGH"
    headline = (
        f"LLM spend at {util * 100:.0f}% of the {scope} cap "
        f"(${spent:.2f} of ${cap:.2f}, {period})"
    )

    async def _dispatch() -> None:
        try:
            from . import pending_actions as _pa
            await _pa.record(
                promise=f"ARIA {scope} LLM spend alert — {headline}",
                reason=(
                    f"{scope} spend crossed {label}% of the ${cap:.2f} cap. "
                    f"At 100% metered LLM calls are refused "
                    f"(ARIA_{'DAILY' if scope == 'daily' else 'MONTHLY'}_CAP_USD)."
                ),
                severity=severity,
                source="cost_tracker",
                operator_prompt=(
                    f"{headline}. Review /api/aria/cost/monthly and "
                    f"/api/aria/cost/daily, then raise the cap or pause spend."
                ),
            )
        except Exception as e:
            logger.warning("cost alert: operator push failed: %s", e)
        try:  # §21 — the brake reports its own firing
            from . import brain_hook as _bh
            await _bh.record_signal(
                module="cost_tracker", success=True, summary=headline[:300],
            )
        except Exception:
            pass

    import asyncio as _aio
    try:
        _aio.get_running_loop()
    except RuntimeError:
        return  # no running loop — the logger.warning above is the record
    _aio.create_task(_dispatch())


async def _refresh_month_cache(force: bool = False) -> float:
    """Pull the current month's total from Redis into the in-process cache.
    Cheap — one key read. Returns the current month total (0.0 if no data)."""
    month = _current_month_key()
    now = time.time()
    if (not force
            and _month_cache["month"] == month
            and (now - float(_month_cache.get("loaded_at") or 0.0)) < _MONTH_CACHE_TTL_S):
        return float(_month_cache["total"])
    try:
        roll = await rs.get_json(f"{COST_MONTH_PREFIX}{month}") or {}
        total = float(roll.get("total_cost_usd") or 0.0)
        # Fallback: if the monthly rollup hasn't been populated yet (fresh
        # deploy, eviction), use the index-derived total so the cap
        # accounts for spend that predates the rollup.
        if total == 0.0:
            index_roll = await _breakdown_from_index(month)
            total = float(index_roll.get("total_cost_usd") or 0.0)
    except Exception:
        total = float(_month_cache.get("total") or 0.0)
    # Month rollover: reset warning latches for the new month.
    if _month_cache["month"] != month:
        _warned_thresholds.clear()
    _month_cache["month"] = month
    _month_cache["total"] = total
    _month_cache["loaded_at"] = now
    return total


@fail_wire(module="cost_tracker", gap_type="engine_failure")
async def get_month_spend() -> dict:
    """Month-to-date LLM spend + cap utilisation."""
    # R-F2234: READ path — force=False. force=True defeated the R-F2172 15s
    # time-gate, so every dashboard cost read did a read-modify-write on the 3
    # hot keys (index/aggregate/month) through the shared write lock, a prime
    # contributor to state_store saturation. force=False piggybacks the gated
    # flush (≤1 write/15s). The $300 cap is enforced by the SEPARATE atomic
    # reserve path (assert_monthly_cap / INCRBYFLOAT), NOT by this read, so a
    # gauge staleness of ≤15s has ZERO cap-safety impact.
    await _flush_cost_pending(force=False)
    spent = await _refresh_month_cache()
    cap = _monthly_cap_usd()
    return {
        "month": _current_month_key(),
        "spent_usd": round(spent, 6),
        "cap_usd": cap,
        "remaining_usd": round(max(0.0, cap - spent), 6),
        "utilisation_pct": round((spent / cap * 100) if cap > 0 else 0.0, 2),
        "warn_only": _warn_only(),
    }


@fail_wire(module="cost_tracker", gap_type="engine_failure", control_flow_exempt=("MonthlyCostCapExceeded",))
async def assert_monthly_cap(estimated_cost_usd: float = 0.02) -> None:
    """Raise MonthlyCostCapExceeded if month-to-date spend is at/over the cap.
    Called by MeteredProvider before every LLM request. Honours
    ARIA_MONTHLY_CAP_WARN_ONLY=1 for investigation mode.

    R-F2111: concurrent-overshoot fix. The 30s _month_cache means N concurrent
    requests can all pass the same sub-cap read before any of them records spend.
    We now RESERVE the estimated cost atomically via Redis INCRBYFLOAT on a
    dedicated reserve key so the cap is a hard ceiling, not a check-then-fire.
    The reserve is reconciled (deducted) in record_call when the actual cost is
    known. A reserve that is never reconciled (crash after reserve, before
    record_call) expires naturally because the reserve key has a 5-minute TTL.
    """
    cap = _monthly_cap_usd()
    if cap <= 0:
        return
    month = _current_month_key()
    reserve_key = f"{COST_MONTH_PREFIX}{month}:reserve"
    est = max(0.001, float(estimated_cost_usd))

    # Atomic reserve: INCRBYFLOAT on the reserve key protects against concurrent
    # overshoot (R-F2111). But the CEILING is on ACTUAL month-to-date spend PLUS the
    # in-flight reserves — NOT the reserve alone. R-F2524: the reserve key has a 5-min
    # TTL and record_call reconciles it back to ~0, so comparing `new_reserve >= cap`
    # NEVER blocked on accumulated spend — an over-cap month passed every check (proven
    # by test_assert_monthly_cap_blocks_once_over: $4.50 spent vs $0.50 cap did not
    # raise). Add the recorded month spend so the cap is a true hard ceiling.
    reserved = False
    try:
        new_reserve = await rs.incrbyfloat(reserve_key, est)
        await rs.expire(reserve_key, 300)  # 5min TTL — a crashed reserve auto-expires
        reserved = True
        spent = await _refresh_month_cache()  # actual recorded month-to-date spend
        new_total = spent + new_reserve
    except Exception:
        # Fall back to cache-based check when Redis is down
        spent = await _refresh_month_cache()
        new_total = spent + est

    # new_total = recorded month spend + in-flight reserves. Compare against cap.
    if new_total >= cap:
        # Roll back the reserve so we don't permanently inflate the counter
        if reserved:
            try:
                await rs.incrbyfloat(reserve_key, -est)
            except Exception:
                pass
        if _warn_only():
            logger.error(
                "ARIA monthly LLM cap $%.2f would block call — allowed because "
                "ARIA_MONTHLY_CAP_WARN_ONLY=1 (reserve pushed total to $%.4f in %s)",
                cap, new_total, month,
            )
            return
        raise MonthlyCostCapExceeded(spent=new_total, cap=cap, month=month)

    # Under cap — cache the reserve-adjusted total so _refresh_month_cache
    # sees it on the next check (belt-and-suspenders with the Redis key).
    _month_cache["total"] = max(_month_cache.get("total", 0.0), new_total)
    _month_cache["loaded_at"] = time.time()


def _provider_from_model(model: str) -> str:
    """Derive a provider label from a model name when the index record
    doesn't carry one (pre-monthly-rollup entries). Covers the providers
    actually wired into ARIA's factory; unknowns fall back to 'unknown'."""
    m = (model or "").lower()
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("deepseek"):
        return "deepseek"
    if m.startswith(("gpt-", "o1-", "o3-")):
        return "openai"
    if m.startswith(("gemini", "gemma")):
        return "google"
    if m.startswith(("llama", "mixtral", "qwen")):
        return "groq"
    if m.startswith(("mistral", "codestral")):
        return "mistral"
    if m.startswith("ollama") or m in {"llama3.1:8b", "llama3.1:70b"}:
        return "ollama"
    return "unknown"


async def _breakdown_from_index(target_month: str) -> dict:
    """Reconstruct a monthly breakdown from the 90-day per-call index.
    Used as a fallback when the monthly rollup key is empty — i.e. on a
    fresh deployment, after Redis key eviction, or for back-calculating
    spend that predates this rollup code landing."""
    try:
        index = await rs.get_json(COST_INDEX_KEY) or []
    except Exception:
        index = []
    total = 0.0
    calls = 0
    tokens = 0
    first_ts = None
    last_ts = None
    by_provider: dict[str, dict] = {}
    by_feature: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    by_tier: dict[str, dict] = {}  # R-F2767
    all_calls: list[dict] = []
    for e in index:
        ts = float(e.get("ts") or 0.0)
        if not ts:
            continue
        if datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m") != target_month:
            continue
        cost = float(e.get("cost_usd") or 0.0)
        tk = int(e.get("total_tokens") or 0)
        model = e.get("model") or "unknown"
        feat = e.get("feature") or "uncategorized"
        provider = e.get("provider") or _provider_from_model(model)
        total += cost
        calls += 1
        tokens += tk
        first_ts = ts if first_ts is None or ts < first_ts else first_ts
        last_ts = ts if last_ts is None or ts > last_ts else last_ts
        for bucket, key in (
            (by_provider, provider),
            (by_feature, feat),
            (by_model, model),
            (by_tier, e.get("tier") or "unattributed"),  # R-F2767
        ):
            cell = bucket.get(key) or {"calls": 0, "tokens": 0, "cost_usd": 0.0}
            cell["calls"] += 1
            cell["tokens"] += tk
            cell["cost_usd"] = round(cell["cost_usd"] + cost, 6)
            bucket[key] = cell
        all_calls.append({
            "id": e.get("id"),
            "ts": ts,
            "model": model,
            "provider": provider,
            "feature": feat,
            "total_tokens": tk,
            "cost_usd": cost,
        })
    all_calls.sort(key=lambda r: r.get("cost_usd", 0.0), reverse=True)
    return {
        "month": target_month,
        "total_cost_usd": round(total, 6),
        "total_calls": calls,
        "total_tokens": tokens,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "by_provider": by_provider,
        "by_feature": by_feature,
        "by_model": by_model,
        "by_tier": by_tier,  # R-F2767
        "top_calls": all_calls[:20],
        "_source": "index_fallback",
    }


@fail_wire(module="cost_tracker", gap_type="engine_failure")
async def get_month_breakdown(month: str | None = None) -> dict:
    """Full breakdown for the requested month (defaults to current).
    Exposes per-provider, per-feature, per-model totals plus the 20 most
    expensive individual calls — the diagnostic the operator reaches for
    when asking 'where did the money go?'.

    Falls back to reconstructing from the 90-day per-call index when the
    monthly rollup key is empty (fresh deploy, eviction, pre-rollup
    historical data). The fallback covers everything the index retains
    (~1000 most recent calls / up to 90 days)."""
    await _flush_cost_pending(force=False)  # R-F2234: READ path — gated flush, no per-read RMW (see get_month_spend)
    target = month or _current_month_key()
    try:
        roll = await rs.get_json(f"{COST_MONTH_PREFIX}{target}") or {}
    except Exception as e:
        return {"error": str(e), "month": target}
    # No rollup yet — reconstruct from the index so historical spend shows
    # up immediately rather than waiting for the next LLM call to bootstrap.
    if not roll or int(roll.get("total_calls") or 0) == 0:
        roll = await _breakdown_from_index(target)
    cap = _monthly_cap_usd()
    spent = float(roll.get("total_cost_usd") or 0.0)
    # Project end-of-month based on elapsed fraction of the current month.
    projected = spent
    if target == _current_month_key():
        now = datetime.now(timezone.utc)
        # Day-of-month / days-in-month is a good-enough linear projection;
        # the user only needs a ballpark of whether they'll hit the cap.
        first_ts = float(roll.get("first_ts") or now.timestamp())
        elapsed_s = max(1.0, now.timestamp() - first_ts)
        # 30 days as a flat month proxy — operators read this as a gauge.
        projected = round(spent * (30 * 86400) / elapsed_s, 4) if spent > 0 else 0.0
    return {
        "month": target,
        "total_cost_usd": round(spent, 6),
        "total_calls": int(roll.get("total_calls") or 0),
        "total_tokens": int(roll.get("total_tokens") or 0),
        "cap_usd": cap,
        "remaining_usd": round(max(0.0, cap - spent), 6),
        "utilisation_pct": round((spent / cap * 100) if cap > 0 else 0.0, 2),
        "projected_monthly_usd": projected,
        "first_call_ts": roll.get("first_ts"),
        "last_call_ts": roll.get("last_ts"),
        "by_provider": roll.get("by_provider", {}),
        "by_feature": roll.get("by_feature", {}),
        "by_model": roll.get("by_model", {}),
        "by_tier": roll.get("by_tier", {}),  # R-F2767 — Claude spend per subscription tier
        "top_calls": roll.get("top_calls", []),
        "warn_only": _warn_only(),
        "_source": roll.get("_source", "rollup"),
    }


# ── Reporting ──────────────────────────────────────────────────────────────

@fail_wire(module="cost_tracker", gap_type="engine_failure")
async def get_cost_summary(window_hours: int = 24) -> dict:
    """Aggregate stats over a rolling window from the index. The cumulative
    aggregate (COST_AGG_KEY) covers all-time; this windowed view answers
    'what did the last 24h cost'."""
    await _flush_cost_pending(force=False)  # R-F2234: READ path — gated flush, no per-read RMW (see get_month_spend)
    try:
        index = await rs.get_json(COST_INDEX_KEY) or []
        cutoff = time.time() - (max(1, window_hours) * 3600)
        windowed = [e for e in index if e.get("ts", 0) >= cutoff]

        by_feature: dict[str, dict] = {}
        by_model: dict[str, dict] = {}
        total_calls = 0
        total_tokens = 0
        total_cost = 0.0

        for e in windowed:
            feat = e.get("feature") or "uncategorized"
            mdl = e.get("model") or "unknown"
            tk = e.get("total_tokens") or 0
            usd = e.get("cost_usd") or 0.0

            total_calls += 1
            total_tokens += tk
            total_cost += usd

            f = by_feature.setdefault(feat, {"calls": 0, "tokens": 0, "cost_usd": 0.0})
            f["calls"] += 1
            f["tokens"] += tk
            f["cost_usd"] = round(f["cost_usd"] + usd, 6)

            m = by_model.setdefault(mdl, {"calls": 0, "tokens": 0, "cost_usd": 0.0})
            m["calls"] += 1
            m["tokens"] += tk
            m["cost_usd"] = round(m["cost_usd"] + usd, 6)

        # Project monthly cost from the windowed rate
        hours = max(1, window_hours)
        projected_monthly = round((total_cost / hours) * 24 * 30, 4)

        return {
            "window_hours": window_hours,
            "total_calls": total_calls,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "projected_monthly_usd": projected_monthly,
            "by_feature": by_feature,
            "by_model": by_model,
        }
    except Exception as e:
        return {"error": str(e)}


@fail_wire(module="cost_tracker", gap_type="engine_failure")
async def get_cumulative_aggregate() -> dict:
    """All-time per-feature totals. Survives index rotation since it's a
    separate key updated on every record_call."""
    await _flush_cost_pending(force=False)  # R-F2234: READ path — gated flush, no per-read RMW (see get_month_spend)
    try:
        return await rs.get_json(COST_AGG_KEY) or {}
    except Exception:
        return {}


@fail_wire(module="cost_tracker", gap_type="engine_failure")
async def list_recent_calls(
    limit: int = 30,
    feature_filter: str | None = None,
    model_filter: str | None = None,
) -> list[dict]:
    await _flush_cost_pending(force=False)  # R-F2234: READ path — gated flush, no per-read RMW (see get_month_spend)
    try:
        index = await rs.get_json(COST_INDEX_KEY) or []
        if feature_filter:
            index = [e for e in index if e.get("feature") == feature_filter]
        if model_filter:
            index = [e for e in index if e.get("model") == model_filter]
        return index[: max(1, min(limit, _INDEX_CAP))]
    except Exception:
        return []


@fail_wire(module="cost_tracker", gap_type="engine_failure")
async def get_call_record(call_id: str) -> dict | None:
    try:
        return await rs.get_json(f"{COST_RECORD_PREFIX}{call_id}")
    except Exception:
        return None
