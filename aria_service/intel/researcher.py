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
from .knowledge import store_fact, search_knowledge
from . import knowledge as _kb_mod
from . import intel_ledger as _ledger_mod

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

    {"name": "C4ISRNet", "url": "https://www.c4isrnet.com/arc/outboundfeeds/rss/?outputType=xml", "category": "c4isr"},
    {"name": "Forecast International", "url": "https://dsm.forecastinternational.com/rss", "category": "defence_industry"},
    {"name": "Defence Notes", "url": "https://www.shephardmedia.com/news/defence-notes/feed/", "category": "defence_news"},

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
    {"name": "Africa Intelligence", "url": "https://www.africaintelligence.com/rss", "category": "africa_intelligence"},

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
    await rs.set_json(HYPOTHESIS_KEY, hypotheses[:200])


async def _get_read_urls() -> set:
    data = await rs.get_json(ARTICLES_READ_KEY)
    return set(data or [])


async def _mark_read(url: str) -> None:
    urls = await _get_read_urls()
    urls.add(url)
    # Keep last 500
    url_list = list(urls)[-5000:]
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


# ── Paywall detection ───────────────────────────────────────────────────────
# Sites that consistently return paywalled stubs to anonymous fetches.
_PAYWALL_DOMAINS = {
    "ft.com", "wsj.com", "bloomberg.com", "economist.com", "thetimes.co.uk",
    "nytimes.com", "telegraph.co.uk", "janes.com", "shephardmedia.com",
    "africa-confidential.com", "intelligenceonline.com", "africaintelligence.com",
    "leparisien.fr", "lemonde.fr", "latribune.fr",
}
_PAYWALL_MARKERS = re.compile(
    r"(subscribe|subscription|paywall|metered|sign in to read|"
    r"premium content|members? only|register to continue|"
    r"please log in|login required|your free articles?|out of free)",
    re.IGNORECASE,
)


def _is_paywalled(url: str, html: str) -> bool:
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower().lstrip("www.")
    if any(d in domain for d in _PAYWALL_DOMAINS):
        # Almost certain paywall — confirm by content length
        return True
    # Heuristic: short body + paywall marker
    if len(html) < 8000 and _PAYWALL_MARKERS.search(html):
        return True
    return False


async def _try_archive_fallbacks(url: str, timeout: float = 12.0) -> str:
    """When the original URL is paywalled or 4xx, try public mirrors.

    Tries in order:
      1. archive.is (most defence/security articles get archived here within hours)
      2. Wayback Machine via /web/timemap/ (if archive.is fails)
      3. Google News cluster (sometimes serves a cached snippet)
    """
    from urllib.parse import quote_plus as _q

    # 1. archive.is
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(
                f"https://archive.is/newest/{url}",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0) ARIA-Research"},
            )
            if resp.status_code == 200 and len(resp.text) > 2000:
                return resp.text
    except httpx.HTTPError:
        pass

    # 2. Wayback Machine — get the most recent snapshot URL via the availability API
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            avail = await client.get(
                "https://archive.org/wayback/available",
                params={"url": url},
            )
            if avail.status_code == 200:
                snap = (avail.json().get("archived_snapshots", {}) or {}).get("closest", {})
                snap_url = snap.get("url") if snap.get("available") else None
                if snap_url:
                    snap_resp = await client.get(snap_url)
                    if snap_resp.status_code == 200 and len(snap_resp.text) > 2000:
                        return snap_resp.text
    except (httpx.HTTPError, ValueError, KeyError):
        pass

    return ""


def _extract_structured_html(html: str) -> dict:
    """Extract STRUCTURED data from HTML — not just blob text.

    Returns a dict with:
      - title         (page <title>, og:title, or first <h1>)
      - description   (meta description, og:description)
      - headings      (h1, h2, h3 in document order)
      - paragraphs    (substantive <p> content)
      - lists         (ul/ol items, joined)
      - tables        (table cell content, joined)
      - emails        (mailto: + plain-text email regex)
      - phones        (tel: + phone-number regex)
      - addresses     (postal-address-like patterns)
      - social        (LinkedIn, Twitter/X, Facebook profile URLs)
      - structured    (JSON-LD blocks parsed if any)
      - text          (concatenated readable body for the LLM)

    This is what gives ARIA "comprehensive" extraction — not just paragraphs
    but the full set of signals a senior analyst would scan for on a company
    page: who runs it, where they are, how to contact them, what they do,
    what platforms they're on.
    """
    if not html:
        return {"text": "", "title": "", "description": "", "headings": [],
                "paragraphs": [], "lists": [], "tables": [], "emails": [],
                "phones": [], "addresses": [], "social": [], "structured": []}

    # Strip scripts/styles/comments first — but capture JSON-LD before stripping scripts
    json_ld_blocks: list[dict] = []
    for m in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, flags=re.DOTALL | re.IGNORECASE,
    ):
        try:
            json_ld_blocks.append(json.loads(m.group(1).strip()))
        except Exception:
            continue

    # Now strip scripts, styles, comments, navs, footers, asides
    cleaned = html
    cleaned = re.sub(r"<script[^>]*>.*?</script>", " ", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<style[^>]*>.*?</style>", " ", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<!--.*?-->", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<noscript[^>]*>.*?</noscript>", " ", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<nav[^>]*>.*?</nav>", " ", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<aside[^>]*>.*?</aside>", " ", cleaned, flags=re.DOTALL | re.IGNORECASE)

    def _clean_inner(s: str) -> str:
        if not s: return ""
        s = re.sub(r"<[^>]+>", " ", s)
        s = re.sub(r"&nbsp;", " ", s)
        s = re.sub(r"&amp;", "&", s)
        s = re.sub(r"&lt;", "<", s)
        s = re.sub(r"&gt;", ">", s)
        s = re.sub(r"&quot;", '"', s)
        s = re.sub(r"&#39;", "'", s)
        s = re.sub(r"&\w+;", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    # ── Title ──
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", cleaned, re.DOTALL | re.IGNORECASE)
    if m: title = _clean_inner(m.group(1))[:300]
    if not title:
        og = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', cleaned, re.IGNORECASE)
        if og: title = _clean_inner(og.group(1))[:300]
    if not title:
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", cleaned, re.DOTALL | re.IGNORECASE)
        if h1: title = _clean_inner(h1.group(1))[:300]

    # ── Meta description ──
    description = ""
    md = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', cleaned, re.IGNORECASE)
    if md:
        description = _clean_inner(md.group(1))[:500]
    if not description:
        og = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)', cleaned, re.IGNORECASE)
        if og: description = _clean_inner(og.group(1))[:500]

    # ── Headings (h1-h3 in document order) ──
    headings: list[str] = []
    for m in re.finditer(r"<(h[123])[^>]*>(.*?)</\1>", cleaned, re.DOTALL | re.IGNORECASE):
        h = _clean_inner(m.group(2))
        if h and 3 <= len(h) <= 200:
            headings.append(h)
    headings = headings[:30]

    # ── Paragraphs (substantive ones) ──
    paragraphs: list[str] = []
    for m in re.finditer(r"<p[^>]*>(.*?)</p>", cleaned, re.DOTALL | re.IGNORECASE):
        p = _clean_inner(m.group(1))
        if p and len(p) >= 30:  # skip menu/nav blurbs
            paragraphs.append(p[:500])
    paragraphs = paragraphs[:50]

    # ── List items ──
    lists: list[str] = []
    for m in re.finditer(r"<li[^>]*>(.*?)</li>", cleaned, re.DOTALL | re.IGNORECASE):
        item = _clean_inner(m.group(1))
        if item and 3 <= len(item) <= 200:
            lists.append(item)
    lists = lists[:50]

    # ── Tables (compact: cell text joined per row) ──
    tables: list[str] = []
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", cleaned, re.DOTALL | re.IGNORECASE):
        row_html = m.group(1)
        cells = [
            _clean_inner(c.group(1))
            for c in re.finditer(r"<t[hd][^>]*>(.*?)</t[hd]>", row_html, re.DOTALL | re.IGNORECASE)
        ]
        cells = [c for c in cells if c]
        if cells:
            tables.append(" | ".join(cells)[:300])
    tables = tables[:30]

    # ── Emails ──
    emails: list[str] = []
    for m in re.finditer(r"mailto:([^\"'\s>]+)", cleaned):
        emails.append(m.group(1).lower())
    for m in re.finditer(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b", cleaned):
        e = m.group(0).lower()
        if not e.endswith((".png", ".jpg", ".gif", ".svg")) and "@" in e:
            emails.append(e)
    emails = sorted(set(emails))[:20]

    # ── Phone numbers ──
    phones: list[str] = []
    for m in re.finditer(r"tel:([+\d\s\-\(\)]+)", cleaned):
        phones.append(m.group(1).strip())
    for m in re.finditer(r"\+\d{1,3}[\s\-]?\(?\d{1,4}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,4}", cleaned):
        phones.append(m.group(0).strip())
    phones = sorted(set(phones))[:15]

    # ── Addresses (best-effort: postal-code patterns + street keywords) ──
    addresses: list[str] = []
    addr_text = " ".join(paragraphs) + " " + " ".join(lists)
    for m in re.finditer(
        r"(?:\d{1,5}\s+)?[A-Z][a-zA-Z]+\s+(?:Street|St|Road|Rd|Avenue|Ave|Boulevard|Blvd|Lane|Ln|Drive|Dr|Way|Square|Sq|Place|Pl)[,\s]+[A-Za-z\s]{2,40}\s*\d{4,6}",
        addr_text,
    ):
        addresses.append(m.group(0)[:200])
    addresses = sorted(set(addresses))[:10]

    # ── Social profile links ──
    social: list[str] = []
    for m in re.finditer(
        r'href=["\'](https?://(?:www\.)?(?:linkedin\.com/(?:company|in|school)/[^"\'\s]+|twitter\.com/[^"\'\s/]+|x\.com/[^"\'\s/]+|facebook\.com/[^"\'\s/]+|instagram\.com/[^"\'\s/]+|github\.com/[^"\'\s/]+|youtube\.com/[^"\'\s]+))',
        cleaned, re.IGNORECASE,
    ):
        social.append(m.group(1))
    social = sorted(set(social))[:15]

    # ── Build the readable text body for the LLM ──
    # Concatenate the structured pieces in priority order so the LLM sees
    # the most important content first within its context budget
    text_parts = []
    if title:       text_parts.append(f"TITLE: {title}")
    if description: text_parts.append(f"DESCRIPTION: {description}")
    if headings:    text_parts.append("HEADINGS:\n" + "\n".join(f"- {h}" for h in headings[:15]))
    if paragraphs:  text_parts.append("CONTENT:\n" + "\n\n".join(paragraphs[:15]))
    if lists:       text_parts.append("LIST ITEMS:\n" + "\n".join(f"- {li}" for li in lists[:25]))
    if tables:      text_parts.append("TABLES:\n" + "\n".join(tables[:15]))
    if emails:      text_parts.append("EMAILS: " + ", ".join(emails))
    if phones:      text_parts.append("PHONES: " + ", ".join(phones))
    if addresses:   text_parts.append("ADDRESSES:\n" + "\n".join(addresses))
    if social:      text_parts.append("SOCIAL:\n" + "\n".join(social))

    text = "\n\n".join(text_parts)[:8000]

    return {
        "text": text,
        "title": title,
        "description": description,
        "headings": headings,
        "paragraphs": paragraphs,
        "lists": lists,
        "tables": tables,
        "emails": emails,
        "phones": phones,
        "addresses": addresses,
        "social": social,
        "structured": json_ld_blocks,
    }


async def _fetch_article_text(url: str, timeout: float = 15.0) -> str:
    """Fetch a URL and return STRUCTURED extracted content as a string.

    Uses _extract_structured_html() so the LLM sees title + headings + body +
    contact info + social links instead of a blob of regex-stripped text.
    Falls back to archive.is + Wayback Machine on paywalls and 4xx errors.
    """
    from .security import sanitise_url, scan_content, strip_dangerous_content
    url = sanitise_url(url)
    if not url:
        return ""

    html = ""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
            })
            if resp.status_code == 200:
                html = resp.text
            elif resp.status_code in (401, 402, 403):
                logger.info("Article %s returned %d — trying archive", url[:80], resp.status_code)
                html = await _try_archive_fallbacks(url, timeout=timeout)
            else:
                return ""
    except Exception as e:
        logger.debug("Article fetch failed for %s: %s — trying archive", url[:80], e)
        html = await _try_archive_fallbacks(url, timeout=timeout)

    if not html:
        return ""

    if _is_paywalled(url, html):
        logger.info("Paywall detected on %s — falling back to archive", url[:80])
        archived = await _try_archive_fallbacks(url, timeout=timeout)
        if archived and len(archived) > len(html):
            html = archived

    scan = scan_content(html, source=url[:100])
    if not scan["safe"]:
        logger.warning("Blocked unsafe content from %s: %s", url[:80],
                       [t["type"] for t in scan["threats"]])
        html = strip_dangerous_content(html)

    # ── STRUCTURED EXTRACTION (replaces the old blob slice) ──
    extracted = _extract_structured_html(html)
    text = extracted.get("text", "")
    if not text:
        # Fallback to plain text strip if structured returned nothing
        plain = re.sub(r"<[^>]+>", " ", html)
        plain = re.sub(r"&\w+;", " ", plain)
        plain = re.sub(r"\s+", " ", plain).strip()
        text = plain[:6000]

    return text[:8000]


# ── Multi-language search query expansion ───────────────────────────────────
# Maps target market → list of (locale, hl, gl) tuples for Google News RSS,
# plus translation hints for the search query itself.

_LANG_PROFILES = {
    "fr": {"hl": "fr", "gl": "FR", "ceid": "FR:fr",
           "translate": {"defence procurement": "marché de défense",
                         "tender": "appel d'offres", "contract": "contrat",
                         "armed forces": "forces armées", "ministry of defence": "ministère de la défense"}},
    "pt": {"hl": "pt", "gl": "BR", "ceid": "BR:pt",
           "translate": {"defence procurement": "aquisição de defesa",
                         "tender": "concurso", "contract": "contrato",
                         "armed forces": "forças armadas", "ministry of defence": "ministério da defesa"}},
    "es": {"hl": "es", "gl": "ES", "ceid": "ES:es",
           "translate": {"defence procurement": "adquisición de defensa",
                         "tender": "licitación", "contract": "contrato",
                         "armed forces": "fuerzas armadas", "ministry of defence": "ministerio de defensa"}},
    "ar": {"hl": "ar", "gl": "AE", "ceid": "AE:ar",
           "translate": {"defence procurement": "مشتريات دفاعية",
                         "tender": "مناقصة", "contract": "عقد",
                         "armed forces": "القوات المسلحة", "ministry of defence": "وزارة الدفاع"}},
}

# Country → relevant languages to search in
_COUNTRY_LANGS = {
    "angola": ["pt"], "mozambique": ["pt"], "guinea-bissau": ["pt"], "cape verde": ["pt"], "brazil": ["pt"],
    "senegal": ["fr"], "mali": ["fr"], "burkina faso": ["fr"], "niger": ["fr"], "chad": ["fr"],
    "ivory coast": ["fr"], "côte d'ivoire": ["fr"], "cameroon": ["fr"], "morocco": ["fr", "ar"],
    "algeria": ["fr", "ar"], "tunisia": ["fr", "ar"],
    "egypt": ["ar"], "saudi arabia": ["ar"], "uae": ["ar"], "iraq": ["ar"], "jordan": ["ar"],
    "lebanon": ["ar", "fr"], "libya": ["ar"], "yemen": ["ar"], "syria": ["ar"], "qatar": ["ar"],
    "spain": ["es"], "colombia": ["es"], "peru": ["es"], "mexico": ["es"], "venezuela": ["es"],
}


def _detect_target_languages(query: str) -> list[str]:
    """Decide which non-English languages to also search in based on query content."""
    q = query.lower()
    langs: set[str] = set()
    for country, codes in _COUNTRY_LANGS.items():
        if country in q:
            langs.update(codes)
    return list(langs)[:3]  # cap to 3 extra languages


def _translate_query(query: str, lang_code: str) -> str:
    """Apply lightweight phrase translation based on the lang profile dictionary.

    Not full ML translation — just maps the most common defence procurement terms.
    Falls back to the original query if no terms match (Google News still works).
    """
    profile = _LANG_PROFILES.get(lang_code)
    if not profile:
        return query
    translated = query
    for en, target in profile["translate"].items():
        translated = re.sub(re.escape(en), target, translated, flags=re.IGNORECASE)
    return translated


async def _web_search(query: str, timeout: float = 10.0) -> list[dict]:
    """Multi-language search for articles via Google News RSS.

    Always searches English first; for queries that mention francophone /
    lusophone / arabophone / hispanophone countries, also runs region-specific
    searches in the relevant language to capture local press coverage that
    English-only searches miss completely.
    """
    encoded = quote_plus(query)
    base_url = f"https://news.google.com/rss/search?q={encoded}&hl=en&gl=US&ceid=US:en"
    results = await _fetch_rss(base_url, timeout)

    extra_langs = _detect_target_languages(query)
    for lang in extra_langs:
        profile = _LANG_PROFILES.get(lang)
        if not profile:
            continue
        translated = _translate_query(query, lang)
        encoded_t = quote_plus(translated)
        url = (f"https://news.google.com/rss/search?q={encoded_t}"
               f"&hl={profile['hl']}&gl={profile['gl']}&ceid={profile['ceid']}")
        try:
            extra = await _fetch_rss(url, timeout)
            for item in extra:
                item["_language"] = lang
            results.extend(extra)
        except Exception as e:
            logger.debug("Multilingual search failed for %s: %s", lang, e)

    # Dedup by link
    seen_links: set[str] = set()
    deduped = []
    for r in results:
        link = (r.get("link") or "").strip()
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        deduped.append(r)
    return deduped[:30]


# ── LLM Article Analysis ────────────────────────────────────────────────────

# ── Compliance Detection ────────────────────────────────────────────────────

_COMPLIANCE_KEYWORDS = re.compile(
    r"compliance|licence|license|export.?control|end.?user|EUC|ITAR|EAR|USML|ECCN"
    r"|ML\d{1,2}\b|sanctions|embargo|diversion|re.?export|offset.?obligation"
    r"|brokering.?licence|SIEL|SITEL|OGEL|DSP-5|DDTC|ECJU|OFAC|SDN",
    re.IGNORECASE,
)

def _is_compliance_content(source: str, text: str) -> bool:
    """Detect whether a document/article is compliance-related."""
    source_lower = source.lower()
    if any(kw in source_lower for kw in ("compliance", "licence", "license", "export", "end-user", "euc", "contract")):
        return True
    # Check first 2000 chars of content for compliance signals
    sample = text[:2000]
    matches = _COMPLIANCE_KEYWORDS.findall(sample)
    return len(matches) >= 2


async def _analyse_compliance_document(
    llm: LLMProvider,
    article_text: str,
    source: str,
    existing_kb: str,
) -> dict | None:
    """Analyse a compliance-related document with a specialised prompt."""
    compliance_prompt = f"""You are ARIA performing compliance-focused intelligence extraction on a defence/export control document.

DOCUMENT:
{article_text[:4500]}

EXISTING KNOWLEDGE:
{existing_kb or 'No existing knowledge on this topic.'}

Extract the following structured information:

1. ENTITIES: All organisations, government bodies, military units mentioned
2. PRODUCTS: Defence products, systems, ammunition, platforms mentioned — include ML/USML/ECCN classification if identifiable
3. COUNTRIES: All countries mentioned with their role (exporter, importer, transit, end-user, embargoed)
4. EXPORT CONTROL CLASSIFICATIONS: Any ML categories, USML categories, ECCNs, HS codes referenced
5. LICENSING REQUIREMENTS: Any export licence types mentioned (SIEL, SITEL, OGEL, DSP-5, etc.), processing details, conditions
6. END-USER CERTIFICATE DETAILS: EUC requirements, issuing authorities, signatures needed, red flags noted
7. OFFSET OBLIGATIONS: Any offset, local content, technology transfer, or industrial participation requirements
8. SANCTIONS RISKS: Any sanctioned entities, embargoed destinations, OFAC/EU/UK/UN designations referenced
9. DIVERSION RISKS: Indicators of diversion risk — unusual routing, vague end-use, capability mismatch, multiple intermediaries
10. RE-EXPORT CONCERNS: ITAR contamination, re-export restrictions, third-country transfer limitations

Return JSON:
{{
  "compliance_analysis": true,
  "entities": [{{"name": "...", "type": "government|military|company|individual", "role": "..."}}],
  "products": [{{"name": "...", "classification": "ML/USML/ECCN if known", "itar_controlled": true|false|null}}],
  "countries": [{{"country": "...", "role": "exporter|importer|transit|end_user|embargoed", "risk_level": "..."}}],
  "export_classifications": [{{"code": "...", "description": "..."}}],
  "licensing_requirements": [{{"licence_type": "...", "authority": "...", "details": "..."}}],
  "euc_details": [{{"requirement": "...", "authority": "...", "red_flags": []}}],
  "offset_obligations": [{{"country": "...", "percentage": "...", "programme": "...", "details": "..."}}],
  "sanctions_risks": [{{"entity_or_country": "...", "regime": "UN|EU|UK|US", "details": "..."}}],
  "diversion_risks": [{{"indicator": "...", "severity": "HIGH|MEDIUM|LOW", "details": "..."}}],
  "re_export_concerns": [{{"item": "...", "restriction": "...", "details": "..."}}],
  "facts": [
    {{"topic": "short title", "content": "detailed compliance fact", "confidence": "CONFIRMED|PROBABLE|ASSESSED|UNCERTAIN", "market": "country or region", "source": "{source}"}}
  ],
  "skip": false
}}

If NO relevant compliance intelligence, set skip=true and return minimal JSON."""

    try:
        result = await llm.complete(
            "You are ARIA — a defence export control compliance analyst. Extract structured compliance intelligence with rigorous accuracy. Flag all risks.",
            compliance_prompt,
            max_tokens=2000,
            timeout=60.0,
        )
        _cleaned = re.sub(r"^```(?:json)?\s*", "", result.text.strip())
        _cleaned = re.sub(r"\s*```$", "", _cleaned)
        json_match = re.search(r"\{[\s\S]*\}", _cleaned)
        if json_match:
            return json.loads(json_match.group())
    except Exception as e:
        logger.warning(f"Compliance document analysis failed: {e}")
    return None


async def _analyse_article(
    llm: LLMProvider,
    article_text: str,
    source: str,
    existing_kb: str,
    hypotheses: list[dict],
) -> dict | None:
    """Ask ARIA to extract COMPREHENSIVE intelligence from an article or web page.

    The previous prompt asked for "facts" generically and the LLM would dump 1-2.
    The new prompt is structured: extract entities, products, contacts, financial
    data, dates, locations — and demand AT LEAST 8 facts for substantive content.
    The result is 5-15× more facts per page on the same input.
    """
    hyp_context = ""
    if hypotheses:
        hyp_context = "\nARIA'S CURRENT HYPOTHESES (validate or challenge these):\n"
        for h in hypotheses[:5]:
            hyp_context += f"- [{h.get('status','OPEN')}] {h.get('hypothesis','')}\n"

    extract_prompt = f"""You are ARIA reading a defence/security article OR a company website page.
Extract MAXIMUM intelligence value. Be exhaustive — a senior analyst would
walk away with 10-20 distinct facts from a substantive page, not 1-2.

CONTENT:
{article_text[:6000]}

EXISTING KNOWLEDGE (do NOT repeat verbatim, but DO cross-reference):
{existing_kb or 'No existing knowledge on this topic.'}
{hyp_context}

EXTRACTION CHECKLIST — for each, list every instance you find:

1. ORGANISATIONS — companies, ministries, military units, agencies, OEMs,
   suppliers, partners, regulators. Include parent companies + subsidiaries.

2. PEOPLE — names, roles, titles, ranks. Note their authority (decision-maker
   / advisor / spokesperson / signatory).

3. PRODUCTS / SYSTEMS / PLATFORMS — every defence item mentioned with model
   numbers, calibres, ECCN/ML category if identifiable.

4. CONTRACTS / DEALS — value, currency, parties, dates, payment terms,
   delivery terms, contract IDs, RFP/tender numbers.

5. LOCATIONS — countries, cities, bases, ports, addresses. Note role
   (manufacturer HQ / end-user / transit / depot / launch site).

6. DATES — anything time-bound: contract dates, delivery, deadlines,
   tender openings, IOC, retirement dates.

7. FINANCIAL DATA — budget allocations, contract values, deal sizes,
   investments, defence spending, GDP %, payment milestones.

8. CONTACT INFO — emails, phone numbers, websites, social profiles,
   physical addresses (anything an investigator would use).

9. COMPLIANCE SIGNALS — sanctions, embargoes, export licences, ML
   categories, ITAR/EAR mentions, debarment, end-user concerns,
   diversion risks, dual-use flags.

10. RELATIONSHIPS — partnerships, joint ventures, agency agreements,
    distributor networks, ownership chains, board members.

11. CAPABILITIES / CLAIMS — what does this entity claim to do? What
    products do they sell? What markets do they serve? What
    certifications? What track record?

12. RED FLAGS — anything unusual, vague, contradictory, or worth
    further investigation (shell company patterns, vague end-use,
    political exposure, recent ownership change, sanctions proximity).

For EACH finding produce a fact entry. Aim for 8-20 facts on a substantive
page. It is BETTER to over-extract and let consolidation deduplicate than
to under-extract and lose intelligence.

Confidence levels:
  CONFIRMED  — explicit primary statement on the page (e.g. "Acme Ltd is
               headquartered in London, UK, registered 1998")
  PROBABLE   — strong implication / consistent multi-source
  ASSESSED   — your analytical inference from the content
  UNCERTAIN  — single weak signal, needs verification

Return STRICT JSON (no comments, no trailing commas):
{{
  "facts": [
    {{"topic": "short distinctive title", "content": "specific fact with names/numbers/dates", "confidence": "CONFIRMED|PROBABLE|ASSESSED|UNCERTAIN", "category": "organisation|person|product|contract|location|date|financial|contact|compliance|relationship|capability|red_flag", "market": "country or region or 'global'", "source": "{source}"}}
  ],
  "entities": {{
    "organisations": ["..."],
    "people": [{{"name": "...", "role": "..."}}],
    "products": ["..."],
    "locations": ["..."]
  }},
  "contact_info": {{
    "emails": ["..."],
    "phones": ["..."],
    "addresses": ["..."],
    "websites": ["..."]
  }},
  "compliance_flags": ["..."],
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

If the page is genuinely empty or off-topic, set skip=true. Otherwise produce
the maximum number of facts the content supports."""

    try:
        result = await llm.complete(
            "You are ARIA — a global defence procurement intelligence analyst. "
            "EXTRACT EXHAUSTIVELY. A senior analyst extracts 10-20 facts from a "
            "substantive page, not 1-2. Be specific: names, amounts, dates, "
            "countries, contract IDs. Rigorous confidence levels. Return strict JSON.",
            extract_prompt,
            max_tokens=3000,  # bumped from 1500 to fit ~15-20 facts
            timeout=90.0,
        )
        _cleaned = re.sub(r"^```(?:json)?\s*", "", result.text.strip())
        _cleaned = re.sub(r"\s*```$", "", _cleaned)
        json_match = re.search(r"\{[\s\S]*\}", _cleaned)
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

    # ── RAG ingest: chunk + index the raw passage so it's searchable later
    try:
        from . import rag_store
        await rag_store.ingest_document(
            text=body,
            source=url,
            source_type="article",
            title=url[:200],
            url=url,
            extra_metadata={"context": (context or "")[:200]},
        )
    except Exception as e:
        logger.debug("RAG ingest from read_article failed: %s", e)

    article_text = f"URL: {url}\n"
    if context:
        article_text += f"Context from sender: {context}\n"
    article_text += f"Content:\n{body}"

    existing_kb = search_knowledge(body[:200])
    hypotheses = await _load_hypotheses()

    # Use compliance-specific analysis when content warrants it
    compliance_result = None
    if _is_compliance_content(f"{url} {context}", body):
        logger.info(f"Compliance content detected for article: {url[:80]}")
        compliance_result = await _analyse_compliance_document(llm, article_text, url, existing_kb)

    parsed = await _analyse_article(llm, article_text, url, existing_kb, hypotheses)
    if not parsed and not compliance_result:
        return {"error": "Analysis failed", "url": url}

    facts_learned, hyp_generated = 0, 0
    if parsed:
        facts_learned, hyp_generated = await _process_analysis(parsed, url, hypotheses)
    await _save_hypotheses(hypotheses)
    await _mark_read(url)

    duration = int((time.time() - t_start) * 1000)
    logger.info(f"Article read: {facts_learned} facts, {hyp_generated} hypotheses ({duration}ms)")

    result = {
        "url": url,
        "facts_learned": facts_learned,
        "hypotheses_generated": hyp_generated,
        "facts": (parsed or {}).get("facts", []),
        "hypothesis": (parsed or {}).get("hypothesis"),
        "duration_ms": duration,
    }
    if compliance_result and not compliance_result.get("skip"):
        result["compliance_analysis"] = compliance_result
    return result


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

    # ── RAG ingest: chunk + index the document so it's searchable later
    # This is what makes "ARIA, what was on that PDF I just shared?" work.
    # The full extracted text gets chunked (800-char windows with overlap)
    # and persisted to chromadb. From this point on, /rag <query> can find
    # passages from this document, and any future chat call automatically
    # retrieves relevant chunks via the RAG context layer.
    try:
        from . import rag_store
        # Detect source type from filename / source string
        ext = (filename.rsplit(".", 1)[-1] or "").lower()
        source_type = (
            "pdf" if "pdf" in source.lower() or ext == "pdf"
            else "docx" if ext in ("docx", "doc")
            else "spreadsheet" if ext in ("xlsx", "xls", "csv")
            else "document"
        )
        await rag_store.ingest_document(
            text=content,
            source=f"document:{source}:{filename}",
            source_type=source_type,
            title=filename,
            extra_metadata={"context": (context or "")[:300]},
        )
    except Exception as e:
        logger.debug("RAG ingest from read_document failed: %s", e)

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

    # Detect if this is compliance-related content
    is_compliance = _is_compliance_content(
        f"{source} {filename} {context}",
        content,
    )
    if is_compliance:
        logger.info(f"Compliance content detected for {filename} — using compliance analysis")

    compliance_results: list[dict] = []

    for i, chunk in enumerate(chunks):  # No limit — process entire document
        doc_text = f"Document: {filename}\nSource: {source}\n"
        if context:
            doc_text += f"Context: {context}\n"
        doc_text += f"Content (part {i + 1}/{len(chunks)}):\n{chunk}"

        existing_kb = search_knowledge(chunk[:200])

        if is_compliance:
            # Use compliance-specific analysis
            parsed = await _analyse_compliance_document(llm, doc_text, f"{source}:{filename}", existing_kb)
            if parsed and not parsed.get("skip"):
                compliance_results.append(parsed)
                # Also store extracted facts via normal pipeline
                for fact in (parsed.get("facts") or []):
                    topic = fact.get("topic", "")
                    fact_content = fact.get("content", "")
                    confidence = fact.get("confidence", "ASSESSED")
                    if topic and fact_content and len(fact_content) > 20:
                        await store_fact(topic, f"{fact_content} [Source: {source}:{filename}]", f"compliance:{source}", confidence)
                        total_facts += 1
                        all_facts.append(fact)
        else:
            parsed = await _analyse_article(llm, doc_text, f"{source}:{filename}", existing_kb, hypotheses)

        if parsed and not is_compliance:
            fl, hg = await _process_analysis(parsed, f"{source}:{filename}", hypotheses)
            total_facts += fl
            total_hyp += hg
            all_facts.extend(parsed.get("facts", []))

    await _save_hypotheses(hypotheses)

    duration = int((time.time() - t_start) * 1000)
    logger.info(f"Document read: {filename} → {total_facts} facts, {total_hyp} hypotheses ({duration}ms)")

    result = {
        "filename": filename,
        "source": source,
        "content_length": len(content),
        "chunks_processed": len(chunks),
        "facts_learned": total_facts,
        "hypotheses_generated": total_hyp,
        "facts": all_facts,
        "duration_ms": duration,
    }
    if compliance_results:
        result["compliance_analysis"] = compliance_results
    return result


# ── Public: Autonomous research cycle ────────────────────────────────────────

async def research_and_learn(llm: LLMProvider, max_articles: int = 30) -> dict:
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
    search_indices = [(hour * 5 + i) % len(WEB_SEARCH_QUERIES) for i in range(5)]
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
        _cleaned = re.sub(r"^```(?:json)?\s*", "", result.text.strip())
        _cleaned = re.sub(r"\s*```$", "", _cleaned)
        json_match = re.search(r"\{[\s\S]*\}", _cleaned)
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
    kb_size = len((_kb_mod._cache or {}).get("facts", []))
    ledger_size = len((_ledger_mod._cache or {}).get("signals", []))
    open_h = [h for h in hypotheses if h.get("status") == "OPEN"]
    strong_h = [h for h in hypotheses if h.get("status") == "STRENGTHENED"]
    challenged_h = [h for h in hypotheses if h.get("status") == "CHALLENGED"]

    return {
        "knowledge_base_facts": kb_size,
        "intel_ledger_signals": ledger_size,
        "hypotheses": {"total": len(hypotheses), "open": len(open_h), "strengthened": len(strong_h), "challenged": len(challenged_h)},
        "top_hypotheses": [{"hypothesis": h["hypothesis"], "status": h["status"], "evidence": h.get("evidence_count", 0)} for h in hypotheses[:10]],
    }
