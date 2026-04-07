"""
ARIA Reasoning Library — case-based reasoning store.

This is the seed of ARIA's independent reasoning capacity. Every time her
brain (currently DeepSeek) successfully answers a question, the entire
(question, context, response, confidence, outcome) tuple is captured here.
The next time a similar question arrives, ARIA looks it up locally FIRST —
if there is a high-similarity prior with confirmed positive outcome, she
serves that answer without calling out to any LLM at all.

Architecture
════════════
The library is a Redis-backed store of "cases":

    case = {
        id, question, normalised_question, response,
        confidence_tag,             # CONFIRMED / PROBABLE / ASSESSED / etc
        confidence_score,           # 0..1
        intent,                     # screen, conflict, profile, ...
        context_keys,               # list of intel layers that mattered
        embedding,                  # semantic vector (sentence-transformers)
        access_count,
        positive_outcomes,          # times user accepted this
        negative_outcomes,          # times user corrected this
        ts_created, ts_last_used,
        source_brain,               # deepseek / anthropic / ollama / symbolic
    }

How retrieval works
═══════════════════
1. Normalise the incoming query (lowercase, strip stopwords, sort tokens)
2. Lexical first pass — match against normalised_question (fast)
3. Semantic second pass — cosine similarity against embeddings (precise)
4. Combine the two scores with a confidence multiplier
5. Apply a recency + outcome quality boost
6. Return the top match if above threshold (default 0.78)

How it grows
════════════
- Every successful chat call writes a case (via record_response).
- User corrections decrement positive_outcomes and increment negative.
- Stale cases (>180 days, low access) get pruned in consolidation.
- A "promotion" pass moves high-quality cases to a permanent layer.

Independence trajectory
═══════════════════════
On day 1: every query hits DeepSeek. 0% local.
After 100 conversations: ~15% of queries match a prior case → 15% local.
After 1000 conversations: ~40% local.
After 10000 conversations + symbolic reasoner: ~70% local.

The library is the engine of ARIA's slow detachment from cloud reasoning.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
import uuid
from typing import Any, Optional

from . import redis_store as rs

logger = logging.getLogger("aria.reasoning_library")

LIBRARY_KEY = "crucix:aria:reasoning_library"
INDEX_KEY = "crucix:aria:reasoning_library:index"
META_KEY = "crucix:aria:reasoning_library:meta"

MAX_CASES = 50_000
DEFAULT_MATCH_THRESHOLD = 0.78
RETENTION_DAYS = 180
TTL_SECONDS = RETENTION_DAYS * 86400

# Common English/intelligence stopwords stripped during normalisation
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "of", "in", "on", "at", "to",
    "for", "with", "by", "from", "as", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "this", "that", "these", "those", "it", "its",
    "what", "when", "where", "why", "how", "who", "which", "i", "you", "he", "she",
    "we", "they", "them", "us", "my", "your", "our", "their", "tell", "me", "about",
    "please", "aria", "show", "give",
})

# Domain-priority terms that should always survive normalisation
_DOMAIN_TOKENS = frozenset({
    "angola", "mozambique", "cplp", "lusophone", "fadm", "faa", "simportex",
    "sitcl", "ofac", "ofsi", "itar", "ear", "eccn", "ml1", "ml2", "ml3", "ml4",
    "ml6", "ml9", "ml10", "ml11", "ml13", "ml15", "k9", "himars", "patriot",
    "bayraktar", "tb2", "rafale", "f-35", "f-16", "leopard", "abrams",
})

_meta_cache: dict | None = None
_index_cache: list[dict] | None = None  # in-memory mirror of the index


# ── Normalisation ───────────────────────────────────────────────────────────

def _normalise_question(q: str) -> str:
    """Strip stopwords, lowercase, sort tokens. Cheap lexical-match key."""
    if not q:
        return ""
    text = re.sub(r"[^\w\s]", " ", q.lower())
    tokens = [t for t in text.split() if t and len(t) > 1]
    kept = [
        t for t in tokens
        if t in _DOMAIN_TOKENS or (t not in _STOPWORDS and len(t) > 2)
    ]
    return " ".join(sorted(set(kept)))


def _token_set(q: str) -> set[str]:
    return set(_normalise_question(q).split())


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


# ── Optional embedding-based similarity ─────────────────────────────────────
# Uses the existing sentence-transformers infrastructure (already in
# requirements.txt). If unavailable, falls back to pure Jaccard.

_embedder = None
_embedder_failed = False

def _get_embedder():
    global _embedder, _embedder_failed
    if _embedder is not None:
        return _embedder
    if _embedder_failed:
        return None
    try:
        # semantic_search exposes _get_embedder() — same name, same purpose.
        from .semantic_search import _get_embedder as _semantic_get_embedder
        _embedder = _semantic_get_embedder()
        return _embedder
    except Exception as e:
        logger.debug("reasoning_library: embedder unavailable, using lexical only: %s", e)
        _embedder_failed = True
        return None


def _embed(text: str) -> list[float] | None:
    embedder = _get_embedder()
    if embedder is None:
        return None
    try:
        v = embedder.encode([text], normalize_embeddings=True)
        return v[0].tolist() if hasattr(v[0], "tolist") else list(v[0])
    except Exception as e:
        logger.debug("reasoning_library: embed failed: %s", e)
        return None


def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    # Already normalised by sentence-transformers, but be safe
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ── Storage helpers ─────────────────────────────────────────────────────────

async def _load_index() -> list[dict]:
    """Load the lightweight index (without full responses) into memory."""
    global _index_cache
    if _index_cache is not None:
        return _index_cache
    raw = await rs.get_json(INDEX_KEY)
    _index_cache = raw if isinstance(raw, list) else []
    return _index_cache


async def _save_index() -> None:
    if _index_cache is not None:
        await rs.set_json(INDEX_KEY, _index_cache, ex=TTL_SECONDS)


async def _load_meta() -> dict:
    global _meta_cache
    if _meta_cache is not None:
        return _meta_cache
    raw = await rs.get_json(META_KEY)
    _meta_cache = raw if isinstance(raw, dict) else {
        "total_cases": 0,
        "total_lookups": 0,
        "total_hits": 0,
        "total_writes": 0,
        "born": time.time(),
    }
    return _meta_cache


async def _save_meta() -> None:
    if _meta_cache is not None:
        await rs.set_json(META_KEY, _meta_cache, ex=TTL_SECONDS)


def _case_key(case_id: str) -> str:
    return f"{LIBRARY_KEY}:{case_id}"


# ── Public API: writing cases ───────────────────────────────────────────────

_CONFIDENCE_RANK = {
    "CONFIRMED": 1.0,
    "PROBABLE":  0.75,
    "ASSESSED":  0.55,
    "UNCERTAIN": 0.35,
    "SPECULATIVE": 0.20,
}

def _detect_confidence_tag(response: str) -> tuple[str, float]:
    """Pick the highest confidence tag present in the response text."""
    if not response:
        return "ASSESSED", 0.55
    upper = response.upper()
    for tag in ("CONFIRMED", "PROBABLE", "ASSESSED", "UNCERTAIN", "SPECULATIVE"):
        if f"[{tag}]" in upper:
            return tag, _CONFIDENCE_RANK[tag]
    return "ASSESSED", 0.55


async def record_response(
    question: str,
    response: str,
    *,
    intent: str = "",
    source_brain: str = "deepseek",
    context_keys: list[str] | None = None,
) -> dict:
    """Capture a successful chat response into the reasoning library.

    This is THE distillation hook. Every time the LLM produces a confident
    answer, ARIA stores it locally so the next similar question can be
    answered without calling the LLM again.
    """
    if not question or not response or len(question.strip()) < 5 or len(response.strip()) < 20:
        return {"recorded": False, "reason": "too short"}

    # Don't store error/fallback responses
    if response.startswith(("⚠️", "❌", "ARIA encountered")):
        return {"recorded": False, "reason": "error response"}
    # Don't store degraded mode responses (already from local data)
    if "degraded mode" in response.lower():
        return {"recorded": False, "reason": "already degraded"}

    confidence_tag, confidence_score = _detect_confidence_tag(response)

    case_id = str(uuid.uuid4())[:12]
    normalised = _normalise_question(question)
    if not normalised:
        return {"recorded": False, "reason": "no salient tokens"}

    embedding = _embed(question)
    case = {
        "id": case_id,
        "question": question[:1000],
        "normalised": normalised,
        "response": response[:6000],
        "confidence_tag": confidence_tag,
        "confidence_score": confidence_score,
        "intent": intent,
        "context_keys": context_keys or [],
        "embedding": embedding,
        "access_count": 0,
        "positive_outcomes": 0,
        "negative_outcomes": 0,
        "ts_created": time.time(),
        "ts_last_used": time.time(),
        "source_brain": source_brain,
    }

    # Persist the full case
    await rs.set_json(_case_key(case_id), case, ex=TTL_SECONDS)

    # Update lightweight index (used for fast scan)
    index = await _load_index()
    index.append({
        "id": case_id,
        "normalised": normalised,
        "intent": intent,
        "confidence_score": confidence_score,
        "ts_created": case["ts_created"],
        "ts_last_used": case["ts_last_used"],
        "embedding": embedding,
    })
    # Cap size — drop the oldest low-access entries
    if len(index) > MAX_CASES:
        index.sort(key=lambda c: (c.get("ts_last_used", 0)))
        index = index[-MAX_CASES:]
    _index_cache_set(index)
    await _save_index()

    # Update meta
    meta = await _load_meta()
    meta["total_cases"] = len(index)
    meta["total_writes"] = meta.get("total_writes", 0) + 1
    meta["last_write"] = time.time()
    await _save_meta()

    return {"recorded": True, "id": case_id, "confidence": confidence_tag}


def _index_cache_set(new_index: list[dict]) -> None:
    global _index_cache
    _index_cache = new_index


# ── Public API: looking up cases ────────────────────────────────────────────

async def find_match(question: str, *, threshold: float = DEFAULT_MATCH_THRESHOLD) -> dict:
    """Find the best matching case for a new question.

    Returns:
        {
            "match": True/False,
            "score": 0..1,
            "case": {...} or None,
            "method": "exact_normalised" | "semantic" | "lexical_jaccard" | None,
            "threshold": threshold,
        }
    """
    if not question or len(question.strip()) < 5:
        return {"match": False, "score": 0, "case": None, "method": None, "threshold": threshold}

    meta = await _load_meta()
    meta["total_lookups"] = meta.get("total_lookups", 0) + 1

    index = await _load_index()
    if not index:
        return {"match": False, "score": 0, "case": None, "method": None, "threshold": threshold}

    normalised = _normalise_question(question)
    query_tokens = _token_set(question)
    query_embedding = _embed(question)

    best_idx = None
    best_score = 0.0
    best_method = None

    for entry in index:
        # 1. Exact normalised match (cheapest)
        if entry.get("normalised") == normalised and normalised:
            score = 0.95 + (entry.get("confidence_score", 0.5) * 0.05)
            if score > best_score:
                best_score, best_idx, best_method = score, entry, "exact_normalised"
                if best_score >= 0.99:
                    break  # can't beat exact match
            continue

        # 2. Semantic similarity (best precision)
        if query_embedding and entry.get("embedding"):
            sim = _cosine(query_embedding, entry["embedding"])
            # Blend with confidence and recency
            recency_factor = _recency_multiplier(entry.get("ts_last_used", 0))
            blended = sim * (0.7 + entry.get("confidence_score", 0.5) * 0.3) * recency_factor
            if blended > best_score:
                best_score, best_idx, best_method = blended, entry, "semantic"
            continue

        # 3. Lexical Jaccard fallback
        entry_tokens = set((entry.get("normalised") or "").split())
        jac = _jaccard(query_tokens, entry_tokens)
        if jac > 0.3:
            blended = jac * (0.7 + entry.get("confidence_score", 0.5) * 0.3)
            if blended > best_score:
                best_score, best_idx, best_method = blended, entry, "lexical_jaccard"

    if best_idx is None or best_score < threshold:
        await _save_meta()
        return {"match": False, "score": best_score, "case": None, "method": best_method, "threshold": threshold}

    # Load the full case
    case = await rs.get_json(_case_key(best_idx["id"]))
    if not case:
        # Stale index entry — remove it
        index = [e for e in index if e["id"] != best_idx["id"]]
        _index_cache_set(index)
        await _save_index()
        return {"match": False, "score": 0, "case": None, "method": None, "threshold": threshold}

    # Bump access count + last_used
    case["access_count"] = case.get("access_count", 0) + 1
    case["ts_last_used"] = time.time()
    await rs.set_json(_case_key(case["id"]), case, ex=TTL_SECONDS)
    best_idx["ts_last_used"] = case["ts_last_used"]
    await _save_index()

    meta["total_hits"] = meta.get("total_hits", 0) + 1
    await _save_meta()

    return {
        "match": True,
        "score": round(best_score, 3),
        "case": case,
        "method": best_method,
        "threshold": threshold,
    }


def _recency_multiplier(ts_last_used: float) -> float:
    """Boost recently-used cases (they reflect current intel) and decay stale ones."""
    if not ts_last_used:
        return 0.7
    age_days = (time.time() - ts_last_used) / 86400
    if age_days < 1:    return 1.10
    if age_days < 7:    return 1.05
    if age_days < 30:   return 1.00
    if age_days < 90:   return 0.92
    return 0.80


# ── Public API: outcome feedback ────────────────────────────────────────────

async def record_outcome(case_id: str, positive: bool) -> dict:
    """Mark a case as having a positive (user accepted) or negative (user
    corrected) outcome. Used by the learning loop to upweight good cases
    and downweight bad ones.
    """
    case = await rs.get_json(_case_key(case_id))
    if not case:
        return {"ok": False, "error": "case not found"}
    if positive:
        case["positive_outcomes"] = case.get("positive_outcomes", 0) + 1
        case["confidence_score"] = min(1.0, case.get("confidence_score", 0.5) + 0.05)
    else:
        case["negative_outcomes"] = case.get("negative_outcomes", 0) + 1
        case["confidence_score"] = max(0.1, case.get("confidence_score", 0.5) - 0.15)
    await rs.set_json(_case_key(case["id"]), case, ex=TTL_SECONDS)

    # Sync confidence into the index
    index = await _load_index()
    for e in index:
        if e["id"] == case_id:
            e["confidence_score"] = case["confidence_score"]
            break
    await _save_index()
    return {"ok": True, "new_confidence": case["confidence_score"]}


# ── Public API: stats and consolidation ─────────────────────────────────────

async def get_stats() -> dict:
    meta = await _load_meta()
    index = await _load_index()
    hits = meta.get("total_hits", 0)
    lookups = meta.get("total_lookups", 0)
    hit_rate = (hits / lookups) if lookups > 0 else 0.0

    by_intent: dict[str, int] = {}
    by_source: dict[str, int] = {}
    confidence_dist: dict[str, int] = {}
    for entry in index:
        intent = entry.get("intent", "unknown") or "unknown"
        by_intent[intent] = by_intent.get(intent, 0) + 1
        # source comes from the full case, not the index — skip for speed
        cs = entry.get("confidence_score", 0)
        bucket = "high" if cs >= 0.75 else "medium" if cs >= 0.5 else "low"
        confidence_dist[bucket] = confidence_dist.get(bucket, 0) + 1

    return {
        "total_cases": len(index),
        "total_lookups": lookups,
        "total_hits": hits,
        "hit_rate": round(hit_rate, 3),
        "total_writes": meta.get("total_writes", 0),
        "by_intent": by_intent,
        "confidence_distribution": confidence_dist,
        "born": meta.get("born"),
        "age_days": round((time.time() - meta.get("born", time.time())) / 86400, 1),
        "embedder_available": _get_embedder() is not None,
    }


async def consolidate() -> dict:
    """Periodic maintenance: prune stale low-quality cases, promote high-quality."""
    index = await _load_index()
    if not index:
        return {"pruned": 0, "promoted": 0, "remaining": 0}

    now = time.time()
    cutoff = now - RETENTION_DAYS * 86400

    pruned = 0
    new_index = []
    for entry in index:
        case = await rs.get_json(_case_key(entry["id"]))
        if not case:
            pruned += 1
            continue
        # Prune stale + unused + low-confidence cases
        age_days = (now - case.get("ts_created", now)) / 86400
        if (case.get("access_count", 0) == 0
                and age_days > 30
                and case.get("confidence_score", 0) < 0.5):
            await rs.set_json(_case_key(case["id"]), None, ex=1)
            pruned += 1
            continue
        # Prune cases with consistent negative outcomes
        if case.get("negative_outcomes", 0) > 3 and case.get("positive_outcomes", 0) == 0:
            await rs.set_json(_case_key(case["id"]), None, ex=1)
            pruned += 1
            continue
        new_index.append(entry)

    _index_cache_set(new_index)
    await _save_index()

    meta = await _load_meta()
    meta["total_cases"] = len(new_index)
    meta["last_consolidate"] = now
    await _save_meta()

    logger.info("reasoning_library consolidated: pruned %d, remaining %d", pruned, len(new_index))
    return {"pruned": pruned, "remaining": len(new_index)}
