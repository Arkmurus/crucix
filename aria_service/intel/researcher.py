"""
ARIA Research Engine — Active learning through article reading and hypothesis validation.

ARIA doesn't just respond to questions — she actively reads defence/security articles,
extracts intelligence, cross-references with existing knowledge, validates or challenges
her own hypotheses, and grows her domain expertise over time.

This is what makes ARIA a learning analyst, not a chatbot.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx

from ..llm.provider import LLMProvider, LLMResult
from . import redis_store as rs
from .knowledge import store_fact, search_knowledge, _cache as kb_cache
from .intel_ledger import _cache as ledger_cache

logger = logging.getLogger("aria.researcher")

# ── Defence & Security Research Sources ──────────────────────────────────────

RESEARCH_FEEDS = [
    # Tier 1: Core defence procurement intelligence
    {"name": "Defense News", "url": "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml", "category": "defence_procurement"},
    {"name": "Janes", "url": "https://www.janes.com/feeds/news", "category": "defence_industry"},
    {"name": "SIPRI Blog", "url": "https://www.sipri.org/rss.xml", "category": "arms_trade"},
    {"name": "DefenceWeb", "url": "https://www.defenceweb.co.za/feed/", "category": "africa_defence"},
    {"name": "Defense One", "url": "https://www.defenseone.com/rss/", "category": "defence_policy"},
    {"name": "Naval News", "url": "https://www.navalnews.com/feed/", "category": "naval"},
    {"name": "The Defense Post", "url": "https://www.thedefensepost.com/feed/", "category": "defence_news"},
    {"name": "Army Recognition", "url": "https://www.armyrecognition.com/rss", "category": "land_systems"},

    # Tier 2: Geopolitics & security
    {"name": "ISS Africa", "url": "https://issafrica.org/iss-today/feed", "category": "africa_security"},
    {"name": "IISS", "url": "https://www.iiss.org/rss", "category": "strategic_studies"},
    {"name": "War on the Rocks", "url": "https://warontherocks.com/feed/", "category": "strategy"},
    {"name": "RUSI", "url": "https://www.rusi.org/rss.xml", "category": "defence_research"},
    {"name": "Chatham House", "url": "https://www.chathamhouse.org/rss.xml", "category": "geopolitics"},

    # Tier 3: Export controls & compliance
    {"name": "Export Compliance Daily", "url": "https://www.bis.doc.gov/index.php/component/rssfeed/feed/2-federal-register-notices?format=feed", "category": "export_controls"},
    {"name": "DSCA Major Arms Sales", "url": "https://www.dsca.mil/press-media/major-arms-sales/feed", "category": "fms"},

    # Tier 4: Lusophone Africa focus
    {"name": "DW Africa", "url": "https://rss.dw.com/xml/rss-en-africa", "category": "africa_news"},
    {"name": "Africa Confidential", "url": "https://www.africa-confidential.com/rss", "category": "africa_intelligence"},
    {"name": "Club of Mozambique", "url": "https://clubofmozambique.com/feed/", "category": "mozambique"},
]

# ── ARIA's Research Interests (what she's looking for) ───────────────────────

RESEARCH_INTERESTS = [
    "defence procurement tender contract award",
    "military modernisation programme budget",
    "arms export licence approval denial",
    "defence cooperation agreement MOU bilateral",
    "artillery howitzer ammunition acquisition",
    "armoured vehicle IFV APC procurement",
    "UAV drone unmanned aerial system military",
    "patrol vessel corvette frigate naval programme",
    "air defence radar SAM missile system",
    "defence offset industrial participation",
    "Angola Mozambique Guinea-Bissau Cape Verde military",
    "Nigeria Kenya Ghana Senegal defence budget",
    "Turkish defence export Baykar Otokar FNSS Africa",
    "Chinese military export Norinco AVIC Africa",
    "South Korean defence export Hanwha KAI",
    "Russian arms replacement sanction alternative",
    "UK export control ECJU SPIRE licence",
    "ITAR EAR OFAC sanctions compliance",
    "CPLP defence cooperation Portuguese",
    "Cabo Delgado insurgency Mozambique military",
    "counter-terrorism equipment Africa procurement",
    "maritime security Gulf of Guinea piracy",
]

# ── Hypothesis Tracker ───────────────────────────────────────────────────────

HYPOTHESIS_KEY = "crucix:aria:hypotheses"


async def _load_hypotheses() -> list[dict]:
    data = await rs.get_json(HYPOTHESIS_KEY)
    return data or []


async def _save_hypotheses(hypotheses: list[dict]) -> None:
    await rs.set_json(HYPOTHESIS_KEY, hypotheses[:50])  # keep top 50


# ── Article Fetching ─────────────────────────────────────────────────────────

async def _fetch_rss(url: str, timeout: float = 15.0) -> list[dict]:
    """Fetch RSS feed and extract article titles + links."""
    articles = []
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "ARIA-Research/2.0"})
            if resp.status_code != 200:
                return []
            text = resp.text

        # Simple XML extraction (no dependency needed)
        items = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
        if not items:
            items = re.findall(r"<entry>(.*?)</entry>", text, re.DOTALL)

        for item in items[:10]:
            title = re.search(r"<title[^>]*>(.*?)</title>", item, re.DOTALL)
            link = re.search(r"<link[^>]*>(.*?)</link>", item, re.DOTALL)
            if not link:
                link = re.search(r'<link[^>]*href=["\']([^"\']+)', item)
            desc = re.search(r"<description[^>]*>(.*?)</description>", item, re.DOTALL)
            if not desc:
                desc = re.search(r"<summary[^>]*>(.*?)</summary>", item, re.DOTALL)
            pub = re.search(r"<pubDate[^>]*>(.*?)</pubDate>", item, re.DOTALL)

            if title:
                t = re.sub(r"<!\[CDATA\[|\]\]>|<[^>]+>", "", title.group(1)).strip()
                l = ""
                if link:
                    l = re.sub(r"<!\[CDATA\[|\]\]>|<[^>]+>", "", link.group(1)).strip()
                d = ""
                if desc:
                    d = re.sub(r"<!\[CDATA\[|\]\]>|<[^>]+>", "", desc.group(1)).strip()[:500]
                articles.append({
                    "title": t,
                    "link": l,
                    "description": d,
                    "published": pub.group(1).strip() if pub else "",
                })
    except Exception as e:
        logger.debug(f"RSS fetch failed for {url}: {e}")

    return articles


async def _fetch_article_text(url: str, timeout: float = 15.0) -> str:
    """Fetch article body text from URL."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; ARIA-Research/2.0)",
                "Accept": "text/html",
            })
            if resp.status_code != 200:
                return ""
            html = resp.text

        # Strip HTML tags, scripts, styles
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        # Take the meaty middle portion (skip nav/header/footer noise)
        if len(text) > 2000:
            text = text[500:5500]
        return text[:5000]
    except Exception as e:
        logger.debug(f"Article fetch failed for {url}: {e}")
        return ""


async def _web_search(query: str, timeout: float = 10.0) -> list[dict]:
    """Search for articles via Google News RSS."""
    encoded = quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en&gl=US&ceid=US:en"
    return await _fetch_rss(url, timeout)


# ── Core Research Loop ───────────────────────────────────────────────────────

async def research_and_learn(llm: LLMProvider, max_articles: int = 8) -> dict:
    """
    ARIA's autonomous research cycle:
    1. Scan RSS feeds for relevant defence/security articles
    2. Read and extract intelligence from the most relevant ones
    3. Cross-reference with existing knowledge — validate or challenge
    4. Generate hypotheses from patterns
    5. Store verified facts and update confidence levels
    """
    if not llm or not llm.is_configured:
        return {"error": "LLM not configured"}

    t_start = time.time()
    logger.info("ARIA research cycle starting...")

    # ── Step 1: Gather articles from feeds ────────────────────────────────
    all_articles: list[dict] = []
    for feed in RESEARCH_FEEDS:
        articles = await _fetch_rss(feed["url"])
        for a in articles:
            a["source"] = feed["name"]
            a["category"] = feed["category"]
        all_articles.extend(articles)

    if not all_articles:
        logger.warning("No articles fetched from any feed")
        return {"articles_scanned": 0, "facts_learned": 0}

    logger.info(f"Gathered {len(all_articles)} articles from {len(RESEARCH_FEEDS)} feeds")

    # ── Step 2: Score relevance to ARIA's interests ───────────────────────
    scored: list[tuple[float, dict]] = []
    for article in all_articles:
        text = f"{article['title']} {article.get('description', '')}".lower()
        score = 0
        for interest in RESEARCH_INTERESTS:
            words = interest.lower().split()
            matches = sum(1 for w in words if w in text)
            if matches >= 2:
                score += matches * 2
        # Boost Lusophone/Africa
        if any(c in text for c in ["angola", "mozambique", "guinea-bissau", "cape verde", "lusophone"]):
            score += 10
        # Boost procurement
        if any(k in text for k in ["tender", "contract", "procure", "award", "billion", "million"]):
            score += 5
        if score > 0:
            scored.append((score, article))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_articles = [a for _, a in scored[:max_articles]]

    if not top_articles:
        logger.info("No highly relevant articles found this cycle")
        return {"articles_scanned": len(all_articles), "relevant": 0, "facts_learned": 0}

    logger.info(f"Selected {len(top_articles)} relevant articles for deep reading")

    # ── Step 3: Read articles and extract intelligence ────────────────────
    facts_learned = 0
    hypotheses_generated = 0
    existing_hypotheses = await _load_hypotheses()

    for article in top_articles:
        # Get article body if we have a link
        body = ""
        if article.get("link"):
            body = await _fetch_article_text(article["link"])

        article_text = f"Title: {article['title']}\nSource: {article['source']}\n"
        if article.get("description"):
            article_text += f"Summary: {article['description']}\n"
        if body:
            article_text += f"Body: {body[:3000]}\n"

        # Build existing knowledge context
        existing_kb = search_knowledge(article["title"])

        # Hypothesis context
        hyp_context = ""
        if existing_hypotheses:
            hyp_context = "\nARIA'S CURRENT HYPOTHESES (validate or challenge these):\n"
            for h in existing_hypotheses[:5]:
                hyp_context += f"- [{h.get('status','OPEN')}] {h.get('hypothesis','')}\n"

        # Ask ARIA to extract intelligence
        extract_prompt = f"""You are ARIA reading a defence/security article. Extract actionable intelligence.

ARTICLE:
{article_text}

EXISTING KNOWLEDGE (do NOT repeat what you already know):
{existing_kb or 'No existing knowledge on this topic.'}
{hyp_context}

Extract ONLY new intelligence. For each finding:
1. State the fact clearly
2. Assign confidence: CONFIRMED (from official source), PROBABLE (multiple signals), ASSESSED (your analysis), UNCERTAIN (single source)
3. Tag the market/country if applicable
4. If this validates or contradicts an existing hypothesis, say so

Also: if this article reveals a pattern or trend, generate a NEW hypothesis.

Return JSON:
{{
  "facts": [
    {{"topic": "short title", "content": "detailed fact", "confidence": "CONFIRMED|PROBABLE|ASSESSED|UNCERTAIN", "market": "country or region", "source": "article source"}}
  ],
  "hypothesis": {{
    "statement": "if any new hypothesis emerges, state it here",
    "evidence": "what supports this",
    "what_would_confirm": "what evidence would confirm this",
    "what_would_refute": "what would prove this wrong"
  }},
  "validates": "hypothesis text if this validates an existing one, or null",
  "challenges": "hypothesis text if this challenges an existing one, or null",
  "skip": false
}}

If the article contains NO new intelligence, set skip=true and return empty facts."""

        try:
            result = await llm.complete(
                "You are ARIA — Arkmurus Research Intelligence Agent. You are reading defence/security articles to build your knowledge. Extract only genuinely new, actionable intelligence. Be rigorous about confidence levels.",
                extract_prompt,
                max_tokens=1500,
                timeout=60.0,
            )

            # Parse response
            text = result.text
            # Try to extract JSON
            json_match = re.search(r"\{[\s\S]*\}", text)
            if json_match:
                parsed = json.loads(json_match.group())
            else:
                continue

            if parsed.get("skip"):
                continue

            # Store extracted facts
            for fact in (parsed.get("facts") or []):
                topic = fact.get("topic", "")
                content = fact.get("content", "")
                confidence = fact.get("confidence", "ASSESSED")
                if topic and content and len(content) > 20:
                    await store_fact(
                        topic,
                        f"{content} [Source: {article['source']}]",
                        f"research:{article['source']}",
                        confidence,
                    )
                    facts_learned += 1

            # Handle hypothesis generation
            hyp = parsed.get("hypothesis") or {}
            if hyp.get("statement") and len(hyp["statement"]) > 20:
                existing_hypotheses.insert(0, {
                    "hypothesis": hyp["statement"],
                    "evidence": hyp.get("evidence", ""),
                    "what_would_confirm": hyp.get("what_would_confirm", ""),
                    "what_would_refute": hyp.get("what_would_refute", ""),
                    "status": "OPEN",
                    "created_at": datetime.now(timezone.utc).isoformat()[:10],
                    "evidence_count": 1,
                })
                hypotheses_generated += 1

            # Handle hypothesis validation/challenge
            validates = parsed.get("validates")
            if validates:
                for h in existing_hypotheses:
                    if validates.lower() in h.get("hypothesis", "").lower():
                        h["evidence_count"] = h.get("evidence_count", 0) + 1
                        if h["evidence_count"] >= 3:
                            h["status"] = "STRENGTHENED"
                        logger.info(f"Hypothesis validated: {h['hypothesis'][:60]}")

            challenges = parsed.get("challenges")
            if challenges:
                for h in existing_hypotheses:
                    if challenges.lower() in h.get("hypothesis", "").lower():
                        h["status"] = "CHALLENGED"
                        logger.info(f"Hypothesis challenged: {h['hypothesis'][:60]}")

        except Exception as e:
            logger.warning(f"Article analysis failed: {e}")
            continue

    # Save updated hypotheses
    await _save_hypotheses(existing_hypotheses)

    duration = int((time.time() - t_start) * 1000)
    logger.info(
        f"Research cycle complete: {len(all_articles)} scanned, "
        f"{len(top_articles)} read, {facts_learned} facts learned, "
        f"{hypotheses_generated} hypotheses generated ({duration}ms)"
    )

    return {
        "articles_scanned": len(all_articles),
        "relevant_articles": len(top_articles),
        "facts_learned": facts_learned,
        "hypotheses_generated": hypotheses_generated,
        "hypotheses_total": len(existing_hypotheses),
        "duration_ms": duration,
        "top_articles": [{"title": a["title"], "source": a["source"]} for a in top_articles],
    }


async def validate_hypothesis(llm: LLMProvider, hypothesis_text: str) -> dict:
    """
    ARIA actively searches for evidence to validate or refute a specific hypothesis.
    She searches the web, reads articles, and updates the hypothesis status.
    """
    if not llm or not llm.is_configured:
        return {"error": "LLM not configured"}

    hypotheses = await _load_hypotheses()
    target = None
    for h in hypotheses:
        if hypothesis_text.lower() in h.get("hypothesis", "").lower():
            target = h
            break

    if not target:
        return {"error": "Hypothesis not found"}

    # Search for evidence
    search_query = f"{target['hypothesis']} defence procurement evidence 2026"
    articles = await _web_search(search_query)

    if not articles:
        return {"hypothesis": target["hypothesis"], "status": "NO_NEW_EVIDENCE"}

    # Read top 3 articles
    evidence_texts = []
    for a in articles[:3]:
        body = ""
        if a.get("link"):
            body = await _fetch_article_text(a["link"])
        evidence_texts.append(f"Title: {a['title']}\n{body[:1500]}")

    evidence_block = "\n---\n".join(evidence_texts)

    prompt = f"""You are ARIA evaluating a hypothesis against new evidence.

HYPOTHESIS: {target['hypothesis']}
What would confirm: {target.get('what_would_confirm', 'unknown')}
What would refute: {target.get('what_would_refute', 'unknown')}
Current evidence count: {target.get('evidence_count', 0)}

NEW EVIDENCE:
{evidence_block}

Evaluate:
1. Does this evidence SUPPORT, CHALLENGE, or have NO BEARING on the hypothesis?
2. Should the hypothesis status change?
3. Any refinement needed?

Return JSON:
{{
  "verdict": "SUPPORTS|CHALLENGES|NEUTRAL",
  "reasoning": "why",
  "refined_hypothesis": "updated statement if needed, or null",
  "new_status": "OPEN|STRENGTHENED|CHALLENGED|REFUTED|CONFIRMED",
  "confidence_delta": 0
}}"""

    try:
        result = await llm.complete(
            "You are ARIA evaluating intelligence hypotheses with rigorous epistemic standards.",
            prompt,
            max_tokens=800,
            timeout=45.0,
        )
        json_match = re.search(r"\{[\s\S]*\}", result.text)
        if json_match:
            parsed = json.loads(json_match.group())
            # Update hypothesis
            target["status"] = parsed.get("new_status", target["status"])
            target["evidence_count"] = target.get("evidence_count", 0) + (1 if parsed.get("verdict") == "SUPPORTS" else 0)
            if parsed.get("refined_hypothesis"):
                target["hypothesis"] = parsed["refined_hypothesis"]
            await _save_hypotheses(hypotheses)
            return {**parsed, "hypothesis": target["hypothesis"]}
    except Exception as e:
        return {"error": str(e)}

    return {"hypothesis": target["hypothesis"], "status": "EVALUATION_FAILED"}


async def get_hypotheses() -> list[dict]:
    """Return all current hypotheses."""
    return await _load_hypotheses()


async def get_research_summary(llm: LLMProvider) -> dict:
    """Generate a summary of what ARIA has learned recently."""
    hypotheses = await _load_hypotheses()
    kb_size = len((kb_cache or {}).get("facts", []))
    ledger_size = len((ledger_cache or {}).get("signals", []))

    open_h = [h for h in hypotheses if h.get("status") == "OPEN"]
    strong_h = [h for h in hypotheses if h.get("status") == "STRENGTHENED"]
    challenged_h = [h for h in hypotheses if h.get("status") == "CHALLENGED"]

    return {
        "knowledge_base_facts": kb_size,
        "intel_ledger_signals": ledger_size,
        "hypotheses": {
            "total": len(hypotheses),
            "open": len(open_h),
            "strengthened": len(strong_h),
            "challenged": len(challenged_h),
        },
        "top_hypotheses": [
            {"hypothesis": h["hypothesis"], "status": h["status"], "evidence": h.get("evidence_count", 0)}
            for h in hypotheses[:10]
        ],
    }
