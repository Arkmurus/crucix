"""Academic-API adapters — Semantic Scholar, OpenAlex, CrossRef.

Returns `SearchResult` objects (same shape as web_search's Brave /
Google News backends) so the adapters plug straight into
`web_search.search()` via `_search_academic()`.

Why direct APIs, not meta-search
────────────────────────────────
The 2026-04-19 triage dropped SearXNG self-host as a dependency
because its public instances are unreliable and operationally painful
to self-host. Direct academic APIs capture the bulk of the value
(papers, technical reports, open-access PDFs, institutional research
outputs) with stable, well-documented contracts and explicit rate
limits. All three endpoints below are free, no auth required.

Rate limits (as of 2026-04)
───────────────────────────
  Semantic Scholar : 1 req/sec without key, 10 req/sec with API key
  OpenAlex         : 10 req/sec polite pool (pass ?mailto=)
  CrossRef         : ~50 req/sec polite pool (pass ?mailto=)

All three are added to the web_search parallel-gather. Because they
respond fast and produce high-credibility snippets, they're worth
querying even when Brave returns plenty — diversity improves
triangulation scoring.

Configuration
─────────────
  SEMANTIC_SCHOLAR_API_KEY   optional; raises rate limit 10×
  ACADEMIC_POLITE_EMAIL      default "aria@arkmurus.com" — stamped into
                             OpenAlex + CrossRef mailto= for polite-pool
                             access (better rate limits, more reliable)
  ACADEMIC_APIS_ENABLED      "0" disables all three adapters (killswitch
                             if any upstream goes down)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from ..ua_rotation import random_ua

logger = logging.getLogger("aria.sources.academic")

_TIMEOUT_SEC = 10.0
_MAX_RESULTS_PER_API = 8


def _is_enabled() -> bool:
    return (os.getenv("ACADEMIC_APIS_ENABLED", "1") or "1").strip() != "0"


def _polite_email() -> str:
    return (os.getenv("ACADEMIC_POLITE_EMAIL") or "aria@arkmurus.com").strip()


def _new_result(title: str, url: str, snippet: str, source: str) -> dict:
    """Lightweight result dict. web_search.py converts this to its
    SearchResult dataclass at the fan-in step. Returning a plain dict
    means this module has zero import of the dataclass (keeps the
    intel/sources/ package free of web_search circular deps)."""
    return {
        "title": (title or "")[:300],
        "url": url or "",
        "snippet": (snippet or "")[:600],
        "source": source,
        # Academic sources are institutional-grade — tier 2
        # (same bucket as RAND / RUSI / SIPRI / worldbank.org).
        "credibility_tier": 2,
        "language": "en",
    }


# ═══════════════════════════════════════════════════════════════════════
# Semantic Scholar
# ═══════════════════════════════════════════════════════════════════════

async def search_semantic_scholar(
    query: str,
    max_results: int = _MAX_RESULTS_PER_API,
) -> list[dict]:
    """Semantic Scholar graph API. Free, optional API key raises rate
    limit 10×. Covers 200M+ papers with abstract, authors, venue, year,
    and open-access PDF URL when available.

    Wrapped with a circuit breaker (F16 fix, 2026-04-27): without an
    API key Semantic Scholar's 1 req/sec limit is shared globally, so
    every research cycle hits 429. Breaker opens for 10 min after 3
    consecutive failures (429 or otherwise) so we stop pinging while
    rate-limited.
    """
    if not query or not _is_enabled():
        return []
    from ..circuit_breaker import get_breaker
    cb = get_breaker("semantic_scholar", failure_threshold=3, cooldown_seconds=600)
    if cb.is_open():
        return []
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    headers = {"User-Agent": random_ua()}
    api_key = (os.getenv("SEMANTIC_SCHOLAR_API_KEY") or "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    params = {
        "query": query,
        "limit": max_results,
        "fields": "title,abstract,authors,year,venue,url,openAccessPdf",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            resp = await client.get(url, headers=headers, params=params)
        if resp.status_code == 429:
            logger.debug("semantic_scholar: rate limited")
            cb.record_failure()
            return []
        if resp.status_code != 200:
            logger.debug("semantic_scholar: HTTP %d", resp.status_code)
            cb.record_failure()
            return []
        cb.record_success()
        data = resp.json()
    except Exception as e:
        logger.debug("semantic_scholar fetch failed: %s", e)
        cb.record_failure()
        return []
    out: list[dict] = []
    for p in (data.get("data") or [])[:max_results]:
        if not isinstance(p, dict):
            continue
        paper_url = ""
        if p.get("openAccessPdf") and isinstance(p["openAccessPdf"], dict):
            paper_url = p["openAccessPdf"].get("url", "")
        if not paper_url:
            paper_url = p.get("url", "")
        # Snippet: prefer abstract, else authors + venue + year
        abstract = (p.get("abstract") or "").strip()
        if abstract:
            snippet = abstract
        else:
            authors = ", ".join(
                a.get("name", "") for a in (p.get("authors") or [])[:3]
                if isinstance(a, dict)
            )
            venue = p.get("venue") or ""
            year = p.get("year") or ""
            snippet = " · ".join(x for x in (authors, venue, str(year) if year else "") if x)
        out.append(_new_result(
            title=p.get("title", ""),
            url=paper_url,
            snippet=snippet,
            source="semantic_scholar",
        ))
    return out


# ═══════════════════════════════════════════════════════════════════════
# OpenAlex
# ═══════════════════════════════════════════════════════════════════════

async def search_openalex(
    query: str,
    max_results: int = _MAX_RESULTS_PER_API,
) -> list[dict]:
    """OpenAlex — 240M+ scholarly works, open data, polite pool via
    mailto= param. Returns work metadata including DOI, concepts, and
    institutional affiliations."""
    if not query or not _is_enabled():
        return []
    from ..circuit_breaker import get_breaker
    cb = get_breaker("openalex", failure_threshold=3, cooldown_seconds=600)
    if cb.is_open():
        return []
    url = "https://api.openalex.org/works"
    params = {
        "search": query,
        "per_page": max_results,
        "mailto": _polite_email(),
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            resp = await client.get(url, params=params,
                                    headers={"User-Agent": random_ua()})
        if resp.status_code != 200:
            logger.debug("openalex: HTTP %d", resp.status_code)
            cb.record_failure()
            return []
        cb.record_success()
        data = resp.json()
    except Exception as e:
        logger.debug("openalex fetch failed: %s", e)
        cb.record_failure()
        return []
    out: list[dict] = []
    for w in (data.get("results") or [])[:max_results]:
        if not isinstance(w, dict):
            continue
        # Prefer open-access URL, fall back to DOI URL
        paper_url = ""
        if w.get("open_access") and isinstance(w["open_access"], dict):
            paper_url = w["open_access"].get("oa_url", "") or ""
        if not paper_url:
            paper_url = w.get("doi", "") or ""
            if paper_url and not paper_url.startswith("http"):
                paper_url = "https://doi.org/" + paper_url.lstrip("/")
        # Snippet: assemble authors + venue + year
        authors_raw = w.get("authorships") or []
        authors = [
            a.get("author", {}).get("display_name", "")
            for a in authors_raw[:3]
            if isinstance(a, dict)
        ]
        venue = ""
        if w.get("primary_location") and isinstance(w["primary_location"], dict):
            src = w["primary_location"].get("source")
            if isinstance(src, dict):
                venue = src.get("display_name", "") or ""
        year = w.get("publication_year") or ""
        snippet = " · ".join(
            x for x in (", ".join(a for a in authors if a), venue, str(year) if year else "") if x
        )
        out.append(_new_result(
            title=w.get("display_name", ""),
            url=paper_url,
            snippet=snippet,
            source="openalex",
        ))
    return out


# ═══════════════════════════════════════════════════════════════════════
# CrossRef
# ═══════════════════════════════════════════════════════════════════════

async def search_crossref(
    query: str,
    max_results: int = _MAX_RESULTS_PER_API,
) -> list[dict]:
    """CrossRef — primary DOI registry; best for recent journal articles
    and conference papers. No auth, polite pool via mailto=."""
    if not query or not _is_enabled():
        return []
    from ..circuit_breaker import get_breaker
    cb = get_breaker("crossref", failure_threshold=3, cooldown_seconds=600)
    if cb.is_open():
        return []
    url = "https://api.crossref.org/works"
    params = {
        "query": query,
        "rows": max_results,
        "mailto": _polite_email(),
        # Select only the fields we need — keeps responses small.
        "select": "DOI,title,author,abstract,published-print,"
                  "container-title,URL,type",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            resp = await client.get(url, params=params,
                                    headers={"User-Agent": random_ua()})
        if resp.status_code != 200:
            logger.debug("crossref: HTTP %d", resp.status_code)
            cb.record_failure()
            return []
        cb.record_success()
        data = resp.json()
    except Exception as e:
        logger.debug("crossref fetch failed: %s", e)
        cb.record_failure()
        return []
    items = (data.get("message") or {}).get("items") or []
    out: list[dict] = []
    for it in items[:max_results]:
        if not isinstance(it, dict):
            continue
        titles = it.get("title") or []
        title = titles[0] if titles else ""
        paper_url = it.get("URL", "") or ""
        if not paper_url and it.get("DOI"):
            paper_url = "https://doi.org/" + it["DOI"]
        # Snippet: abstract (HTML-tagged from JATS, strip tags), else
        # authors + container-title + year.
        abstract = it.get("abstract", "") or ""
        if abstract:
            # Strip JATS / HTML tags
            import re as _re
            snippet = _re.sub(r"<[^>]+>", " ", abstract)
            snippet = _re.sub(r"\s+", " ", snippet).strip()
        else:
            authors = [
                f"{a.get('given','')} {a.get('family','')}".strip()
                for a in (it.get("author") or [])[:3]
                if isinstance(a, dict)
            ]
            ct = it.get("container-title") or []
            venue = ct[0] if ct else ""
            year = ""
            pp = it.get("published-print") or {}
            if isinstance(pp, dict):
                parts = pp.get("date-parts") or [[]]
                if parts and parts[0]:
                    year = str(parts[0][0])
            snippet = " · ".join(
                x for x in (", ".join(a for a in authors if a), venue, year) if x
            )
        out.append(_new_result(
            title=title,
            url=paper_url,
            snippet=snippet,
            source="crossref",
        ))
    return out


# ═══════════════════════════════════════════════════════════════════════
# Fan-out — called by web_search
# ═══════════════════════════════════════════════════════════════════════

async def search_all(
    query: str,
    max_results_per_api: int = _MAX_RESULTS_PER_API,
) -> list[dict]:
    """Fire all three adapters in parallel. Returns a flat list of
    result dicts (NOT dedup'd — web_search.search does that across all
    backends). Exceptions from any single adapter are logged and
    swallowed so one upstream outage doesn't blank academic results."""
    if not _is_enabled():
        return []
    tasks = [
        search_semantic_scholar(query, max_results_per_api),
        search_openalex(query, max_results_per_api),
        search_crossref(query, max_results_per_api),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    flat: list[dict] = []
    for r in results:
        if isinstance(r, Exception):
            logger.debug("academic adapter raised: %s", r)
            continue
        if isinstance(r, list):
            flat.extend(r)
    return flat
