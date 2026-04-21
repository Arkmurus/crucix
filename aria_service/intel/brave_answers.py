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

Memory doctrine (2026-04-21): every successful Brave answer is absorbed
into all three memory tiers (brain_hook → mastery + knowledge + neural,
rag_store for semantic recall, intel_ledger for permanent event log).
Next time a semantically-close question is asked, the memory-first path
returns the cached answer for $0 — the paid API is only called when
memory is empty. Paying $0.04 buys a permanent entry, not a one-shot
reply.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from . import redis_store as rs

logger = logging.getLogger("aria.brave_answers")

# Memory-first similarity threshold. chromadb cosine similarity ranges 0-1;
# an identical query scores 1.0, a paraphrase ~0.85-0.95, a merely related
# question ~0.6-0.8. 0.80 is the conservative cutoff that catches paraphrases
# without returning off-topic hits. Tune via env if needed.
_MEMORY_HIT_THRESHOLD = float(os.getenv("BRAVE_ANSWERS_MEMORY_HIT_THRESHOLD", "0.80"))

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


async def _memory_lookup(query: str) -> dict[str, Any] | None:
    """Semantic RAG search over prior brave_answer chunks.

    Returns a cached-answer dict when a sufficiently similar past
    Brave answer exists; None when we should call the API. The threshold
    is conservative to avoid serving wrong answers — a paraphrase of a
    known question hits memory, an unrelated query does not.
    """
    try:
        from . import rag_store as _rag
        hits = await _rag.search(query, top_k=3, source_type="brave_answer")
    except Exception as e:
        logger.debug("memory-first RAG lookup failed (non-fatal): %s", e)
        return None
    if not hits:
        return None
    top = hits[0]
    similarity = float(top.get("similarity") or 0.0)
    if similarity < _MEMORY_HIT_THRESHOLD:
        return None
    text = (top.get("text") or "").strip()
    if not text:
        return None
    # Strip the "Question: ...\n\nAnswer: " prefix we write at ingest time
    # so callers see the bare answer.
    answer = text
    if text.startswith("Question:") and "\n\nAnswer:" in text:
        answer = text.split("\n\nAnswer:", 1)[1].strip()
    return {
        "answer": answer,
        "similarity": round(similarity, 4),
        "ingested_at": top.get("ingested_at"),
        "source_id": top.get("source"),
    }


async def _absorb(query: str, api_result: dict[str, Any]) -> None:
    """Write a successful Brave answer to all three memory tiers.

    1. brain_hook.absorb → cascades into mastery + knowledge + neural
    2. rag_store.ingest_document → powers memory-first on next ask
    3. intel_ledger.add_signal → permanent event log

    All three are wrapped in try/except because:
      - absorption must never break the caller's response
      - these stores may be slow (ChromaDB embeddings); we log and move on
    """
    answer = (api_result.get("answer") or "").strip()
    if not answer:
        return

    qhash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    source_id = f"brave_answers:{qhash}"

    # Tier 1 — brain_hook (mastery + knowledge + neural in one call)
    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="brave_answers",
            summary=f"Q: {query[:160]}",
            detail=answer[:3000],
            success=True,
            source_id=source_id,
            confidence="ASSESSED",
        )
    except Exception as e:
        logger.warning("brave_answers brain_hook absorb failed: %s", e)

    # Tier 2 — rag_store (semantic search enables memory-first next time)
    try:
        from . import rag_store as _rag
        usage = api_result.get("usage") or {}
        await _rag.ingest_document(
            f"Question: {query}\n\nAnswer: {answer}",
            source=source_id,
            source_type="brave_answer",
            title=query[:200],
            url="",
            extra_metadata={
                "query": query[:500],
                "model": api_result.get("model") or "brave",
                "cost_usd": float(api_result.get("cost_usd") or 0.0),
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
            },
        )
    except Exception as e:
        logger.warning("brave_answers rag_store ingest failed: %s", e)

    # Tier 3 — intel_ledger (permanent event log)
    try:
        from . import intel_ledger as _il
        await _il.add_signal({
            "title": f"Brave Answer: {query[:120]}",
            "summary": answer[:500],
            "source": "brave_answers",
            "type": "answer",
            "severity": "info",
            "tags": ["brave_answers", "factual_qa"],
        })
    except Exception as e:
        logger.warning("brave_answers intel_ledger add_signal failed: %s", e)


async def ask(
    query: str,
    *,
    memory_first: bool = True,
    timeout: float = 25.0,
) -> dict[str, Any]:
    """Ask Brave's Answers API — with memory-first check and absorption.

    Flow:
      1. If memory_first=True, search RAG for prior Brave answers with
         semantic similarity ≥ _MEMORY_HIT_THRESHOLD. Hit → return cached
         answer for $0. Miss → fall through.
      2. Call the paid API (after spend-cap check).
      3. On success, absorb into brain_hook + rag_store + intel_ledger
         so next identical/paraphrased query hits memory.

    Returns:
        { ok, query, answer, model, source,
          cost_usd, duration_ms, usage?, spend_after?, error? }

        `source` is "memory" when served from RAG, "brave_api" on a
        paid call, "none" on failure. Callers who want to force a fresh
        API call can pass memory_first=False.

    When monthly spend ≥ budget, returns ok=False with error='budget_cap'
    so callers can fall back to web_search instead of blowing the cap.
    """
    t0 = time.time()
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "empty query", "duration_ms": 0}

    # ── 1. Memory-first — free recall from prior Brave answers ──────────
    if memory_first:
        cached = await _memory_lookup(query)
        if cached is not None:
            return {
                "ok": True,
                "query": query,
                "answer": cached["answer"],
                "model": "memory",
                "source": "memory",
                "cost_usd": 0.0,
                "duration_ms": int((time.time() - t0) * 1000),
                "memory_hit": {
                    "similarity": cached["similarity"],
                    "ingested_at": cached["ingested_at"],
                    "source_id": cached["source_id"],
                },
            }

    # ── 2. Paid API call ────────────────────────────────────────────────
    key = (os.getenv("BRAVE_ANSWERS_API_KEY") or "").strip()
    if not key:
        return {
            "ok": False, "error": "BRAVE_ANSWERS_API_KEY not set",
            "query": query, "source": "none", "duration_ms": 0,
        }

    spend = await get_month_spend()
    if spend["at_cap"]:
        logger.warning(
            "brave_answers budget cap hit — spend=$%.4f budget=$%.2f calls=%d",
            spend["spend_usd"], spend["budget_usd"], spend["call_count"],
        )
        return {
            "ok": False, "error": "budget_cap",
            "query": query, "spend": spend, "source": "none", "duration_ms": 0,
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
            "ok": False, "error": "timeout", "query": query, "source": "none",
            "duration_ms": int((time.time() - t0) * 1000),
        }
    except Exception as e:
        logger.exception("brave_answers request failed")
        return {
            "ok": False, "error": f"{type(e).__name__}: {e}",
            "query": query, "source": "none",
            "duration_ms": int((time.time() - t0) * 1000),
        }

    dur_ms = int((time.time() - t0) * 1000)
    if r.status_code != 200:
        logger.warning(
            "brave_answers HTTP %d for query=%r body=%s",
            r.status_code, query[:80], r.text[:200],
        )
        return {
            "ok": False, "error": f"http_{r.status_code}", "source": "none",
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

    result = {
        "ok": bool(answer),
        "query": query,
        "answer": answer,
        "model": data.get("model") or _MODEL,
        "source": "brave_api",
        "usage": usage,
        "cost_usd": round(cost, 6),
        "duration_ms": dur_ms,
        "spend_after": spend_after,
    }

    # ── 3. Absorb into memory so the $0.04 buys permanent recall ────────
    if result["ok"]:
        await _absorb(query, result)

    return result
