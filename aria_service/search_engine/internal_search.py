"""internal_search — query the curated index.

R-F503 (2026-05-14). This is the query engine that pairs with
`aria_service/search_index/db.py` (R-F500) and the crawler/indexer
(R-F501/F502). It's what chat will call instead of (or alongside) the
legacy `web_search.search()`.

Drop-in shape: returns the same dict keys that `web_search.SearchResult`
exposes via `to_dict()`, so callers don't have to branch:
    {title, url, snippet, source, credibility_tier, language,
     relevance_score}

What this layer adds over raw `db.search_fts`:
  1. Drop-in API surface compatible with web_search.search().
  2. Recency boost — small additive bonus for documents fetched in the
     last 14 days. Doesn't dominate; just breaks ties.
  3. Tier boost — official (tier 1) + sectoral (tier 2) get a small
     additive bonus over tier 3+ at equal BM25.
  4. Source-aware deduplication — collapse near-duplicates by domain
     (keep best-scoring per domain when the corpus has many sister
     pages on the same story).
  5. coverage_report() — structured trace to expose WHICH index slice
     answered, how many candidates considered, which filters were
     applied, what the user can ask for to widen the search.

Phase 3 (later R-numbers) will add: vector retrieval fused with BM25,
entity-direct lookups, homograph detector. For now Phase 1 ships a
clean BM25 + recency + tier engine — already enough to win the
Modirum-Gespi case because the curated index doesn't have the
academic-variable noise that broke chat originally.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from aria_service.search_index import db
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.search_engine.internal_search")


# Tier-bias additive bonus, applied after BM25 inversion. Small enough
# to not flip clearly-better hits, large enough to break ties.
_TIER_BONUS = {1: 0.30, 2: 0.20, 3: 0.10, 4: 0.0, 5: -0.10}
_RECENCY_HALFLIFE_DAYS = 14.0


def _recency_bonus(fetched_at: float | None) -> float:
    """Linear-ish bonus that fades to ~0 after 14 days. Caps at 0.15."""
    if not fetched_at:
        return 0.0
    age_days = (time.time() - fetched_at) / 86400.0
    if age_days <= 0:
        return 0.15
    if age_days >= _RECENCY_HALFLIFE_DAYS:
        return 0.0
    return 0.15 * (1.0 - age_days / _RECENCY_HALFLIFE_DAYS)


def _adjusted_score(row: dict) -> float:
    """BM25-derived score + tier bonus + recency bonus."""
    base = float(row.get("score") or 0.0)  # already inverted (bigger=better)
    tier = row.get("source_tier") or 4
    return base + _TIER_BONUS.get(int(tier), 0.0) + _recency_bonus(
        row.get("fetched_at"))


@fail_wire(module="internal_search", gap_type="source_failure")
async def search(
    query: str,
    *,
    max_results: int = 10,
    language: str | None = "en",
    min_credibility: int = 6,
    require_triangulation: bool = False,
    dedupe_by_domain: bool = True,
) -> list[dict]:
    """Query the curated index and return ranked results.

    Args:
      query: free-text query (sanitised internally for FTS5 safety).
      max_results: cap on the returned list.
      language: when set, restrict to documents with that language tag.
        Pass None to search across all languages.
      min_credibility: tier ceiling — drop docs whose source_tier is
        worse than this (higher number = worse). Default 6 = no filter.
      require_triangulation: kept for shape compatibility with
        web_search.search(); not used in Phase 1 (we have one corpus,
        not multiple backends to triangulate). Phase 3 will use it.
      dedupe_by_domain: keep at most one result per domain (highest-
        scoring). Avoids one site flooding the result list.

    Returns:
      List of dicts shaped like web_search.SearchResult.to_dict().
      Empty list when the engine is unready, the query is empty, or
      nothing matches.
    """
    if not query or not query.strip():
        return []
    if db.get_connection() is None:
        # Engine not initialised. The chat-path caller treats this as
        # "fall back to legacy web search" — same as a 0-result return.
        logger.debug("internal_search: db not ready, returning []")
        return []

    # Pull a deeper candidate set than max_results so the post-FTS5
    # tier/recency/dedup re-rank can do useful work.
    candidate_k = max(max_results * 4, 30)
    min_tier = min_credibility if min_credibility < 6 else None

    raw = await db.search_fts(
        query=query, top_k=candidate_k,
        lang=language if language else None,
        min_tier=min_tier,
    )
    if not raw:
        return []

    # Apply tier + recency adjustments.
    scored: list[dict] = []
    for r in raw:
        r2 = dict(r)
        r2["_adjusted"] = _adjusted_score(r)
        scored.append(r2)

    # Optional dedupe-by-domain.
    if dedupe_by_domain:
        seen: dict[str, dict] = {}
        for r in scored:
            d = r.get("domain") or ""
            cur = seen.get(d)
            if cur is None or r["_adjusted"] > cur["_adjusted"]:
                seen[d] = r
        scored = list(seen.values())

    scored.sort(key=lambda r: r["_adjusted"], reverse=True)
    top = scored[:max_results]

    return [_to_dict(r) for r in top]


def _to_dict(r: dict) -> dict:
    return {
        "title": r.get("title") or "",
        "url": r.get("url") or "",
        "snippet": r.get("snippet") or "",
        "source": "aria_internal_index",
        "credibility_tier": int(r.get("source_tier") or 4),
        "language": r.get("language") or "",
        "relevance_score": round(float(r.get("_adjusted") or 0.0), 4),
        "fetched_at": r.get("fetched_at"),
        "domain": r.get("domain"),
    }


# ─────────────────────────────────────────────────────────────────
# Coverage report
# ─────────────────────────────────────────────────────────────────

@fail_wire(module="internal_search", gap_type="source_failure")
async def coverage_report(query: str | None = None) -> dict:
    """Structured trace for operator + UI. Aiming to replace the silent
    "0 results" failure mode with: 'we searched N docs across M domains
    in K languages; the topic isn't in our curated index yet'.

    When `query` is given, also reports how many candidates the FTS5
    query produced before filtering — useful for debugging why a
    specific query returned thin results.
    """
    out: dict[str, Any] = {
        "engine": "aria_internal_index",
        "ready": db.get_connection() is not None,
    }
    if not out["ready"]:
        return out
    try:
        s = await db.stats()
        out.update({
            "indexed_documents": s.get("documents", 0),
            "domains_enabled": s.get("domains_enabled", 0),
            "by_language": s.get("by_language", {}),
            "by_tier": s.get("by_tier", {}),
            "oldest_fetch": s.get("oldest_fetch"),
            "newest_fetch": s.get("newest_fetch"),
        })
    except Exception as e:
        out["error"] = str(e)

    if query:
        try:
            raw = await db.search_fts(query, top_k=100)
            out["query"] = query
            out["raw_candidates"] = len(raw)
            tiers = {}
            langs = {}
            for r in raw:
                t = r.get("source_tier")
                tiers[f"tier_{t}"] = tiers.get(f"tier_{t}", 0) + 1
                l = r.get("language") or "unknown"
                langs[l] = langs.get(l, 0) + 1
            out["candidates_by_tier"] = tiers
            out["candidates_by_language"] = langs
        except Exception as e:
            out["query_error"] = str(e)
    return out
