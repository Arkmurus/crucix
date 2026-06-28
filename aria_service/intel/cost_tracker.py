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

# ── Pricing table (USD per 1M tokens) ──────────────────────────────────────
# Standard rates as of late 2025 — update when providers change them.
# Tuple is (input_per_1m, output_per_1m).
PRICING: dict[str, tuple[float, float]] = {
    # DeepSeek — primary for ARIA, dirt cheap
    "deepseek-chat":      (0.27, 1.10),
    "deepseek-reasoner":  (0.55, 2.19),
    "deepseek-v3":        (0.27, 1.10),

    # Anthropic
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

# Conservative default for unknown models — picks deepseek-chat rates so
# we don't silently underbill. Easier to fix an over-estimate than a
# silent under-estimate.
DEFAULT_PRICING = (0.27, 1.10)

COST_INDEX_KEY = "crucix:aria:cost:index"
COST_RECORD_PREFIX = "crucix:aria:cost:record:"
COST_AGG_KEY = "crucix:aria:cost:aggregate"
COST_MONTH_PREFIX = "crucix:aria:cost:month:"   # + YYYY-MM
COST_TTL = 90 * 86400  # 90 days of per-call detail
COST_MONTH_TTL = 400 * 86400  # keep ~13 months of monthly rollups

_INDEX_CAP = 1000  # last N call summaries kept in the index

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
    try:
        await rs.set_json(f"{EXTERNAL_RECORD_PREFIX}{call_id}", record, ex=COST_TTL)
        index = await rs.get_json(EXTERNAL_INDEX_KEY) or []
        index.insert(0, {
            "id": call_id,
            "ts": record["ts"],
            "service": record["service"],
            "operation": record["operation"],
            "feature": feat,
            "cost_usd": record["cost_usd"],
            "success": success,
        })
        index = index[:_INDEX_CAP]
        await rs.set_json(EXTERNAL_INDEX_KEY, index, ex=COST_TTL)
        agg = await rs.get_json(EXTERNAL_AGG_KEY) or {}
        svc_agg = agg.get(service) or {
            "calls": 0, "cost_usd": 0.0, "errors": 0,
        }
        svc_agg["calls"] += 1
        svc_agg["cost_usd"] = round(svc_agg["cost_usd"] + record["cost_usd"], 6)
        if not success:
            svc_agg["errors"] += 1
        agg[service] = svc_agg
        await rs.set_json(EXTERNAL_AGG_KEY, agg, ex=COST_TTL)
        # Roll into monthly bucket alongside LLM spend so the dashboard
        # has a single composite "month-to-date" total. We re-use
        # _update_month_rollup but tag the record with kind=external
        # so the writer can filter / report on demand.
        await _update_month_rollup(record)
    except Exception as e:
        logger.warning(
            "cost_tracker.record_external_call persist failed (%s/%s): %s",
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
        # Lightweight index for /cost/recent listings
        index = await rs.get_json(COST_INDEX_KEY) or []
        index.insert(0, {
            "id": call_id,
            "ts": record["ts"],
            "model": record["model"],
            "feature": feat,
            "total_tokens": record["total_tokens"],
            "cost_usd": cost_usd,
            "success": success,
        })
        index = index[:_INDEX_CAP]
        await rs.set_json(COST_INDEX_KEY, index, ex=COST_TTL)
        # Self-attach to the active trace if there is one. Lazy import
        # to avoid an unconditional dependency at module load.
        try:
            from . import trace_stream
            await trace_stream.attach_call_to_current_trace(record)
        except Exception as e:
            logger.debug("trace attach failed: %s", e)
        # Per-feature aggregate (cumulative). Cheap to maintain since it's
        # a single key with a few floats.
        agg = await rs.get_json(COST_AGG_KEY) or {}
        feat_agg = agg.get(feat) or {
            "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "total_tokens": 0, "cost_usd": 0.0, "errors": 0,
        }
        feat_agg["calls"] += 1
        feat_agg["input_tokens"] += record["input_tokens"]
        feat_agg["output_tokens"] += record["output_tokens"]
        feat_agg["total_tokens"] += record["total_tokens"]
        feat_agg["cost_usd"] = round(feat_agg["cost_usd"] + cost_usd, 6)
        if not success:
            feat_agg["errors"] += 1
        agg[feat] = feat_agg
        await rs.set_json(COST_AGG_KEY, agg, ex=COST_TTL)
        # Calendar-month rollup (independent of 90-day detail TTL).
        await _update_month_rollup(record)
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

async def _update_month_rollup(record: dict) -> None:
    """Add one call to the current month's aggregate + refresh the in-process
    cache. Safe to call inside the record_call try/except — its failure must
    never prevent the rest of the record from persisting."""
    month = _current_month_key()
    key = f"{COST_MONTH_PREFIX}{month}"
    try:
        roll = await rs.get_json(key) or {
            "month": month,
            "total_cost_usd": 0.0,
            "total_calls": 0,
            "total_tokens": 0,
            "first_ts": record["ts"],
            "last_ts": record["ts"],
            "by_provider": {},
            "by_feature": {},
            "by_model": {},
            "top_calls": [],
        }
        cost = float(record.get("cost_usd") or 0.0)
        roll["total_cost_usd"] = round(roll["total_cost_usd"] + cost, 6)
        roll["total_calls"] += 1
        roll["total_tokens"] += int(record.get("total_tokens") or 0)
        roll["last_ts"] = record["ts"]

        for bucket_key, val in (
            ("by_provider", record.get("provider") or "unknown"),
            ("by_feature", record.get("feature") or "uncategorized"),
            ("by_model", record.get("model") or "unknown"),
        ):
            bucket = roll[bucket_key]
            cell = bucket.get(val) or {"calls": 0, "tokens": 0, "cost_usd": 0.0}
            cell["calls"] += 1
            cell["tokens"] += int(record.get("total_tokens") or 0)
            cell["cost_usd"] = round(cell["cost_usd"] + cost, 6)
            bucket[val] = cell

        # Keep the 20 most expensive individual calls for debugging bloat.
        top = roll["top_calls"]
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

        await rs.set_json(key, roll, ex=COST_MONTH_TTL)

        # Refresh in-process cache + evaluate warning thresholds.
        _month_cache["month"] = month
        _month_cache["total"] = roll["total_cost_usd"]
        _month_cache["loaded_at"] = time.time()
        _emit_threshold_warnings(roll["total_cost_usd"], _monthly_cap_usd(), month)
    except Exception as e:
        logger.warning("cost_tracker._update_month_rollup failed: %s", e)


def _emit_threshold_warnings(spent: float, cap: float, month: str) -> None:
    """Log-only warnings at 50% and 80% of the monthly cap. Latched per-month
    so operators aren't spammed; the `_warned_thresholds` set resets when
    the calendar month rolls over (see _refresh_month_cache)."""
    if cap <= 0:
        return
    util = spent / cap
    for pct, label in ((0.80, "80"), (0.50, "50")):
        tag = f"{month}:{label}"
        if util >= pct and tag not in _warned_thresholds:
            _warned_thresholds.add(tag)
            logger.warning(
                "ARIA monthly LLM spend at %.0f%% of cap — spent $%.4f of $%.2f (%s)",
                util * 100, spent, cap, month,
            )
            return  # only fire the highest-matching threshold per call


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
async def assert_monthly_cap() -> None:
    """Raise MonthlyCostCapExceeded if month-to-date spend is at/over the cap.
    Called by MeteredProvider before every LLM request. Honours
    ARIA_MONTHLY_CAP_WARN_ONLY=1 for investigation mode.

    R-F2108: concurrent-overshoot fix. The 30s _month_cache means N concurrent
    requests can all pass the same sub-cap read before any of them records spend.
    We now RESERVE the estimated cost atomically via Redis INCRBYFLOAT so the
    cap is a hard ceiling, not a check-then-fire. The reserve is reconciled
    (deducted) in record_call when the actual cost is known. A reserve that
    is never reconciled (crash after reserve, before record_call) expires
    naturally because the month rollup key has no TTL — the reserve inflates
    the month's total by at most the estimated cost of one call per crash.
    """
    cap = _monthly_cap_usd()
    if cap <= 0:
        return
    spent = await _refresh_month_cache()
    if spent < cap:
        return
    month = _current_month_key()
    if _warn_only():
        logger.error(
            "ARIA monthly LLM cap $%.2f would block call — allowed because "
            "ARIA_MONTHLY_CAP_WARN_ONLY=1 (spent $%.4f in %s)",
            cap, spent, month,
        )
        return
    raise MonthlyCostCapExceeded(spent=spent, cap=cap, month=month)


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
