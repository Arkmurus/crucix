"""R-F2205 — local cross-encoder re-ranker for search / RAG candidates.

Re-orders candidates by semantic relevance to the query using a small CPU cross-encoder,
surfacing relevant-but-lexically-different sources that keyword + credibility ranking buries.

Design for SAFETY (this loads an ML model on the single-process brain):
  - ENV-GATED: OFF unless ARIA_RERANK_ENABLED=1, so it can be enabled after observing load.
  - LAZY: the model is loaded on first use only (never at import / boot).
  - OFFLOADED: model.predict runs in a thread so it never blocks the event loop.
  - SAFE NO-OP: returns the input order unchanged when disabled, empty, or unavailable;
    never raises.
  - §6-clean: local OSS model (sentence-transformers, already a dep), no paid service.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_MODEL = None
_MODEL_TRIED = False
_MODEL_NAME = os.getenv("ARIA_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
_MAX_CANDIDATES = int(os.getenv("ARIA_RERANK_MAX_CANDIDATES", "30"))


def is_enabled() -> bool:
    return os.getenv("ARIA_RERANK_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def _get_model():
    """Lazy-load the cross-encoder once. Returns None (re-ranking stays off) on any failure."""
    global _MODEL, _MODEL_TRIED
    if _MODEL is not None or _MODEL_TRIED:
        return _MODEL
    _MODEL_TRIED = True
    try:
        from sentence_transformers import CrossEncoder
        _MODEL = CrossEncoder(_MODEL_NAME, max_length=512)
        logger.info("[reranker] loaded cross-encoder %s", _MODEL_NAME)
        try:  # R-F2259 §21a — the reranker's readiness is observable
            from .engine_wiring import wire_success
            wire_success(module="reranker", summary=f"cross-encoder loaded: {_MODEL_NAME}",
                         source_id="reranker:_get_model")
        except Exception:
            pass
    except Exception as e:
        logger.warning("[reranker] model load failed (%s) — re-ranking stays off", e)
        _MODEL = None
        try:  # R-F2259 §21a — a failed load means ranking silently stays OFF; surface it
            from .engine_wiring import wire_failure
            wire_failure(module="reranker", detail=f"cross-encoder load failed: {e}",
                         gap_type="engine_failure", source="reranker:_get_model")
        except Exception:
            pass
    return _MODEL


def _text_of(c) -> str:
    if isinstance(c, dict):
        v = c.get("snippet") or c.get("text") or c.get("title") or ""
    else:
        v = getattr(c, "snippet", "") or getattr(c, "text", "") or getattr(c, "title", "")
    return str(v or "")[:2000]


async def rerank_results(query: str, candidates: list, *, top_k: int | None = None) -> list:
    """Re-order `candidates` by cross-encoder relevance to `query`.

    Returns the input (optionally truncated to top_k) UNCHANGED when disabled, empty, or the
    model is unavailable. Never raises. Only the head (ARIA_RERANK_MAX_CANDIDATES) is scored
    to bound the per-query cost; the tail keeps its original order.
    """
    if not candidates or not query or not is_enabled():
        return candidates[:top_k] if top_k else candidates
    # R-F2259 — offload the model LOAD off the event loop. The cold CrossEncoder load is
    # ~60s on the shared-cpu box; a synchronous _get_model() here would FREEZE the whole
    # single-process brain on the first search (R-F703 stall). prewarm() below normally
    # loads it at boot so this is a no-op cache hit, but offloading is the safety net.
    model = await asyncio.to_thread(_get_model)
    if model is None:
        return candidates[:top_k] if top_k else candidates
    head = candidates[:_MAX_CANDIDATES]
    tail = candidates[_MAX_CANDIDATES:]
    try:
        pairs = [[query, _text_of(c)] for c in head]
        scores = await asyncio.to_thread(model.predict, pairs)
        ranked = [c for c, _ in sorted(zip(head, scores), key=lambda z: float(z[1]), reverse=True)]
        out = ranked + tail
        return out[:top_k] if top_k else out
    except Exception as e:
        logger.debug("[reranker] rerank failed: %s", e)
        return candidates[:top_k] if top_k else candidates


async def prewarm() -> bool:
    """R-F2259 — load the cross-encoder in the BACKGROUND at boot (off the event loop) so
    the FIRST live search doesn't eat the ~60s cold model load. No-op when the reranker is
    disabled or the model is already loaded. Never raises. Returns True if the model is ready."""
    if not is_enabled() or _MODEL is not None:
        return _MODEL is not None
    try:
        m = await asyncio.to_thread(_get_model)
        if m is not None:
            logger.info("[reranker] prewarm complete — cross-encoder ready before first search")
        return m is not None
    except Exception as e:  # noqa: BLE001 — prewarm must never break boot
        logger.warning("[reranker] prewarm failed (%s) — will lazy-load on first search", e)
        return False
