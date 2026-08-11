"""search_searxng — SearXNG 

free web-search adapter (R-F86, 2026-05-09).

Why this module exists
──────────────────────
Phase 1 of the independence roadmap requires a free general-purpose
web search to replace Brave Search (paid; circuit-breaker OPEN).
SearXNG is a self-hostable metasearch aggregator that queries Google /
Bing / DuckDuckGo / Brave / etc. without exposing the user's IP and
without an API key. We deploy SearXNG on a Fly.io machine and point
this adapter at it.

Env-gated on SEARXNG_URL. When unset, the adapter returns a clean
"not configured" result and the existing search_doctrine fallback chain
keeps using its current backends (OpenAlex / Semantic Scholar /
CrossRef / Google News). This is behaviour-neutral until the operator
deploys SearXNG and sets the URL.

Public API
──────────
    is_configured() -> bool
    search(query: str, *, count: int = 10, lang: str = 'en') -> dict
    summary() -> dict"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import asyncio
import logging
import os
from typing import Any
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.search_searxng")

# R-F1873: MUST stay below web_search.REQUEST_TIMEOUT (12s) — the parent search
# gather wraps every backend in asyncio.wait_for(REQUEST_TIMEOUT). If SearXNG's
# own HTTP timeout is longer, a slow SearXNG call is cancelled by the gather
# before it can return OR fail cleanly, so the PRIMARY self-host backend silently
# contributes nothing. 10s leaves ~2s headroom for result parsing within the
# gather window. (See web_search.py:82 REQUEST_TIMEOUT — keep these in sync.)
_DEFAULT_TIMEOUT = 10.0
_USER_AGENT = "AriaIntelligence/1.0 (defence-DD; aria@arkmurus.com)"

# R-F2938 — bound concurrency against the SELF-HOSTED SearXNG instance.
#
# Why: a DD's adverse-media deep search fires up to 30 query templates through an
# unbounded asyncio.gather (researcher.py). Every template hits `search()`, and
# SearXNG is a single self-hosted box on the private network — the 30-way
# stampede rate-limits it, the caller-side circuit breaker
# (web_search._search_searxng) trips OPEN after 3 consecutive failures, and the
# REMAINING templates skip. That set `partial=True` on the adverse-media blob
# even when Brave (the paid PRIMARY backend) had answered — and the Grade-A
# readiness grader reads `partial` as "adverse-media UNRESOLVED", capping the
# report. Live 2026-07-23 on Chemring: circuit_breaker_skips=4, partial=True,
# 7 real findings, yet the question graded UNRESOLVED.
#
# This is a self-inflicted DOS on our OWN backend — the same class as the
# state_store writer wedge — so the fix is at the source: serialise access so
# SearXNG is never asked to serve more than N at once. It stays a real second
# backend (corroboration value); it just stops being stampeded. Mirrors the
# existing researcher._doc_sem pattern. Module-level so ALL callers share ONE
# limiter (a per-call semaphore would not bound the fan-out). Lazily created so
# it binds to the running loop, and cached per-loop so tests / a loop swap don't
# reuse a semaphore bound to a dead loop.
_SEARXNG_CONCURRENCY = max(1, int(os.getenv("ARIA_SEARXNG_CONCURRENCY", "4") or "4"))
_searxng_sem: "asyncio.Semaphore | None" = None
_searxng_sem_loop: object = None


def _get_searxng_sem() -> "asyncio.Semaphore":
    global _searxng_sem, _searxng_sem_loop
    # Always called from inside async search(), so a loop is running. Bind the
    # semaphore to THIS loop and rebuild if the loop changed (tests, restart) —
    # a semaphore bound to a dead loop would raise on acquire.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _searxng_sem is None or _searxng_sem_loop is not loop:
        _searxng_sem = asyncio.Semaphore(_SEARXNG_CONCURRENCY)
        _searxng_sem_loop = loop
    return _searxng_sem


@fail_wire(module="search_searxng", gap_type="source_failure")
def is_configured() -> bool:
    """SearXNG URL set in env."""
    return bool((os.getenv("SEARXNG_URL") or "").strip())


def _base_url() -> str | None:
    """Normalised base URL with no trailing slash."""
    raw = (os.getenv("SEARXNG_URL") or "").strip()
    if not raw:
        return None
    return raw.rstrip("/")


#: R-F3844 — boolean operators and other query syntax carry no topical meaning, so
#: they must never be treated as evidence that a result matched.
_QUERY_OPERATORS = {"and", "or", "not", "near", "site", "filetype", "intitle", "inurl"}

#: Minimum token length to match on. Below this, coincidental substring hits ("the",
#: "a", "plc", "bae") would rescue an entirely unrelated result set.
_MIN_TOKEN = 4


def _query_tokens(query: str) -> set[str]:
    """Meaningful, matchable tokens from a query — operators and noise stripped."""
    import re as _re
    raw = _re.split(r"[^0-9a-z]+", (query or "").lower())
    return {t for t in raw if len(t) >= _MIN_TOKEN and t not in _QUERY_OPERATORS}


def _is_query_independent(query: str, results: list) -> bool:
    """True when NOT ONE result bears any lexical relation to the query.

    R-F3844 — the discriminator for "this backend answered a DIFFERENT question".

    Reproduced live 2026-08-11: the same DD query run four times returned four
    unrelated result sets ("Nova Launcher FAQ", "Outlook", a Danish Google Help page)
    all from `engine=bing`, with `ok: True` and ten well-formed results each. Four
    identical inputs producing four different outputs rules out query mangling — a
    deterministic bug repeats itself — so SearXNG was serving result sets belonging
    to other queries while every downstream consumer saw a normal success.

    DELIBERATELY CONSERVATIVE, and the asymmetry is the whole design. It fires only
    on TOTAL absence of relation: one matching result anywhere in the set is enough
    to pass. A legitimate result set nearly always shares something with its query;
    the observed pathology shared nothing at all. So this catches "answered a
    different question", never "answered badly" — it must not become a quality
    judgement, because a search gate that editorialises will eventually suppress
    real intelligence, which is far worse than the noise it removes.

    Returns False when it CANNOT judge — no results, or a query with no usable
    tokens. Refusing to measure is not the same as measuring a failure (§22).
    """
    if not results:
        return False                      # "nothing found" is an honest answer
    tokens = _query_tokens(query)
    if not tokens:
        return False                      # no basis to judge
    for r in results:
        if not isinstance(r, dict):
            continue
        hay = " ".join(str(r.get(k) or "") for k in ("title", "snippet", "url")).lower()
        if any(t in hay for t in tokens):
            return False
    return True


def _drop_query_independent_engines(query: str, results: list) -> tuple[list, dict]:
    """Drop the contribution of any ENGINE that answered a different question.

    R-F3853 — the same discriminator as `_is_query_independent`, applied per
    source instead of to the merged set.

    Why the whole-set check is not enough. Measured live 2026-08-11, bing answers
    correctly for popular queries and serves a soft-404 / trending page for queries
    it has no hits on, which SearXNG scrapes into ten well-formed results
    ("Rosoboronexport" → 0/10 related; "BAE Systems" → 9/10). Once `yep` is enabled
    a niche query returns ~20 good results AND ~10 bing artefacts. The merged set
    plainly relates to the query, so the R-F3844 gate correctly passes it — and
    carries the junk through diluted. Diluted junk is what a citation is drawn
    from; it is how a French Chrome help page became "press coverage" (C-19).

    THIS IS NOT A QUALITY FILTER, and the distinction is the reason it is safe.
    R-F3844's docstring warns that a gate which editorialises will eventually
    suppress real intelligence. This makes no judgement about whether a result is
    GOOD — it asks only the same yes/no question R-F3844 asks, per engine: did
    this source answer THIS query at all? An engine keeps every one of its results
    the moment ONE of them relates to the query.

    Single-engine sets are left ALONE: with nothing to compare against, dropping
    the only contributor would just be the whole-set check with a worse name, and
    that check still runs afterwards as the backstop.
    """
    by_engine: dict[str, list] = {}
    for r in results:
        if not isinstance(r, dict):
            continue
        by_engine.setdefault((r.get("engine") or "").strip().lower(), []).append(r)
    if len(by_engine) < 2:
        return results, {}
    dropped: dict[str, int] = {}
    for engine, rows in by_engine.items():
        if _is_query_independent(query, rows):
            dropped[engine or "unknown"] = len(rows)
    if not dropped:
        return results, {}
    kept = [r for r in results
            if isinstance(r, dict)
            and (r.get("engine") or "").strip().lower() not in
            {e for e in by_engine if (e or "unknown") in dropped}]
    return kept, dropped


@fail_wire(module="search_searxng", gap_type="source_failure")
async def search(
    query: str,
    *,
    count: int = 10,
    lang: str = "en",
    categories: str = "general",
) -> dict[str, Any]:
    """Run a SearXNG search. Returns standard ARIA-shaped result.

    Returns:
        {
          "ok": bool,
          "backend": "searxng",
          "configured": bool,
          "results": [
            {"title": str, "url": str, "snippet": str, "engine": str},
            ...
          ],
          "count": int,
          "query": str,
          "error": str | None,
        }
    """
    if not query or not query.strip():
        return {"ok": False, "error": "empty query", "results": []}
    # R-F2109: cap query length to prevent resource exhaustion on SearXNG.
    query = query.strip()[:500]
    base = _base_url()
    if not base:
        return {
            "ok":         False,
            "configured": False,
            "backend":    "searxng",
            "error":      "SEARXNG_URL not set — operator action pending",
            "results":    [],
            "query":      query,
        }
    try:
        import httpx
    except ImportError as e:
        return {"ok": False, "error": f"httpx unavailable: {e}", "results": []}

    params = {
        "q":          query,
        "format":     "json",
        "language":   lang,
        "categories": categories,
        "count":      str(min(max(count, 1), 50)),
    }
    try:
        # R-F2938 — serialise against the single self-hosted SearXNG box so a
        # 30-template adverse-media fan-out cannot stampede it into rate-limiting
        # and tripping the caller's circuit breaker. The semaphore only gates the
        # OUTBOUND request; parsing happens after release.
        async with _get_searxng_sem():
            async with httpx.AsyncClient(  # no-breaker: SearXNG is a self-hosted internal service on Fly's private network, not an external API. Circuit breaker is at the caller (web_search._search_searxng).
                timeout=_DEFAULT_TIMEOUT,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            ) as client:
                resp = await client.get(f"{base}/search", params=params)
    except Exception as e:
        kind = "timeout" if "timeout" in str(e).lower() else "fetch_error"
        logger.warning("searxng search %s failed: %s: %s", query[:60], kind, e)
        # R-F2109 §21a — wire failure so the brain knows SearXNG was unreachable
        try:
            wire_failure(module="search_searxng", detail=f"{kind}: {e}",
                         gap_type="source_failure", source="search_searxng.search")
        except Exception:
            pass
        return {
            "ok":         False,
            "configured": True,
            "backend":    "searxng",
            "error":      f"{kind}: {e}",
            "results":    [],
            "query":      query,
        }

    if resp.status_code != 200:
        # R-F2109 §21a — wire failure on non-200
        try:
            wire_failure(module="search_searxng", detail=f"HTTP {resp.status_code}",
                         gap_type="source_failure", source="search_searxng.search")
        except Exception:
            pass
        return {
            "ok":         False,
            "configured": True,
            "backend":    "searxng",
            "error":      f"http_{resp.status_code}",
            "results":    [],
            "query":      query,
        }

    try:
        data = resp.json()
    except Exception as e:
        return {
            "ok":         False,
            "configured": True,
            "backend":    "searxng",
            "error":      f"json_decode: {e}",
            "results":    [],
            "query":      query,
        }

    raw_results = data.get("results") or []
    normalised: list[dict[str, str]] = []
    for r in raw_results[:count]:
        if not isinstance(r, dict):
            continue
        normalised.append({
            "title":   (r.get("title") or "").strip(),
            "url":     (r.get("url") or "").strip(),
            "snippet": (r.get("content") or r.get("snippet") or "").strip(),
            "engine":  (r.get("engine") or "").strip(),
        })

    # R-F3853 — drop any single ENGINE that answered a different question, before
    # the whole-set check below. With yep enabled, a niche query returns real yep
    # results alongside bing's soft-404 artefacts; the merged set relates to the
    # query, so the R-F3844 gate passes it and the artefacts ride through diluted.
    # See _drop_query_independent_engines for why this is not a quality filter.
    _dropped_engines: dict[str, int] = {}
    if normalised:
        normalised, _dropped_engines = _drop_query_independent_engines(query, normalised)
        if _dropped_engines:
            logger.warning(
                "[R-F3853] searxng: dropped query-independent engine(s) for %r: %s",
                query[:80], _dropped_engines,
            )
            # §21a — a silently-degrading engine is exactly what ran unnoticed for
            # 52 days. ARIA must know WHICH source is answering the wrong question.
            try:
                from .engine_wiring import wire_failure
                wire_failure(
                    module="search_searxng",
                    detail=(f"engine(s) answered a different question for "
                            f"{query[:80]!r}: {_dropped_engines}"),
                    gap_type="search_backend_failure",
                    source="search_searxng:_drop_query_independent_engines",
                )
            except Exception:      # pragma: no cover - observability never blocks search
                pass

    # R-F3844 — a result set unrelated to the query is a BACKEND FAILURE, not a
    # result. Returning it as ok:True is what let a degraded SearXNG feed random
    # pages into DD research (and, via researcher.py's auto-registration, seed the
    # crawl registry with porn and gambling farms) while every consumer saw success.
    if _is_query_independent(query, normalised):
        _sample = [r.get("title", "")[:70] for r in normalised[:3]]
        logger.warning(
            "[R-F3844] searxng returned a QUERY-INDEPENDENT result set for %r — "
            "treating as backend failure, not results. sample=%s",
            query[:80], _sample,
        )
        # §21a — ARIA must KNOW her primary search is answering the wrong question.
        # Silence here is exactly how this ran for 52 days unnoticed.
        try:
            from .engine_wiring import wire_failure
            wire_failure(
                module="search_searxng",
                detail=(f"query-independent result set for {query[:80]!r}; "
                        f"backend answered a different question. sample={_sample}"),
                gap_type="search_backend_failure",
                source="search_searxng:search",
            )
        except Exception:      # pragma: no cover - observability never blocks search
            pass
        return {
            "ok":            False,
            "configured":    True,
            "backend":       "searxng",
            "error":         "noise: query-independent result set",
            "results":       [],
            "count":         0,
            "discarded":     len(normalised),
            "query":         query,
        }

    return {
        "ok":         True,
        "configured": True,
        "backend":    "searxng",
        "results":    normalised,
        "count":      len(normalised),
        "query":      query,
        # R-F3853 — reported, never silent: a caller (and the operator) can see
        # that a source was withheld and why, rather than inferring a clean run
        # from a smaller result count.
        "dropped_engines": _dropped_engines,
    }


@fail_wire(module="search_searxng", gap_type="source_failure")
def summary() -> dict[str, Any]:

    # R-F996 — wire to brain
    wire_success(
        module="search_searxng",
        summary="SearXNG search",
        source_id="search_searxng:R-F996",
    )
    return {
        "module":     "search_searxng",
        "configured": is_configured(),
        "url":        _base_url(),
        "purpose":    "free web-search backend (replacement for Brave when self-hosted)",
    }
