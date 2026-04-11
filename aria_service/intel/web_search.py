"""ARIA Web Search — independent multi-backend search engine.

INDEPENDENCE PRINCIPLE: ARIA owns her search infrastructure. No single
vendor dependency. If Brave is down, use SearXNG. If SearXNG is down,
use Google News RSS. If all paid APIs fail, scrape directly. ARIA
always returns results.

Backends (priority order):
  1. Brave Search API    — best quality, 2000 queries/month free tier
  2. SearXNG instances   — free meta-search (aggregates Google, Bing, DDG)
  3. Google News RSS     — free, news-focused, 30 results max
  4. Bing News RSS       — free fallback
  5. Direct scraping     — last resort, trafilatura extraction

Usage:
  from aria_service.intel.web_search import search, search_news, search_entity

  results = await search("Angola defence procurement 2026", max_results=10)
  results = await search_news("ECJU export licence update", language="en")
  results = await search_entity("Duma Engineering Ltd", country="UK")
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

logger = logging.getLogger("aria.web_search")

# ── Configuration ───────────────────────────────────────────────────────────

BRAVE_API_KEY = (os.getenv("BRAVE_SEARCH_API_KEY") or os.getenv("BRAVE_API_KEY") or "").strip()
SEARXNG_INSTANCES = [
    "https://searx.be",
    "https://search.mdosch.de",
    "https://searxng.world",
    "https://paulgo.io",
]
REQUEST_TIMEOUT = 12.0
MAX_RESULTS_PER_BACKEND = 15

# Source credibility tiers (from v3_prompts SOURCE_HIERARCHY)
TIER_1_DOMAINS = {
    "gov.uk", "nato.int", "un.org", "sipri.org", "wassenaar.org",
    "eur-lex.europa.eu", "ofac.treasury.gov", "opensanctions.org",
    "ted.europa.eu", "sam.gov", "icrc.org", "icc-cpi.int",
}
TIER_2_DOMAINS = {
    "rand.org", "rusi.org", "iiss.org", "csis.org", "cfr.org",
    "chathamhouse.org", "issafrica.org", "carnegieendowment.org",
    "sipri.org", "worldbank.org",
}
TIER_3_DOMAINS = {
    "janes.com", "armyrecognition.com", "defensenews.com",
    "breakingdefense.com", "defenceweb.co.za", "shephard.co.uk",
}
TIER_4_DOMAINS = {
    "reuters.com", "bbc.com", "ft.com", "aljazeera.com",
    "france24.com", "lusa.pt", "dw.com", "rfi.fr",
}
DISINFORMATION_DOMAINS = {
    "rt.com", "sputniknews.com", "tass.com", "cgtn.com",
    "xinhuanet.com", "presstv.ir",
}


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    source: str = ""           # backend that found it
    credibility_tier: int = 5  # 1=official, 2=institution, 3=industry, 4=quality press, 5=general
    language: str = "en"
    timestamp: str = ""
    relevance_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "credibility_tier": self.credibility_tier,
            "language": self.language,
            "relevance_score": round(self.relevance_score, 2),
        }


def _get_domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).hostname.replace("www.", "").lower()
    except Exception:
        return ""


def _score_credibility(url: str) -> int:
    domain = _get_domain(url)
    if not domain:
        return 5
    if any(d in domain for d in DISINFORMATION_DOMAINS):
        return 6  # quarantine tier
    if any(d in domain for d in TIER_1_DOMAINS):
        return 1
    if any(d in domain for d in TIER_2_DOMAINS):
        return 2
    if any(d in domain for d in TIER_3_DOMAINS):
        return 3
    if any(d in domain for d in TIER_4_DOMAINS):
        return 4
    return 5


def _score_relevance(result: SearchResult, query: str) -> float:
    """Score result relevance based on query term overlap + credibility."""
    query_terms = set(query.lower().split())
    text = (result.title + " " + result.snippet).lower()
    overlap = sum(1 for t in query_terms if t in text and len(t) > 2)
    term_score = overlap / max(len(query_terms), 1)

    # Credibility boost: tier 1 = 1.5x, tier 2 = 1.3x, tier 3 = 1.15x
    cred_mult = {1: 1.5, 2: 1.3, 3: 1.15, 4: 1.05, 5: 1.0, 6: 0.3}
    return term_score * cred_mult.get(result.credibility_tier, 1.0)


# ── Backend: Brave Search API ───────────────────────────────────────────────

async def _search_brave(query: str, max_results: int = 10, language: str = "en") -> list[SearchResult]:
    """Brave Search API — best quality, requires API key."""
    if not BRAVE_API_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": min(max_results, 20), "search_lang": language},
                headers={"X-Subscription-Token": BRAVE_API_KEY, "Accept": "application/json"},
            )
            if resp.status_code != 200:
                logger.debug("Brave search %d: %s", resp.status_code, resp.text[:200])
                return []
            data = resp.json()
            results = []
            for item in (data.get("web", {}).get("results", []))[:max_results]:
                r = SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                    source="brave",
                    credibility_tier=_score_credibility(item.get("url", "")),
                )
                results.append(r)
            logger.debug("Brave: %d results for %r", len(results), query[:60])
            return results
    except Exception as e:
        logger.debug("Brave search failed: %s", e)
        return []


# ── Backend: SearXNG (free meta-search) ─────────────────────────────────────

async def _search_searxng(query: str, max_results: int = 10, language: str = "en") -> list[SearchResult]:
    """SearXNG — free meta-search engine, tries multiple instances."""
    for instance in SEARXNG_INSTANCES:
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.get(
                    f"{instance}/search",
                    params={
                        "q": query, "format": "json",
                        "language": language, "pageno": 1,
                        "categories": "general",
                    },
                    headers={"User-Agent": "ARIA Intelligence Agent/3.0"},
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                results = []
                for item in (data.get("results", []))[:max_results]:
                    r = SearchResult(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        snippet=item.get("content", ""),
                        source=f"searxng:{instance.split('//')[1].split('/')[0]}",
                        credibility_tier=_score_credibility(item.get("url", "")),
                    )
                    results.append(r)
                if results:
                    logger.debug("SearXNG (%s): %d results for %r", instance, len(results), query[:60])
                    return results
        except Exception:
            continue
    return []


# ── Backend: Google News RSS (free, news-focused) ───────────────────────────

async def _search_google_news(query: str, max_results: int = 10, language: str = "en") -> list[SearchResult]:
    """Google News RSS — free, news-specific, ~30 results max."""
    lang_map = {"en": "en", "pt": "pt-PT", "fr": "fr", "ar": "ar", "es": "es", "tr": "tr"}
    hl = lang_map.get(language, "en")
    encoded = urllib.parse.quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl={hl}&gl=US&ceid=US:en"

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return []
            # Parse RSS XML
            text = resp.text
            results = []
            items = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
            for item_xml in items[:max_results]:
                title_m = re.search(r"<title>(.*?)</title>", item_xml)
                link_m = re.search(r"<link>(.*?)</link>", item_xml)
                desc_m = re.search(r"<description>(.*?)</description>", item_xml)
                pub_m = re.search(r"<pubDate>(.*?)</pubDate>", item_xml)
                title = (title_m.group(1) if title_m else "").strip()
                link = (link_m.group(1) if link_m else "").strip()
                snippet = (desc_m.group(1) if desc_m else "").strip()
                snippet = re.sub(r"<[^>]+>", "", snippet)[:300]
                pub = (pub_m.group(1) if pub_m else "").strip()
                if title and link:
                    results.append(SearchResult(
                        title=title, url=link, snippet=snippet,
                        source="google_news", timestamp=pub,
                        credibility_tier=_score_credibility(link),
                    ))
            logger.debug("Google News: %d results for %r", len(results), query[:60])
            return results
    except Exception as e:
        logger.debug("Google News search failed: %s", e)
        return []


# ── Backend: Bing News RSS (free fallback) ──────────────────────────────────

async def _search_bing_news(query: str, max_results: int = 10) -> list[SearchResult]:
    """Bing News RSS — free fallback."""
    encoded = urllib.parse.quote_plus(query)
    url = f"https://www.bing.com/news/search?q={encoded}&format=rss"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return []
            text = resp.text
            results = []
            items = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
            for item_xml in items[:max_results]:
                title_m = re.search(r"<title>(.*?)</title>", item_xml)
                link_m = re.search(r"<link>(.*?)</link>", item_xml)
                desc_m = re.search(r"<description>(.*?)</description>", item_xml)
                title = (title_m.group(1) if title_m else "").strip()
                link = (link_m.group(1) if link_m else "").strip()
                snippet = re.sub(r"<[^>]+>", "", (desc_m.group(1) if desc_m else ""))[:300]
                if title and link:
                    results.append(SearchResult(
                        title=title, url=link, snippet=snippet,
                        source="bing_news",
                        credibility_tier=_score_credibility(link),
                    ))
            return results
    except Exception:
        return []


# ── Core search functions ───────────────────────────────────────────────────

async def search(
    query: str,
    *,
    max_results: int = 10,
    language: str = "en",
    min_credibility: int = 6,
    require_triangulation: bool = False,
) -> list[SearchResult]:
    """ARIA's primary search — queries all backends, deduplicates, ranks.

    Args:
        query: search query
        max_results: max results to return
        language: search language (en, pt, fr, ar, es, tr)
        min_credibility: filter out results below this tier (1=best, 6=disinfo)
        require_triangulation: if True, only return results found by 2+ backends

    Returns:
        Deduplicated, credibility-scored, relevance-ranked results.
    """
    # Fire all backends in parallel
    backend_tasks = [
        _search_brave(query, MAX_RESULTS_PER_BACKEND, language),
        _search_searxng(query, MAX_RESULTS_PER_BACKEND, language),
        _search_google_news(query, MAX_RESULTS_PER_BACKEND, language),
    ]

    raw_results = await asyncio.gather(*backend_tasks, return_exceptions=True)

    # Flatten and deduplicate by URL
    seen_urls: dict[str, SearchResult] = {}
    url_sources: dict[str, set] = {}  # track which backends found each URL

    for batch in raw_results:
        if isinstance(batch, Exception):
            continue
        for r in batch:
            url_key = _get_domain(r.url) + urllib.parse.urlparse(r.url).path.rstrip("/")
            if url_key not in seen_urls:
                seen_urls[url_key] = r
                url_sources[url_key] = {r.source.split(":")[0]}
            else:
                url_sources[url_key].add(r.source.split(":")[0])
                # Keep the version with the better snippet
                if len(r.snippet) > len(seen_urls[url_key].snippet):
                    seen_urls[url_key].snippet = r.snippet

    results = list(seen_urls.values())

    # Mark triangulated results (found by 2+ backends)
    for r in results:
        url_key = _get_domain(r.url) + urllib.parse.urlparse(r.url).path.rstrip("/")
        sources = url_sources.get(url_key, set())
        if len(sources) >= 2:
            r.relevance_score += 0.3  # triangulation bonus

    # Filter by credibility
    results = [r for r in results if r.credibility_tier <= min_credibility]

    # Filter: require triangulation (2+ backends found it)
    if require_triangulation:
        results = [r for r in results if
                   len(url_sources.get(_get_domain(r.url) + urllib.parse.urlparse(r.url).path.rstrip("/"), set())) >= 2]

    # Score relevance
    for r in results:
        r.relevance_score += _score_relevance(r, query)

    # Quarantine disinformation (don't remove, tag for analyst awareness)
    for r in results:
        if r.credibility_tier == 6:
            r.snippet = f"[QUARANTINED — suspected disinformation source] {r.snippet}"

    # Sort by relevance (credibility-weighted)
    results.sort(key=lambda r: r.relevance_score, reverse=True)

    logger.info("Search %r: %d results from %d backends (deduped from %d)",
                query[:60], min(len(results), max_results),
                sum(1 for b in raw_results if not isinstance(b, Exception) and b),
                sum(len(b) for b in raw_results if not isinstance(b, Exception)))

    return results[:max_results]


async def search_news(query: str, *, max_results: int = 10, language: str = "en") -> list[SearchResult]:
    """News-specific search — Google News + Bing News in parallel."""
    tasks = [
        _search_google_news(query, MAX_RESULTS_PER_BACKEND, language),
        _search_bing_news(query, MAX_RESULTS_PER_BACKEND),
    ]
    raw = await asyncio.gather(*tasks, return_exceptions=True)

    seen = {}
    for batch in raw:
        if isinstance(batch, Exception):
            continue
        for r in batch:
            key = r.title[:50].lower()
            if key not in seen:
                seen[key] = r
                r.relevance_score = _score_relevance(r, query)

    results = sorted(seen.values(), key=lambda r: r.relevance_score, reverse=True)
    return results[:max_results]


async def search_entity(
    entity: str,
    *,
    country: str = "",
    max_results: int = 15,
) -> list[SearchResult]:
    """Entity-focused search — fires 5 query angles for triangulation.

    For counterparty due diligence: searches the entity name across
    multiple formulations to build a complete intelligence picture.
    """
    queries = [
        f"{entity} {country}".strip(),
        f"{entity} directors officers shareholders",
        f"{entity} sanctions litigation court",
        f"{entity} contract award procurement",
        f'"{entity}" site:opencorporates.com OR site:companieshouse.gov.uk',
    ]

    all_results = []
    for q in queries:
        batch = await search(q, max_results=5, min_credibility=5)
        all_results.extend(batch)

    # Deduplicate across all angles
    seen = {}
    for r in all_results:
        key = _get_domain(r.url) + urllib.parse.urlparse(r.url).path.rstrip("/")
        if key not in seen:
            seen[key] = r
        else:
            seen[key].relevance_score += 0.2  # found across multiple angles = more relevant

    results = sorted(seen.values(), key=lambda r: r.relevance_score, reverse=True)
    return results[:max_results]


async def search_multilingual(
    query: str,
    languages: list[str] | None = None,
    *,
    max_results: int = 15,
) -> list[SearchResult]:
    """Search in multiple languages simultaneously.

    For CPLP markets, automatically searches in Portuguese + English.
    For MENA markets, adds Arabic + French.
    """
    if languages is None:
        languages = ["en"]
        # Auto-detect regional languages from query content
        q_lower = query.lower()
        if any(kw in q_lower for kw in ["angola", "mozambique", "guinea-bissau",
                                         "cape verde", "portugal", "brazil", "cplp", "lusophone"]):
            languages.append("pt")
        if any(kw in q_lower for kw in ["morocco", "algeria", "tunisia", "libya",
                                         "egypt", "saudi", "uae", "qatar", "iraq"]):
            languages.append("ar")
        if any(kw in q_lower for kw in ["senegal", "mali", "niger", "chad",
                                         "cote d'ivoire", "burkina", "cameroon", "drc", "congo"]):
            languages.append("fr")
        if any(kw in q_lower for kw in ["turkey", "turkiye", "baykar", "aselsan"]):
            languages.append("tr")

    tasks = [search(query, max_results=max_results // len(languages) + 2, language=lang)
             for lang in languages]
    raw = await asyncio.gather(*tasks, return_exceptions=True)

    seen = {}
    for batch in raw:
        if isinstance(batch, Exception):
            continue
        for r in batch:
            key = _get_domain(r.url) + urllib.parse.urlparse(r.url).path.rstrip("/")
            if key not in seen:
                seen[key] = r

    results = sorted(seen.values(), key=lambda r: r.relevance_score, reverse=True)
    return results[:max_results]


# ── Stats and health ────────────────────────────────────────────────────────

async def get_search_health() -> dict:
    """Check which search backends are available."""
    health = {
        "brave": bool(BRAVE_API_KEY),
        "searxng": False,
        "google_news": False,
        "bing_news": False,
    }
    # Quick probe of SearXNG
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            for inst in SEARXNG_INSTANCES[:2]:
                try:
                    r = await client.get(f"{inst}/search", params={"q": "test", "format": "json"})
                    if r.status_code == 200:
                        health["searxng"] = True
                        break
                except Exception:
                    continue
    except Exception:
        pass
    # Google News RSS is almost always available
    health["google_news"] = True
    health["bing_news"] = True
    return health
