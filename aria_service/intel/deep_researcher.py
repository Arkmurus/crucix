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

    Includes 1 retry with 2s delay on failure and rotates User-Agent strings.
    """
    import asyncio as _asyncio
    from .security import sanitise_url, scan_content, strip_dangerous_content
    url = sanitise_url(url)
    if not url:
        return "", []

    max_attempts = 2
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

            links = await _extract_links(url, html)

            # Extract text
            text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<nav[^>]*>.*?</nav>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<header[^>]*>.*?</header>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<footer[^>]*>.*?</footer>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"&\w+;", " ", text)
            text = re.sub(r"\s+", " ", text).strip()

            if len(text) > 2000:
                text = text[200:8000]
            return text[:8000], links

        except Exception as e:
            logger.debug(f"Page fetch attempt {attempt+1} failed for {url}: {e}")
            if attempt < max_attempts - 1:
                await _asyncio.sleep(2)
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

async def crawl_website(
    llm: LLMProvider,
    start_url: str,
    max_pages: int = 50,
    context: str = "",
) -> dict:
    """
    Spider a website — follow links, read all relevant pages, extract intelligence.
    Like sending a research analyst to spend a day on a website.
    """
    if not llm or not llm.is_configured:
        return {"error": "LLM not configured"}

    t_start = time.time()
    domain = urlparse(start_url).netloc
    logger.info(f"ARIA crawling website: {domain} (max {max_pages} pages)")

    visited: set[str] = set()
    to_visit: list[tuple[float, str]] = [(100, start_url)]  # (priority, url)
    pages_read = 0
    total_facts = 0
    total_hyp = 0
    all_facts: list[dict] = []
    hypotheses = await _load_hypotheses()

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

        # Analyse content
        article_text = f"URL: {url}\nWebsite: {domain}\n"
        if context:
            article_text += f"Research context: {context}\n"
        article_text += f"Content:\n{text}"

        existing_kb = search_knowledge(text[:200])
        parsed = await _analyse_article(llm, article_text, f"crawl:{domain}", existing_kb, hypotheses)

        if parsed:
            fl, hg = await _process_analysis(parsed, f"crawl:{domain}", hypotheses)
            total_facts += fl
            total_hyp += hg
            all_facts.extend(parsed.get("facts", []))

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

    return {
        "website": domain,
        "start_url": start_url,
        "pages_crawled": pages_read,
        "links_discovered": len(visited),
        "facts_learned": total_facts,
        "hypotheses_generated": total_hyp,
        "facts": all_facts,
        "duration_ms": duration,
    }


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

    # Step 1: Generate search angles using LLM
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
    total_facts = 0
    total_hyp = 0
    all_facts: list[dict] = []
    articles_read = 0
    hypotheses = await _load_hypotheses()
    read_urls = await _get_read_urls()

    for query in queries[:max_searches]:
        results = await _web_search(query)
        unread = [a for a in results if a.get("link") not in read_urls]

        for article in unread[:max_articles_per_search]:
            body = await _fetch_article_text(article.get("link", ""))
            if not body or len(body) < 100:
                continue

            article_text = f"Title: {article['title']}\nSearch: {query}\nContent:\n{body[:4000]}"
            existing_kb = search_knowledge(article["title"])
            parsed = await _analyse_article(llm, article_text, f"investigation:{topic[:30]}", existing_kb, hypotheses)

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
    return {
        "entity": entity,
        "facts_gathered": total_facts,
        "sources_searched": len(searches),
        "profile": profile,
        "raw_facts": all_facts,
        "duration_ms": duration,
    }
