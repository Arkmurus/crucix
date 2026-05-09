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

from .ua_rotation import random_ua

logger = logging.getLogger("aria.web_search")

# ── Configuration ───────────────────────────────────────────────────────────

BRAVE_API_KEY = (os.getenv("BRAVE_SEARCH_API_KEY") or os.getenv("BRAVE_API_KEY") or "").strip()
SEARXNG_INSTANCES: list[str] = [
    # Removed 2026-04-20 — all 5 previously-listed public instances
    # (search.sapti.me, searxng.world, search.bus-hit.me, searx.tiekoetter.com,
    # search.ononoki.org) were sitting in circuit_breaker.open permanently,
    # polluting the open-circuit-breakers metric. Per the 2026-04-19 audit
    # triage: Brave is primary and the academic-API direct integrations
    # (Semantic Scholar / OpenAlex / CrossRef) capture the fallback value.
    # SearXNG self-host is deferred; re-populate this list or gate the
    # backend behind an env flag if it ever comes back. `_search_searxng`
    # returns [] safely when this list is empty.
]
REQUEST_TIMEOUT = 12.0
MAX_RESULTS_PER_BACKEND = 15


def _classify_http_status(status: int) -> str:
    """Map an HTTP error status to a circuit-breaker failure reason.
    Used to record the right capability_gap type when a backend trips
    the breaker — billing/rate-limit/auth failures need different
    operator action and should not all surface as 'timeout'."""
    if status == 402:
        return "billing"
    if status == 429:
        return "rate_limit"
    if status in (401, 403):
        return "auth"
    if 500 <= status < 600:
        return "server"
    return "other"


# Brave's `search_lang` rejects bare codes for languages with regional
# variants (Portuguese, Chinese, Japanese) with HTTP 422. Live evidence
# 2026-05-01 06:34:08: a Mozambique sweep sent `search_lang=pt` and got
# 422; the Lusophone backend silently dropped to 1/3 backends. Map
# unsupported bare codes to a regional default. `pt-pt` covers
# Angola/Mozambique; Brazil callers should pass `pt-BR` explicitly.
_BRAVE_LANG_NORMALISE = {
    "pt": "pt-pt",
    "zh": "zh-hans",
    "ja": "jp",
}


def _normalise_brave_lang(language: str) -> str:
    if not language:
        return "en"
    code = language.strip().lower()
    return _BRAVE_LANG_NORMALISE.get(code, code)

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
    """Brave Search API — best quality, requires API key.

    Wrapped with a circuit breaker (F15 fix, 2026-04-27): the live key
    was returning 402 Payment Required on every call (~9 wasted POSTs
    per research cycle). After 3 consecutive 4xx/5xx the breaker opens
    for 30 minutes; ARIA stops attempting Brave until the cooldown
    expires, then a single probe request decides whether to re-enable.
    """
    if not BRAVE_API_KEY:
        return []
    from .circuit_breaker import get_breaker
    cb = get_breaker("brave_search", failure_threshold=3, cooldown_seconds=1800)
    if cb.is_open():
        return []
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": min(max_results, 20), "search_lang": _normalise_brave_lang(language)},
                headers={"X-Subscription-Token": BRAVE_API_KEY, "Accept": "application/json"},
            )
            if resp.status_code != 200:
                logger.debug("Brave search %d: %s", resp.status_code, resp.text[:200])
                # F94: classify the failure so the capability_gap reflects
                # the real cause. Brave 402 = subscription/credit dead;
                # logging it as "timeout" sent triage to the wrong place.
                cb.record_failure(reason=_classify_http_status(resp.status_code))
                return []
            cb.record_success()
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
    except httpx.TimeoutException as e:
        logger.debug("Brave search timeout: %s", e)
        cb.record_failure(reason="timeout")
        return []
    except Exception as e:
        logger.debug("Brave search failed: %s", e)
        cb.record_failure()
        return []


# ── Backend: SearXNG (free meta-search) ─────────────────────────────────────

async def _search_searxng(query: str, max_results: int = 10, language: str = "en") -> list[SearchResult]:
    """SearXNG — free meta-search engine, tries multiple instances.
    Circuit breaker per instance — skips instances that are DOWN."""
    from .circuit_breaker import get_breaker
    for instance in SEARXNG_INSTANCES:
        cb = get_breaker(f"searx:{instance.split('//')[1].split('/')[0]}", cooldown_seconds=600)
        if cb.is_open():
            continue
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.get(
                    f"{instance}/search",
                    params={
                        "q": query, "format": "json",
                        "language": language, "pageno": 1,
                        "categories": "general",
                    },
                    headers={"User-Agent": random_ua()},
                )
                if resp.status_code != 200:
                    cb.record_failure()
                    continue
                cb.record_success()
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
            cb.record_failure()
            continue
    return []


# ── Backend: Academic APIs (Semantic Scholar + OpenAlex + CrossRef) ─────────

async def _search_academic(
    query: str,
    max_results: int = 10,
    language: str = "en",
) -> list[SearchResult]:
    """Tier-2 fan-out across three academic registries. Not Tier-1
    (those are official gov / IGO) but stronger signal than general-web
    press for technical, research, compliance, and programme-history
    questions. Added 2026-04-20 to fill the gap left by the dropped
    SearXNG backend.

    `language` is currently ignored — academic APIs default to English
    and translation-aware search is out of scope for this integration.
    """
    # Academic endpoints return mostly English results; skip quietly for
    # non-English queries so they don't dilute the Brave multilingual path.
    if language and language not in ("en", "english"):
        return []
    from .sources import academic as _ac
    try:
        raw = await _ac.search_all(query, max_results_per_api=max_results)
    except Exception as exc:
        logger.debug("_search_academic fan-out failed: %s", exc)
        return []
    out: list[SearchResult] = []
    for r in raw:
        # Convert the dict shape academic.py returns into the dataclass
        # web_search uses internally. credibility_tier is set by the
        # adapter (tier 2 for all academic sources).
        out.append(SearchResult(
            title=r.get("title", ""),
            url=r.get("url", ""),
            snippet=r.get("snippet", ""),
            source=r.get("source", "academic"),
            credibility_tier=int(r.get("credibility_tier", 2)),
            language=r.get("language", "en"),
        ))
    return out


# ── Backend: Google News RSS (free, news-focused) ───────────────────────────

async def _search_google_news(query: str, max_results: int = 10, language: str = "en") -> list[SearchResult]:
    """Google News RSS — free, news-specific, ~30 results max."""
    lang_map = {"en": "en", "pt": "pt-PT", "fr": "fr", "ar": "ar", "es": "es", "tr": "tr"}
    hl = lang_map.get(language, "en")
    encoded = urllib.parse.quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl={hl}&gl=US&ceid=US:en"

    try:
        # F78c 2026-04-29: news.google.com/rss/search returns 302 to
        # the actual feed URL (cluster-specific). httpx does NOT follow
        # redirects by default, so we silently dropped Google News from
        # every parallel-gather web_search call ("1 backends" in the
        # live log when only Crossref returned). follow_redirects=True
        # restores the second backend.
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": random_ua()})
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


# ── Backend: DuckDuckGo HTML scrape (free, no auth, no API) ────────────────

async def _search_duckduckgo(query: str, max_results: int = 10) -> list[SearchResult]:
    """R-F120 (2026-05-09): DuckDuckGo HTML scrape — free, no API key,
    no rate limit hard cap, hits the same general-web index Brave does.
    Critical fallback when Brave billing exhausts (circuit OPEN) and no
    SearXNG instance is configured. Live coverage of trade shows, contract
    signings, defence press releases that academic backends miss."""
    encoded = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = await client.post(
                url,
                headers={
                    "User-Agent": random_ua(),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            if resp.status_code != 200:
                return []
            html = resp.text
            results: list[SearchResult] = []
            # DDG HTML format: <a class="result__a" href="...">Title</a>
            #                  <a class="result__snippet">Snippet</a>
            pattern = re.compile(
                r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.+?)</a>'
                r'.*?<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.+?)</a>',
                re.DOTALL | re.IGNORECASE,
            )
            for m in pattern.finditer(html):
                u = m.group("url")
                title = re.sub(r"<[^>]+>", "", m.group("title")).strip()
                snippet = re.sub(r"<[^>]+>", "", m.group("snippet")).strip()[:300]
                # DDG redirects through /l/?uddg=<url>; unwrap
                if "/l/?uddg=" in u:
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
                    if "uddg" in qs:
                        u = qs["uddg"][0]
                if not title or not u or not u.startswith("http"):
                    continue
                results.append(SearchResult(
                    title=title, url=u, snippet=snippet,
                    source="duckduckgo",
                    credibility_tier=_score_credibility(u),
                ))
                if len(results) >= max_results:
                    break
            logger.debug("DuckDuckGo: %d results for %r", len(results), query[:60])
            return results
    except Exception as e:
        logger.debug("DuckDuckGo search failed: %s", e)
        return []


# ── Backend: Bing News RSS (free fallback) ──────────────────────────────────

async def _search_bing_news(query: str, max_results: int = 10) -> list[SearchResult]:
    """Bing News RSS — free fallback."""
    encoded = urllib.parse.quote_plus(query)
    url = f"https://www.bing.com/news/search?q={encoded}&format=rss"
    try:
        # Same redirect issue as Google News (F78c) — Bing routes RSS
        # requests through interstitial 302s for region/locale.
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": random_ua()})
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
    # R-F120 (2026-05-09): added DuckDuckGo + Bing News to the main
    # parallel-gather. Live evidence: operator searched SAHA 2026
    # (Turkish defence trade show) when Brave was OPEN — Google News
    # alone returned thin coverage; academic backends had nothing
    # because the topic is industry news not papers; SearXNG not yet
    # deployed. DDG covers general web (substitutes for Brave), Bing
    # News covers news (mirrors Google News for redundancy).
    backend_tasks = [
        _search_brave(query, MAX_RESULTS_PER_BACKEND, language),
        _search_searxng(query, MAX_RESULTS_PER_BACKEND, language),
        _search_duckduckgo(query, MAX_RESULTS_PER_BACKEND),
        _search_google_news(query, MAX_RESULTS_PER_BACKEND, language),
        _search_bing_news(query, MAX_RESULTS_PER_BACKEND),
        _search_academic(query, MAX_RESULTS_PER_BACKEND, language),
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

    final = results[:max_results]

    # ── Brain hook: feed search outcomes to learning ──
    try:
        from . import brain_hook as _bh
        backends_hit = sorted({r.source.split(":")[0] for r in final}) if final else []
        # F72 fix 2026-04-28: include language code in the gap detail so
        # the F66 (gap_type, detail) dedupe distinguishes per-language
        # variants. Without it, a Portuguese query that returns 0 would
        # block the English variant of the same query string from
        # recording its own (genuinely different) outcome for the next
        # hour, and the ledger would carry "no results for X" while
        # crossref+openalex actually serve hl=en X with 12 results
        # seconds later.
        await _bh.absorb(
            module="web_search",
            summary=f"web_search '{query[:80]}': {len(final)} results from {len(backends_hit)} backend(s) [{', '.join(backends_hit) or 'none'}]",
            detail="; ".join(f"{r.title[:80]} → {r.url[:120]}" for r in final[:5]),
            entity_name=query[:80],
            # Backend execution succeeded as long as we returned without
            # raising. Zero results is a valid outcome (rare query, long
            # tail, niche entity) — not a backend failure. Conflating the
            # two produced a 26% "success rate" on prod which masked real
            # failures and triggered noise alerts.
            success=True,
            gap_type=None if final else "search_zero_results",
            gap_detail=(None if final else
                        f"All {len(backend_tasks)} backends returned 0 for "
                        f"[lang={language}] {query[:120]}"),
            confidence="ASSESSED",
        )
    except Exception:
        pass

    return final


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
    final = results[:max_results]

    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="web_search",
            summary=f"search_news '{query[:80]}': {len(final)} results",
            detail="; ".join(f"{r.title[:80]}" for r in final[:5]),
            entity_name=query[:80],
            success=bool(final),
            extra_topics=["osint"],
            confidence="ASSESSED",
        )
    except Exception:
        pass

    return final


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
    final = results[:max_results]

    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="web_search",
            summary=f"search_entity '{entity}' ({country or 'no country'}): {len(final)} results across {len(queries)} angles",
            detail="; ".join(f"{r.title[:80]} ({r.url[:80]})" for r in final[:5]),
            entity_name=entity,
            success=bool(final),
            extra_topics=["osint", "compliance"],
            confidence="ASSESSED",
        )
    except Exception:
        pass

    return final


# ── P6: Native-language query expansion ──────────────────────────────────
#
# search_multilingual previously only switched the language LOCALE on the
# same English query. A query like "Angola defence tender" run against
# Portuguese locale still matched English words — missing actual
# Portuguese-language terms like "concurso defesa" or "licitação forças
# armadas". The fix is to translate the defence-BD core vocabulary into
# the target language and run the translated query alongside the English
# one. ARIA is a GLOBAL advisor, so she must be able to find
# Portuguese/French/Arabic/Spanish/Russian/Turkish/Chinese procurement
# portals even when the user asks in English.
#
# The dictionary below covers the high-frequency defence-BD terms that
# dominate procurement and compliance queries. It is NOT a full machine-
# translation layer — it is a targeted substitution map that captures
# 80% of the language-barrier value for 5% of the complexity.

_TERM_TRANSLATIONS: dict[str, dict[str, list[str]]] = {
    # Core procurement vocabulary
    "tender":       {"pt": ["concurso", "licitação"], "fr": ["appel d'offres"], "es": ["licitación"], "ar": ["مناقصة"], "ru": ["тендер"], "tr": ["ihale"], "zh": ["招标"], "ro": ["licitație"]},
    "tenders":      {"pt": ["concursos", "licitações"], "fr": ["appels d'offres"], "es": ["licitaciones"], "ar": ["مناقصات"], "ru": ["тендеры"], "tr": ["ihaleler"], "zh": ["招标"], "ro": ["licitații"]},
    "procurement":  {"pt": ["aquisição", "compra pública"], "fr": ["marché public", "acquisition"], "es": ["adquisición", "contratación pública"], "ar": ["مشتريات", "شراء"], "ru": ["закупки"], "tr": ["tedarik", "satın alma"], "zh": ["采购"], "ro": ["achiziție publică"]},
    "contract":     {"pt": ["contrato"], "fr": ["contrat", "marché"], "es": ["contrato"], "ar": ["عقد"], "ru": ["контракт"], "tr": ["sözleşme"], "zh": ["合同"]},
    "contract award":{"pt": ["adjudicação"], "fr": ["attribution de marché"], "es": ["adjudicación"], "ar": ["ترسية"], "ru": ["присуждение контракта"], "tr": ["ihale tahsisi"], "zh": ["合同授予"]},
    "bid":          {"pt": ["proposta"], "fr": ["offre"], "es": ["oferta"], "ar": ["عرض"], "ru": ["заявка"], "tr": ["teklif"], "zh": ["投标"]},

    # Defence vocabulary
    "defence":      {"pt": ["defesa"], "fr": ["défense"], "es": ["defensa"], "ar": ["دفاع"], "ru": ["оборона"], "tr": ["savunma"], "zh": ["国防"], "ro": ["apărare"]},
    "defense":      {"pt": ["defesa"], "fr": ["défense"], "es": ["defensa"], "ar": ["دفاع"], "ru": ["оборона"], "tr": ["savunma"], "zh": ["国防"], "ro": ["apărare"]},
    "military":     {"pt": ["militar"], "fr": ["militaire"], "es": ["militar"], "ar": ["عسكري"], "ru": ["военный"], "tr": ["askeri"], "zh": ["军事"], "ro": ["militar"]},
    "army":         {"pt": ["exército"], "fr": ["armée"], "es": ["ejército"], "ar": ["جيش"], "ru": ["армия"], "tr": ["ordu"], "zh": ["陆军"]},
    "navy":         {"pt": ["marinha"], "fr": ["marine"], "es": ["marina"], "ar": ["بحرية"], "ru": ["военно-морской флот"], "tr": ["donanma"], "zh": ["海军"]},
    "air force":    {"pt": ["força aérea"], "fr": ["armée de l'air"], "es": ["fuerza aérea"], "ar": ["القوات الجوية"], "ru": ["военно-воздушные силы"], "tr": ["hava kuvvetleri"], "zh": ["空军"]},
    "armed forces": {"pt": ["forças armadas"], "fr": ["forces armées"], "es": ["fuerzas armadas"], "ar": ["القوات المسلحة"], "ru": ["вооружённые силы"], "tr": ["silahlı kuvvetler"], "zh": ["武装部队"], "ro": ["forțele armate"]},
    "weapon":       {"pt": ["arma"], "fr": ["arme"], "es": ["arma"], "ar": ["سلاح"], "ru": ["оружие"], "tr": ["silah"], "zh": ["武器"]},
    "weapons":      {"pt": ["armas", "armamento"], "fr": ["armes", "armement"], "es": ["armas", "armamento"], "ar": ["أسلحة"], "ru": ["оружие", "вооружение"], "tr": ["silahlar"], "zh": ["武器"]},
    "ammunition":   {"pt": ["munição"], "fr": ["munitions"], "es": ["munición"], "ar": ["ذخيرة"], "ru": ["боеприпасы"], "tr": ["mühimmat"], "zh": ["弹药"], "ro": ["muniție"]},
    "missile":      {"pt": ["míssil"], "fr": ["missile"], "es": ["misil"], "ar": ["صاروخ"], "ru": ["ракета"], "tr": ["füze"], "zh": ["导弹"]},
    "drone":        {"pt": ["drone"], "fr": ["drone"], "es": ["dron"], "ar": ["طائرة بدون طيار"], "ru": ["беспилотник"], "tr": ["insansız hava aracı"], "zh": ["无人机"]},
    "uav":          {"pt": ["VANT"], "fr": ["drone"], "es": ["VANT"], "ar": ["طائرة بدون طيار"], "ru": ["БПЛА"], "tr": ["İHA"], "zh": ["无人机"]},
    "tank":         {"pt": ["carro de combate"], "fr": ["char"], "es": ["tanque"], "ar": ["دبابة"], "ru": ["танк"], "tr": ["tank"], "zh": ["坦克"]},
    "artillery":    {"pt": ["artilharia"], "fr": ["artillerie"], "es": ["artillería"], "ar": ["مدفعية"], "ru": ["артиллерия"], "tr": ["topçu"], "zh": ["炮兵"]},

    # Compliance/regulatory
    "sanction":     {"pt": ["sanção"], "fr": ["sanction"], "es": ["sanción"], "ar": ["عقوبة"], "ru": ["санкция"], "tr": ["yaptırım"], "zh": ["制裁"]},
    "sanctions":    {"pt": ["sanções"], "fr": ["sanctions"], "es": ["sanciones"], "ar": ["عقوبات"], "ru": ["санкции"], "tr": ["yaptırımlar"], "zh": ["制裁"], "ro": ["sancțiuni"]},
    "embargo":      {"pt": ["embargo"], "fr": ["embargo"], "es": ["embargo"], "ar": ["حظر"], "ru": ["эмбарго"], "tr": ["ambargo"], "zh": ["禁运"]},
    "export":       {"pt": ["exportação"], "fr": ["exportation"], "es": ["exportación"], "ar": ["تصدير"], "ru": ["экспорт"], "tr": ["ihracat"], "zh": ["出口"]},
    "import":       {"pt": ["importação"], "fr": ["importation"], "es": ["importación"], "ar": ["استيراد"], "ru": ["импорт"], "tr": ["ithalat"], "zh": ["进口"]},
    "licence":      {"pt": ["licença"], "fr": ["licence"], "es": ["licencia"], "ar": ["ترخيص"], "ru": ["лицензия"], "tr": ["lisans"], "zh": ["许可证"]},
    "license":      {"pt": ["licença"], "fr": ["licence"], "es": ["licencia"], "ar": ["ترخيص"], "ru": ["лицензия"], "tr": ["lisans"], "zh": ["许可证"]},
    "broker":       {"pt": ["corretor", "intermediário"], "fr": ["courtier"], "es": ["corredor", "intermediario"], "ar": ["وسيط"], "ru": ["брокер", "посредник"], "tr": ["aracı"], "zh": ["经纪人"]},
    "brokering":    {"pt": ["corretagem", "intermediação"], "fr": ["courtage"], "es": ["correduría", "intermediación"], "ar": ["الوساطة"], "ru": ["брокерство"], "tr": ["aracılık"], "zh": ["经纪"]},
    "compliance":   {"pt": ["conformidade"], "fr": ["conformité"], "es": ["cumplimiento"], "ar": ["امتثال"], "ru": ["соблюдение"], "tr": ["uyum"], "zh": ["合规"]},

    # Government entities
    "ministry of defence":{"pt": ["ministério da defesa"], "fr": ["ministère de la défense"], "es": ["ministerio de defensa"], "ar": ["وزارة الدفاع"], "ru": ["министерство обороны"], "tr": ["savunma bakanlığı"], "zh": ["国防部"], "ro": ["ministerul apărării naționale"]},
    "government":   {"pt": ["governo"], "fr": ["gouvernement"], "es": ["gobierno"], "ar": ["حكومة"], "ru": ["правительство"], "tr": ["hükümet"], "zh": ["政府"]},

    # Country names (only where they differ materially)
    "turkey":       {"pt": ["turquia"], "fr": ["turquie"], "es": ["turquía"], "ar": ["تركيا"], "ru": ["турция"], "tr": ["türkiye"], "zh": ["土耳其"]},
    "russia":       {"pt": ["rússia"], "fr": ["russie"], "es": ["rusia"], "ar": ["روسيا"], "ru": ["россия"], "tr": ["rusya"], "zh": ["俄罗斯"]},
    "china":        {"pt": ["china"], "fr": ["chine"], "es": ["china"], "ar": ["الصين"], "ru": ["китай"], "tr": ["çin"], "zh": ["中国"]},
    "france":       {"pt": ["frança"], "fr": ["france"], "es": ["francia"], "ar": ["فرنسا"], "ru": ["франция"], "tr": ["fransa"], "zh": ["法国"]},

    # LatAm-specific terms
    "armoured vehicle": {"pt": ["viatura blindada"], "fr": ["véhicule blindé"], "es": ["vehículo blindado"], "tr": ["zırhlı araç"], "zh": ["装甲车"]},
    "infantry fighting vehicle": {"pt": ["viatura de combate de infantaria"], "fr": ["véhicule de combat d'infanterie"], "es": ["vehículo de combate de infantería"], "tr": ["piyade savaş aracı"]},
    "modernisation": {"pt": ["modernização"], "fr": ["modernisation"], "es": ["modernización"], "tr": ["modernizasyon"]},
    "budget":       {"pt": ["orçamento"], "fr": ["budget"], "es": ["presupuesto"], "ar": ["ميزانية"], "ru": ["бюджет"], "tr": ["bütçe"], "zh": ["预算"]},
    "detonator":    {"pt": ["detonador"], "fr": ["détonateur"], "es": ["detonador"], "ar": ["صاعق"], "tr": ["fünye"]},
    "explosive":    {"pt": ["explosivo"], "fr": ["explosif"], "es": ["explosivo"], "ar": ["متفجر"], "tr": ["patlayıcı"]},
    "border":       {"pt": ["fronteira"], "fr": ["frontière"], "es": ["frontera"], "ar": ["حدود"], "tr": ["sınır"]},
    "patrol":       {"pt": ["patrulha"], "fr": ["patrouille"], "es": ["patrulla"], "ar": ["دورية"], "tr": ["devriye"]},
    "security":     {"pt": ["segurança"], "fr": ["sécurité"], "es": ["seguridad"], "ar": ["أمن"], "ru": ["безопасность"], "tr": ["güvenlik"], "zh": ["安全"]},
}


def _translate_query(query: str, lang: str) -> str:
    """Substitute known defence-BD terms in `query` with their `lang` equivalents.

    Longest-phrase-first matching to prevent "air force" being broken into
    "air" + "force". Returns the English query unchanged if no substitutions
    apply — we still issue the original query alongside to catch
    English-language sources in the target locale.
    """
    if not query or lang == "en":
        return query
    q = query
    # Sort by descending phrase length so multi-word keys match before
    # their single-word components.
    for term in sorted(_TERM_TRANSLATIONS.keys(), key=lambda k: -len(k)):
        choices = _TERM_TRANSLATIONS[term].get(lang)
        if not choices:
            continue
        replacement = choices[0]
        # Case-insensitive replacement with word boundaries on
        # alphabetic terms; Arabic / Chinese scripts don't use \b so
        # we fall back to plain replacement for non-latin.
        pat = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
        q = pat.sub(replacement, q)
    return q


async def search_multilingual(
    query: str,
    languages: list[str] | None = None,
    *,
    max_results: int = 15,
    translate_query: bool = True,
) -> list[SearchResult]:
    """Search in multiple languages simultaneously.

    When translate_query is True (default), defence-BD terms in the
    query are substituted into each target language so ARIA actually
    finds native-language procurement sources — not just English
    sources that happen to be in a different locale. Critical for
    discovering Angolan, Turkish, Russian, Chinese procurement
    portals that publish in their native language.

    Auto-detects regional languages from query content:
      - Lusophone countries + CPLP → add pt
      - Turkey + Turkish OEMs       → add tr
      - Russia / former USSR         → add ru
      - LatAm Spanish markets        → add es
      - MENA Arabic markets          → add ar
      - Francophone Africa            → add fr
      - China / Chinese OEMs         → add zh
    """
    if languages is None:
        languages = ["en"]
        q_lower = query.lower()
        # Portuguese (Lusophone Africa + Brazil + Portugal)
        if any(kw in q_lower for kw in ["angola", "mozambique", "guinea-bissau",
                                         "cape verde", "cabo verde", "portugal",
                                         "brazil", "brasil", "cplp", "lusophone",
                                         "são tomé", "sao tome"]):
            languages.append("pt")
        # French (Francophone Africa + France)
        if any(kw in q_lower for kw in ["senegal", "mali", "niger", "chad",
                                         "cote d'ivoire", "ivory coast",
                                         "burkina", "cameroon", "cameroun",
                                         "drc", "congo", "gabon", "benin",
                                         "togo", "france", "french"]):
            languages.append("fr")
        # Arabic (MENA + Gulf)
        if any(kw in q_lower for kw in ["morocco", "algeria", "tunisia", "libya",
                                         "egypt", "saudi", "uae", "emirates",
                                         "qatar", "kuwait", "bahrain", "oman",
                                         "iraq", "jordan", "lebanon", "yemen"]):
            languages.append("ar")
        # Turkish
        if any(kw in q_lower for kw in ["turkey", "turkiye", "türkiye", "baykar",
                                         "aselsan", "roketsan", "ssb", "otokar"]):
            languages.append("tr")
        # Russian (CIS + former Soviet)
        if any(kw in q_lower for kw in ["russia", "belarus", "kazakhstan",
                                         "uzbekistan", "armenia", "azerbaijan",
                                         "rosoboronexport", "kalashnikov",
                                         "almaz-antey", "s-400", "s-300"]):
            languages.append("ru")
        # Spanish (LatAm)
        if any(kw in q_lower for kw in ["argentina", "chile", "peru", "colombia",
                                         "mexico", "ecuador", "venezuela", "spain",
                                         "bolivia", "uruguay", "paraguay",
                                         "latin america", "latam", "spanish",
                                         "famae", "indumil", "fadea", "fabricaciones militares",
                                         "fuerzas armadas", "ministerio de defensa",
                                         "licitación", "contrato militar",
                                         "guatemala", "honduras", "el salvador",
                                         "costa rica", "panama", "dominican",
                                         "cuba", "nicaragua"]):
            languages.append("es")
        # Romanian
        if any(kw in q_lower for kw in ["romania", "romanian", "bucharest", "bucuresti",
                                         "onrc", "ancex", "romarm", "romaero",
                                         "aerostar", "pro optica", "srl",
                                         "ministerul apărării", "licitatie"]):
            languages.append("ro")
        # Chinese
        if any(kw in q_lower for kw in ["china", "chinese", "norinco", "chengdu",
                                         "poly technologies", "sastind", "catic"]):
            languages.append("zh")

    # Build translated-query variants per language
    queries_by_lang: list[tuple[str, str]] = []
    for lang in languages:
        if translate_query and lang != "en":
            translated = _translate_query(query, lang)
            queries_by_lang.append((lang, translated))
            # Also run the untranslated query with target locale — catches
            # English-language sources indexed against the locale.
            if translated != query:
                queries_by_lang.append((lang, query))
        else:
            queries_by_lang.append((lang, query))

    per_query_cap = max(3, max_results // max(1, len(queries_by_lang)) + 2)
    tasks = [search(q, max_results=per_query_cap, language=lang)
             for lang, q in queries_by_lang]
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
    final = results[:max_results]

    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="web_search",
            summary=f"search_multilingual '{query[:60]}' [{','.join(languages)}]: {len(final)} results from {len(queries_by_lang)} variants",
            detail="; ".join(f"[{r.language}] {r.title[:70]}" for r in final[:5]),
            entity_name=query[:80],
            success=bool(final),
            extra_topics=["osint"] + [f"lang:{l}" for l in languages if l != "en"],
            confidence="ASSESSED",
        )
    except Exception:
        pass

    return final


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
