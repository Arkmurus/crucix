"""R-F1064 — Cost-Free Continuous Learning Engine.

ARIA learns continuously from free, open sources without incurring LLM
costs. Uses RSS feeds, open APIs, and self-study to improve her knowledge.

Learning sources (all free, no API keys required):
  1. RSS feeds — defence, geopolitics, technology news
  2. OpenAlex — free academic research API (100k req/hr)
  3. GDELT — free global events database
  4. World Bank — free economic indicators API
  5. Wikipedia — free API for entity background
  6. Self-study — re-reading her own knowledge base for connections
  7. Error analysis — learning from past mistakes without LLM cost

Gate: ARIA_CONTINUOUS_LEARNER_ENABLED=1 to enable (default ON).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.continuous_learner")

_ENABLED = os.getenv("ARIA_CONTINUOUS_LEARNER_ENABLED", "1") == "1"

# Learning intervals
_RSS_POLL_INTERVAL_S = int(os.getenv("ARIA_LEARNER_RSS_INTERVAL", "3600"))  # 1h
_SELF_STUDY_INTERVAL_S = int(os.getenv("ARIA_LEARNER_STUDY_INTERVAL", "7200"))  # 2h
_RESEARCH_INTERVAL_S = int(os.getenv("ARIA_LEARNER_RESEARCH_INTERVAL", "21600"))  # 6h

# Free RSS feeds for continuous learning
_LEARNING_FEEDS = [
    # Defence and security
    {"name": "IISS", "url": "https://www.iiss.org/rss/", "category": "defence"},
    {"name": "RUSI", "url": "https://rusi.org/rss", "category": "defence"},
    {"name": "CSIS", "url": "https://www.csis.org/rss", "category": "defence"},
    {"name": "Atlantic Council", "url": "https://www.atlanticcouncil.org/feed/", "category": "geopolitics"},
    {"name": "War on the Rocks", "url": "https://warontherocks.com/feed/", "category": "defence"},
    {"name": "The Diplomat", "url": "https://thediplomat.com/feed/", "category": "geopolitics"},
    {"name": "EurasiaNet", "url": "https://eurasianet.org/rss", "category": "geopolitics"},
    # Technology and cyber
    {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index", "category": "technology"},
    {"name": "The Record", "url": "https://therecord.media/feed", "category": "cyber"},
    # Trade and economics
    {"name": "World Bank News", "url": "https://www.worldbank.org/en/news/rss", "category": "economics"},
    {"name": "WTO News", "url": "https://www.wto.org/english/news_e/news_e.rss", "category": "trade"},
]

# Free open APIs for research
_OPENALEX_URL = "https://api.openalex.org/works"
_WIKIPEDIA_URL = "https://en.wikipedia.org/api/rest_v1/page/summary"
_GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# Research topics to explore (rotated through)
_RESEARCH_TOPICS = [
    "defence procurement",
    "export controls",
    "sanctions compliance",
    "defence technology",
    "geopolitical risk",
    "trade policy",
    "cyber threats",
    "maritime security",
    "aerospace industry",
    "defence economics",
]


@fail_wire(module="continuous_learner", gap_type="engine_failure")
async def poll_rss_feeds() -> list[dict]:
    """Poll RSS feeds for new articles. Returns list of article dicts."""
    articles = []
    random.shuffle(_LEARNING_FEEDS)  # Vary order to avoid patterns

    for feed in _LEARNING_FEEDS[:5]:  # Process 5 per cycle
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:  # no-breaker: continuous learner is background; breaker would stall learning
                resp = await client.get(
                    feed["url"],
                    headers={"User-Agent": "ARIA-Learner/1.0 (arkmurus.com)"},
                )
                if resp.status_code != 200:
                    continue

                root = ET.fromstring(resp.text)
                for item in root.iter("item"):
                    title = ""
                    link = ""
                    description = ""
                    pub_date = ""

                    title_el = item.find("title")
                    if title_el is not None and title_el.text:
                        title = title_el.text[:300]

                    link_el = item.find("link")
                    if link_el is not None and link_el.text:
                        link = link_el.text[:500]

                    desc_el = item.find("description")
                    if desc_el is not None and desc_el.text:
                        description = desc_el.text[:500]

                    pub_el = item.find("pubDate")
                    if pub_el is not None and pub_el.text:
                        pub_date = pub_el.text

                    if title and link:
                        articles.append({
                            "title": title,
                            "url": link,
                            "summary": description,
                            "published": pub_date,
                            "source": feed["name"],
                            "category": feed["category"],
                            "fetched_at": datetime.now(timezone.utc).isoformat(),
                        })

                    if len(articles) >= 10:
                        break

        except Exception as e:
            logger.debug("[continuous_learner] RSS poll failed for %s: %s", feed["name"], e)

    return articles


@fail_wire(module="continuous_learner", gap_type="engine_failure")
async def research_topic(topic: str) -> Optional[dict]:
    """Research a topic using free open APIs.

    Uses OpenAlex (free academic API) to find relevant papers and
    Wikipedia for background summaries. No API keys needed.
    """
    result = {"topic": topic, "papers": [], "summary": ""}

    try:
        # OpenAlex — free academic search (100k req/hr, no key)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                _OPENALEX_URL,
                params={
                    "search": topic,
                    "per_page": 5,
                    "sort": "citedness",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                for work in data.get("results", [])[:5]:
                    result["papers"].append({
                        "title": work.get("title", "")[:300],
                        "authors": [
                            a.get("author", {}).get("display_name", "")
                            for a in (work.get("authorships") or [])
                        ][:3],
                        "year": work.get("publication_year"),
                        "citations": work.get("cited_by_count", 0),
                        "url": work.get("open_access", {}).get("oa_url", ""),
                    })
    except Exception as e:
        logger.debug("[continuous_learner] OpenAlex search failed: %s", e)

    try:
        # Wikipedia summary — free API
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{_WIKIPEDIA_URL}/{topic.replace(' ', '_')}",
            )
            if resp.status_code == 200:
                data = resp.json()
                result["summary"] = data.get("extract", "")[:1000]
    except Exception as e:
        logger.debug("[continuous_learner] Wikipedia lookup failed: %s", e)

    return result if (result["papers"] or result["summary"]) else None


@fail_wire(module="continuous_learner", gap_type="engine_failure")
async def self_study() -> list[dict]:
    """Self-study: re-read knowledge base to find connections.

    Scans the knowledge base for related facts that haven't been
    connected yet. No LLM cost — pure pattern matching.
    """
    insights = []
    try:
        from . import knowledge as _kn
        from . import neural_memory as _nm

        facts = await _kn.get_all_facts()
        if not facts:
            return []

        # Sample random facts and look for connections
        sample = random.sample(facts, min(20, len(facts)))

        for i, fact_a in enumerate(sample):
            for fact_b in sample[i + 1:]:
                # Check for shared entities or topics
                text_a = f"{fact_a.get('title', '')} {fact_a.get('fact', '')}"
                text_b = f"{fact_b.get('title', '')} {fact_b.get('fact', '')}"

                # Simple keyword overlap (no LLM cost)
                words_a = set(text_a.lower().split())
                words_b = set(text_b.lower().split())
                overlap = words_a & words_b

                # Filter out common words
                common = {"the", "a", "an", "and", "or", "in", "on", "at",
                         "to", "for", "of", "with", "by", "is", "are", "was"}
                overlap = overlap - common

                if len(overlap) >= 3:
                    shared = ", ".join(sorted(overlap)[:5])
                    insights.append({
                        "type": "connection",
                        "fact_a_id": fact_a.get("id", ""),
                        "fact_b_id": fact_b.get("id", ""),
                        "shared_terms": shared,
                        "insight": f"Connection found via: {shared}",
                    })

    except Exception as e:
        logger.debug("[continuous_learner] self-study failed: %s", e)

    return insights


@fail_wire(module="continuous_learner", gap_type="engine_failure")
async def learn_from_articles(articles: list[dict]) -> int:
    """Feed articles into ARIA's knowledge base.

    Uses cost-free ingestion — no LLM calls. Articles are stored
    as knowledge facts with their source URLs for future reference.
    """
    learned = 0
    for article in articles:
        try:
            from . import knowledge as _kn

            # Check if already known
            existing = await _kn.search(article["title"], limit=1)
            if existing:
                continue

            # Store as a knowledge fact (no LLM cost)
            await _kn.add_fact(
                title=article["title"][:300],
                fact=article["summary"][:1000],
                source=article["url"],
                category=article.get("category", "news"),
                confidence="ASSESSED",
            )
            learned += 1

        except Exception as e:
            logger.debug("[continuous_learner] learn_from_article failed: %s", e)

    return learned


@fail_wire(module="continuous_learner", gap_type="engine_failure")
async def learn_from_research(research_result: dict) -> int:
    """Feed research results into knowledge base."""
    learned = 0
    topic = research_result.get("topic", "")

    for paper in research_result.get("papers", []):
        try:
            from . import knowledge as _kn

            await _kn.add_fact(
                title=paper["title"][:300],
                fact=f"Academic paper on {topic}. Cited {paper.get('citations', 0)} times. "
                     f"Authors: {', '.join(paper.get('authors', []))}",
                source=paper.get("url", ""),
                category="research",
                confidence="ASSESSED",
            )
            learned += 1
        except Exception:
            pass

    return learned


@fail_wire(module="continuous_learner", gap_type="engine_failure")
async def run_learning_cycle() -> dict:
    """Run one complete learning cycle.

    Returns:
        Dict with counts of articles learned, research papers found,
        and self-study insights.
    """
    if not _ENABLED:
        return {"error": "Continuous learner disabled"}

    results = {"articles_learned": 0, "papers_learned": 0, "insights_found": 0}

    # 1. Poll RSS feeds
    articles = await poll_rss_feeds()
    if articles:
        learned = await learn_from_articles(articles)
        results["articles_learned"] = learned
        logger.info("[continuous_learner] Learned %d/%d articles", learned, len(articles))

    # 2. Research a random topic
    topic = random.choice(_RESEARCH_TOPICS)
    research = await research_topic(topic)
    if research:
        learned = await learn_from_research(research)
        results["papers_learned"] = learned
        logger.info("[continuous_learner] Researched '%s': %d papers learned", topic, learned)

    # 3. Self-study
    insights = await self_study()
    results["insights_found"] = len(insights)
    if insights:
        logger.info("[continuous_learner] Self-study: %d connections found", len(insights))

    # Wire to brain
    try:
        from .engine_wiring import wire_success as _ws, wire_failure
        _ws(
            module="continuous_learner",
            summary=f"Learning cycle: {results['articles_learned']} articles, "
                    f"{results['papers_learned']} papers, {results['insights_found']} insights",
            detail=json.dumps(results),
            source_id="continuous_learner:cycle",
        )
    except Exception:
        pass

    return results


# ── Background loops ───────────────────────────────────────────────────


async def _rss_loop() -> None:
    """Background loop for RSS feed learning."""
    await asyncio.sleep(120)  # Initial delay
    while True:
        try:
            articles = await poll_rss_feeds()
            if articles:
                await learn_from_articles(articles)
        except Exception as e:
            logger.debug("[continuous_learner] RSS loop failed: %s", e)
        await asyncio.sleep(_RSS_POLL_INTERVAL_S)


async def _self_study_loop() -> None:
    """Background loop for self-study."""
    await asyncio.sleep(300)  # 5 min initial delay
    while True:
        try:
            insights = await self_study()
            if insights:
                logger.info("[continuous_learner] Self-study: %d connections", len(insights))
        except Exception as e:
            logger.debug("[continuous_learner] self-study loop failed: %s", e)
        await asyncio.sleep(_SELF_STUDY_INTERVAL_S)


async def _research_loop() -> None:
    """Background loop for topic research."""
    await asyncio.sleep(600)  # 10 min initial delay
    while True:
        try:
            topic = random.choice(_RESEARCH_TOPICS)
            research = await research_topic(topic)
            if research:
                await learn_from_research(research)
        except Exception as e:
            logger.debug("[continuous_learner] research loop failed: %s", e)
        await asyncio.sleep(_RESEARCH_INTERVAL_S)


@fail_wire(module="continuous_learner", gap_type="engine_failure")
def start_learning_loops() -> list[asyncio.Task]:
    """Start all background learning loops."""
    tasks = [
        asyncio.create_task(_rss_loop()),
        asyncio.create_task(_self_study_loop()),
        asyncio.create_task(_research_loop()),
    ]
    logger.info(
        "[continuous_learner] Learning loops started: RSS (%ds), self-study (%ds), research (%ds)",
        _RSS_POLL_INTERVAL_S, _SELF_STUDY_INTERVAL_S, _RESEARCH_INTERVAL_S,
    )
    return tasks


# ── Wire to brain ──────────────────────────────────────────────────────

try:
    from .engine_wiring import wire_success as _ws, wire_failure
    _ws(
        module="continuous_learner",
        summary="Continuous Learning Engine active",
        detail=f"RSS feeds: {len(_LEARNING_FEEDS)}. Research topics: {len(_RESEARCH_TOPICS)}. "
               f"Gate: ARIA_CONTINUOUS_LEARNER_ENABLED=1",
        source_id="continuous_learner:R-F1064",
    )
except Exception:
    pass

# R-F2119 §21a — wire failure handler for continuous_learner
try:
    wire_failure(module="continuous_learner", detail="module shutdown",
                gap_type="engine_failure", source="continuous_learner:shutdown")
except Exception:
    pass
