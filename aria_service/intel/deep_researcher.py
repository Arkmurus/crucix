"""
ARIA Deep Research Engine — Web crawling, multi-page investigation, scenario analysis.

ARIA doesn't just read one page — she crawls entire websites, follows links,
digs into procurement portals, ministry pages, OEM product catalogues, and
think tank archives. She investigates like a senior analyst with unlimited time.

Capabilities:
1. CRAWL — spider a website, follow relevant links, read everything
2. INVESTIGATE — deep-dive a topic across multiple web searches and sources
3. SCENARIO — generate and evaluate multiple strategic scenarios
4. PROFILE — build a complete intelligence profile on an entity/country/OEM
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse, quote_plus
from .engine_wiring import wire_success  # R-F2112 §21a — hook requires at least one wire call in every intel module

import httpx

from ..llm.provider import LLMProvider, LLMResult
from .wire import fail_wire
from . import redis_store as rs
from .knowledge import store_fact, search_knowledge
from .llm_json import parse_llm_json
from .researcher import (
    _fetch_article_text,
    _web_search,
    _analyse_article,
    _process_analysis,
    _load_hypotheses,
    _save_hypotheses,
    _mark_read,
    _get_read_urls,
)

logger = logging.getLogger("aria.deep_research")

# ── User-Agent Rotation Pool ────────────────────────────────────────────────
import random as _random

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
]

def _random_ua() -> str:
    return _random.choice(_USER_AGENTS)  # nosec B311

# Maximum concurrent page fetches during crawl
MAX_CONCURRENT_FETCHES = 5

# ── Web Crawler ──────────────────────────────────────────────────────────────

async def _extract_links(url: str, html: str) -> list[str]:
    """Extract all links from HTML, resolve relative URLs."""
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    links = re.findall(r'href=["\']([^"\'#]+)', html)
    resolved = set()
    for link in links:
        if link.startswith("javascript:") or link.startswith("mailto:"):
            continue
        if link.startswith("//"):
            link = f"https:{link}"
        elif link.startswith("/"):
            link = urljoin(base, link)
        elif not link.startswith("http"):
            link = urljoin(url, link)
        # Stay on same domain or closely related domains
        link_domain = urlparse(link).netloc.lower()
        base_domain = urlparse(url).netloc.lower()
        # Allow same domain and subdomains
        if base_domain in link_domain or link_domain in base_domain:
            resolved.add(link.split("?")[0].split("#")[0])  # Strip query/fragment
    return list(resolved)


async def _fetch_page_with_links(url: str, timeout: float = 15.0) -> tuple[str, list[str]]:
    """Fetch a page and return (text_content, discovered_links). Security validated.

    Now uses the SAME structured extractor as researcher._fetch_article_text
    so the crawler captures titles + headings + paragraphs + lists + tables +
    contact info (emails/phones/addresses) + social links — not just blob text.
    Includes 1 retry with 2s delay on failure and rotates User-Agent strings.

    Lightpanda fallback (2026-04-18): when static fetch returns thin content
    (JS-rendered site — SPA shell), retry through Lightpanda headless
    rendering. Past incident: /teach on synthesismanual.jbi.global
    returned "0 pages / 0 facts" silently because the site redirects to a
    Confluence SPA where static HTML is a JS shell. The fix tries static
    first (cheap), detects JS-rendering via headless.is_thin_content, and
    falls back to full render only when needed.
    """
    import asyncio as _asyncio
    from .security import sanitise_url
    from .researcher import extract_structured_html_async

    url = sanitise_url(url)
    if not url:
        return "", []

    max_attempts = 2
    html = ""
    for attempt in range(max_attempts):
        try:
            from . import url_safety as _us
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:  # no-breaker: uses url_safety.safe_get (SSRF guard) which wraps the actual HTTP call; breaker belongs on safe_get itself  # no-breaker: uses url_safety.safe_get (SSRF guard); breaker belongs on safe_get itself
                resp = await _us.safe_get(client, url, headers={  # R-F1825 (C2-broaden): SSRF guard on researched/discovered URL
                    "User-Agent": _random_ua(),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                })
                if resp.status_code != 200:
                    if attempt < max_attempts - 1:
                        await _asyncio.sleep(2)
                        continue
                    return "", []
                html = resp.text
                break

        except Exception as e:
            logger.debug(f"Page fetch attempt {attempt+1} failed for {url}: {e}")
            if attempt < max_attempts - 1:
                await _asyncio.sleep(2)

    if not html:
        # Primary fetch failed OR returned thin content. Try the
        # professional-researcher fallback stack (PDF detection,
        # Wayback Machine snapshot). This is the legitimate,
        # publicly-citable fallback — NOT CAPTCHA bypass or other
        # evasion techniques.
        try:
            from . import crawl_enhancements as _ce
            pro = await _ce.fetch_with_fallbacks(
                url, allow_pdf=True, allow_wayback=True,
                respect_robots=True, timeout=timeout,
            )
            if pro.get("ok"):
                logger.info(
                    "[crawl] %s served via fallback chain (source=%s)",
                    url[:80], pro.get("source"),
                )
                # Return text + any discovered links from the HTML variant
                text = (pro.get("text") or "")[:8000]
                if pro.get("html"):
                    try:
                        links = await _extract_links(url, pro["html"])
                    except Exception:
                        links = []
                else:
                    links = []
                return text, links
        except Exception as e:
            logger.debug("[crawl] crawl_enhancements fallback failed: %s", e)
        return "", []

    # Check if the static HTML is a JS-rendered shell. If so, retry via
    # Lightpanda (which is baked into the image at /usr/local/bin/lightpanda).
    try:
        from . import headless
        if headless.is_thin_content(html) and headless.is_available():
            logger.info(
                "[crawl] %s returned thin content — falling back to Lightpanda",
                url[:80],
            )
            rendered = await headless.fetch_rendered_html(url, timeout=45.0)
            if rendered and len(rendered) > len(html):
                html = rendered
    except Exception as e:
        logger.debug("[crawl] Lightpanda fallback failed for %s: %s", url[:80], e)

    try:
        links = await _extract_links(url, html)
        extracted = await extract_structured_html_async(html)
        text = extracted.get("text", "")
        if not text:
            # Fallback to plain strip if structured extraction returned nothing
            fallback = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
            fallback = re.sub(r"<style[^>]*>.*?</style>", " ", fallback, flags=re.DOTALL | re.IGNORECASE)
            fallback = re.sub(r"<[^>]+>", " ", fallback)
            fallback = re.sub(r"&\w+;", " ", fallback)
            text = re.sub(r"\s+", " ", fallback).strip()[:8000]

        return text[:8000], links
    except Exception as e:
        logger.debug(f"Page extraction failed for {url}: {e}")
        return "", []


def _score_link_relevance(url: str, link_text: str = "") -> float:
    """Score how relevant a link is for defence/security research."""
    ul = url.lower()
    score = 0

    # High-value page types
    high_kws = ["procurement", "tender", "contract", "acquisition", "rfp", "rfq",
                "defence", "defense", "military", "armed-forces", "security",
                "arms", "weapon", "ammunition", "naval", "aircraft", "armour",
                "export", "licence", "sanction", "embargo", "compliance"]
    for kw in high_kws:
        if kw in ul:
            score += 5

    # Medium-value
    med_kws = ["news", "press", "release", "article", "report", "analysis",
               "budget", "spending", "ministry", "government", "mod", "dod",
               "industry", "manufacturer", "supplier", "partner"]
    for kw in med_kws:
        if kw in ul:
            score += 3

    # Country signals
    countries = ["angola", "mozambique", "nigeria", "kenya", "indonesia", "philippines",
                 "saudi", "uae", "turkey", "korea", "india", "poland", "brazil"]
    for c in countries:
        if c in ul:
            score += 4

    # Penalise non-content pages
    skip_kws = ["login", "signup", "register", "cart", "checkout", "privacy",
                "cookie", "terms", "advertise", "subscribe", "careers", "jobs",
                "css", "js", "image", "font", "static", "assets"]
    for kw in skip_kws:
        if kw in ul:
            score -= 10

    # Penalise file types
    if re.search(r"\.(jpg|jpeg|png|gif|svg|mp4|mp3|zip|exe|css|js|woff)$", ul):
        score -= 20

    return score


# ── Public: Crawl a website ──────────────────────────────────────────────────

async def _publish_crawl_progress(domain: str, status: dict) -> None:
    """Publish crawl progress to Redis so external pollers (e.g. WhatsApp
    listeners) can show live status. Cheap, fire-and-forget — failure is
    silent so it never blocks the actual crawl.
    """
    try:
        key = f"crucix:aria:crawl_progress:{domain}"
        await rs.set_json(key, status, ex=900)  # 15 min TTL
    except Exception as e:
        logger.debug("crawl progress publish failed: %s", e)


@fail_wire(module="deep_researcher", gap_type="source_failure")
async def crawl_website(
    llm: LLMProvider,
    start_url: str,
    max_pages: int = 50,
    context: str = "",
) -> dict:
    """
    Spider a website — follow links, read all relevant pages, extract intelligence.
    Like sending a research analyst to spend a day on a website.

    Live progress is published to Redis at crucix:aria:crawl_progress:{domain}
    every page so external clients can poll for status.
    """
    if not llm or not llm.is_configured:
        return {"error": "LLM not configured"}

    t_start = time.time()
    domain = urlparse(start_url).netloc
    logger.info(f"ARIA crawling website: {domain} (max {max_pages} pages)")

    # ── Known-publisher API shortcut (2026-04-18) ────────────────────
    # Past incident: /teach https://www.nature.com/... timed out at 3min
    # because Nature's bot detection + heavy JS defeated even Lightpanda.
    # But Nature (like arxiv, pubmed, springer, ieee, etc.) has a proper
    # CrossRef / E-utilities / arxiv API that returns structured metadata
    # in under 2 seconds. Try that first; fall through to scraping only
    # if the publisher API fails or returns no content.
    try:
        from . import known_publisher_router as _kpr
        if _kpr.is_known_publisher(start_url):
            logger.info(f"ARIA: {domain} is a known publisher — using API route")
            kpr_result = await _kpr.fetch(start_url)
            if kpr_result.get("ok") and kpr_result.get("text_for_ingest"):
                # Persist to RAG for future retrieval
                try:
                    from . import rag_store as _rag
                    await _rag.ingest_document(
                        kpr_result["text_for_ingest"],
                        source=f"publisher_api:{kpr_result['source']}:{kpr_result.get('doi') or start_url}",
                        source_type="article",
                        title=kpr_result.get("title", "")[:200],
                        url=kpr_result.get("url_canonical") or start_url,
                        extra_metadata={
                            "authors": kpr_result.get("authors", [])[:10],
                            "doi": kpr_result.get("doi", ""),
                            "publication_date": kpr_result.get("publication_date", ""),
                            "citations": kpr_result.get("citations"),
                            "publisher_adapter": kpr_result["source"],
                        },
                    )
                except Exception as _e:
                    logger.debug("publisher RAG ingest failed: %s", _e)

                duration_ms = int((time.time() - t_start) * 1000)
                facts = [{
                    "topic": "publication_metadata",
                    "confidence": "CONFIRMED",
                    "content": f"{kpr_result.get('title','')} — {', '.join(kpr_result.get('authors',[])[:3])} ({kpr_result.get('publication_date','?')})",
                }]
                # Feed brain — this is a proper ingestion, not a failure
                try:
                    from . import brain_hook as _bh
                    await _bh.absorb(
                        module="knowledge_ingestor",
                        summary=f"Publisher-API ingest via {kpr_result['source']}: {kpr_result.get('title','')[:80]}",
                        entity_name=domain,
                        success=True,
                        confidence="CONFIRMED",
                    )
                except Exception:
                    pass

                return {
                    "status": "complete",
                    "domain": domain,
                    "start_url": start_url,
                    "pages_crawled": 1,
                    "pages_read": 1,
                    "facts_learned": len(facts),
                    "hypotheses_generated": 0,
                    "facts": facts,
                    "pages": [{
                        "url": kpr_result.get("url_canonical") or start_url,
                        "title": kpr_result.get("title", ""),
                        "source": f"publisher_api:{kpr_result['source']}",
                    }],
                    "duration_ms": duration_ms,
                    "publisher_api_used": kpr_result["source"],
                }
            else:
                logger.info(
                    "Publisher adapter %s returned no content (%s) — "
                    "falling through to scrape",
                    kpr_result.get("source"), kpr_result.get("error"),
                )
    except Exception as _e:
        logger.debug("publisher router pre-check failed: %s", _e)

    visited: set[str] = set()
    to_visit: list[tuple[float, str]] = [(100, start_url)]  # (priority, url)
    pages_read = 0
    total_facts = 0
    total_hyp = 0
    all_facts: list[dict] = []
    pages_metadata: list[dict] = []
    hypotheses = await _load_hypotheses()

    # Publish initial status
    await _publish_crawl_progress(domain, {
        "status": "starting",
        "domain": domain,
        "start_url": start_url,
        "max_pages": max_pages,
        "pages_read": 0,
        "facts_learned": 0,
        "current_url": start_url,
        "started_at": t_start,
    })

    while to_visit and pages_read < max_pages:
        # Sort by priority (highest first)
        to_visit.sort(key=lambda x: x[0], reverse=True)
        _, url = to_visit.pop(0)

        # Normalise URL
        url = url.rstrip("/")
        if url in visited:
            continue
        visited.add(url)

        # Fetch page
        text, links = await _fetch_page_with_links(url)
        if not text or len(text) < 100:
            continue

        pages_read += 1
        logger.info(f"  [{pages_read}/{max_pages}] Reading: {url[:80]} ({len(text)} chars, {len(links)} links)")

        # Publish per-page progress so live pollers see motion
        await _publish_crawl_progress(domain, {
            "status": "crawling",
            "domain": domain,
            "start_url": start_url,
            "max_pages": max_pages,
            "pages_read": pages_read,
            "facts_learned": total_facts,
            "current_url": url,
            "links_queued": len(to_visit),
            "elapsed_ms": int((time.time() - t_start) * 1000),
        })

        # Analyse content
        article_text = f"URL: {url}\nWebsite: {domain}\n"
        if context:
            article_text += f"Research context: {context}\n"
        article_text += f"Content:\n{text}"

        # ── RAG ingest: chunk + index this page's raw text ──────────────
        try:
            from . import rag_store
            await rag_store.ingest_document(
                text=text,
                source=url,
                source_type="crawl",
                title=url.split("/")[-1] or domain,
                url=url,
                market="",  # could detect from content; left empty for now
                extra_metadata={"crawl_session": domain, "context": context[:200] if context else ""},
            )
        except Exception as e:
            logger.debug("RAG ingest during crawl failed for %s: %s", url, e)

        existing_kb = await asyncio.to_thread(search_knowledge,text[:200])
        parsed = await _analyse_article(llm, article_text, f"crawl:{domain}", existing_kb, hypotheses)

        page_facts = 0
        if parsed:
            fl, hg = await _process_analysis(parsed, f"crawl:{domain}", hypotheses)
            total_facts += fl
            total_hyp += hg
            all_facts.extend(parsed.get("facts", []))
            page_facts = fl

        pages_metadata.append({
            "url": url,
            "chars": len(text),
            "facts": page_facts,
            "links_found": len(links),
        })

        await _mark_read(url)

        # Score and queue discovered links
        for link in links:
            if link not in visited and urlparse(link).netloc.lower() == domain.lower():
                score = _score_link_relevance(link)
                if score > 0:
                    to_visit.append((score, link))

    await _save_hypotheses(hypotheses)

    duration = int((time.time() - t_start) * 1000)
    logger.info(f"Crawl complete: {domain} — {pages_read} pages, {total_facts} facts, {total_hyp} hypotheses ({duration}ms)")

    # Publish final status
    await _publish_crawl_progress(domain, {
        "status": "complete",
        "domain": domain,
        "start_url": start_url,
        "max_pages": max_pages,
        "pages_read": pages_read,
        "facts_learned": total_facts,
        "hypotheses_generated": total_hyp,
        "duration_ms": duration,
        "completed_at": time.time(),
    })

    _crawl_result = {
        "website": domain,
        "start_url": start_url,
        "pages_crawled": pages_read,
        "links_discovered": len(visited),
        "facts_learned": total_facts,
        "hypotheses_generated": total_hyp,
        "facts": all_facts,
        "pages": pages_metadata,
        "duration_ms": duration,
    }

    # ── Brain hook: feed crawl findings to learning ──
    try:
        from . import brain_hook
        _dr_facts = "; ".join(str(f)[:100] for f in all_facts[:10]) if all_facts else "no facts"
        await brain_hook.absorb(
            module="deep_researcher",
            summary=f"Deep research on {domain}: {pages_read} pages, {total_facts} facts, {total_hyp} hypotheses",
            detail=_dr_facts,
            entity_name=domain,
            success=pages_read > 0,
            confidence="ASSESSED",
        )
    except Exception as _bh:
        logger.debug("deep_researcher brain_hook failed: %s", _bh)

    return _crawl_result


@fail_wire(module="deep_researcher", gap_type="source_failure")
async def get_crawl_progress(domain: str) -> dict:
    """Public API: query the live crawl progress for a domain."""
    try:
        key = f"crucix:aria:crawl_progress:{urlparse('https://' + domain).netloc or domain}"
        data = await rs.get_json(key)
        return data or {"status": "no_active_crawl", "domain": domain}
    except Exception as e:
        return {"status": "error", "error": str(e), "domain": domain}


# ── Query chunker — prevents upstream hypothesis leaks hitting search APIs ──


def _chunk_long_query(query: str, max_chars: int = 200) -> list[str]:
    """Split a potentially-long research query into ≤max_chars sub-queries.

    Why: the LLM query-planner occasionally emits a full-sentence hypothesis
    as a single query. Brave Search API returns HTTP 422 past ~400 chars;
    Semantic Scholar tolerates more but 429s under load on long strings.
    Downstream caps truncate, which is worse than splitting because a
    truncated sentence loses the distinguishing keywords at the tail.

    Strategy (no LLM, pure heuristic — fast and deterministic):
      1. If already ≤ max_chars, return [query] unchanged.
      2. Split on natural delimiters that imply parallel clauses:
         ", " / " and " / " including " / " with " / " between " /
         " — " / " or " / ";" — preferring earlier delimiters first.
      3. Keep only segments ≥10 chars and ≤ max_chars; dedupe.
      4. If splits yield <2 usable segments, fall back to a word-boundary
         truncation at max_chars (single segment, still obeys the limit).
      5. Cap at 5 sub-queries — beyond that diminishing returns.
    """
    q = (query or "").strip()
    if len(q) <= max_chars:
        return [q] if q else []

    # Natural-clause delimiters, ordered by preference. Earlier splits
    # usually yield more coherent sub-queries than later ones.
    for delim in (", ", " including ", " and ", " with ", " between ",
                  " — ", " - ", " or ", "; "):
        if delim in q:
            parts = [p.strip(" .,:;-?!\"'\n") for p in q.split(delim)]
            usable = [
                p[:max_chars] for p in parts
                if p and len(p) >= 10 and len(p) <= max_chars * 2
            ]
            # For fragments that exceed max_chars after split, truncate
            # at word boundary rather than dropping them — they still
            # carry useful keywords.
            out: list[str] = []
            seen: set[str] = set()
            for p in usable:
                if len(p) > max_chars:
                    cut = p[:max_chars]
                    if " " in cut:
                        cut = cut.rsplit(" ", 1)[0]
                    p = cut
                k = p.lower()
                if p and k not in seen:
                    seen.add(k)
                    out.append(p)
                if len(out) >= 5:
                    break
            if len(out) >= 2:
                return out

    # Fall-through: no delimiter split worked. Truncate at word boundary.
    cut = q[:max_chars]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return [cut] if cut else []


# ── R-F392 (2026-05-13): query anchor extraction ─────────────────────────
#
# ARIA self-reported on 2026-05-13 that deep_research's "single most
# damaging operational failure" was searching the entire user sentence
# as a raw query — "we need a full DD on Arnaldo La Scala" got passed
# verbatim to the search backend, returning psychology papers instead
# of the person. Four investigations (ADS-Saudi, Arnaldo La Scala,
# Swisscraft network, Luke Oil network) returned zero relevant results
# because the search query was the full instruction string.
#
# Fix: before the fallback paths (LLM-returned-empty / LLM-threw)
# embed `topic` into hardcoded templates, run it through the anchor
# extractor below. It reuses query_decomposer._extract_entities() to
# pick a named entity or country, and falls back to a regex strip
# of common ARIA-voice imperatives ("Aria, run a full DD on…",
# "deep investigation of…", "give me a report on…", etc).
#
# Heuristic — not perfect, intentionally lightweight (no LLM, no NER
# library). Goal is "much better than the full sentence", not "ideal
# entity extraction". The LLM-success path (line 651+) is untouched —
# Claude/DeepSeek extract entities fine when working.

_RF392_PREFIX_PATTERNS = [
    # Greeting + politeness
    re.compile(
        r"^(aria[,:!\s]+)?(please\s+|can\s+you\s+|could\s+you\s+|kindly\s+)?",
        re.IGNORECASE,
    ),
    # "do/run/conduct/launch a full DD/investigation/research on X"
    re.compile(
        r"^(do|run|conduct|perform|execute|launch|start|kick\s+off|begin)\s+"
        r"(an?\s+)?(full\s+|deep\s+|comprehensive\s+|complete\s+|quick\s+)?"
        r"(investigation|dd|due\s+diligence|research|analysis|check|review|"
        r"report|brief|profile|assessment|verdict)\s+(on|of|for|about|into)\s+",
        re.IGNORECASE,
    ),
    # "investigate/research/profile X"
    re.compile(
        r"^(investigate|research|analyse|analyze|review|check|find|profile|"
        r"look\s+up|look\s+into|dig\s+into)\s+"
        r"(into\s+|for\s+|about\s+|on\s+)?",
        re.IGNORECASE,
    ),
    # "give/tell/show me a report on X"
    re.compile(
        r"^(give|tell|show|provide|produce|deliver)\s+(me|us)?\s*"
        r"(a\s+|the\s+|your\s+)?"
        r"(report|brief|summary|profile|info|details?|update|breakdown|"
        r"assessment|verdict|status|view|take|opinion|analysis)\s+"
        r"(on|of|about|for|regarding)\s+",
        re.IGNORECASE,
    ),
    # "i/we need a DD on X"
    re.compile(
        r"^(i|we|the\s+team)\s+(need|want|would\s+like|require|"
        r"are\s+looking\s+for)\s+"
        r"(a\s+|the\s+)?(full\s+|deep\s+|quick\s+)?"
        r"(investigation|dd|due\s+diligence|research|analysis|report|brief)\s+"
        r"(on|of|for|about)\s+",
        re.IGNORECASE,
    ),
    # Leading question words ("what has Saudi imported last year" → strip "what has")
    re.compile(
        r"^(what|who|where|when|why|how|is|are|can|do|does|did|has|have)\s+"
        r"(has|have|did|does|do|is|are|was|were)?\s*",
        re.IGNORECASE,
    ),
]


def _extract_search_anchor(topic: str) -> str:
    """R-F392: pick the cleanest searchable anchor from a natural-
    language topic. Returns named entity if found, else country, else
    the topic with greetings/imperatives stripped and truncated to
    ~8 words. Falls back to the original topic if nothing usable
    remains. Used by deep_research fallback paths so the search query
    isn't the full user sentence.
    """
    if not topic or not topic.strip():
        return topic

    # Stage 1: named-entity / country extraction via query_decomposer
    try:
        from . import query_decomposer as _qd
        named, countries, _years, _amounts = _qd._extract_entities(topic)
        if named:
            return named[0]
        if countries:
            return countries[0]
    except Exception:
        pass

    # Stage 2: regex strip of ARIA-voice imperatives + question words
    cleaned = topic.strip()
    for pat in _RF392_PREFIX_PATTERNS:
        new = pat.sub("", cleaned, count=1)
        if new and new != cleaned:
            cleaned = new
    cleaned = cleaned.strip(",.!?:;\" ").strip()

    if not cleaned:
        return topic.strip()

    # Stage 3: cap length so the query isn't still a multi-clause sentence
    words = cleaned.split()
    if len(words) > 8:
        return " ".join(words[:8])
    return cleaned


# ── Public: Deep investigation on a topic ────────────────────────────────────

def _dd_targeted_queries(entity: str) -> list[str]:
    """R-F1812 — targeted person + procurement + native-language role queries for
    entity DD. A generic web search misses the people behind a company and its
    tender footprint; these surface them. LinkedIn result SNIPPETS name people +
    roles even when the profile page itself can't be fetched. Role/procurement
    nouns are multilingual (PT/FR/IT/ES) so a Portuguese-registered company's
    directors and 'adjudicação' awards are reachable, not English-only."""
    e = (entity or "").strip().strip('"').strip()
    if len(e) < 3:
        return []
    return [
        f'"{e}" (director OR CEO OR founder OR owner OR partner OR administrador OR gerente OR sócio OR proprietário OR directeur OR amministratore)',
        f'"{e}" site:linkedin.com',
        f'"{e}" (contract OR tender OR procurement OR award OR concurso OR adjudicação OR licitação OR appalto OR marché)',
    ]


# R-F1823 — TAINT MITIGATION. Person names reach this engine from UNTRUSTED web
# content (website crawl, search snippets, LLM extraction) and flow into
# investigate_person's LLM synthesis prompt (deep_researcher.py ~1540:
# `...PERSON: "{name}"...`) — a prompt-injection sink. Sanitize at the boundary.
_NAME_INJECTION_MARKERS = (
    "ignore previous", "ignore all", "ignore the", "instruction", "system prompt",
    "disregard", "```", "{{", "}}", "<script", "</", "http://", "https://", "\n",
)


def _sanitize_person_name(raw) -> str | None:
    """Return a safe person name (unicode letters + space/.'-, only, <=80 chars)
    or None to reject. Neutralises prompt injection: no braces/quotes/newlines/
    URLs/marker phrases survive, and over-long blobs are rejected outright."""
    if isinstance(raw, dict):
        raw = raw.get("name")
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not (3 <= len(s) <= 80):          # real names fit this; longer = likely injection blob
        return None
    low = s.lower()
    if any(m in low for m in _NAME_INJECTION_MARKERS):
        return None
    cleaned = " ".join("".join(ch for ch in s if ch.isalpha() or ch in " .'-,").split())
    return cleaned if len(cleaned) >= 3 else None


def _looks_like_person_name(text: str) -> bool:
    """Heuristic: does the text look like a person name (not a company)?

    Returns True if the text has 2-4 words, all start with capital letters,
    and doesn't contain company suffixes (Ltd, GmbH, Inc, etc.) or URL patterns.
    Used by R-F1828 to decide whether to run maigret username enumeration.
    """
    if not text or not isinstance(text, str):
        return False
    s = text.strip()
    if len(s) < 5 or len(s) > 80:
        return False
    # Reject if it looks like a URL
    if "://" in s or "." in s.replace(" ", "") or s.startswith("www."):
        return False
    # Reject if it contains company suffixes
    _company_suffixes = [
        "ltd", "limited", "gmbh", "inc", "corp", "llc", "llp", "plc",
        "sa", "sarl", "sas", "bv", "nv", "ag", "kg", "eood", "sp",
        "lda", "ltda", "sl", "srl",
    ]
    s_lower = s.lower()
    for suffix in _company_suffixes:
        if s_lower.endswith(suffix) or s_lower.endswith(f" {suffix}"):
            return False
    # Count words — person names are typically 2-4 words
    words = s.split()
    if len(words) < 2 or len(words) > 5:
        return False
    # Check if all words start with capital letters (person name pattern)
    capital_count = sum(1 for w in words if w and w[0].isupper())
    if capital_count >= len(words) - 1:  # allow one lowercase (de, van, etc.)
        return True
    return False


async def _discover_and_investigate_people(
    llm: LLMProvider, topic: str, all_facts: list[dict],
    *, max_people: int, t_start: float, budget_s: float,
    seed_people: list | None = None,
    disclosures: list[str] | None = None,   # R-F3966 (C-55) — failure sink
) -> list[dict]:
    """R-F1812/R-F1823 — investigate NAMED INDIVIDUALS (PEP/sanctions/adverse-media)
    via a bounded, time-guarded recursive investigate_person. Candidates come from
    (1) seed_people the caller already KNOWS — registry directors + contact names
    (R-F1823: these get investigated even when web search/LLM extraction miss them,
    e.g. a registry-listed director with no web footprint), then (2) names the LLM
    extracts from the gathered facts. ALL names are taint-sanitized before use.
    Each person costs ~7 searches, so it is capped + budget-skipped."""
    if max_people <= 0:
        return []

    # R-F3966 (C-55) — every failure below used to be a bare logger.debug, so a
    # person who could not be investigated was indistinguishable from a person
    # who does not exist. `disclosures` is an optional sink so existing callers
    # are unaffected; `investigate()` passes one and surfaces it on the result
    # as `people_disclosures`, which dd_orchestrator._surface_research_disclosures
    # already renders. Reusing that channel rather than inventing a second one is
    # deliberate — the sibling investigate() path has disclosed this way since
    # R-F3259, and the person path was simply never given the same wire.
    def _disclose(msg: str, *, module: str) -> None:
        if disclosures is not None:
            disclosures.append(msg)
        # §21a: success AND failure reach the brain. record_gap is deduped 1h
        # (R-F66), so a persistently dead extractor files one gap an hour rather
        # than one per person per DD.
        try:
            from .engine_wiring import wire_failure
            wire_failure(
                module=module,
                detail=msg[:300],
                gap_type="engine_failure",
                source="deep_researcher._discover_and_investigate_people:R-F3966",
            )
        except Exception:
            pass

    candidates: list[dict] = []
    # (1) Seed names the caller already knows (registry/contacts) — high priority.
    for s in (seed_people or []):
        nm = _sanitize_person_name(s)
        if nm:
            candidates.append({"name": nm, "role": "known (registry/contact)"})

    # (2) LLM-extract additional named individuals from the facts.
    if all_facts:
        facts_blob = "\n".join(f"- {f.get('content', '')[:200]}" for f in all_facts[:30])
        extract_prompt = (
            f'From the research facts below about "{topic}", list the NAMED INDIVIDUALS '
            f'(real people) explicitly associated with the entity — directors, officers, '
            f'owners, executives, founders, beneficial owners. Only people actually named '
            f'in the facts; do NOT invent. Return JSON: '
            f'{{"people": [{{"name": str, "role": str}}]}}\n\nFACTS:\n{facts_blob}'
        )
        try:
            r = await llm.complete("ARIA — entity-person extractor.", extract_prompt,
                                   max_tokens=400, timeout=30.0)
            parsed = parse_llm_json(r.text, default={}, source="deep_researcher")
            for p in (parsed.get("people", []) if isinstance(parsed, dict) else []):
                nm = _sanitize_person_name(p)
                if nm:
                    candidates.append({"name": nm,
                                       "role": (p.get("role") or "") if isinstance(p, dict) else ""})
        except Exception as _e:
            # R-F3966 (C-55) §21a — this was logger.debug ONLY, so an LLM outage
            # made the report say "zero named individuals" for an entity whose
            # board is public, with nothing anywhere recording that the extractor
            # never ran.
            logger.debug("person-extraction failed: %s", _e)
            _disclose(
                f"the named-individual extractor did NOT run "
                f"({type(_e).__name__}: {str(_e)[:120]}) — the absence of named "
                f"people below is NOT a finding that none exist",
                module="deep_researcher.person_extraction",
            )

    seen_names: set[str] = set()
    out: list[dict] = []
    for p in candidates:
        if len(out) >= max_people:
            break
        name = p["name"]  # already sanitized
        if name.lower() in seen_names:
            continue
        if time.time() - t_start > budget_s:
            logger.info("R-F1812: person drill-down budget (%.0fs) hit — %d/%d investigated",
                        budget_s, len(out), max_people)
            break
        seen_names.add(name.lower())
        try:
            dossier = await investigate_person(llm, name, context=topic)
        except Exception as _e:
            # R-F3966 (C-55) §21a — `continue` DROPS the person entirely. For a
            # seed_people name that is a director the registry handed us
            # (R-F1823), so a known officer vanished from the report with only a
            # debug line. Name them: a disclosure the reader cannot act on is
            # not a disclosure.
            logger.debug("investigate_person(%s) failed: %s", name, _e)
            _disclose(
                f"could not investigate named individual '{name}' "
                f"({type(_e).__name__}: {str(_e)[:120]}) — this person is "
                f"MISSING from the people section below, not absent from the entity",
                module="deep_researcher.investigate_person",
            )
            continue
        out.append({"name": name, "role": p.get("role", ""), "dossier": dossier})
    return out


def _coerce_topic(raw: object) -> tuple[str, str | None]:
    """R-F3258 — normalise the investigation subject to a searchable string.

    `investigate()` is typed `topic: str`, but every caller derives it from data
    that is never type-checked: `dd_orchestrator._run_digital` builds it as
    ``report.identity.entity_name or target.get("query", "")`` where `target` is
    the caller-supplied DD request and `entity_name: str = ""` is a dataclass
    ANNOTATION — which Python does not enforce at runtime.

    A list therefore reaches `researcher._detect_target_languages(query)`, whose
    first statement is `query.lower()`, and the AttributeError unwinds the whole
    function. `dd_orchestrator.py:6692` catches it and turns it into a data-gap
    string, so EVERY article read and fact learned in that layer is discarded.
    Observed live on the AZURE PARKING LTD DD:
        "deep_research failed: 'list' object has no attribute 'lower'"
        "Raw, unfiltered search results returned: 0"

    Fixed at the BOUNDARY, not at the leaf: `_detect_target_languages(query: str)`
    is correctly typed and its contract was violated by the caller. Scattering
    isinstance guards through every leaf would be symptom-patching (§1).

    Returns ``(topic, coerced_from)``. `coerced_from` is None when the caller
    already passed a clean string; otherwise it names what actually arrived, so a
    repair is DISCLOSED rather than silently papered over.
    """
    if isinstance(raw, str):
        return raw.strip(), None
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, str) and item.strip():
                return item.strip(), f"{type(raw).__name__}[{len(raw)}]"
        return "", f"{type(raw).__name__}[{len(raw)}]"
    if raw is None:
        return "", "None"
    return "", type(raw).__name__


@fail_wire(module="deep_researcher", gap_type="source_failure")
async def investigate(
    llm: LLMProvider,
    topic: str,
    depth: str = "thorough",  # quick (5), thorough (15), exhaustive (30)
    investigate_people: int | None = None,  # R-F1812: recursive person drill-down cap
    seed_people: list | None = None,  # R-F1823: caller-known names (registry/contacts) to investigate
    deadline_s: float | None = None,  # R-F3018: cooperative wall-clock budget
    progress: dict | None = None,     # R-F3316: caller-owned, survives cancellation
) -> dict:
    """
    Deep multi-source investigation on a topic.
    ARIA searches multiple angles, reads articles, cross-references,
    builds and validates hypotheses, and produces a complete intelligence picture.

    R-F1812 — DD depth: for entity/company DD the query fan-out adds targeted
    person, procurement, and native-language role queries; facts carry their
    source URL; and (bounded + time-guarded) each NAMED INDIVIDUAL discovered is
    run through investigate_person so "directors found" become "directors
    investigated". investigate_people caps how many people to drill into
    (default by depth: quick=0, thorough=2, exhaustive=3); 0 disables it.
    """
    if not llm or not llm.is_configured:
        return {"error": "LLM not configured"}

    # R-F3258 — normalise the subject BEFORE any string operation touches it.
    topic, _topic_coerced_from = _coerce_topic(topic)
    if not topic:
        # Refuse loudly rather than run a sweep against nothing: an empty search
        # returning no adverse media must never be mistakable for a clean one.
        return {
            "error": f"investigation subject is unusable (received {_topic_coerced_from or 'empty'}) "
                     "— no search was performed; this is NOT a clean result",
            "topic_coerced_from": _topic_coerced_from,
            "articles_read": 0,
            "facts_learned": 0,
        }
    if _topic_coerced_from:
        logger.warning(
            "[R-F3258] investigation subject arrived as %s, not str — coerced to %r. "
            "The CALLER should be fixed; this guard only stops it costing the layer "
            "all of its research.", _topic_coerced_from, topic[:80],
        )

    t_start = time.time()
    max_searches = {"quick": 3, "thorough": 8, "exhaustive": 15}.get(depth, 8)
    max_articles_per_search = {"quick": 2, "thorough": 3, "exhaustive": 5}.get(depth, 3)
    if investigate_people is None:
        investigate_people = {"quick": 0, "thorough": 2, "exhaustive": 3}.get(depth, 2)
    # Time guard: never let the (expensive ~7-search-each) person drill-down blow
    # the DD budget — skip it if the investigation has already run long.
    try:
        _person_budget_s = float(os.getenv("ARIA_DD_PERSON_BUDGET_S", "200"))
    except (ValueError, TypeError):
        _person_budget_s = 200.0

    # ── R-F3018 — COOPERATIVE DEADLINE ───────────────────────────────────────
    #
    # THE DEFECT. Inside a DD this coroutine ran under an OUTER
    # `asyncio.wait_for(..., 40s)` (`dd_orchestrator._OP_T_DEEPRESEARCH`). Two
    # budgets that never agreed: the outer 40s, and this function's own 200s
    # person-drill-down guard — 5× larger, so it could never be honoured. And the
    # work is structurally bigger than 40s regardless: N sequential web searches
    # (each spaced 0.5s by R-F1594) + per-article LLM analysis + a person walk +
    # a synthesis call.
    #
    # The consequence was not "less research" — `wait_for` CANCELS, so every
    # article read and every fact learned was DISCARDED at the 40s mark and the
    # DD received `{}`. The report then said "deep research did not complete
    # within 40s (bounded) — partial result", which was itself false: the result
    # was not partial, it was zero. Every DD report carried that line.
    #
    # THE FIX (root cause, not a bigger bound). The budget becomes COOPERATIVE
    # and owned by this function: it is checked at every stage boundary, each
    # sub-budget is derived from what REMAINS (never a fixed constant that can
    # exceed the caller's budget), the article gather is harvested with
    # `asyncio.wait(timeout=)` so finished articles survive, and the function
    # RETURNS what it has with `partial=True` + `stopped_after`. 40s of real
    # evidence beats 40s of work thrown away. The caller keeps its `wait_for` as
    # a hard backstop for a genuinely wedged call.
    _t_deadline = (t_start + deadline_s) if (deadline_s and deadline_s > 0) else None

    def _remaining() -> float:
        """Seconds left in the caller's budget; +inf when unbounded."""
        return float("inf") if _t_deadline is None else (_t_deadline - time.time())

    # Reserve for the closing synthesis LLM call, so a bounded run still returns
    # an ASSESSMENT rather than a bag of raw facts. Proportional: reserving a flat
    # 12s out of a 10s budget would leave nothing to research with.
    _synth_reserve_s = 12.0 if _t_deadline is None else max(2.0, min(12.0, deadline_s * 0.3))
    _partial = False
    _stopped_after = ""

    def _mark_partial(stage: str) -> None:
        nonlocal _partial, _stopped_after
        if not _partial:
            _partial, _stopped_after = True, stage
            logger.info("[R-F3018] deep research bounded at %.0fs — stopping after %s, "
                        "returning partial results", deadline_s or 0, stage)

    def _stage(name: str, **detail) -> None:
        """R-F3316 — publish where we are into the CALLER's dict.

        A cooperative stop returns `stopped_after`, but a HARD cancel
        (dd_orchestrator's wait_for backstop) destroys this frame and returns
        nothing, so the report could only say "did not complete within 300s".
        Three attempts at that timeout were hypotheses for exactly this reason.
        Writes here land in the caller's object and outlive the cancellation.
        Never raises: a diagnostic must not be able to break the thing it watches.
        """
        if progress is None:
            return
        try:
            progress["stage"] = name
            for k, v in detail.items():
                progress[k] = v
        except Exception:
            pass

    logger.info(f"ARIA investigating: '{topic}' (depth={depth}, {max_searches} search angles)")

    # Step 1a: Try the query decomposer FIRST — pure regex, zero cost.
    # When intent is clear (DD, compliance, tender, technical, etc.),
    # domain-aware templates produce better queries than the LLM would,
    # AND we skip one LLM round-trip entirely.
    queries: list[str] = []
    _is_entity_dd = False        # R-F1812: gates targeted DD fan-out + person drill-down
    _dd_entity = ""
    try:
        from . import query_decomposer as _qd
        intent = _qd.classify(topic)
        _is_entity_dd = intent.intent.value in ("DD", "COMPANY_RESEARCH")
        _dd_entity = (intent.entities[0] if intent.entities else "") or _extract_search_anchor(topic)
        if not _qd.should_fallback_to_llm(intent):
            decomposed = _qd.decompose(intent, max_queries=max_searches)
            if decomposed:
                queries = decomposed
                logger.info(
                    "ARIA: query_decomposer handled intent=%s conf=%.2f → %d queries (LLM saved)",
                    intent.intent.value, intent.confidence, len(queries),
                )
    except Exception as _e:
        logger.debug("query_decomposer failed, falling through to LLM: %s", _e)

    # R-5002 (2026-05-11) — language-native query expansion. Operator
    # complaint: DD reports are shallow because hypothesis-generation
    # was English-only despite the topic naming non-English jurisdictions.
    # Existing `_detect_target_languages` + `_translate_query` in
    # researcher.py already provide the translation; we just plumb them
    # into the deep-researcher's query list. For topics naming a non-
    # English country, we append translated versions of the top queries
    # so press coverage in the target language is searched alongside
    # English. This closes the gap that made `lngtradinginternational
    # panamasa.com` only get English-language results in the WhatsApp DD.
    def _expand_with_target_languages(qs: list[str]) -> list[str]:
        if not qs:
            return qs
        try:
            from .researcher import _detect_target_languages, _translate_query
        except Exception:
            return qs
        target_langs = _detect_target_languages(topic)
        if not target_langs:
            return qs
        expanded = list(qs)
        # Translate at most the top third of queries, cap at 2 extra langs
        translate_count = max(1, len(qs) // 3)
        for q in qs[:translate_count]:
            for lang in target_langs[:2]:
                try:
                    tq = _translate_query(q, lang)
                except Exception:
                    continue
                if tq and tq != q and tq not in expanded:
                    expanded.append(tq)
        return expanded[:max_searches * 2]  # allow up to 2× for multilingual

    # Step 1b: If decomposer couldn't handle it, fall back to the LLM.
    if not queries:
        angle_prompt = f"""You are ARIA planning a deep intelligence investigation on: "{topic}"

Generate {max_searches} distinct search queries that would cover ALL angles of this topic.
Think like a senior defence analyst — cover:
- Recent news and developments
- Historical context and precedents
- Key players and stakeholders
- Financial/economic dimensions
- Compliance and regulatory aspects
- Competitive dynamics
- Regional/geopolitical implications
- Supply chain and logistics
- Technology and capability gaps
- Future projections and scenarios

Return JSON: {{"queries": ["query1", "query2", ...]}}"""

        try:
            result = await llm.complete(
                "ARIA — intelligence investigation planner.",
                angle_prompt,
                max_tokens=800,
                timeout=30.0,
            )
            parsed_q = parse_llm_json(result.text, default={}, source='deep_researcher')
            queries = parsed_q.get("queries", []) if isinstance(parsed_q, dict) else []
            if not queries:
                # R-F392: LLM returned empty → fall back to the anchor,
                # NOT the full user sentence (the bug ARIA flagged).
                anchor = _extract_search_anchor(topic)
                queries = [anchor]
                if anchor != topic:
                    logger.info(
                        "R-F392: deep_research extracted anchor %r from full topic %r (LLM-empty path)",
                        anchor[:80], topic[:120],
                    )
        except Exception:
            # R-F392: LLM threw → templates use the extracted anchor,
            # not the full user sentence. This was the path that
            # turned "Aria, run a full DD on Arnaldo La Scala please"
            # into 5 searches all containing the full instruction.
            anchor = _extract_search_anchor(topic)
            if anchor != topic:
                logger.info(
                    "R-F392: deep_research extracted anchor %r from full topic %r (LLM-threw path)",
                    anchor[:80], topic[:120],
                )
            queries = [
                f"{anchor} latest news 2026",
                f"{anchor} defence procurement",
                f"{anchor} military contract award",
                f"{anchor} export compliance",
                f"{anchor} competitive landscape",
            ]

    # R-5002 (2026-05-11) — expand with target-language variants when
    # topic names a non-English jurisdiction. Translated queries are
    # appended to the existing English query list so the parallel search
    # in step 2 catches press coverage in the target language too.
    queries = _expand_with_target_languages(queries)

    # Step 2: Search and read articles for each angle
    #
    # 2026-04-08 round 6: this loop was the round-5 timeout bottleneck.
    # Sequential per-article _fetch_article_text + _analyse_article calls:
    #   thorough = 8 queries × 3 articles × 3-5s each = 72-120s
    # Plus angle planning + final synthesis pushed total over 240s, which
    # tripped every timeout in the chain on officeholder questions.
    #
    # Refactor: collect all (query, article) pairs first, then run the
    # per-article fetch + analyse tasks in PARALLEL with a concurrency cap
    # of 6. Same total work, ~6-10x lower wall time.
    import asyncio as _aio
    total_facts = 0
    total_hyp = 0
    all_facts: list[dict] = []
    articles_read = 0
    hypotheses = await _load_hypotheses()
    read_urls = await _get_read_urls()

    # Collect article jobs from all search queries first (web_search itself
    # is fast — the bottleneck is the per-article LLM analysis below).
    # 2026-04-21: chunk long hypotheses into ≤200-char sub-queries BEFORE
    # hitting web_search. Brave returns HTTP 422 past ~400 chars, Semantic
    # Scholar 429s under load on long strings. The LLM query-planner
    # occasionally emits a full sentence as a single query — _chunk_long_query
    # splits on natural delimiters (", " / " and " / " including " / " with ")
    # so a hypothesis like "Angola artillery modernisation including CAESAR
    # howitzer and NATO 155mm procurement with Nexter and Rheinmetall" becomes
    # 3-5 focused searches instead of one 422-triggering string.
    expanded_queries: list[str] = []
    for q in queries[:max_searches]:
        expanded_queries.extend(_chunk_long_query(q))
    # R-F1812 — for entity/company DD, inject targeted person + procurement +
    # native-language role queries HERE (after the max_searches clip) so they
    # always survive into queries_to_run (the max_searches*2 cap below covers
    # them) — the gap behind "Zero named individuals" on shallow DDs.
    if _is_entity_dd and _dd_entity:
        expanded_queries.extend(_dd_targeted_queries(_dd_entity))
    # Dedupe while preserving order
    seen: set[str] = set()
    dedup: list[str] = []
    for q in expanded_queries:
        k = q.lower().strip()
        if k and k not in seen:
            seen.add(k)
            dedup.append(q)
    # R-W10 (2026-05-11): when the original queries list was expanded
    # for multilingual fan-out (R-5002 + R-W6's 40+ language profiles),
    # the post-expansion list can be 2x-4x the original. Capping at
    # max_searches clipped most translated queries off — defeating the
    # whole point of the fan-out. New cap: max_searches * 2 when the
    # expanded list is larger than the original (indicating fan-out
    # happened); otherwise the original cap applies.
    _effective_cap = max_searches
    if len(dedup) > max_searches:
        _effective_cap = max_searches * 2
    queries_to_run = dedup[:_effective_cap]

    article_jobs: list[tuple[str, dict]] = []  # (query, article)
    _queries_run = 0
    for query in queries_to_run:
        # R-F3018 — stop SEARCHING while there is still budget left to READ what
        # we already found. Searching until the clock dies returns links nobody
        # analysed, which is not evidence.
        if _remaining() <= _synth_reserve_s:
            _mark_partial(f"search fan-out ({_queries_run} of {len(queries_to_run)} angles)")
            break
        try:
            results = await _web_search(query)
        except Exception as _e:
            logger.debug("web_search failed for %r: %s", query, _e)
            continue
        _queries_run += 1
        _stage("search fan-out", angles_run=_queries_run)
        # R-F1594: space sequential searches to avoid DDG rate limiting
        await asyncio.sleep(0.5)
        # R-F4201 — a repeat DD is a fresh measurement, not a continuation
        # of a personal reading queue. Applying the process-wide read cache to
        # entity DD made a second run on the same company return zero articles
        # and facts. Ignore historical reads for DD, while preserving the
        # existing fan-out and non-DD discovery behaviour exactly.
        unread = [
            a for a in results
            if _is_entity_dd or a.get("link") not in read_urls
        ]
        for article in unread[:max_articles_per_search]:
            article_jobs.append((query, article))

    # R-F1828: parallel username enumeration via maigret.
    # If the topic looks like a person name (not a company), run maigret
    # in the background to find social/professional profiles.
    _username_results: list[dict] = []
    if _looks_like_person_name(topic):
        try:
            from .osint_username_enum import search_username as _maigret_search
            _username_results = await _maigret_search(topic, timeout=10.0)
        except Exception:
            pass

    # Run fetch + analyse in parallel with a concurrency cap so we don't
    # blow up the deepseek API rate limit. Cap at 6 concurrent — empirically
    # the sweet spot for deepseek's per-key throughput.
    _semaphore = _aio.Semaphore(6)

    async def _process_one_article(query: str, article: dict) -> dict | None:
        """Fetch + analyse a single article. Returns the parsed result or None.
        Side effects (mark_read, _process_analysis) are deferred to the
        post-gather sequential pass to keep the hypothesis dict consistent."""
        async with _semaphore:
            try:
                body = await _fetch_article_text(article.get("link", ""))
            except Exception as e:
                logger.debug("fetch_article_text failed: %s", e)
                return None
            if not body or len(body) < 100:
                return None
            article_text = f"Title: {article['title']}\nSearch: {query}\nContent:\n{body[:4000]}"
            existing_kb = await asyncio.to_thread(search_knowledge,article["title"])
            try:
                parsed = await _analyse_article(
                    llm, article_text, f"investigation:{topic[:30]}", existing_kb, hypotheses
                )
            except Exception as e:
                logger.debug("analyse_article failed: %s", e)
                return None
            return {"parsed": parsed, "article": article}

    # R-F3306 — RETAIN each article as it is analysed, never in a second pass that
    # the first pass has already starved.
    #
    # The R-F3300 live run (dd_f89fdb2e18f6) is the evidence. It reported, HONESTLY
    # this time: "bounded at 297s and stopped after article read (28 of 33 articles
    # analysed) — 0 article(s) analysed, 0 fact(s) retained". Twenty-eight articles
    # were fetched, read and LLM-analysed, and not one fact reached the customer.
    #
    # The cause is the budget split, not the guard. `_article_budget` was
    # `_remaining() - _synth_reserve_s`, i.e. everything except the synthesis
    # reserve, so whenever the article stage used its budget it left retention
    # exactly nothing and R-F3300's floor check fired on the first iteration.
    # Analysing 28 articles and retaining none is strictly worse than analysing 18
    # and retaining 18.
    #
    # Reserving a fixed slice for retention would just move the guess: per-article
    # retention cost depends on state-store latency and is not knowable up front.
    # Retaining incrementally removes the need to predict. Each article is retained
    # as soon as it is analysed, so the split is self-balancing and a cut can only
    # ever lose the un-analysed tail. Retention is still SEQUENTIAL, in the parent
    # coroutine, which preserves the reason it was a separate pass at all:
    # _process_analysis mutates the hypothesis dict and is not reentrant-safe.
    async def _retain(results: list) -> None:
        """Fold analysed articles into facts and hypotheses. Sequential by design."""
        nonlocal articles_read, total_facts, total_hyp
        for _pp_i, r in enumerate(results):
            # R-F3300's floor stays: a slow store must not push us past the bound.
            if _t_deadline is not None and _remaining() <= _synth_reserve_s:
                # CUMULATIVE, not batch-local. _retain is now called once per
                # completed batch, so the loop index counts only this batch and
                # would understate the run as a whole. articles_read and
                # parallel_results are both run totals at this point.
                _mark_partial(
                    f"fact retention ({articles_read} of {len(parallel_results)} "
                    "analysed articles retained)"
                )
                return
            if not r:
                continue
            parsed = r.get("parsed")
            article = r.get("article", {})
            if parsed:
                # R-F3317 — bound the SINGLE call, not just the loop.
                #
                # PROVEN by the R-F3316 diagnostic on its first live run
                # (dd_cd7e7adc36e9): "last stage: fact retention (angles_run=11,
                # jobs=33, analysed=33, retained=14)". All 33 articles were read
                # and 14 were banked, then the caller's hard cancel landed DURING
                # retention and threw all 14 away.
                #
                # The loop already checks the budget before every item, so the
                # only way to overshoot is for ONE item to run longer than the
                # whole remaining budget. _process_analysis writes facts to the
                # knowledge store, and a slow store write does exactly that.
                # Checking before an unbounded await cannot bound it.
                #
                # Capped at what is left minus the synthesis reserve, so the call
                # can never eat the margin the caller needs to return normally. A
                # write that exceeds it is abandoned and the run stops here with
                # everything retained so far INTACT, which is strictly better
                # than being cancelled and losing all of it.
                try:
                    if _t_deadline is None:
                        fl, hg = await _process_analysis(
                            parsed, f"investigation:{topic[:30]}", hypotheses)
                    else:
                        fl, hg = await _aio.wait_for(
                            _process_analysis(parsed, f"investigation:{topic[:30]}", hypotheses),
                            timeout=max(1.0, _remaining() - _synth_reserve_s),
                        )
                except (_aio.TimeoutError, TimeoutError):
                    _mark_partial(
                        f"fact retention ({articles_read} of {len(parallel_results)} "
                        "analysed articles retained; a fact-store write exceeded "
                        "the remaining budget)"
                    )
                    return
                total_facts += fl
                total_hyp += hg
                # R-F1812 — attach the source URL to each fact so findings are
                # auditable back to their article (fixes citation collapse).
                _src = article.get("link", "")
                _facts = parsed.get("facts", []) or []
                for _f in _facts:
                    if isinstance(_f, dict) and _src and not _f.get("source_url"):
                        _f["source_url"] = _src
                all_facts.extend(_facts)
            if article.get("link"):
                await _mark_read(article["link"])
            articles_read += 1
            # R-F4202 — the caller's hard wait_for backstop can cancel this
            # frame after hundreds of seconds. Counts alone survived through
            # progress, while the retained facts did not. Publish a bounded
            # evidence snapshot after each completed retention so the caller can
            # return real partial work even if cancellation lands on the next
            # store operation. Source URLs were attached above; synthesis is
            # deliberately absent and the DD consumer labels these UNVERIFIED.
            _stage("fact retention",
                retained=articles_read,
                partial_result={
                    "topic": topic,
                    "depth": depth,
                    "synthesis_error": "hard operation boundary reached before synthesis",
                    "search_angles": len(queries),
                    "articles_read": articles_read,
                    "facts_learned": total_facts,
                    "hypotheses_generated": total_hyp,
                    "facts": list(all_facts),
                    "synthesis": None,
                    "people": [],
                    "people_disclosures": [],
                    "verification_summary": {
                        "facts_total": len(all_facts),
                        "failed": "verification did not run before the hard boundary",
                    },
                    "partial": True,
                    "stopped_after": "hard operation boundary during fact retention",
                    "budget_s": deadline_s,
                },
            )

    # R-F3018 — harvest what FINISHES inside the budget instead of discarding
    # everything. `gather` is all-or-nothing under an outer cancel; `wait` with a
    # timeout hands back the completed set and lets us cancel the stragglers.
    _stage("article read", jobs=len(article_jobs))
    _article_budget = _remaining() - _synth_reserve_s
    if _t_deadline is not None and _article_budget <= 0:
        _mark_partial("article read (no budget left to analyse articles)")
        parallel_results = []
    elif _t_deadline is None:
        parallel_results = await _aio.gather(
            *(_process_one_article(q, a) for q, a in article_jobs),
            return_exceptions=False,
        )
        await _retain(parallel_results)
    else:
        _tasks = [_aio.ensure_future(_process_one_article(q, a)) for q, a in article_jobs]
        parallel_results = []
        _left: set = set(_tasks)
        while _left:
            # Re-derived every pass, so time spent RETAINING correctly reduces the
            # time left for further reading. That is the self-balancing part.
            _batch_budget = _remaining() - _synth_reserve_s
            if _batch_budget <= 0:
                break
            _done, _left = await _aio.wait(
                _left, timeout=_batch_budget, return_when=_aio.FIRST_COMPLETED
            )
            if not _done:
                break  # budget expired with nothing new finished
            _batch = []
            for _t in _done:
                try:
                    _batch.append(_t.result())
                except Exception as _e:
                    logger.debug("article task failed: %s", _e)
            parallel_results.extend(_batch)
            _stage("article read", analysed=len(parallel_results), jobs=len(_tasks))
            # Retain NOW, while there is still budget, not after it is gone.
            await _retain(_batch)
        for _t in _left:
            _t.cancel()
        if _left:
            _mark_partial(
                f"article read ({len(_tasks) - len(_left)} of {len(_tasks)} "
                "articles analysed)"
            )


    # R-F3300 — a state-store write on the critical path, previously unbounded.
    # A wedged or reconnecting store here would overrun the caller's bound and
    # cost the whole run, trading everything gathered for a hypothesis save.
    # Failing to persist hypotheses degrades the NEXT run; being cancelled
    # destroys THIS one.
    try:
        if _t_deadline is None:
            await _save_hypotheses(hypotheses)
        else:
            await _aio.wait_for(
                _save_hypotheses(hypotheses),
                timeout=max(1.0, min(5.0, _remaining())),
            )
    except (_aio.TimeoutError, TimeoutError):
        _mark_partial("hypothesis save (state store did not respond in budget)")
    except Exception as _e:
        logger.debug("hypothesis save failed: %s", _e)

    # R-F1812 — recursive person drill-down (before synthesis so the people
    # appear IN the assessment). Bounded by investigate_people + time-guarded.
    people: list[dict] = []
    # R-F3018 — the person budget is now DERIVED from what remains, never a fixed
    # 200s constant that can exceed the caller's whole budget. Starting a ~7-search
    # walk with 4s left produced nothing and cost the synthesis its reserve.
    _person_min_s = 15.0
    _person_avail = _remaining() - _synth_reserve_s
    _person_budget_effective = (
        _person_budget_s if _t_deadline is None
        else min(_person_budget_s, (time.time() - t_start) + _person_avail)
    )
    # R-F3966 (C-55) — collects the drill-down's swallowed failures so they
    # reach the report instead of a debug log.
    _people_disclosures: list[str] = []
    if (_is_entity_dd or seed_people) and investigate_people > 0 and _t_deadline is not None \
            and _person_avail < _person_min_s:
        _mark_partial("person drill-down (skipped — insufficient remaining budget)")
    elif (_is_entity_dd or seed_people) and investigate_people > 0 and (time.time() - t_start) < _person_budget_s:
        people = await _discover_and_investigate_people(
            llm, topic, all_facts,
            max_people=investigate_people, t_start=t_start, budget_s=_person_budget_effective,
            seed_people=seed_people,
            disclosures=_people_disclosures,   # R-F3966 (C-55)
        )
        if people:
            logger.info("R-F1812: investigated %d named individual(s) for '%s'", len(people), topic[:60])

    # Step 3: Synthesise findings into an assessment
    synthesis = None
    synthesis_error: str | None = None
    if all_facts:
        # ── R-F3259 — THE ASSESSMENT MAY NOT COST US THE RESEARCH ────────────
        #
        # Everything below formats dicts built from LLM output and scraped web
        # content, and it used to index them DIRECTLY (f['confidence'],
        # h['hypothesis'], p['name']). One malformed entry raised out of
        # investigate(), and dd_orchestrator.py:6692 catches ANY exception from
        # this call and turns it into a data-gap string — so every article read
        # and every fact learned was discarded because the SUMMARY could not be
        # formatted. Live: three separate crashes at these lines (KeyError
        # 'confidence'; 'str' has no 'get'; 'NoneType' has no 'lower').
        #
        # Same failure R-F3018 fixed for the TIMEOUT path ("the result was not
        # partial, it was zero") — this is the ERROR path. The assessment is the
        # LAST step, so losing it must cost the assessment ONLY.
        #
        # .get() everywhere, and a guard that degrades the PROMPT, not the RUN.
        try:
            # R-F1812 — facts carry their source URL so the assessment can cite them.
            def _fact_line(f: dict) -> str:
                if not isinstance(f, dict):
                    return f"- {str(f)[:150]}"
                src = f.get("source_url")
                cite = f" [src: {src}]" if src else ""
                return (f"- [{f.get('confidence', 'ASSESSED')}] {f.get('topic', '?')}: "
                        f"{str(f.get('content', ''))[:150]}{cite}")
            facts_block = "\n".join(_fact_line(f) for f in all_facts[:20])
            # `topic.lower().split()[0]` also IndexError'd on a whitespace-only
            # subject — same blast radius, so anchor defensively.
            _anchor = next(iter(topic.lower().split()), "")
            hyp_block = "\n".join(
                f"- {h['hypothesis']}" for h in hypotheses[:5]
                if isinstance(h, dict) and isinstance(h.get("hypothesis"), str)
                and _anchor and _anchor in h["hypothesis"].lower()
            ) or "None specific to this topic."
            # R-F1812 — fold investigated people into the assessment.
            if people:
                def _person_line(p: dict) -> str:
                    if not isinstance(p, dict):
                        return f"- {str(p)[:120]}"
                    d = p.get("dossier") or {}
                    if not isinstance(d, dict):
                        d = {}
                    risk = d.get("risk_assessment", "?")
                    pep = d.get("pep_status", "")
                    flags = "; ".join(str(x) for x in (d.get("red_flags") or [])[:2])
                    return (f"- {p.get('name', '?')} ({p.get('role') or 'role unknown'}) — risk={risk}"
                            f"{', PEP: ' + pep if pep else ''}{', flags: ' + flags if flags else ''}")
                people_block = "\n".join(_person_line(p) for p in people)
            else:
                people_block = "None investigated (no named individuals surfaced)." if _is_entity_dd else "N/A"
        except Exception as _be:
            synthesis_error = (
                f"assessment prompt could not be built ({type(_be).__name__}: {str(_be)[:100]})"
            )
            logger.warning("[R-F3259] synthesis prompt build failed: %s", _be)
            facts_block = (f"{len(all_facts)} fact(s) were gathered; detail omitted "
                           f"— {synthesis_error}")
            hyp_block = "None specific to this topic."
            people_block = "N/A"

        synth_prompt = f"""ARIA has completed a deep investigation on: "{topic}"

FACTS DISCOVERED ({len(all_facts)} total):
{facts_block}

NAMED INDIVIDUALS INVESTIGATED:
{people_block}

RELEVANT HYPOTHESES:
{hyp_block}

Synthesise these findings into a senior-level intelligence assessment:
1. KEY FINDINGS — the 3-5 most important things discovered
2. STRATEGIC IMPLICATIONS — what this means for Arkmurus
3. OPPORTUNITIES — specific actionable opportunities identified
4. RISKS — what could go wrong, compliance flags
5. PEOPLE — named individuals, their roles, and any risk/PEP/sanctions proximity
6. INTELLIGENCE GAPS — what we still don't know
7. RECOMMENDED ACTIONS — specific next steps, who does what

Return JSON:
{{
  "key_findings": [str],
  "strategic_implications": str,
  "opportunities": [str],
  "risks": [str],
  "people": [{{"name": str, "role": str, "risk": str}}],
  "intelligence_gaps": [str],
  "recommended_actions": [str],
  "confidence": int,
  "epistemic_status": "CONFIRMED|PROBABLE|ASSESSED|UNCERTAIN"
}}"""

        try:
            # R-F3018 — the synthesis call is capped by what is actually left, so a
            # bounded run still returns an assessment (the reserve exists for this)
            # instead of blocking past the caller's deadline and being cancelled.
            _synth_timeout = 60.0 if _t_deadline is None else max(5.0, min(60.0, _remaining()))
            result = await llm.complete(
                "ARIA — senior intelligence analyst producing an assessment.",
                synth_prompt,
                max_tokens=2000,
                timeout=_synth_timeout,
            )
            parsed = parse_llm_json(result.text, source='deep_researcher')
            if isinstance(parsed, dict):
                synthesis = parsed
        except Exception as e:
            logger.warning(f"Synthesis failed: {e}")
            # R-F3259 — disclose it. Without this the caller sees an empty
            # assessment and cannot tell "nothing was found" from "the summary
            # step failed", which is the difference between a clean read and an
            # unmeasured one.
            synthesis_error = (
                synthesis_error
                or f"assessment call failed ({type(e).__name__}: {str(e)[:100]})"
            )
            _mark_partial("synthesis (did not complete in the remaining budget)")

    duration = int((time.time() - t_start) * 1000)
    logger.info(f"Investigation complete: '{topic}' — {articles_read} articles, {total_facts} facts ({duration}ms)")

    # ── Fact verification tagging (2026-04-18) ──────────────────────
    # Per ARIA's 5-point plan: wire verified_intel into deep_research
    # output. This is a CHEAP heuristic pass — no web search, no LLM —
    # that downgrades confidence on unsupported claims and cross-checks
    # against the verified_intel store for past contradictions.
    verification_summary = {
        "facts_total": len(all_facts),
        "confirmed_kept": 0,
        "downgraded_no_source": 0,
        "downgraded_past_contradiction": 0,
        "cross_fact_contradictions": 0,
        # R-F3260 — always present so a consumer can distinguish "ran, found
        # nothing" (None) from "did not run" (a reason string). An absent key
        # would read as the former.
        "failed": None,
    }
    try:
        from . import verified_intel as _vi
        # 1. Pull past-verified facts relevant to this topic (cheap Redis read)
        past_facts = await _vi.get_relevant_verified_facts(topic, limit=20)
        past_by_entity: dict[str, list[dict]] = {}
        for pf in past_facts:
            ent = (pf.get("entity_name") or "").lower().strip()
            if ent:
                past_by_entity.setdefault(ent, []).append(pf)

        # 2. Cheap in-run contradiction detector — same topic, different
        # asserted value → both get flagged UNCERTAIN
        seen_topic_values: dict[str, list[int]] = {}
        for idx, fact in enumerate(all_facts):
            topic_key = (fact.get("topic") or "").lower().strip()
            content = (fact.get("content") or "")[:300].lower()
            if topic_key:
                seen_topic_values.setdefault(topic_key, []).append(idx)

        # 3. Apply downgrade rules
        for idx, fact in enumerate(all_facts):
            original = fact.get("confidence", "ASSESSED")
            content = fact.get("content") or ""
            topic_key = (fact.get("topic") or "").lower().strip()

            # Rule A: if no citation marker in content, downgrade one tier
            has_citation = any(m in content for m in (
                "[from ", "[source:", "per http", "see http",
                "[url:", "via http", "according to ", "stated in ",
            ))
            if not has_citation and original == "CONFIRMED":
                fact["confidence"] = "PROBABLE"
                fact["_verification_note"] = "no inline citation — auto-downgraded"
                verification_summary["downgraded_no_source"] += 1

            # Rule B: if multiple facts share the same topic in this run,
            # both get flagged unless their content is near-identical
            peers = seen_topic_values.get(topic_key, [])
            if len(peers) >= 2 and idx in peers:
                other_idx = [p for p in peers if p != idx][0]
                other_content = (all_facts[other_idx].get("content") or "").lower()
                this_content = content.lower()
                # If they don't share substantial overlap, treat as contradiction
                overlap_words = (
                    set(this_content.split())
                    & set(other_content.split())
                )
                if len(overlap_words) < 5:
                    if fact.get("confidence") in ("CONFIRMED", "PROBABLE"):
                        fact["confidence"] = "UNCERTAIN"
                        fact["_verification_note"] = (
                            "contradicts fact in same run on same topic"
                        )
                        verification_summary["cross_fact_contradictions"] += 1

            # Rule C: cross-check against stored verified facts
            for ent_key, past_list in past_by_entity.items():
                if ent_key and ent_key in content.lower():
                    for pf in past_list:
                        pf_status = pf.get("verification_status", "")
                        if pf_status == "CONTRADICTED":
                            fact["confidence"] = "UNCERTAIN"
                            fact["_verification_note"] = (
                                f"entity '{ent_key}' has CONTRADICTED "
                                f"past-verified facts in store"
                            )
                            verification_summary["downgraded_past_contradiction"] += 1
                            break

            if fact.get("confidence") == "CONFIRMED" and not fact.get("_verification_note"):
                verification_summary["confirmed_kept"] += 1
    except Exception as _ve:
        # R-F3260 — RECORD IT. This tagger is what downgrades uncited claims,
        # flags in-run contradictions and cross-checks past verified facts. At
        # DEBUG, a failure meant all three silently did not run while the report
        # still published "Claims traced to a source 30%" and a confidence floor
        # — traceability computed from a step that never executed. A check that
        # did not run must never be indistinguishable from one that found nothing.
        verification_summary["failed"] = f"{type(_ve).__name__}: {str(_ve)[:120]}"
        logger.warning(
            "[R-F3260] verification tagger failed — citation downgrade, contradiction "
            "detection and past-fact cross-check did NOT run: %s", _ve,
        )

    # ── Brain hook: feed investigation findings to learning ──
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="deep_researcher",
            summary=f"Deep investigation '{topic}': {articles_read} articles, {total_facts} facts, {total_hyp} hypotheses",
            detail=str(synthesis)[:3000] if synthesis else "",
            entity_name=topic,
            success=articles_read > 0,
            confidence="ASSESSED",
        )
    except Exception as _bh:
        logger.debug("deep_researcher investigate brain_hook failed: %s", _bh)

    return {
        "topic": topic,
        # R-F3258 — non-None when the caller passed a non-string subject. Carried
        # into the result so a malformed caller is visible downstream instead of
        # being silently repaired here and forgotten.
        "topic_coerced_from": _topic_coerced_from,
        # R-F3259 — non-None when the assessment could not be produced. The
        # articles/facts below are still real; only the summary is missing.
        "synthesis_error": synthesis_error,
        "depth": depth,
        "search_angles": len(queries),
        "articles_read": articles_read,
        "facts_learned": total_facts,
        "hypotheses_generated": total_hyp,
        "facts": all_facts,
        "synthesis": synthesis,
        "people": people,  # R-F1812: structured per-person dossiers (recursive DD)
        # R-F3966 (C-55) — WHY the people list is short (or empty).
        # _surface_research_disclosures renders these as data gaps.
        "people_disclosures": _people_disclosures,
        "username_enumeration": _username_results,  # R-F1828: maigret social profile discovery
        "verification_summary": verification_summary,
        "duration_ms": duration,
        # R-F3018 — say honestly whether the budget cut the work short, and WHERE.
        # A caller that reports "partial" must be able to show what was gathered
        # and what was skipped; before this, "partial" meant zero.
        "partial": _partial,
        "stopped_after": _stopped_after,
        "budget_s": deadline_s,
    }


# ── Public: Scenario analysis ────────────────────────────────────────────────

@fail_wire(module="deep_researcher", gap_type="source_failure")
async def analyse_scenarios(
    llm: LLMProvider,
    situation: str,
    num_scenarios: int = 4,
) -> dict:
    """
    Generate and evaluate multiple strategic scenarios.
    For each scenario, ARIA assesses probability, implications, and recommended positioning.
    """
    if not llm or not llm.is_configured:
        return {"error": "LLM not configured"}

    t_start = time.time()
    logger.info(f"ARIA scenario analysis: '{situation[:60]}'")

    # Gather relevant knowledge
    existing_kb = await asyncio.to_thread(search_knowledge,situation)
    hypotheses = await _load_hypotheses()
    hyp_block = "\n".join(f"- [{h['status']}] {h['hypothesis']}" for h in hypotheses[:10])

    prompt = f"""You are ARIA conducting a scenario analysis for Arkmurus.

SITUATION: {situation}

EXISTING INTELLIGENCE:
{existing_kb or 'No specific intelligence on file.'}

CURRENT HYPOTHESES:
{hyp_block or 'None.'}

Generate {num_scenarios} distinct, plausible scenarios for how this situation could develop.
For EACH scenario:
- Name it clearly
- Describe what happens (be specific — names, timelines, amounts)
- Probability (0-100%)
- Key indicators that would signal this scenario is unfolding
- Implications for Arkmurus (opportunity or threat)
- Recommended Arkmurus positioning/action
- Compliance considerations

Also identify:
- Which scenario is MOST LIKELY
- Which is MOST DANGEROUS for Arkmurus
- Which is the BIGGEST OPPORTUNITY
- What single piece of intelligence would most help narrow which scenario plays out

Return JSON:
{{
  "scenarios": [
    {{
      "name": str,
      "description": str,
      "probability_pct": int,
      "key_indicators": [str],
      "implications": str,
      "recommended_action": str,
      "compliance_flags": [str],
      "timeline": str
    }}
  ],
  "most_likely": "scenario name",
  "most_dangerous": "scenario name",
  "biggest_opportunity": "scenario name",
  "critical_intelligence_gap": str,
  "overall_recommendation": str
}}"""

    try:
        result = await llm.complete(
            "ARIA — senior strategic analyst conducting scenario planning for defence procurement.",
            prompt,
            max_tokens=3000,
            timeout=90.0,
        )
        parsed = parse_llm_json(result.text, source='deep_researcher')
        if not isinstance(parsed, dict):
            return {"error": "Failed to parse scenarios"}
    except Exception as e:
        return {"error": str(e)}

    duration = int((time.time() - t_start) * 1000)

    # ── Brain hook: feed scenario analysis to learning ──
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="deep_researcher",
            summary=f"Scenario analysis: most_likely={parsed.get('most_likely', '?')}, most_dangerous={parsed.get('most_dangerous', '?')}",
            detail=str(parsed)[:3000],
            entity_name=situation[:80],
            success=True,
            extra_topics=["geopolitics"],
            confidence="ASSESSED",
        )
    except Exception as _bh:
        logger.debug("deep_researcher scenarios brain_hook failed: %s", _bh)

    return {
        "situation": situation,
        "scenarios": parsed.get("scenarios", []),
        "most_likely": parsed.get("most_likely"),
        "most_dangerous": parsed.get("most_dangerous"),
        "biggest_opportunity": parsed.get("biggest_opportunity"),
        "critical_intelligence_gap": parsed.get("critical_intelligence_gap"),
        "overall_recommendation": parsed.get("overall_recommendation"),
        "duration_ms": duration,
    }


# ── Public: Entity/country/OEM profiler ──────────────────────────────────────

@fail_wire(module="deep_researcher", gap_type="source_failure")
async def build_profile(
    llm: LLMProvider,
    entity: str,
    profile_type: str = "auto",  # country, oem, person, organisation
) -> dict:
    """
    Build a complete intelligence profile on any entity.
    Searches multiple sources, reads articles, and produces a comprehensive dossier.
    """
    if not llm or not llm.is_configured:
        return {"error": "LLM not configured"}

    t_start = time.time()
    logger.info(f"ARIA profiling: '{entity}' (type={profile_type})")

    # Search from multiple angles
    searches = [
        f"{entity} defence procurement 2026",
        f"{entity} military contract award",
        f"{entity} arms export import",
        f"{entity} defence budget spending",
        f"{entity} security cooperation agreement",
        f"{entity} sanctions compliance risk",
        f"{entity} defence industry capability",
    ]

    total_facts = 0
    all_facts: list[dict] = []
    hypotheses = await _load_hypotheses()

    for query in searches:
        articles = await _web_search(query)
        # R-F1594: space sequential searches to avoid DDG rate limiting
        await asyncio.sleep(0.5)
        for article in articles[:3]:
            body = await _fetch_article_text(article.get("link", ""))
            if not body or len(body) < 100:
                continue

            article_text = f"Title: {article['title']}\nProfile target: {entity}\nContent:\n{body[:4000]}"
            existing_kb = await asyncio.to_thread(search_knowledge,entity)
            parsed = await _analyse_article(llm, article_text, f"profile:{entity}", existing_kb, hypotheses)

            if parsed:
                fl, _ = await _process_analysis(parsed, f"profile:{entity}", hypotheses)
                total_facts += fl
                all_facts.extend(parsed.get("facts", []))

            if article.get("link"):
                await _mark_read(article["link"])

    await _save_hypotheses(hypotheses)

    # Synthesise into a profile
    profile = None
    if all_facts:
        facts_block = "\n".join(f"- [{f['confidence']}] {f['content'][:200]}" for f in all_facts[:25])

        _kb_existing = await asyncio.to_thread(search_knowledge, entity)
        profile_prompt = f"""ARIA has gathered intelligence on: "{entity}"

DISCOVERED FACTS ({len(all_facts)}):
{facts_block}

EXISTING KNOWLEDGE:
{_kb_existing or 'None on file.'}

Produce a comprehensive intelligence profile:

Return JSON:
{{
  "entity": "{entity}",
  "type": "country|oem|person|organisation",
  "summary": "2-3 sentence overview",
  "key_facts": [str],
  "defence_capabilities": [str],
  "procurement_history": [str],
  "active_programmes": [str],
  "key_relationships": [str],
  "compliance_status": str,
  "risk_factors": [str],
  "opportunities_for_arkmurus": [str],
  "recommended_approach": str,
  "intelligence_gaps": [str],
  "confidence": int
}}"""

        try:
            result = await llm.complete(
                "ARIA — intelligence profiler building a complete dossier.",
                profile_prompt,
                max_tokens=2000,
                timeout=60.0,
            )
            parsed_p = parse_llm_json(result.text, source='deep_researcher')
            if isinstance(parsed_p, dict):
                profile = parsed_p
        except Exception as e:
            logger.warning(f"Profile synthesis failed: {e}")

    duration = int((time.time() - t_start) * 1000)

    # ── Brain hook: feed entity profile to learning ──
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="deep_researcher",
            summary=f"Profile built for '{entity}': {total_facts} facts, {len(searches)} sources",
            detail=str(profile)[:3000] if profile else "",
            entity_name=entity,
            success=total_facts > 0,
            confidence="ASSESSED",
        )
    except Exception as _bh:
        logger.debug("deep_researcher profile brain_hook failed: %s", _bh)

    return {
        "entity": entity,
        "facts_gathered": total_facts,
        "sources_searched": len(searches),
        "profile": profile,
        "raw_facts": all_facts,
        "duration_ms": duration,
    }


# ── Public: Person investigation ────────────────────────────────────────────

@fail_wire(module="deep_researcher", gap_type="source_failure")
async def investigate_person(llm: LLMProvider, name: str, context: str = "") -> dict:
    """Deep investigation of a person — maps professional network, flags risks."""
    if not llm or not llm.is_configured:
        return {"error": "LLM not configured"}

    t_start = time.time()
    logger.info(f"ARIA investigating person: '{name}'")

    # Search from multiple angles
    search_suffixes = [
        "defence procurement",
        "military",
        "sanctions",
        "board director",
        "LinkedIn",
        "corruption allegations",
    ]
    if context:
        search_suffixes.append(context)

    total_facts = 0
    all_facts: list[dict] = []
    hypotheses = await _load_hypotheses()

    for suffix in search_suffixes:
        query = f"{name} {suffix}"
        articles = await _web_search(query)
        # R-F1594: space sequential searches to avoid DDG rate limiting
        await asyncio.sleep(0.5)
        for article in articles[:3]:
            body = await _fetch_article_text(article.get("link", ""))
            if not body or len(body) < 100:
                continue

            article_text = f"Title: {article['title']}\nInvestigation target (person): {name}\nContent:\n{body[:4000]}"
            existing_kb = await asyncio.to_thread(search_knowledge,name)
            parsed = await _analyse_article(llm, article_text, f"person_investigation:{name}", existing_kb, hypotheses)

            if parsed:
                fl, _ = await _process_analysis(parsed, f"person_investigation:{name}", hypotheses)
                total_facts += fl
                all_facts.extend(parsed.get("facts", []))

            if article.get("link"):
                await _mark_read(article["link"])

    await _save_hypotheses(hypotheses)

    # Synthesise into structured person report
    report = None
    if all_facts:
        facts_block = "\n".join(f"- [{f['confidence']}] {f['content'][:200]}" for f in all_facts[:25])
        existing_kb = await asyncio.to_thread(search_knowledge,name)

        synth_prompt = f"""ARIA has investigated a PERSON: "{name}"
{f'Context: {context}' if context else ''}

DISCOVERED FACTS ({len(all_facts)}):
{facts_block}

EXISTING KNOWLEDGE:
{existing_kb or 'None on file.'}

Follow the PERSON INVESTIGATION protocol. Produce a structured intelligence report.

Return JSON:
{{
  "name": "{name}",
  "aliases": [str],
  "roles": [{{"title": str, "organisation": str, "current": bool}}],
  "network": [{{"entity": str, "relationship": str, "relevance": str}}],
  "red_flags": [str],
  "sanctions_proximity": str,
  "pep_status": str,
  "adverse_media": [str],
  "financial_indicators": [str],
  "military_connections": [str],
  "confidence": int,
  "sources": [str],
  "intelligence_gaps": [str],
  "risk_assessment": "LOW|MEDIUM|HIGH|CRITICAL",
  "recommendation": str
}}"""

        try:
            result = await llm.complete(
                "ARIA — senior investigator building a person dossier following the PERSON INVESTIGATION protocol.",
                synth_prompt,
                max_tokens=2000,
                timeout=60.0,
            )
            parsed_r = parse_llm_json(result.text, source='deep_researcher')
            if isinstance(parsed_r, dict):
                report = parsed_r
        except Exception as e:
            logger.warning(f"Person investigation synthesis failed: {e}")

    duration = int((time.time() - t_start) * 1000)

    # ── Brain hook: feed person investigation to learning ──
    try:
        from . import brain_hook
        _risk = report.get("risk_assessment", "unknown") if report else "unknown"
        await brain_hook.absorb(
            module="deep_researcher",
            summary=f"Person investigation '{name}': {total_facts} facts, risk={_risk}",
            detail=str(report)[:3000] if report else "",
            entity_name=name,
            success=total_facts > 0,
            extra_topics=["relationships"],
            confidence="ASSESSED",
        )
    except Exception as _bh:
        logger.debug("deep_researcher person brain_hook failed: %s", _bh)

    return {
        "name": name,
        "facts_gathered": total_facts,
        "report": report,
        "raw_facts": all_facts,
        "duration_ms": duration,
    }


# ── Public: Company investigation ───────────────────────────────────────────

@fail_wire(module="deep_researcher", gap_type="source_failure")
async def investigate_company(llm: LLMProvider, company: str, country: str = "") -> dict:
    """Deep company investigation — ownership, sanctions, compliance history."""
    if not llm or not llm.is_configured:
        return {"error": "LLM not configured"}

    t_start = time.time()
    logger.info(f"ARIA investigating company: '{company}' (country={country})")

    # Search from multiple angles
    search_suffixes = [
        "ownership structure directors",
        "sanctions compliance",
        "defence contracts military",
        "beneficial owner UBO",
        "annual accounts financial",
        "corruption investigation",
        "export control violation",
    ]
    if country:
        search_suffixes.append(f"{country} corporate registry")

    total_facts = 0
    all_facts: list[dict] = []
    hypotheses = await _load_hypotheses()

    for suffix in search_suffixes:
        query = f"{company} {suffix}"
        articles = await _web_search(query)
        # R-F1594: space sequential searches to avoid DDG rate limiting
        await asyncio.sleep(0.5)
        for article in articles[:3]:
            body = await _fetch_article_text(article.get("link", ""))
            if not body or len(body) < 100:
                continue

            article_text = f"Title: {article['title']}\nInvestigation target (company): {company}\nContent:\n{body[:4000]}"
            existing_kb = await asyncio.to_thread(search_knowledge,company)
            parsed = await _analyse_article(llm, article_text, f"company_investigation:{company}", existing_kb, hypotheses)

            if parsed:
                fl, _ = await _process_analysis(parsed, f"company_investigation:{company}", hypotheses)
                total_facts += fl
                all_facts.extend(parsed.get("facts", []))

            if article.get("link"):
                await _mark_read(article["link"])

    await _save_hypotheses(hypotheses)

    # Synthesise into structured company report
    report = None
    if all_facts:
        facts_block = "\n".join(f"- [{f['confidence']}] {f['content'][:200]}" for f in all_facts[:25])
        existing_kb = await asyncio.to_thread(search_knowledge,company)

        synth_prompt = f"""ARIA has investigated a COMPANY: "{company}"
{f'Country: {country}' if country else ''}

DISCOVERED FACTS ({len(all_facts)}):
{facts_block}

EXISTING KNOWLEDGE:
{existing_kb or 'None on file.'}

Follow the COMPANY INVESTIGATION protocol. Produce a structured intelligence report.

Return JSON:
{{
  "company": "{company}",
  "country_of_incorporation": str,
  "corporate_structure": {{
    "parent": str,
    "subsidiaries": [str],
    "beneficial_owners": [str],
    "directors": [str]
  }},
  "ownership_flags": [str],
  "sanctions_exposure": str,
  "business_relationships": [{{"entity": str, "type": str, "relevance": str}}],
  "financial_health": str,
  "compliance_history": [str],
  "adverse_media": [str],
  "defence_connections": [str],
  "government_contracts": [str],
  "red_flags": [str],
  "confidence": int,
  "sources": [str],
  "intelligence_gaps": [str],
  "risk_assessment": "LOW|MEDIUM|HIGH|CRITICAL",
  "recommendation": str
}}"""

        try:
            result = await llm.complete(
                "ARIA — senior investigator building a company dossier following the COMPANY INVESTIGATION protocol.",
                synth_prompt,
                max_tokens=2000,
                timeout=60.0,
            )
            parsed_r = parse_llm_json(result.text, source='deep_researcher')
            if isinstance(parsed_r, dict):
                report = parsed_r
        except Exception as e:
            logger.warning(f"Company investigation synthesis failed: {e}")

    duration = int((time.time() - t_start) * 1000)

    # ── Brain hook: feed company investigation to learning ──
    try:
        from . import brain_hook
        _risk = report.get("risk_assessment", "unknown") if report else "unknown"
        await brain_hook.absorb(
            module="deep_researcher",
            summary=f"Company investigation '{company}' ({country}): {total_facts} facts, risk={_risk}",
            detail=str(report)[:3000] if report else "",
            entity_name=company,
            success=total_facts > 0,
            extra_topics=["compliance", "finance"],
            confidence="ASSESSED",
        )
    except Exception as _bh:
        logger.debug("deep_researcher company brain_hook failed: %s", _bh)

    return {
        "company": company,
        "country": country,
        "facts_gathered": total_facts,
        "report": report,
        "raw_facts": all_facts,
        "duration_ms": duration,
    }


# ── Public: Network mapping ─────────────────────────────────────────────────

@fail_wire(module="deep_researcher", gap_type="source_failure")
async def map_network(llm: LLMProvider, entities: list, context: str = "") -> dict:
    """Map relationships between multiple entities — find hidden connections."""
    if not llm or not llm.is_configured:
        return {"error": "LLM not configured"}

    t_start = time.time()
    entity_names = [str(e).strip() for e in entities if str(e).strip()]
    if len(entity_names) < 2:
        return {"error": "At least 2 entities required for network mapping"}

    logger.info(f"ARIA mapping network: {entity_names}")

    # Search for connections between entities
    total_facts = 0
    all_facts: list[dict] = []
    hypotheses = await _load_hypotheses()

    # Search each entity individually
    for entity in entity_names:
        for suffix in ["defence", "sanctions", "director board"]:
            query = f"{entity} {suffix}"
            articles = await _web_search(query)
            for article in articles[:2]:
                body = await _fetch_article_text(article.get("link", ""))
                if not body or len(body) < 100:
                    continue

                article_text = f"Title: {article['title']}\nNetwork mapping target: {entity}\nContent:\n{body[:4000]}"
                existing_kb = await asyncio.to_thread(search_knowledge,entity)
                parsed = await _analyse_article(llm, article_text, f"network:{','.join(entity_names[:3])}", existing_kb, hypotheses)

                if parsed:
                    fl, _ = await _process_analysis(parsed, f"network:{','.join(entity_names[:3])}", hypotheses)
                    total_facts += fl
                    all_facts.extend(parsed.get("facts", []))

                if article.get("link"):
                    await _mark_read(article["link"])

    # Search for pairwise connections
    for i in range(len(entity_names)):
        for j in range(i + 1, min(i + 3, len(entity_names))):
            query = f'"{entity_names[i]}" "{entity_names[j]}"'
            articles = await _web_search(query)
            for article in articles[:2]:
                body = await _fetch_article_text(article.get("link", ""))
                if not body or len(body) < 100:
                    continue

                article_text = f"Title: {article['title']}\nSearching connection: {entity_names[i]} ↔ {entity_names[j]}\nContent:\n{body[:3000]}"
                parsed = await _analyse_article(llm, article_text, f"network:{','.join(entity_names[:3])}", "", hypotheses)

                if parsed:
                    fl, _ = await _process_analysis(parsed, f"network:{','.join(entity_names[:3])}", hypotheses)
                    total_facts += fl
                    all_facts.extend(parsed.get("facts", []))

                if article.get("link"):
                    await _mark_read(article["link"])

    await _save_hypotheses(hypotheses)

    # Synthesise network map
    network_map = None
    if all_facts:
        facts_block = "\n".join(f"- [{f['confidence']}] {f['content'][:200]}" for f in all_facts[:30])

        # Gather existing knowledge on each entity
        kb_blocks = []
        for entity in entity_names:
            kb = await asyncio.to_thread(search_knowledge, entity)
            if kb:
                kb_blocks.append(f"{entity}: {kb[:300]}")
        kb_text = "\n".join(kb_blocks) if kb_blocks else "None on file."

        synth_prompt = f"""ARIA has investigated a NETWORK of entities: {entity_names}
{f'Context: {context}' if context else ''}

DISCOVERED FACTS ({len(all_facts)}):
{facts_block}

EXISTING KNOWLEDGE:
{kb_text}

Follow the NETWORK ANALYSIS protocol. Map relationships, find hidden connections, assess influence flows.

Return JSON:
{{
  "entities": [
    {{"name": str, "type": "person|company|government", "role_in_network": str}}
  ],
  "connections": [
    {{"from": str, "to": str, "relationship": str, "strength": "strong|moderate|weak|suspected", "evidence": str}}
  ],
  "gatekeepers": [str],
  "hidden_connections": [str],
  "influence_flows": [str],
  "risk_nodes": [{{"entity": str, "risk": str, "severity": "LOW|MEDIUM|HIGH|CRITICAL"}}],
  "network_assessment": str,
  "confidence": int,
  "intelligence_gaps": [str],
  "recommended_actions": [str]
}}"""

        try:
            result = await llm.complete(
                "ARIA — senior investigator mapping a relationship network following the NETWORK ANALYSIS protocol.",
                synth_prompt,
                max_tokens=2500,
                timeout=90.0,
            )
            parsed_n = parse_llm_json(result.text, source='deep_researcher')
            if isinstance(parsed_n, dict):
                network_map = parsed_n
        except Exception as e:
            logger.warning(f"Network mapping synthesis failed: {e}")

    duration = int((time.time() - t_start) * 1000)

    # ── Brain hook: feed network mapping to learning ──
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="deep_researcher",
            summary=f"Network mapping {entity_names[:3]}: {total_facts} facts, {len(entity_names)} entities analysed",
            detail=str(network_map)[:3000] if network_map else "",
            entity_name=", ".join(entity_names[:3]),
            success=total_facts > 0,
            extra_topics=["relationships"],
            confidence="ASSESSED",
        )
    except Exception as _bh:
        logger.debug("deep_researcher network brain_hook failed: %s", _bh)

    # R-F2112 §21a — wire success so the brain knows deep_researcher is active
    try:
        wire_success(module="deep_researcher",
                     summary=f"network mapping: {total_facts} facts, {len(entity_names)} entities",
                     source_id="deep_researcher:network_mapping")
    except Exception:
        pass

    return {
        "entities": entity_names,
        "facts_gathered": total_facts,
        "network": network_map,
        "raw_facts": all_facts,
        "duration_ms": duration,
    }
