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
import time
import uuid
from contextlib import contextmanager
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.cost")

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
COST_TTL = 90 * 86400  # 90 days of per-call detail

_INDEX_CAP = 1000  # last N call summaries kept in the index


# ── Feature attribution via contextvar ─────────────────────────────────────
# contextvar (not threading.local) so it propagates correctly through
# asyncio tasks — research_tasks fires multiple parallel LLM calls and
# each needs the right feature label.
_current_feature: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aria_cost_feature", default="uncategorized",
)


def get_current_feature() -> str:
    return _current_feature.get()


def set_feature(name: str) -> contextvars.Token:
    """Set the active feature attribution. Returns a token the caller can
    use to reset later. Prefer the `feature()` context manager for paired
    set/reset semantics."""
    return _current_feature.set(name or "uncategorized")


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


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = _get_price(model)
    return round(
        (input_tokens / 1_000_000) * in_rate
        + (output_tokens / 1_000_000) * out_rate,
        6,
    )


# ── Recording ──────────────────────────────────────────────────────────────

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
    except Exception as e:
        logger.warning("cost_tracker.record_call persist failed: %s", e)
    return record


# ── Reporting ──────────────────────────────────────────────────────────────

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


async def get_cumulative_aggregate() -> dict:
    """All-time per-feature totals. Survives index rotation since it's a
    separate key updated on every record_call."""
    try:
        return await rs.get_json(COST_AGG_KEY) or {}
    except Exception:
        return {}


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


async def get_call_record(call_id: str) -> dict | None:
    try:
        return await rs.get_json(f"{COST_RECORD_PREFIX}{call_id}")
    except Exception:
        return None
