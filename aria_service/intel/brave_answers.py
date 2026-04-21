"""Brave Search — Answers API (OpenAI-compatible /chat/completions endpoint).

Separate product from Web Search:
  - Web Search (`/res/v1/web/search`) → list of link results
  - Answers     (`/res/v1/chat/completions`) → AI-generated answer + citations,
    grounded in Brave's search index. Single call replaces
    search→extract→summarize for factual queries.

Pricing (as of 2026-04): $4 per 1k requests + $5 per 1M prompt tokens.
Each call retrieves ~8k tokens of search context, so ~$0.04/call.
A $10/mo budget fits ~250 calls/mo — track spend in Redis so the cap
becomes a soft gate rather than an end-of-month billing surprise.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from . import redis_store as rs

logger = logging.getLogger("aria.brave_answers")

_ENDPOINT = "https://api.search.brave.com/res/v1/chat/completions"
_MODEL = "brave"  # response reports back "brave-pro" but request must say "brave"
_SPEND_KEY = "crucix:brave_answers:spend_usd:ym"  # key suffix is :YYYYMM
_COUNT_KEY = "crucix:brave_answers:count:ym"

# Per-call cost model — keep conservative. Brave's pricing can change;
# recompute from response.usage if it ever exposes cost.
_COST_PER_REQUEST = 0.004  # $4 / 1000 requests
_COST_PER_PROMPT_TOKEN = 5e-6  # $5 / 1M input tokens
_COST_PER_COMPLETION_TOKEN = 5e-6  # same, conservative

# Default $10 matches the user's spend-limit plan. Override via env.
_MONTHLY_BUDGET_USD = float(os.getenv("BRAVE_ANSWERS_MONTHLY_USD", "10.0"))


def _ym() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m")


def _estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return (
        _COST_PER_REQUEST
        + prompt_tokens * _COST_PER_PROMPT_TOKEN
        + completion_tokens * _COST_PER_COMPLETION_TOKEN
    )


async def get_month_spend() -> dict[str, Any]:
    """Return the current YTD spend + call count for the active month."""
    ym = _ym()
    spend_raw = await rs.get(f"{_SPEND_KEY}:{ym}")
    count_raw = await rs.get(f"{_COUNT_KEY}:{ym}")
    try:
        spend = float(spend_raw) if spend_raw is not None else 0.0
    except (TypeError, ValueError):
        spend = 0.0
    try:
        count = int(count_raw) if count_raw is not None else 0
    except (TypeError, ValueError):
        count = 0
    return {
        "ym": ym,
        "spend_usd": round(spend, 4),
        "call_count": count,
        "budget_usd": _MONTHLY_BUDGET_USD,
        "remaining_usd": round(max(0.0, _MONTHLY_BUDGET_USD - spend), 4),
        "at_cap": spend >= _MONTHLY_BUDGET_USD,
    }


async def _record_spend(cost: float) -> None:
    ym = _ym()
    # Redis INCRBYFLOAT + INCR — atomic and survives restarts. Keys
    # never expire (permanent-memory doctrine from 1f0b554 applies).
    try:
        await rs.incrbyfloat(f"{_SPEND_KEY}:{ym}", cost)
        await rs.incr(f"{_COUNT_KEY}:{ym}")
    except Exception as e:
        logger.warning("brave_answers spend record failed (non-fatal): %s", e)


async def ask(query: str, *, timeout: float = 25.0) -> dict[str, Any]:
    """Ask Brave's Answers API a question. Returns:

        { ok: bool, answer: str, model: str, usage: {...},
          cost_usd: float, query: str, error?: str, duration_ms: int,
          spend_after: {spend_usd, call_count, budget_usd, remaining_usd, at_cap} }

    Spend is tracked in Redis per-month. When spend ≥ budget, this
    function refuses to call and returns ok=False with error='budget_cap'
    so callers can fall back to web_search instead of blowing the cap.
    """
    t0 = time.time()
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "empty query", "duration_ms": 0}

    key = (os.getenv("BRAVE_ANSWERS_API_KEY") or "").strip()
    if not key:
        return {
            "ok": False, "error": "BRAVE_ANSWERS_API_KEY not set",
            "query": query, "duration_ms": 0,
        }

    # Soft cap — if we've already burned the monthly budget, refuse.
    spend = await get_month_spend()
    if spend["at_cap"]:
        logger.warning(
            "brave_answers budget cap hit — spend=$%.4f budget=$%.2f calls=%d",
            spend["spend_usd"], spend["budget_usd"], spend["call_count"],
        )
        return {
            "ok": False, "error": "budget_cap",
            "query": query, "spend": spend, "duration_ms": 0,
        }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                _ENDPOINT,
                headers={
                    "Content-Type": "application/json",
                    "X-Subscription-Token": key,
                },
                json={
                    "model": _MODEL,
                    "messages": [{"role": "user", "content": query[:2000]}],
                    "stream": False,
                },
            )
    except httpx.TimeoutException:
        return {
            "ok": False, "error": "timeout", "query": query,
            "duration_ms": int((time.time() - t0) * 1000),
        }
    except Exception as e:
        logger.exception("brave_answers request failed")
        return {
            "ok": False, "error": f"{type(e).__name__}: {e}",
            "query": query, "duration_ms": int((time.time() - t0) * 1000),
        }

    dur_ms = int((time.time() - t0) * 1000)
    if r.status_code != 200:
        logger.warning(
            "brave_answers HTTP %d for query=%r body=%s",
            r.status_code, query[:80], r.text[:200],
        )
        return {
            "ok": False, "error": f"http_{r.status_code}",
            "status_body": r.text[:400], "query": query, "duration_ms": dur_ms,
        }

    data = r.json()
    choices = data.get("choices") or []
    msg = (choices[0] or {}).get("message", {}) if choices else {}
    answer = (msg.get("content") or "").strip()
    usage = data.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    cost = _estimate_cost(prompt_tokens, completion_tokens)
    await _record_spend(cost)
    spend_after = await get_month_spend()

    return {
        "ok": bool(answer),
        "query": query,
        "answer": answer,
        "model": data.get("model") or _MODEL,
        "usage": usage,
        "cost_usd": round(cost, 6),
        "duration_ms": dur_ms,
        "spend_after": spend_after,
    }
