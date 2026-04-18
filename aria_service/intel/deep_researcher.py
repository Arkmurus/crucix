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

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse, quote_plus

import httpx

from ..llm.provider import LLMProvider, LLMResult
from . import redis_store as rs
from .knowledge import store_fact, search_knowledge
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
    return _random.choice(_USER_AGENTS)

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
    from .researcher import _extract_structured_html

    url = sanitise_url(url)
    if not url:
        return "", []

    max_attempts = 2
    html = ""
    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(url, headers={
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
        extracted = _extract_structured_html(html)
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

        existing_kb = search_knowledge(text[:200])
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


async def get_crawl_progress(domain: str) -> dict:
    """Public API: query the live crawl progress for a domain."""
    try:
        key = f"crucix:aria:crawl_progress:{urlparse('https://' + domain).netloc or domain}"
        data = await rs.get_json(key)
        return data or {"status": "no_active_crawl", "domain": domain}
    except Exception as e:
        return {"status": "error", "error": str(e), "domain": domain}


# ── Public: Deep investigation on a topic ────────────────────────────────────

async def investigate(
    llm: LLMProvider,
    topic: str,
    depth: str = "thorough",  # quick (5), thorough (15), exhaustive (30)
) -> dict:
    """
    Deep multi-source investigation on a topic.
    ARIA searches multiple angles, reads articles, cross-references,
    builds and validates hypotheses, and produces a complete intelligence picture.
    """
    if not llm or not llm.is_configured:
        return {"error": "LLM not configured"}

    t_start = time.time()
    max_searches = {"quick": 3, "thorough": 8, "exhaustive": 15}.get(depth, 8)
    max_articles_per_search = {"quick": 2, "thorough": 3, "exhaustive": 5}.get(depth, 3)

    logger.info(f"ARIA investigating: '{topic}' (depth={depth}, {max_searches} search angles)")

    # Step 1a: Try the query decomposer FIRST — pure regex, zero cost.
    # When intent is clear (DD, compliance, tender, technical, etc.),
    # domain-aware templates produce better queries than the LLM would,
    # AND we skip one LLM round-trip entirely.
    queries: list[str] = []
    try:
        from . import query_decomposer as _qd
        intent = _qd.classify(topic)
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
            json_match = re.search(r"\{[\s\S]*\}", result.text)
            if json_match:
                queries = json.loads(json_match.group()).get("queries", [])
            else:
                queries = [topic]
        except Exception:
            queries = [
                f"{topic} latest news 2026",
                f"{topic} defence procurement",
                f"{topic} military contract award",
                f"{topic} export compliance",
                f"{topic} competitive landscape",
            ]

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
    article_jobs: list[tuple[str, dict]] = []  # (query, article)
    for query in queries[:max_searches]:
        try:
            results = await _web_search(query)
        except Exception as _e:
            logger.debug("web_search failed for %r: %s", query, _e)
            continue
        unread = [a for a in results if a.get("link") not in read_urls]
        for article in unread[:max_articles_per_search]:
            article_jobs.append((query, article))

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
            existing_kb = search_knowledge(article["title"])
            try:
                parsed = await _analyse_article(
                    llm, article_text, f"investigation:{topic[:30]}", existing_kb, hypotheses
                )
            except Exception as e:
                logger.debug("analyse_article failed: %s", e)
                return None
            return {"parsed": parsed, "article": article}

    parallel_results = await _aio.gather(
        *(_process_one_article(q, a) for q, a in article_jobs),
        return_exceptions=False,
    )

    # Sequential post-processing so _process_analysis can mutate the
    # hypothesis dict consistently (it isn't reentrant-safe).
    for r in parallel_results:
        if not r:
            continue
        parsed = r.get("parsed")
        article = r.get("article", {})
        if parsed:
            fl, hg = await _process_analysis(parsed, f"investigation:{topic[:30]}", hypotheses)
            total_facts += fl
            total_hyp += hg
            all_facts.extend(parsed.get("facts", []))
        if article.get("link"):
            await _mark_read(article["link"])
        articles_read += 1

    await _save_hypotheses(hypotheses)

    # Step 3: Synthesise findings into an assessment
    synthesis = None
    if all_facts:
        facts_block = "\n".join(f"- [{f['confidence']}] {f['topic']}: {f['content'][:150]}" for f in all_facts[:20])
        hyp_block = "\n".join(f"- {h['hypothesis']}" for h in hypotheses[:5] if topic.lower().split()[0] in h.get("hypothesis", "").lower()) or "None specific to this topic."

        synth_prompt = f"""ARIA has completed a deep investigation on: "{topic}"

FACTS DISCOVERED ({len(all_facts)} total):
{facts_block}

RELEVANT HYPOTHESES:
{hyp_block}

Synthesise these findings into a senior-level intelligence assessment:
1. KEY FINDINGS — the 3-5 most important things discovered
2. STRATEGIC IMPLICATIONS — what this means for Arkmurus
3. OPPORTUNITIES — specific actionable opportunities identified
4. RISKS — what could go wrong, compliance flags
5. INTELLIGENCE GAPS — what we still don't know
6. RECOMMENDED ACTIONS — specific next steps, who does what

Return JSON:
{{
  "key_findings": [str],
  "strategic_implications": str,
  "opportunities": [str],
  "risks": [str],
  "intelligence_gaps": [str],
  "recommended_actions": [str],
  "confidence": int,
  "epistemic_status": "CONFIRMED|PROBABLE|ASSESSED|UNCERTAIN"
}}"""

        try:
            result = await llm.complete(
                "ARIA — senior intelligence analyst producing an assessment.",
                synth_prompt,
                max_tokens=2000,
                timeout=60.0,
            )
            json_match = re.search(r"\{[\s\S]*\}", result.text)
            if json_match:
                synthesis = json.loads(json_match.group())
        except Exception as e:
            logger.warning(f"Synthesis failed: {e}")

    duration = int((time.time() - t_start) * 1000)
    logger.info(f"Investigation complete: '{topic}' — {articles_read} articles, {total_facts} facts ({duration}ms)")

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
        "depth": depth,
        "search_angles": len(queries),
        "articles_read": articles_read,
        "facts_learned": total_facts,
        "hypotheses_generated": total_hyp,
        "facts": all_facts,
        "synthesis": synthesis,
        "duration_ms": duration,
    }


# ── Public: Scenario analysis ────────────────────────────────────────────────

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
    existing_kb = search_knowledge(situation)
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
        json_match = re.search(r"\{[\s\S]*\}", result.text)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
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
        for article in articles[:3]:
            body = await _fetch_article_text(article.get("link", ""))
            if not body or len(body) < 100:
                continue

            article_text = f"Title: {article['title']}\nProfile target: {entity}\nContent:\n{body[:4000]}"
            existing_kb = search_knowledge(entity)
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

        profile_prompt = f"""ARIA has gathered intelligence on: "{entity}"

DISCOVERED FACTS ({len(all_facts)}):
{facts_block}

EXISTING KNOWLEDGE:
{search_knowledge(entity) or 'None on file.'}

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
            json_match = re.search(r"\{[\s\S]*\}", result.text)
            if json_match:
                profile = json.loads(json_match.group())
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
        for article in articles[:3]:
            body = await _fetch_article_text(article.get("link", ""))
            if not body or len(body) < 100:
                continue

            article_text = f"Title: {article['title']}\nInvestigation target (person): {name}\nContent:\n{body[:4000]}"
            existing_kb = search_knowledge(name)
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
        existing_kb = search_knowledge(name)

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
            json_match = re.search(r"\{[\s\S]*\}", result.text)
            if json_match:
                report = json.loads(json_match.group())
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
        for article in articles[:3]:
            body = await _fetch_article_text(article.get("link", ""))
            if not body or len(body) < 100:
                continue

            article_text = f"Title: {article['title']}\nInvestigation target (company): {company}\nContent:\n{body[:4000]}"
            existing_kb = search_knowledge(company)
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
        existing_kb = search_knowledge(company)

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
            json_match = re.search(r"\{[\s\S]*\}", result.text)
            if json_match:
                report = json.loads(json_match.group())
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
                existing_kb = search_knowledge(entity)
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
            kb = search_knowledge(entity)
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
            json_match = re.search(r"\{[\s\S]*\}", result.text)
            if json_match:
                network_map = json.loads(json_match.group())
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

    return {
        "entities": entity_names,
        "facts_gathered": total_facts,
        "network": network_map,
        "raw_facts": all_facts,
        "duration_ms": duration,
    }
