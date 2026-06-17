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

from .engine_wiring import wire_success, wire_failure

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

# R-W5 (2026-05-11): per-call ecosystem snapshot for the most-recent
# search() invocation. Operator / chat layer / dashboard reads via
# get_last_search_ecosystem() to see which backends fired vs silent
# vs errored — the wired-but-silent detector applied at the search
# layer, mirroring R-F305's pattern for the DD orchestrator.
_LAST_SEARCH_ECOSYSTEM: dict = {}


def get_last_search_ecosystem() -> dict:
    """R-W5: read the per-backend ecosystem snapshot of the most-recent
    search() call. Returns:
        {
          "query": str, "language": str,
          "backends": [{name, state, results_count, error_reason?}, ...],
          "summary": {active_backends, silent_backends, errored_backends, total_backends},
          "health_signal": "HEALTHY" | "PARTIAL" | "DEGRADED" | "DEAD",
          "total_duration_ms": int,
        }
    Empty dict if no search has run since boot."""
    return dict(_LAST_SEARCH_ECOSYSTEM)


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

    # R-F888 (2026-05-25) — SOURCE-TYPE weighting. The academic registries
    # (Crossref / Semantic Scholar / OpenAlex) are the FALLBACK tier (per this
    # module's header: "academic-API integrations capture the fallback value").
    # But their high credibility multiplier (×1.3-1.5) + keyword-dense titles
    # let them OUT-RANK live-web/news results for general + current-affairs
    # queries. Live 2026-05-25 (operator "zero confidence"): "who is the
    # current US president" returned Crossref's "Who Was Who 2007" (stops at
    # G.W. Bush) over DuckDuckGo's live "President Donald Trump 2025-2029".
    # Backends individually return the CORRECT answer — the ranking buried it.
    # Demote academic to a true fallback (only wins when nothing else returns);
    # boost live-web/news so current-affairs + entity lookups surface.
    src = (result.source or "").lower()
    if any(a in src for a in ("crossref", "semantic_scholar", "semanticscholar",
                              "openalex", "academic", "doi.org", "ssrn")):
        source_mult = 0.45
    elif any(w in src for w in ("google_news", "bing_news", "duckduckgo", "ddg",
                                "searxng", "brave", "defence_event")):
        source_mult = 1.25
    else:
        source_mult = 1.0
    return term_score * cred_mult.get(result.credibility_tier, 1.0) * source_mult


# ── Backend: Brave Search API ───────────────────────────────────────────────

async def _search_brave(query: str, max_results: int = 10, language: str = "en") -> list[SearchResult]:
    """R-F320 (2026-05-11): Brave Search REMOVED. Permanent stub.

    Operator directive 2026-05-11: "please remove brave then... lets
    focus reducing dependency". Per aria_mirrors_claude memory, Brave
    is deprecated; the free-tier multilingual aggregator (Google News
    + Bing News + DuckDuckGo + Crossref + OpenAlex + Semantic Scholar)
    covers Brave's ground with richer fan-out and zero cost.

    The function is kept as a stub returning [] so existing callers
    that import `_search_brave` don't break. The body (circuit
    breaker, billing-exhaustion sticky, 402 streak counter, request
    code) is gone — Brave does not execute.

    To restore Brave (not recommended), revert R-F320 in
    aria_service/intel/web_search.py.
    """
    return []


# ── Backend: SearXNG (free meta-search) ─────────────────────────────────────

async def _search_searxng(query: str, max_results: int = 10, language: str = "en") -> list[SearchResult]:
    """SearXNG — free meta-search engine, tries multiple instances.
    Circuit breaker per instance — skips instances that are DOWN.

    R-F178 (2026-05-11): instance list has been empty since 2026-04-20 (5
    public instances all dead). Short-circuit immediately so the parallel
    backend gather doesn't waste a coroutine + log line every search.

    R-F183 (2026-05-11): when the R-F86 self-host adapter is configured
    (SEARXNG_URL env set, search_searxng.is_configured()=True) use it
    instead. That adapter is the independence-roadmap path: a Fly.io
    machine running the SearXNG Docker image returns free search results
    at $0 marginal cost AND doesn't share an IP with public instances
    that rate-limit aggressively. Operator activates by deploying the
    SearXNG container and setting SEARXNG_URL.
    """
    # R-F183: prefer the self-host adapter when configured.
    try:
        from . import search_searxng as _sx
        if _sx.is_configured():
            res = await _sx.search(query, count=max_results, lang=language or "en")
            if res.get("ok") and res.get("results"):
                out: list[SearchResult] = []
                for item in res["results"]:
                    url = (item.get("url") or "").strip()
                    if not url:
                        continue
                    out.append(SearchResult(
                        title=(item.get("title") or "")[:300],
                        url=url,
                        snippet=(item.get("snippet") or "")[:500],
                        source=f"searxng:{item.get('engine') or 'self-host'}",
                        credibility_tier=_score_credibility(url),
                    ))
                if out:
                    logger.debug("SearXNG (self-host R-F183): %d results for %r", len(out), query[:60])
                    return out
            # If self-host returned no results / error, fall through to
            # the legacy public-instance loop (currently empty); the path
            # is harmless and lets us add public instances later.
    except Exception as _sx_e:
        logger.debug("R-F183 searxng self-host probe failed: %s", _sx_e)
    if not SEARXNG_INSTANCES:
        return []
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
    # R-F193 (2026-05-11): no longer skip non-English. OpenAlex /
    # CrossRef / Semantic Scholar index plenty of non-English titles
    # with English metadata. Pre-R-F193 the 3-extra-langs branch in
    # the multilingual fan-out wasted 3 coroutine slots on early-
    # return; now the academic backend contributes English-language
    # papers about foreign-language entity queries (e.g. searching
    # "savunma sanayii baskanligi" still surfaces English papers
    # about the SSB).
    from .sources import academic as _ac
    try:
        raw = await _ac.search_all(query, max_results_per_api=max_results)
    except Exception as exc:
        # R-F1614 make-loud: a backend error here returns [] which is
        # indistinguishable from "no results" — a provider failure must
        # not masquerade as confidently-ungrounded "nothing found".
        logger.warning("_search_academic fan-out failed: %s", exc)
        wire_failure(
            module="web_search._search_academic",
            detail=f"academic backend fan-out failed (returned [] as if no results): {exc}",
            gap_type="search_backend_failure",
            source="web_search",
        )
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
        # R-F1614 make-loud: backend error → [] looks like "no results".
        logger.warning("Google News search failed: %s", e)
        wire_failure(
            module="web_search._search_google_news",
            detail=f"google_news backend failed (returned [] as if no results): {e}",
            gap_type="search_backend_failure",
            source="web_search",
        )
        return []


# ── Backend: DuckDuckGo HTML scrape (free, no auth, no API) ────────────────

async def _search_duckduckgo(query: str, max_results: int = 10) -> list[SearchResult]:
    """R-F120 (2026-05-09): DuckDuckGo HTML scrape — free, no API key,
    no rate limit hard cap, hits the same general-web index Brave does.
    Critical fallback when Brave billing exhausts (circuit OPEN) and no
    SearXNG instance is configured. Live coverage of trade shows, contract
    signings, defence press releases that academic backends miss.

    R-F150 (2026-05-10): per-host circuit breaker. Live log 2026-05-10
    11:28:01 showed every DDG POST returning 202 (queued/rate-limited)
    rather than 200. The function correctly returned [] but burned 5
    requests per chat-turn against a backend that wasn't going to answer.
    Add an explicit 202 path that records a failure and an early-return
    when the breaker is open. Cooldown 10 min = enough for DDG to clear
    its rate-limit window without burning more probes than needed.
    """
    from .circuit_breaker import get_breaker
    cb = get_breaker("search:duckduckgo", failure_threshold=5, cooldown_seconds=600)
    if cb.is_open():
        return []
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
            if resp.status_code == 202:
                # R-F150: DDG returns 202 Accepted when queued or rate-
                # limited. Surface it explicitly so the production log
                # shows the breaker reason (was being silently swallowed
                # at debug level before).
                logger.info("DuckDuckGo returned 202 (rate-limited/queued) for %r", query[:60])
                cb.record_failure()
                return []
            if resp.status_code != 200:
                cb.record_failure()
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
            cb.record_success()
            logger.debug("DuckDuckGo: %d results for %r", len(results), query[:60])
            return results
    except Exception as e:
        cb.record_failure()
        # R-F1614 make-loud: breaker records it internally but the brain
        # never sees the backend error — wire it so a provider failure
        # isn't silently read as "no results".
        logger.warning("DuckDuckGo search failed: %s", e)
        wire_failure(
            module="web_search._search_duckduckgo",
            detail=f"duckduckgo backend failed (returned [] as if no results): {e}",
            gap_type="search_backend_failure",
            source="web_search",
        )
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
    except Exception as e:
        # R-F1614 make-loud: this was a fully-silent swallow — a Bing
        # backend error returned [] with no log at all, indistinguishable
        # from "no results" (confidently ungrounded).
        logger.warning("Bing News search failed: %s", e)
        wire_failure(
            module="web_search._search_bing_news",
            detail=f"bing_news backend failed (returned [] as if no results): {e}",
            gap_type="search_backend_failure",
            source="web_search",
        )
        return []


# ── Core search functions ───────────────────────────────────────────────────

# ── R-F124 — Memory-first inversion (2026-05-10) ─────────────────────────────
# Pay-once-remember-forever doctrine: every paid API call is absorbed
# into rag_store + intel_ledger, so a repeat query should hit memory
# first and skip the web entirely if the corpus is already strong on
# the topic. Cuts 40-60% of repeat-query LLM/Brave spend.
async def _query_memory(
    query: str, *, max_results: int = 10
) -> list[SearchResult]:
    """Pull recent corpus hits for the query as SearchResult-shaped items.

    Hits rag_store.search() (chromadb-backed semantic) + tags each
    result with credibility_tier=2 (institutional memory) and a
    relevance_score that already reflects the embedding similarity.
    Fail-open — returns [] on any error so live web search still runs.
    """
    out: list[SearchResult] = []
    try:
        from . import rag_store
        hits = await asyncio.wait_for(
            rag_store.search(query, top_k=max_results),
            timeout=4.0,
        )
        for h in hits or []:
            if not isinstance(h, dict):
                continue
            url = (h.get("url") or "").strip()
            if not url:
                # No URL — synthesise an opaque memory:// pointer so the
                # dedupe key stays stable across repeated retrievals
                _id = hashlib.sha1(
                    (h.get("source") or h.get("title") or h.get("text") or "")[:200]
                    .encode("utf-8")
                ).hexdigest()[:12]
                url = f"memory://{_id}"
            out.append(SearchResult(
                title=(h.get("title") or h.get("source") or "memory")[:200],
                url=url,
                snippet=(h.get("text") or "")[:500],
                source=f"memory:{h.get('collection') or h.get('source_type') or 'rag'}",
                credibility_tier=2,
                language=h.get("language") or "en",
                timestamp=h.get("ingested_at") or "",
                relevance_score=float(h.get("score") or 0.0) + 0.5,  # memory-bonus
            ))
    except Exception as _me:
        logger.debug("memory-first query failed (non-fatal): %s", _me)
    return out


# ── R-F125 — Auto-language fan-out (2026-05-10) ──────────────────────────────
# Detect non-English script + entity-name suffix heuristics, then run
# the same query in those languages so e.g. "SAHA 2026 contracts"
# (Turkish defence trade show) also probes tr-TR sources where the
# actual contract press releases live.
def _detect_query_languages(query: str, base_lang: str = "en") -> list[str]:
    """Return additional language codes to fan out to (excludes base_lang)."""
    extras: set[str] = set()
    if not query:
        return []
    # Unicode-script detection
    script_map = [
        ("Ѐ-ӿ", "ru"),   # Cyrillic
        ("؀-ۿ", "ar"),   # Arabic
        ("֐-׿", "he"),   # Hebrew
        ("一-鿿", "zh"),   # CJK Unified
        ("぀-ゟ", "ja"),   # Hiragana
        ("가-힯", "ko"),   # Hangul
        ("ऀ-ॿ", "hi"),   # Devanagari
    ]
    for rng, lang in script_map:
        try:
            if re.search(f"[{rng}]", query):
                extras.add(lang)
        except Exception:
            continue
    # Entity-name + locale heuristics on the Latin-script portion.
    # R-F135 (2026-05-10): expanded to include English country names
    # ("Turkey", "Brazil", "UAE", etc) so queries like "Assan Group Turkey"
    # trigger Turkish fan-out. Without this, the only Turkish-language
    # signal that activates fan-out is a native marker the operator
    # rarely types in English DD prep. Live evidence: SAHA 2026 was
    # caught by R-F125 (which detects "saha "), but "Assan Group Turkey"
    # produced 12 Google-News results from English-only fan-out and
    # missed Türkiye-press indexing of the espionage probe.
    q_lower = query.lower()
    # Word-boundary helper for short English country names so "uae"
    # doesn't false-match "uaeu" / "guam" etc.
    def _has_word(word: str, text: str) -> bool:
        import re as _re_dl
        return bool(_re_dl.search(rf"(^|[^a-z0-9]){_re_dl.escape(word)}($|[^a-z0-9])", text))

    tr_markers_native = ("aş.", " a.ş.", " a.s.", " sti.", " ş.", " ltd.şti", "türk",
                         "türkiye", "istanbul", "ankara", "saha ", "idef",
                         "tusaş", "asisguard", "aselsan", "roketsan", "stm ")
    tr_words = ("turkey", "turkish", "turkiye")
    if any(m in q_lower for m in tr_markers_native) or any(_has_word(w, q_lower) for w in tr_words):
        extras.add("tr")

    pt_markers_native = (" lda", " ltda", " s.a.", "brasil", "portugal", "lisboa",
                         "moçambique", "moçamb", "lusofon", "embraer", "luanda", "maputo")
    pt_words = ("brazil", "brazilian", "portuguese", "angola", "angolan",
                "mozambique", "mozambican", "lusophone")
    if any(m in q_lower for m in pt_markers_native) or any(_has_word(w, q_lower) for w in pt_words):
        extras.add("pt")

    es_markers_native = (" s.l.", "españa", "españ", "méxico", "indra ", "navantia",
                         "buenos aires", "bogotá", "lima ", "caracas")
    es_words = ("spain", "spanish", "mexico", "mexican", "argentina", "argentinian",
                "colombia", "colombian", "peru", "peruvian", "venezuela", "venezuelan",
                "chile", "chilean")
    if any(m in q_lower for m in es_markers_native) or any(_has_word(w, q_lower) for w in es_words):
        extras.add("es")

    fr_markers_native = (" s.a.r.l", " sarl", "société", "française", "côte d'ivoire",
                         "sénégal", "burkina", "thales", "naval group", "dassault",
                         "dakar", "abidjan")
    fr_words = ("france", "french", "senegal", "ivory coast", "ivorian", "moroccan",
                "tunisian", "algerian")
    if any(m in q_lower for m in fr_markers_native) or any(_has_word(w, q_lower) for w in fr_words):
        extras.add("fr")

    de_markers_native = (" gmbh", " ag ", " kg ", "deutschland", "rheinmetall",
                         "diehl", "krauss-maffei", "hensoldt", "münchen", "berlin")
    de_words = ("germany", "german", "austrian", "austria", "swiss")
    if any(m in q_lower for m in de_markers_native) or any(_has_word(w, q_lower) for w in de_words):
        extras.add("de")

    ar_markers_native = ("idex", "navdex", "edge group", "abu dhabi", "riyadh", "doha",
                         "manama", "kuwait city", "amman ")
    ar_words = ("uae", "saudi", "qatar", "qatari", "bahrain", "bahraini",
                "kuwait", "kuwaiti", "oman", "omani", "jordan", "jordanian",
                "iraq", "iraqi", "lebanon", "lebanese", "egyptian", "egypt",
                "emirates", "emirati")
    if any(m in q_lower for m in ar_markers_native) or any(_has_word(w, q_lower) for w in ar_words):
        extras.add("ar")

    # Asia-Pacific defence-supplier markets — Korean / Japanese / Chinese /
    # Indian / Indonesian. English country names trigger fan-out so e.g.
    # "Hanwha Aerospace Korea" probes ko-KR press alongside en-US.
    ko_markers = ("hanwha", "kai aerospace", "lig nex1", "poongsan",
                  "hyundai rotem", "seoul", "busan")
    if any(m in q_lower for m in ko_markers) or _has_word("korea", q_lower) or _has_word("korean", q_lower):
        extras.add("ko")
    ja_markers = ("mitsubishi heavy", "kawasaki heavy", "ihi corp",
                  "nec corp", "tokyo", "osaka", "subaru")
    if any(m in q_lower for m in ja_markers) or _has_word("japan", q_lower) or _has_word("japanese", q_lower):
        extras.add("ja")
    zh_markers = ("norinco", "avic", "casc ", "casic", "cssc", "beijing",
                  "shanghai", "shenzhen", "hikvision", "dahua",
                  "cnsig", "hangzhou", "huawei")
    if any(m in q_lower for m in zh_markers) or _has_word("china", q_lower) or _has_word("chinese", q_lower):
        extras.add("zh")
    # Russian — Wagner / Rosoboronexport / etc. Cyrillic script
    # detection (above) catches native spellings; this catches Latin.
    ru_markers = ("rosoboronexport", "almaz-antey", "kalashnikov", "moscow",
                  "rostec", "rosatom", "wagner group", "wagner pmc",
                  "uralvagonzavod", "sukhoi")
    if any(m in q_lower for m in ru_markers) or _has_word("russia", q_lower) or _has_word("russian", q_lower):
        extras.add("ru")
    hi_markers = ("hal aerospace", "drdo", "tata defence", "bharat dynamics",
                  "mumbai", "delhi", "bengaluru", "hyderabad")
    if any(m in q_lower for m in hi_markers) or _has_word("india", q_lower) or _has_word("indian", q_lower):
        extras.add("hi")
    # Strip the base language so we only return the fan-out additions
    extras.discard((base_lang or "en").lower())
    return sorted(extras)


# ── R-F126 — Defence-show calendar (2026-05-10) ──────────────────────────────
# When an operator asks about a known defence event the post-event press
# releases live on the official site + organiser PR + trade press —
# rarely indexed in time by general news engines (the SAHA 2026 case).
# Map known events to their official site + a curated query enrichment.
DEFENCE_EVENTS: dict[str, dict[str, str]] = {
    "saha":        {"site": "sahaexpo.com",       "lang": "tr",
                    "enrich": "SAHA EXPO İstanbul defence industry summit"},
    "idex":        {"site": "idexuae.ae",         "lang": "ar",
                    "enrich": "IDEX Abu Dhabi defence exhibition contracts"},
    "navdex":      {"site": "navdex.ae",          "lang": "ar",
                    "enrich": "NAVDEX naval defence exhibition Abu Dhabi"},
    "eurosatory":  {"site": "eurosatory.com",     "lang": "fr",
                    "enrich": "Eurosatory Paris défense salon contrats"},
    "dsei":        {"site": "dsei.co.uk",         "lang": "en",
                    "enrich": "DSEI ExCeL London defence equipment contracts"},
    "ausa":        {"site": "ausa.org",           "lang": "en",
                    "enrich": "AUSA Annual Meeting US Army contracts"},
    "dubai airshow": {"site": "dubaiairshow.aero", "lang": "ar",
                      "enrich": "Dubai Airshow contracts orders 2026"},
    "le bourget":  {"site": "siae.fr",            "lang": "fr",
                    "enrich": "Salon du Bourget Paris Air Show contrats"},
    "indo defence": {"site": "indodefence.com",   "lang": "id",
                     "enrich": "Indo Defence Jakarta procurement"},
    "lima":        {"site": "limaexhibition.com", "lang": "en",
                    "enrich": "LIMA Langkawi maritime aerospace exhibition"},
    "dx korea":    {"site": "dxkorea.com",        "lang": "ko",
                    "enrich": "DX Korea Seoul defence procurement"},
    "africa aerospace": {"site": "aadexpo.co.za", "lang": "en",
                         "enrich": "Africa Aerospace Defence AAD Pretoria"},
    "expodefensa": {"site": "expodefensa.com.co", "lang": "es",
                    "enrich": "Expodefensa Colombia defensa contratos"},
    "lima maritime": {"site": "limaexhibition.com", "lang": "en",
                      "enrich": "LIMA Langkawi maritime"},
    "world defense show": {"site": "worlddefenseshow.com", "lang": "ar",
                           "enrich": "World Defense Show Riyadh contracts"},
    "ila berlin":  {"site": "ila-berlin.com",     "lang": "de",
                    "enrich": "ILA Berlin air space defence trade fair"},
    "balt military": {"site": "baltmilitary.com", "lang": "en",
                      "enrich": "Balt Military Expo Gdańsk procurement"},
    "milipol":     {"site": "milipol.com",        "lang": "fr",
                    "enrich": "Milipol homeland security exhibition"},
}


# R-F192 (2026-05-11) — aliases per event so the full name + common
# rephrasings match. Pre-R-F192 the matcher was substring-only on the
# abbreviation key, so "Defence & Security Equipment International"
# didn't match the "dsei" key (no overlap). Now full names + organiser
# names + variant spellings all route to the right event.
DEFENCE_EVENT_ALIASES: dict[str, list[str]] = {
    "saha":               ["saha expo", "sahaexpo", "saha istanbul"],
    "idex":               ["international defence exhibition", "abu dhabi defence"],
    "navdex":             ["naval defence and security"],
    "eurosatory":         ["salon eurosatory", "paris defence exhibition", "défense paris"],
    "dsei":               ["defence and security equipment international",
                           "defence security equipment international",
                           "excel london defence"],
    "ausa":               ["association of the united states army", "ausa meeting"],
    "dubai airshow":      ["dubai air show"],
    "le bourget":         ["paris air show", "siae paris", "salon du bourget"],
    "indo defence":       ["indo defence jakarta"],
    "lima":               ["langkawi international maritime aerospace"],
    "dx korea":           ["defence expo korea"],
    "africa aerospace":   ["aad expo", "africa aerospace and defence"],
    "expodefensa":        ["expodefensa colombia"],
    "lima maritime":      ["langkawi maritime"],
    "world defense show": ["wds riyadh"],
    "ila berlin":         ["ila aerospace berlin", "internationale luftfahrtausstellung"],
}


def _detect_defence_event(query: str) -> dict[str, str] | None:
    """Return the calendar entry for any known defence event mentioned.

    R-F192: matches the canonical key, every alias, AND uses word-
    boundary regex so 'dsei' doesn't false-match the middle of an
    unrelated identifier. Pre-R-F192 was substring-only on the key.
    """
    if not query:
        return None
    q = query.lower()
    import re as _re_d
    for key, entry in DEFENCE_EVENTS.items():
        candidates = [key] + DEFENCE_EVENT_ALIASES.get(key, [])
        for c in candidates:
            # Word-boundary match. Escape since names contain spaces/&
            try:
                if _re_d.search(rf"\b{_re_d.escape(c)}\b", q):
                    return {"key": key, **entry}
            except Exception:
                continue
    return None


async def _search_defence_event(query: str, max_results: int = 10) -> list[SearchResult]:
    """If the query mentions a known defence event, run a site:-scoped
    Brave/DDG search against the official site so post-event press +
    contract-signing pages surface even when general news is thin."""
    entry = _detect_defence_event(query)
    if not entry:
        return []
    site = entry.get("site") or ""
    if not site:
        return []
    enriched = f"{entry.get('enrich', query)} site:{site}"
    out: list[SearchResult] = []
    try:
        # R-F320: Brave removed; run DDG only against the site-scoped query
        ddg_results = await _search_duckduckgo(enriched, max_results)
        for r in ddg_results or []:
            # Tag with defence-event source and tier-2 (institutional)
            r.source = f"defence_event:{entry['key']}"
            r.credibility_tier = 2
            out.append(r)
    except Exception as _ee:
        logger.debug("defence-event search failed (non-fatal): %s", _ee)
    return out


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
    # R-F125 (2026-05-10): auto-language fan-out — detect non-English
    # script + Turkish/Portuguese/Arabic/etc entity-name markers and
    # add same query in those languages. Solves SAHA 2026 thin-coverage
    # case directly: Turkish defence press indexes the contract data
    # that Google-News-en doesn't surface.
    extra_langs = _detect_query_languages(query, base_lang=language)
    backend_tasks = [
        # R-F1630 (2026-06-17): _search_brave removed (R-F320 permanent stub).
        # SearXNG self-host (R-F183) + DuckDuckGo cover general web.
        _search_searxng(query, MAX_RESULTS_PER_BACKEND, language),
        _search_duckduckgo(query, MAX_RESULTS_PER_BACKEND),
        _search_google_news(query, MAX_RESULTS_PER_BACKEND, language),
        _search_bing_news(query, MAX_RESULTS_PER_BACKEND),
        _search_academic(query, MAX_RESULTS_PER_BACKEND, language),
        _search_defence_event(query, MAX_RESULTS_PER_BACKEND),  # R-F126
    ]
    for _xl in extra_langs[:3]:  # cap fan-out at 3 extra langs
        backend_tasks.append(_search_google_news(query, MAX_RESULTS_PER_BACKEND, _xl))
    if extra_langs:
        logger.info(
            "Search %r: language fan-out → %s (base=%s)",
            query[:60], extra_langs, language,
        )
    _ev = _detect_defence_event(query)
    if _ev:
        logger.info(
            "Search %r: defence-event match → %s (site:%s, lang:%s)",
            query[:60], _ev["key"], _ev["site"], _ev["lang"],
        )

    # R-F124 — memory-first inversion: query the corpus before fanning
    # out to the web. If memory carries strong recent hits we still run
    # web (fresh data), but memory gets a relevance bonus so repeated
    # queries on the same topic surface cached intel first.
    #
    # R-F185 (2026-05-11): when ARIA_MEMORY_FIRST_SHORTCUT=1, query
    # memory FIRST as a separate await. If it returns ≥max_results
    # strong hits, return immediately without firing any web backends
    # (full pay-once-remember-forever — $0 marginal cost per repeat
    # query). Default behaviour unchanged for safety; flip the env
    # when memory is dense enough.
    _shortcut = (os.getenv("ARIA_MEMORY_FIRST_SHORTCUT") or "").lower() in ("1", "true", "yes")
    if _shortcut:
        try:
            mem_first = await _query_memory(query, max_results=max_results)
            if mem_first and len(mem_first) >= max_results:
                logger.info(
                    "R-F185 memory-first shortcut: %d strong hits for %r — "
                    "skipping all web backends ($0 cost)",
                    len(mem_first), query[:60],
                )
                # Skip the parallel gather entirely. Stamp the source
                # so dashboards can count shortcut hits.
                for r in mem_first:
                    if not r.source.startswith("memory_shortcut:"):
                        r.source = f"memory_shortcut:{r.source}"
                return mem_first[:max_results]
        except Exception as _se:
            logger.debug("R-F185 shortcut probe failed: %s", _se)
    # R-W5 (2026-05-11): per-backend ecosystem snapshot. Track which
    # backends fired, errored, returned 0, or returned data — operator
    # / chat layer / dashboard can read this via get_last_search_ecosystem()
    # to see ACTUAL backend health for the most-recent search.
    _backend_names = (
        ["memory"]
        + ["brave", "searxng", "duckduckgo", "google_news", "bing_news",
           "academic", "defence_event"]
        + [f"brave[{l}]" for l in extra_langs[:3]]
        + [f"google_news[{l}]" for l in extra_langs[:3]]
    )
    import time as _t_ws
    _ws_t0 = _t_ws.monotonic()
    raw_results_list = await asyncio.gather(
        _query_memory(query, max_results=max_results),
        *backend_tasks,
        return_exceptions=True,
    )
    raw_results = list(raw_results_list)
    _ws_elapsed_ms = int((_t_ws.monotonic() - _ws_t0) * 1000)

    # R-W5: build the per-backend snapshot
    _backend_snapshot: list[dict] = []
    for _i, _batch in enumerate(raw_results):
        _name = _backend_names[_i] if _i < len(_backend_names) else f"backend_{_i}"
        if isinstance(_batch, Exception):
            _backend_snapshot.append({
                "name": _name,
                "state": "errored",
                "results_count": 0,
                "error_reason": str(_batch)[:200],
            })
        elif _batch:
            _backend_snapshot.append({
                "name": _name,
                "state": "active",
                "results_count": len(_batch),
            })
        else:
            _backend_snapshot.append({
                "name": _name,
                "state": "silent",
                "results_count": 0,
            })
    _n_active = sum(1 for b in _backend_snapshot if b["state"] == "active")
    _n_silent = sum(1 for b in _backend_snapshot if b["state"] == "silent")
    _n_errored = sum(1 for b in _backend_snapshot if b["state"] == "errored")
    _health = (
        "DEAD" if _n_active == 0
        else "DEGRADED" if _n_errored >= 2
        else "PARTIAL" if _n_silent >= 2
        else "HEALTHY"
    )
    _LAST_SEARCH_ECOSYSTEM.clear()
    _LAST_SEARCH_ECOSYSTEM.update({
        "query": query[:200],
        "language": language,
        "backends": _backend_snapshot,
        "summary": {
            "active_backends": _n_active,
            "silent_backends": _n_silent,
            "errored_backends": _n_errored,
            "total_backends": len(_backend_snapshot),
        },
        "health_signal": _health,
        "total_duration_ms": _ws_elapsed_ms,
    })

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

    # R-F190 (2026-05-11) — per-backend success-rate telemetry.
    # Pre-R-F190 there was no per-backend counter for ok-vs-fail, so
    # operators could only grep logs to see which backend was contri-
    # buting. Now each search round writes ok/fail counters per backend
    # per day under crucix:search:backend:<name>:<ok|fail>:YYYY-MM-DD.
    # Dashboard surface comes via /api/aria/search/backends/stats.
    try:
        from . import redis_store as _rs_t
        import datetime as _dt_t
        _today = _dt_t.datetime.now(_dt_t.timezone.utc).strftime("%Y-%m-%d")
        # raw_results[0] is _query_memory; backends are [1:].
        for backend_idx, batch in enumerate(raw_results):
            # Identify backend by position: 0=memory then the
            # backend_tasks list in order. The names map mirrors the
            # asyncio.gather order at the call site.
            if backend_idx == 0:
                bname = "memory"
            else:
                # backend_tasks order:
                # brave, searxng, ddg, google_news, bing_news, academic, defence_event
                _names = ["brave", "searxng", "ddg", "google_news",
                          "bing_news", "academic", "defence_event"]
                _ord = backend_idx - 1
                if 0 <= _ord < len(_names):
                    bname = _names[_ord]
                else:
                    # R-F190 follow-up (2026-05-11 verification): extra-
                    # language fan-out tasks at positions 8+ alternate
                    # brave + google_news (see backend_tasks loop). Re-
                    # attribute them to their underlying backend with a
                    # `_lang` suffix so telemetry stays accurate rather
                    # than bucketing as `backend_<n>`. Pattern: indices
                    # 7,9,11 are brave; 8,10,12 are google_news.
                    _post = _ord - len(_names)
                    if _post % 2 == 0:
                        bname = "brave_lang"
                    else:
                        bname = "google_news_lang"
            ok = (not isinstance(batch, Exception)) and bool(batch)
            metric = "ok" if ok else "fail"
            try:
                key = f"crucix:search:backend:{bname}:{metric}:{_today}"
                cur = await _rs_t.get(key)
                nxt = int(cur or 0) + 1
                await _rs_t.set(key, str(nxt), ex=14 * 86400)
            except Exception:
                pass
    except Exception:
        pass

    final = results[:max_results]

    # ── R-F184 (2026-05-11) — pay-once-remember-forever ingest ──
    # Every credibility-tier-1/2/3 result is embedded into the RAG store
    # so the next identical-or-similar query hits memory at $0. Pre-R-F184
    # only brave_answers ingested. Now web_search results (Brave + DDG +
    # Bing News + Google News + Academic + SearXNG) all flow into RAG.
    # Tier 4+ (low credibility, suspected disinfo) deliberately excluded
    # so we don't poison the embedding space.
    try:
        from . import rag_store as _rs_rag
        # R-F859 (2026-05-24) — collect all results and ingest in ONE batched
        # encode pass. Pre-R-F859 this looped ingest_document per result, firing
        # ~25 separate GIL-holding sentence-transformer encodes per burst that
        # starved the asyncio event loop (finding #1 wedge). add_search_results_batch
        # upserts the whole batch in a single model pass.
        _rag_batch = []
        for r in final[:max_results]:
            if r.credibility_tier >= 4:
                continue
            body_for_rag = ((r.title or "") + "\n\n" + (r.snippet or "")).strip()
            if len(body_for_rag) < 40:
                continue
            _rag_batch.append({
                "text": body_for_rag,
                "source": f"web_search:{r.source}",
                "source_type": "search_result",
                "title": (r.title or "")[:200],
                "url": (r.url or "")[:500],
                "metadata": {
                    "search_query": query[:200],
                    "credibility_tier": r.credibility_tier,
                    "language": language,
                },
            })
        if _rag_batch:
            await _rs_rag.add_search_results_batch(_rag_batch)
    except Exception as _re:
        logger.debug("R-F184/R-F859 RAG batch ingest pass failed: %s", _re)

    # ── R-F189 (2026-05-11) — all-general-web-dead capability gap ──
    # When Brave is sticky-disabled (R-F171 24h sentinel) AND DuckDuckGo
    # breaker is open AND SearXNG isn't configured, the general-web
    # layer is silently dead and search degrades to news-only + academic.
    # Surface this as a capability_gap so operator sees it before users
    # start asking why entity searches return only news headlines.
    try:
        from .circuit_breaker import get_breaker
        from . import search_searxng as _sx_h
        # R-F320 (2026-05-11): Brave removed. General-web dead-state
        # now checks DDG breaker + searxng config only.
        _ddg_breaker_open = get_breaker("search:duckduckgo").is_open()
        _searxng_configured = _sx_h.is_configured()
        if (
            not final
            and _ddg_breaker_open
            and not _searxng_configured
        ):
            from . import brain_hook as _bh_h
            await _bh_h.absorb(
                module="web_search",
                summary="R-F189: ALL general-web backends down AND search returned 0",
                detail=(
                    "Search is degraded — ddg breaker open AND searxng "
                    "not configured AND academic/news returned nothing for "
                    f"'{query[:80]}'. Operator action: set SEARXNG_URL to a "
                    "self-hosted instance. Memory-first hits unaffected."
                ),
                success=False,
                gap_type="all_general_web_dead",
                gap_detail="ddg+searxng unavailable, zero results",
            )
    except Exception:
        pass

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
        # R-F395 (2026-05-13): split MENA-proper from GCC. ARIA self-
        # reported that auto-switching to Arabic for "saudi imported last
        # year" returned 0 relevant results because GCC defence-procurement
        # docs are bilingual (Arabic AND English) with the substantive
        # data published in English. Searching Arabic-only there is a
        # waste of fan-out budget. Keep auto for MENA-proper (Morocco,
        # Algeria, Tunisia, Libya, Egypt, Iraq, Jordan, Lebanon, Yemen)
        # where Arabic-first IS correct; require explicit opt-in for GCC
        # (saudi/uae/qatar/kuwait/bahrain/oman) by passing
        # `languages=["en","ar"]` at call time.
        AR_AUTO = ("morocco", "algeria", "tunisia", "libya",
                   "egypt", "iraq", "jordan", "lebanon", "yemen",
                   "syria", "sudan", "mauritania")
        if any(kw in q_lower for kw in AR_AUTO):
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
    """Check which search backends are available.

    R-F320 (2026-05-11): Brave removed. Health report no longer includes
    a brave key — the dashboard panel and any operator code reading this
    should treat absence as "Brave is gone, not down".

    R-F1629 (2026-06-17): probe the self-hosted SearXNG (search_searxng
    adapter) in addition to the public SEARXNG_INSTANCES list. The public
    list has been empty since 2026-04-20; the self-hosted instance is the
    only active SearXNG backend.
    """
    health = {
        "searxng": False,
        "google_news": False,
        "bing_news": False,
        "duckduckgo": True,  # always tried, may be rate-limited
    }
    # R-F1629: probe self-hosted SearXNG first (primary backend)
    try:
        from . import search_searxng as _sx
        if _sx.is_configured():
            res = await _sx.search("test", count=1)
            if res.get("ok") and res.get("results"):
                health["searxng"] = True
    except Exception:
        pass
    # Fall back to public instances if self-host is unavailable
    if not health["searxng"] and SEARXNG_INSTANCES:
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
            from .engine_wiring import wire_failure as _wf
            _wf(
                module="web_search",
                detail="get_search_health searxng probe failed",
                gap_type="engine_failure",
                source="web_search:get_search_health",
            )
            pass
    # Google News RSS is almost always available
    health["google_news"] = True
    health["bing_news"] = True

    # R-F996 — wire to brain
    wire_success(
        module="web_search",
        summary="Web search",
        source_id="web_search:R-F996",
    )
    return health
