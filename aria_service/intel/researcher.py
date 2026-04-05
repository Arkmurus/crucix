"""
ARIA Research Engine — Active learning through article reading and hypothesis validation.

ARIA doesn't just respond to questions — she actively reads defence/security articles,
extracts intelligence, cross-references with existing knowledge, validates or challenges
her own hypotheses, and grows her domain expertise over time.

Three modes of learning:
1. AUTONOMOUS — scans 30+ RSS feeds + web searches every 6 hours
2. ON-DEMAND — reads any article URL you give her
3. WHATSAPP — reads articles shared via WhatsApp links

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

# ── GLOBAL Defence & Security Research Sources ───────────────────────────────

RESEARCH_FEEDS = [
    # ── Global Defence Procurement ────────────────────────────────────────
    {"name": "Defense News", "url": "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml", "category": "defence_procurement"},
    {"name": "Janes", "url": "https://www.janes.com/feeds/news", "category": "defence_industry"},
    {"name": "Defense One", "url": "https://www.defenseone.com/rss/", "category": "defence_policy"},
    {"name": "The Defense Post", "url": "https://www.thedefensepost.com/feed/", "category": "defence_news"},
    {"name": "Army Recognition", "url": "https://www.armyrecognition.com/rss", "category": "land_systems"},
    {"name": "Naval News", "url": "https://www.navalnews.com/feed/", "category": "naval"},
    {"name": "Air Force Technology", "url": "https://www.airforce-technology.com/feed/", "category": "aerospace"},
    {"name": "Army Technology", "url": "https://www.army-technology.com/feed/", "category": "land_systems"},
    {"name": "Naval Technology", "url": "https://www.naval-technology.com/feed/", "category": "naval"},
    {"name": "Shephard Media", "url": "https://www.shephardmedia.com/feed/", "category": "defence_industry"},

    # ── Arms Trade & Policy ───────────────────────────────────────────────
    {"name": "SIPRI Blog", "url": "https://www.sipri.org/rss.xml", "category": "arms_trade"},
    {"name": "DSCA Major Arms Sales", "url": "https://www.dsca.mil/press-media/major-arms-sales/feed", "category": "fms"},
    {"name": "IISS", "url": "https://www.iiss.org/rss", "category": "strategic_studies"},
    {"name": "RUSI", "url": "https://www.rusi.org/rss.xml", "category": "defence_research"},
    {"name": "War on the Rocks", "url": "https://warontherocks.com/feed/", "category": "strategy"},
    {"name": "Chatham House", "url": "https://www.chathamhouse.org/rss.xml", "category": "geopolitics"},
    {"name": "CSIS", "url": "https://www.csis.org/rss.xml", "category": "strategy"},
    {"name": "RAND", "url": "https://www.rand.org/pubs/rss.xml", "category": "defence_research"},

    # ── Regional: Africa ──────────────────────────────────────────────────
    {"name": "DefenceWeb", "url": "https://www.defenceweb.co.za/feed/", "category": "africa_defence"},
    {"name": "ISS Africa", "url": "https://issafrica.org/iss-today/feed", "category": "africa_security"},
    {"name": "DW Africa", "url": "https://rss.dw.com/xml/rss-en-africa", "category": "africa_news"},
    {"name": "Africa Confidential", "url": "https://www.africa-confidential.com/rss", "category": "africa_intelligence"},
    {"name": "Club of Mozambique", "url": "https://clubofmozambique.com/feed/", "category": "mozambique"},

    # ── Regional: Middle East ─────────────────────────────────────────────
    {"name": "Al-Monitor Defence", "url": "https://www.al-monitor.com/rss", "category": "middle_east"},
    {"name": "Middle East Eye", "url": "https://www.middleeasteye.net/rss", "category": "middle_east"},

    # ── Regional: Asia-Pacific ────────────────────────────────────────────
    {"name": "The Diplomat", "url": "https://thediplomat.com/feed/", "category": "asia_pacific"},
    {"name": "Asia Times", "url": "https://asiatimes.com/feed/", "category": "asia_pacific"},

    # ── Regional: Europe & NATO ───────────────────────────────────────────
    {"name": "EurActiv Defence", "url": "https://www.euractiv.com/sections/defence-and-security/feed/", "category": "europe_defence"},
    {"name": "Breaking Defense", "url": "https://breakingdefense.com/feed/", "category": "defence_procurement"},

    # ── Export Controls & Compliance ──────────────────────────────────────
    {"name": "BIS Federal Register", "url": "https://www.bis.doc.gov/index.php/component/rssfeed/feed/2-federal-register-notices?format=feed", "category": "export_controls"},
]

# ── ARIA's Research Interests — GLOBAL scope ─────────────────────────────────

RESEARCH_INTERESTS = [
    # Global procurement
    "defence procurement tender contract award billion million",
    "military modernisation programme budget acquisition",
    "arms deal export agreement signed delivered",
    "defence cooperation bilateral MOU agreement partnership",
    "offset industrial participation local content requirement",
    "defence budget increase spending military allocation",
    "FMS foreign military sale DSCA notification",

    # Platforms & systems
    "fighter aircraft F-35 Rafale Eurofighter Gripen Su-35 J-10",
    "armoured vehicle IFV APC MRAP tank Leopard Abrams K2",
    "artillery howitzer K9 CAESAR M777 ammunition calibre",
    "UAV drone unmanned Bayraktar Anka Wing Loong MQ-9 Heron",
    "patrol vessel corvette frigate submarine destroyer OPV",
    "air defence SAM missile radar Patriot THAAD Iron Dome S-400",
    "helicopter Blackhawk Apache Chinook NH90 Tiger",
    "missile cruise anti-ship ATACMS HIMARS JASSM",

    # Key OEMs & exporters
    "Lockheed Martin Boeing Raytheon Northrop General Dynamics",
    "BAE Systems Leonardo Rheinmetall Thales MBDA Dassault",
    "Turkish defence Baykar TAI Otokar FNSS Aselsan Roketsan",
    "Chinese military Norinco AVIC CATIC Poly Technologies",
    "South Korean Hanwha KAI Hyundai Rotem LIG Nex1",
    "Israeli Elbit Rafael IAI EuroSpike Iron Dome",
    "Russian arms Rostec Almaz Antey Sukhoi replacement sanction",
    "Indian defence DRDO HAL BrahMos Tejas",
    "Embraer Paramount Denel South African",

    # Key markets & regions
    "Angola Mozambique Guinea-Bissau Cape Verde military FAA FADM",
    "Nigeria Kenya Ghana Senegal Ethiopia defence budget",
    "Saudi Arabia UAE Qatar Kuwait Oman Bahrain defence",
    "Indonesia Philippines Vietnam Thailand Malaysia defence",
    "Poland Romania Ukraine NATO eastern flank",
    "India Pakistan Bangladesh Sri Lanka defence",
    "Egypt Morocco Algeria Tunisia Libya defence",
    "Brazil Colombia Mexico Peru Chile defence",
    "Australia Japan South Korea Taiwan defence",

    # Compliance & regulation
    "UK export control ECJU SPIRE licence SIEL",
    "ITAR EAR OFAC sanctions compliance embargo",
    "EU dual use arms embargo regulation",
    "UN Security Council sanctions arms embargo",
    "end user certificate diversion proliferation",

    # Strategic themes
    "counter terrorism COIN special forces",
    "maritime security piracy Gulf of Guinea Indo-Pacific",
    "border security surveillance reconnaissance ISR SIGINT",
    "cyber warfare electronic warfare EW",
    "space defence satellite constellation",
    "CPLP defence cooperation Portuguese Lusophone",
    "Cabo Delgado insurgency Mozambique",
    "NATO expansion enlargement spending target",
    "AUKUS Quad Indo-Pacific alliance",
]

# ── Web Search Topics (cycled through for broader coverage) ──────────────────

WEB_SEARCH_QUERIES = [
    "defence procurement contract award 2026",
    "military arms deal signed delivered 2026",
    "fighter jet procurement tender 2026",
    "naval vessel frigate corvette contract 2026",
    "artillery howitzer ammunition procurement 2026",
    "UAV drone military export 2026",
    "air defence missile system deal 2026",
    "armoured vehicle IFV tender Africa Asia 2026",
    "defence offset agreement 2026",
    "arms export licence denied approved 2026",
    "Turkey Baykar military export Africa 2026",
    "South Korea Hanwha KAI defence export 2026",
    "China military export Africa Asia 2026",
    "Angola Mozambique defence procurement 2026",
    "Saudi Arabia UAE military contract 2026",
    "Indonesia Philippines defence modernisation 2026",
    "Poland NATO defence spending 2026",
    "India defence acquisition tender 2026",
    "DSCA FMS notification major arms sale 2026",
    "UK ECJU export licence defence 2026",
]

# ── Hypothesis Tracker ───────────────────────────────────────────────────────

HYPOTHESIS_KEY = "crucix:aria:hypotheses"
ARTICLES_READ_KEY = "crucix:aria:articles_read"


async def _load_hypotheses() -> list[dict]:
    data = await rs.get_json(HYPOTHESIS_KEY)
    return data or []


async def _save_hypotheses(hypotheses: list[dict]) -> None:
    await rs.set_json(HYPOTHESIS_KEY, hypotheses[:50])


async def _get_read_urls() -> set:
    data = await rs.get_json(ARTICLES_READ_KEY)
    return set(data or [])


async def _mark_read(url: str) -> None:
    urls = await _get_read_urls()
    urls.add(url)
    # Keep last 500
    url_list = list(urls)[-500:]
    await rs.set_json(ARTICLES_READ_KEY, url_list)


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
                articles.append({"title": t, "link": l, "description": d, "published": pub.group(1).strip() if pub else ""})
    except Exception as e:
        logger.debug(f"RSS fetch failed for {url}: {e}")
    return articles


async def _fetch_article_text(url: str, timeout: float = 15.0) -> str:
    """Fetch article body text from URL."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
            })
            if resp.status_code != 200:
                return ""
            html = resp.text

        # Strip scripts, styles, nav elements
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<nav[^>]*>.*?</nav>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<header[^>]*>.*?</header>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<footer[^>]*>.*?</footer>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&\w+;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        # Take substantial middle portion
        if len(text) > 2000:
            text = text[300:6000]
        return text[:6000]
    except Exception as e:
        logger.debug(f"Article fetch failed for {url}: {e}")
        return ""


async def _web_search(query: str, timeout: float = 10.0) -> list[dict]:
    """Search for articles via Google News RSS."""
    encoded = quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en&gl=US&ceid=US:en"
    return await _fetch_rss(url, timeout)


# ── LLM Article Analysis ────────────────────────────────────────────────────

async def _analyse_article(
    llm: LLMProvider,
    article_text: str,
    source: str,
    existing_kb: str,
    hypotheses: list[dict],
) -> dict | None:
    """Ask ARIA to extract intelligence from an article."""
    hyp_context = ""
    if hypotheses:
        hyp_context = "\nARIA'S CURRENT HYPOTHESES (validate or challenge these):\n"
        for h in hypotheses[:5]:
            hyp_context += f"- [{h.get('status','OPEN')}] {h.get('hypothesis','')}\n"

    extract_prompt = f"""You are ARIA reading a defence/security article. Extract actionable intelligence for global defence procurement.

ARTICLE:
{article_text[:4000]}

EXISTING KNOWLEDGE (do NOT repeat what you already know):
{existing_kb or 'No existing knowledge on this topic.'}
{hyp_context}

Extract ONLY new intelligence. For each finding:
1. State the fact clearly and specifically (names, values, dates)
2. Assign confidence: CONFIRMED (official/primary source), PROBABLE (multiple signals), ASSESSED (your analysis), UNCERTAIN (single source)
3. Tag the market/country/region
4. If this validates or contradicts an existing hypothesis, say so

Also: if this article reveals a pattern or trend worth tracking, generate a NEW hypothesis.

Return JSON:
{{
  "facts": [
    {{"topic": "short title", "content": "detailed fact with specifics", "confidence": "CONFIRMED|PROBABLE|ASSESSED|UNCERTAIN", "market": "country or region", "source": "{source}"}}
  ],
  "hypothesis": {{
    "statement": "if any new hypothesis emerges",
    "evidence": "supporting evidence",
    "what_would_confirm": "confirming signal",
    "what_would_refute": "refuting signal"
  }},
  "validates": "hypothesis text if validates existing, or null",
  "challenges": "hypothesis text if challenges existing, or null",
  "skip": false
}}

If NO new intelligence, set skip=true."""

    try:
        result = await llm.complete(
            "You are ARIA — a global defence procurement intelligence analyst. Extract genuinely new, actionable intelligence. Be specific: names, amounts, dates, countries. Rigorous confidence levels.",
            extract_prompt,
            max_tokens=1500,
            timeout=60.0,
        )
        json_match = re.search(r"\{[\s\S]*\}", result.text)
        if json_match:
            return json.loads(json_match.group())
    except Exception as e:
        logger.warning(f"Article analysis failed: {e}")
    return None


async def _process_analysis(parsed: dict, source: str, hypotheses: list[dict]) -> tuple[int, int]:
    """Process LLM analysis — store facts and update hypotheses. Returns (facts_learned, hyp_generated)."""
    facts_learned = 0
    hyp_generated = 0

    if parsed.get("skip"):
        return 0, 0

    for fact in (parsed.get("facts") or []):
        topic = fact.get("topic", "")
        content = fact.get("content", "")
        confidence = fact.get("confidence", "ASSESSED")
        if topic and content and len(content) > 20:
            await store_fact(topic, f"{content} [Source: {source}]", f"research:{source}", confidence)
            facts_learned += 1

    hyp = parsed.get("hypothesis") or {}
    if hyp.get("statement") and len(hyp["statement"]) > 20:
        hypotheses.insert(0, {
            "hypothesis": hyp["statement"],
            "evidence": hyp.get("evidence", ""),
            "what_would_confirm": hyp.get("what_would_confirm", ""),
            "what_would_refute": hyp.get("what_would_refute", ""),
            "status": "OPEN",
            "created_at": datetime.now(timezone.utc).isoformat()[:10],
            "evidence_count": 1,
        })
        hyp_generated += 1

    validates = parsed.get("validates")
    if validates:
        for h in hypotheses:
            if validates.lower() in h.get("hypothesis", "").lower():
                h["evidence_count"] = h.get("evidence_count", 0) + 1
                if h["evidence_count"] >= 3:
                    h["status"] = "STRENGTHENED"

    challenges = parsed.get("challenges")
    if challenges:
        for h in hypotheses:
            if challenges.lower() in h.get("hypothesis", "").lower():
                h["status"] = "CHALLENGED"

    return facts_learned, hyp_generated


# ── Public: Read a specific article URL ──────────────────────────────────────

async def read_article(llm: LLMProvider, url: str, context: str = "") -> dict:
    """
    Read a specific article URL and extract intelligence.
    Use this when someone shares an article via WhatsApp, chat, or API.
    """
    if not llm or not llm.is_configured:
        return {"error": "LLM not configured"}

    t_start = time.time()
    logger.info(f"ARIA reading article: {url[:80]}")

    body = await _fetch_article_text(url)
    if not body or len(body) < 100:
        return {"error": "Could not fetch article content", "url": url}

    article_text = f"URL: {url}\n"
    if context:
        article_text += f"Context from sender: {context}\n"
    article_text += f"Content:\n{body}"

    existing_kb = search_knowledge(body[:200])
    hypotheses = await _load_hypotheses()

    parsed = await _analyse_article(llm, article_text, url, existing_kb, hypotheses)
    if not parsed:
        return {"error": "Analysis failed", "url": url}

    facts_learned, hyp_generated = await _process_analysis(parsed, url, hypotheses)
    await _save_hypotheses(hypotheses)
    await _mark_read(url)

    duration = int((time.time() - t_start) * 1000)
    logger.info(f"Article read: {facts_learned} facts, {hyp_generated} hypotheses ({duration}ms)")

    return {
        "url": url,
        "facts_learned": facts_learned,
        "hypotheses_generated": hyp_generated,
        "facts": parsed.get("facts", []),
        "hypothesis": parsed.get("hypothesis"),
        "duration_ms": duration,
    }


# ── Public: Read a document (PDF, DOCX, text — already extracted) ────────────

async def read_document(
    llm: LLMProvider,
    content: str,
    filename: str = "unknown",
    source: str = "document",
    context: str = "",
) -> dict:
    """
    Read a document's extracted text and learn from it.
    Handles any format — the text extraction happens on the Node.js side
    (WhatsApp/email already extract PDF, DOCX, TXT, CSV content).
    """
    if not llm or not llm.is_configured:
        return {"error": "LLM not configured"}

    t_start = time.time()
    logger.info(f"ARIA reading document: {filename} ({len(content)} chars) from {source}")

    # For long documents, process in chunks
    chunks = []
    if len(content) > 5000:
        # Split into ~4000 char chunks with overlap
        for i in range(0, len(content), 3500):
            chunk = content[i:i + 4500]
            if len(chunk) > 100:
                chunks.append(chunk)
    else:
        chunks = [content]

    total_facts = 0
    total_hyp = 0
    all_facts: list[dict] = []
    hypotheses = await _load_hypotheses()

    for i, chunk in enumerate(chunks[:5]):  # Max 5 chunks per document
        doc_text = f"Document: {filename}\nSource: {source}\n"
        if context:
            doc_text += f"Context: {context}\n"
        doc_text += f"Content (part {i + 1}/{min(len(chunks), 5)}):\n{chunk}"

        existing_kb = search_knowledge(chunk[:200])
        parsed = await _analyse_article(llm, doc_text, f"{source}:{filename}", existing_kb, hypotheses)

        if parsed:
            fl, hg = await _process_analysis(parsed, f"{source}:{filename}", hypotheses)
            total_facts += fl
            total_hyp += hg
            all_facts.extend(parsed.get("facts", []))

    await _save_hypotheses(hypotheses)

    duration = int((time.time() - t_start) * 1000)
    logger.info(f"Document read: {filename} → {total_facts} facts, {total_hyp} hypotheses ({duration}ms)")

    return {
        "filename": filename,
        "source": source,
        "content_length": len(content),
        "chunks_processed": min(len(chunks), 5),
        "facts_learned": total_facts,
        "hypotheses_generated": total_hyp,
        "facts": all_facts,
        "duration_ms": duration,
    }


# ── Public: Autonomous research cycle ────────────────────────────────────────

async def research_and_learn(llm: LLMProvider, max_articles: int = 15) -> dict:
    """
    ARIA's autonomous research cycle:
    1. Scan 30+ RSS feeds for relevant articles
    2. Run web searches on rotating topics
    3. Read and extract intelligence from the best articles
    4. Cross-reference with existing knowledge
    5. Generate and validate hypotheses
    """
    if not llm or not llm.is_configured:
        return {"error": "LLM not configured"}

    t_start = time.time()
    logger.info("ARIA research cycle starting (global scope)...")

    # ── Step 1: Gather articles from RSS feeds ────────────────────────────
    all_articles: list[dict] = []
    for feed in RESEARCH_FEEDS:
        articles = await _fetch_rss(feed["url"])
        for a in articles:
            a["source"] = feed["name"]
            a["category"] = feed["category"]
        all_articles.extend(articles)

    logger.info(f"RSS feeds: {len(all_articles)} articles from {len(RESEARCH_FEEDS)} feeds")

    # ── Step 2: Web search on rotating topics ─────────────────────────────
    # Pick 3 search queries based on current hour (rotates through all 20)
    hour = datetime.now(timezone.utc).hour
    search_indices = [(hour * 3 + i) % len(WEB_SEARCH_QUERIES) for i in range(3)]
    for idx in search_indices:
        query = WEB_SEARCH_QUERIES[idx]
        results = await _web_search(query)
        for a in results:
            a["source"] = f"web_search:{query[:30]}"
            a["category"] = "web_search"
        all_articles.extend(results)

    logger.info(f"Total: {len(all_articles)} articles (RSS + web search)")

    if not all_articles:
        return {"articles_scanned": 0, "facts_learned": 0}

    # ── Step 3: Filter already-read articles ──────────────────────────────
    read_urls = await _get_read_urls()
    all_articles = [a for a in all_articles if a.get("link") not in read_urls]

    # ── Step 4: Score relevance ───────────────────────────────────────────
    scored: list[tuple[float, dict]] = []
    for article in all_articles:
        text = f"{article['title']} {article.get('description', '')}".lower()
        score = 0
        for interest in RESEARCH_INTERESTS:
            words = interest.lower().split()
            matches = sum(1 for w in words if w in text)
            if matches >= 2:
                score += matches * 2
        # Boost procurement signals
        if any(k in text for k in ["tender", "contract", "procure", "award", "billion", "million", "deal"]):
            score += 5
        # Boost Lusophone (core market)
        if any(c in text for c in ["angola", "mozambique", "guinea-bissau", "cape verde", "lusophone"]):
            score += 8
        # Boost other priority markets
        if any(c in text for c in ["nigeria", "kenya", "saudi", "uae", "indonesia", "philippines", "poland"]):
            score += 3
        if score > 0:
            scored.append((score, article))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_articles = [a for _, a in scored[:max_articles]]

    if not top_articles:
        return {"articles_scanned": len(all_articles), "relevant": 0, "facts_learned": 0}

    logger.info(f"Selected {len(top_articles)} articles for deep reading")

    # ── Step 5: Read and extract intelligence ─────────────────────────────
    facts_learned = 0
    hypotheses_generated = 0
    existing_hypotheses = await _load_hypotheses()

    for article in top_articles:
        body = ""
        if article.get("link"):
            body = await _fetch_article_text(article["link"])
            await _mark_read(article["link"])

        article_text = f"Title: {article['title']}\nSource: {article['source']}\n"
        if article.get("description"):
            article_text += f"Summary: {article['description']}\n"
        if body:
            article_text += f"Body: {body[:3500]}\n"

        existing_kb = search_knowledge(article["title"])
        parsed = await _analyse_article(llm, article_text, article["source"], existing_kb, existing_hypotheses)

        if parsed:
            fl, hg = await _process_analysis(parsed, article["source"], existing_hypotheses)
            facts_learned += fl
            hypotheses_generated += hg

    await _save_hypotheses(existing_hypotheses)

    duration = int((time.time() - t_start) * 1000)
    logger.info(
        f"Research cycle complete: {len(all_articles)} scanned, "
        f"{len(top_articles)} read, {facts_learned} facts, "
        f"{hypotheses_generated} hypotheses ({duration}ms)"
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


# ── Public: Validate a hypothesis ────────────────────────────────────────────

async def validate_hypothesis(llm: LLMProvider, hypothesis_text: str) -> dict:
    """Search for evidence to validate or refute a specific hypothesis."""
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

    articles = await _web_search(f"{target['hypothesis']} evidence 2026")
    if not articles:
        return {"hypothesis": target["hypothesis"], "status": "NO_NEW_EVIDENCE"}

    evidence_texts = []
    for a in articles[:3]:
        body = await _fetch_article_text(a.get("link", "")) if a.get("link") else ""
        evidence_texts.append(f"Title: {a['title']}\n{body[:1500]}")

    prompt = f"""Evaluate this hypothesis against new evidence.

HYPOTHESIS: {target['hypothesis']}
Confirm signal: {target.get('what_would_confirm', '?')}
Refute signal: {target.get('what_would_refute', '?')}
Evidence count: {target.get('evidence_count', 0)}

EVIDENCE:
{"---".join(evidence_texts)}

Return JSON:
{{"verdict": "SUPPORTS|CHALLENGES|NEUTRAL", "reasoning": "why", "refined_hypothesis": "or null", "new_status": "OPEN|STRENGTHENED|CHALLENGED|REFUTED|CONFIRMED"}}"""

    try:
        result = await llm.complete("ARIA evaluating intelligence hypothesis.", prompt, max_tokens=800, timeout=45.0)
        json_match = re.search(r"\{[\s\S]*\}", result.text)
        if json_match:
            parsed = json.loads(json_match.group())
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
    return await _load_hypotheses()


async def get_research_summary(llm: LLMProvider) -> dict:
    hypotheses = await _load_hypotheses()
    kb_size = len((kb_cache or {}).get("facts", []))
    ledger_size = len((ledger_cache or {}).get("signals", []))
    open_h = [h for h in hypotheses if h.get("status") == "OPEN"]
    strong_h = [h for h in hypotheses if h.get("status") == "STRENGTHENED"]
    challenged_h = [h for h in hypotheses if h.get("status") == "CHALLENGED"]

    return {
        "knowledge_base_facts": kb_size,
        "intel_ledger_signals": ledger_size,
        "hypotheses": {"total": len(hypotheses), "open": len(open_h), "strengthened": len(strong_h), "challenged": len(challenged_h)},
        "top_hypotheses": [{"hypothesis": h["hypothesis"], "status": h["status"], "evidence": h.get("evidence_count", 0)} for h in hypotheses[:10]],
    }
