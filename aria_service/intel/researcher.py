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

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx

from ..llm.provider import LLMProvider, LLMResult
from . import redis_store as rs
from .ua_rotation import random_ua
from .knowledge import store_fact, search_knowledge
from . import knowledge as _kb_mod
from . import intel_ledger as _ledger_mod

logger = logging.getLogger("aria.researcher")

# ── GLOBAL Defence & Security Research Sources ───────────────────────────────

RESEARCH_FEEDS = [
    # ── Global Defence Procurement ────────────────────────────────────────
    {"name": "Defense News", "url": "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml", "category": "defence_procurement"},
    {"name": "Janes", "url": "https://www.janes.com/osint-insights/defence-news", "category": "defence_industry"},  # old /feeds/news → 404
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
    {"name": "SIPRI Blog", "url": "https://www.sipri.org/rss", "category": "arms_trade"},  # old /rss.xml → 404
    {"name": "DSCA Major Arms Sales", "url": "https://www.dsca.mil/dsca-rss-really-simple-syndication", "category": "fms"},  # old /feed → 403
    {"name": "IISS", "url": "https://www.iiss.org/online-analysis/military-balance/feed", "category": "strategic_studies"},  # old /rss → 404
    {"name": "RUSI", "url": "https://www.rusi.org/rusi-rss-feeds", "category": "defence_research"},  # old /rss.xml → 404
    {"name": "War on the Rocks", "url": "https://warontherocks.com/feed/", "category": "strategy"},
    {"name": "Chatham House", "url": "https://www.chathamhouse.org/rss.xml", "category": "geopolitics"},
    {"name": "CSIS", "url": "https://www.csis.org/rss", "category": "strategy"},  # fixed from /rss.xml
    {"name": "RAND", "url": "https://www.rand.org/pubs/rss.xml", "category": "defence_research"},
    {"name": "ISW", "url": "https://understandingwar.org/feed", "category": "conflict_intelligence"},
    {"name": "UCDP", "url": "https://ucdp.uu.se/apidocs/", "category": "conflict_data"},
    {"name": "CrisisWatch", "url": "https://www.crisisgroup.org/rss/crisiswatch", "category": "conflict_early_warning"},
    {"name": "Crisis Group Africa", "url": "https://www.crisisgroup.org/rss/1", "category": "africa_security"},

    # ── Regional: Africa ──────────────────────────────────────────────────
    {"name": "DefenceWeb", "url": "https://www.defenceweb.co.za/feed/", "category": "africa_defence"},
    {"name": "ISS Africa", "url": "https://issafrica.org/iss-today/feed", "category": "africa_security"},
    {"name": "DW Africa", "url": "https://rss.dw.com/xml/rss-en-africa", "category": "africa_news"},
    {"name": "Africa Confidential", "url": "https://www.africa-confidential.com/rss", "category": "africa_intelligence"},
    {"name": "Club of Mozambique", "url": "https://clubofmozambique.com/feed/", "category": "mozambique"},
    # Africa Intelligence has no public RSS — paywall site, removed (was 404)
    # {"name": "Africa Intelligence", "url": "https://www.africaintelligence.com/rss", "category": "africa_intelligence"},

    # ── Regional: Middle East ─────────────────────────────────────────────
    {"name": "Al-Monitor Defence", "url": "https://www.al-monitor.com/rss", "category": "middle_east"},
    {"name": "Middle East Eye", "url": "https://www.middleeasteye.net/rss", "category": "middle_east"},

    # ── Regional: Asia-Pacific ────────────────────────────────────────────
    {"name": "The Diplomat", "url": "https://thediplomat.com/feed/", "category": "asia_pacific"},
    {"name": "Asia Times", "url": "https://asiatimes.com/feed/", "category": "asia_pacific"},

    # ── Regional: Europe & NATO ───────────────────────────────────────────
    {"name": "European Security & Defence", "url": "https://euro-sd.com/feed/", "category": "europe_defence"},  # EurActiv /feed → 404, replaced with euro-sd.com
    {"name": "Breaking Defense", "url": "https://breakingdefense.com/feed/", "category": "defence_procurement"},

    # ── Regional: Latin America (Spanish) ────────────────────────────────
    {"name": "Infodefensa", "url": "https://www.infodefensa.com/feed", "category": "latam_defence"},  # old /latam/rss.xml + /espana/rss.xml → 404, consolidated
    {"name": "Defensa.com", "url": "https://www.defensa.com/rss", "category": "latam_defence"},
    {"name": "Zona Militar", "url": "https://www.zona-militar.com/feed/", "category": "latam_defence"},
    {"name": "Dialogo Americas", "url": "https://dialogo-americas.com/feed/", "category": "latam_security"},
    # BN Americas RSS removed — /rss/infrastructure → 404, paywall site
    # {"name": "BN Americas Defence", "url": "https://www.bnamericas.com/en/rss/infrastructure", "category": "latam_procurement"},

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
    "Brazil Colombia Mexico Peru Chile Argentina Ecuador defence",
    # Spanish-language LatAm procurement keywords
    "licitación defensa ministerio fuerzas armadas adquisición",
    "contrato militar modernización presupuesto defensa",
    "FAMAE INDUMIL SIMDE SEMAN FAdeA industria defensa",
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
            resp = await client.get(url, headers={"User-Agent": random_ua()})
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
                headers={"User-Agent": random_ua()},
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
                "User-Agent": random_ua(),
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

    # ── Lightpanda JS-rendering fallback ──────────────────────────────
    # If the page returned thin/JS-only content (React SPA, dashboard,
    # etc.), try rendering with Lightpanda headless browser.
    from . import headless as _headless
    if _headless.is_thin_content(html) and _headless.is_available():
        logger.info("Thin content from %s (%d chars) — trying Lightpanda",
                     url[:80], len(html))
        rendered = await _headless.fetch_rendered_html(url, timeout=20)
        if rendered and len(rendered) > len(html):
            html = rendered

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
    """ARIA's independent multi-backend web search.

    Uses ARIA's own search engine (web_search.py) which queries multiple
    backends in parallel (Brave, SearXNG, Google News, Bing News),
    deduplicates, applies credibility scoring, and returns ranked results.

    Falls back to legacy Google News RSS if the search engine fails.

    INDEPENDENCE: ARIA does not depend on any single search provider.
    If Brave is down, SearXNG covers. If SearXNG is down, Google News
    RSS covers. ARIA always returns results.
    """
    try:
        from . import web_search as ws

        # Detect languages for multilingual search
        extra_langs = _detect_target_languages(query)
        languages = ["en"] + list(extra_langs)

        # Use ARIA's multi-backend search with multilingual support
        raw_results = await ws.search_multilingual(
            query, languages=languages, max_results=30,
        )

        # Convert SearchResult objects to the dict format the rest of
        # the pipeline expects (title, link, snippet, source)
        results = []
        for r in raw_results:
            results.append({
                "title": r.title,
                "link": r.url,
                "snippet": r.snippet,
                "source": r.source,
                "_credibility_tier": r.credibility_tier,
                "_relevance_score": r.relevance_score,
                "_language": r.language if r.language != "en" else None,
            })

        if results:
            logger.info("ARIA search: %d results for %r (backends: %s)",
                        len(results), query[:60],
                        ", ".join(set(r["source"].split(":")[0] for r in results)))
            return results

    except Exception as e:
        logger.warning("ARIA search engine failed, falling back to Google News RSS: %s", e)

    # ── Legacy fallback: Google News RSS only ─────────────────────────
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


# ── Public: Web search (Brave API preferred, DuckDuckGo HTML fallback) ──────

async def web_search(query: str, max_results: int = 8, timeout: float = 15.0) -> dict:
    """Perform a web search and return structured results.

    Tries providers in order:
      1. Brave Search API (when BRAVE_SEARCH_API_KEY env var is set —
         2k queries/month free, 1 q/s rate limit, well-structured JSON)
      2. DuckDuckGo HTML scraping (free, no key, fragile but adequate
         for low-volume DD)

    Returns:
      {
        "ok": bool,
        "query": str,
        "provider": "brave" | "ddg" | "none",
        "results": [{"title": str, "url": str, "snippet": str}, ...],
        "error": str (only present when ok=False),
        "duration_ms": int,
      }

    Past incident 2026-04-09: ARIA's URL-only investigation of
    modirumgespi.com couldn't surface the company's actual jurisdiction
    (Finnish HQ + Brazilian defence ops + multi-jurisdiction structure)
    because all that information lives in OSINT sources OFF the company
    website (news articles, registries, LinkedIn). Web search closes
    this gap by giving the LLM a snippet-level view of the broader OSINT
    surface for an entity, which then guides further extract_url calls
    on the most relevant results.
    """
    t0 = time.time()
    if not query or not isinstance(query, str):
        return {
            "ok": False, "query": "", "provider": "none", "results": [],
            "error": "empty query", "duration_ms": 0,
        }
    query = query.strip()[:300]  # cap query length

    # ── Provider 1: Brave Search API ──────────────────────────────────
    brave_key = (os.getenv("BRAVE_SEARCH_API_KEY") or "").strip()
    logger.info(
        "web_search ENTRY query=%r brave_key_present=%s brave_key_len=%d",
        query[:80], bool(brave_key), len(brave_key),
    )
    if brave_key:
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": min(max_results, 20)},
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": brave_key,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json() or {}
                    results = []
                    for r in (data.get("web", {}) or {}).get("results", [])[:max_results]:
                        if not isinstance(r, dict):
                            continue
                        results.append({
                            "title": (r.get("title") or "")[:200],
                            "url": r.get("url") or "",
                            "snippet": (r.get("description") or "")[:400],
                        })
                    return {
                        "ok": True, "query": query, "provider": "brave",
                        "results": results,
                        "duration_ms": int((time.time() - t0) * 1000),
                    }
                else:
                    logger.warning(
                        "web_search Brave API returned HTTP %d for query %r",
                        resp.status_code, query[:80],
                    )
        except Exception as e:
            logger.warning("web_search Brave API failed for %r: %s", query[:80], e)
        # fall through to DuckDuckGo

    # ── Provider 2: DuckDuckGo HTML scraping ──────────────────────────
    # Free, no key required. The HTML endpoint at html.duckduckgo.com
    # returns standard HTML with anchor tags we can parse out. Fragile
    # against DDG layout changes but works as of 2026-04-09.
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query, "kl": "us-en"},
                headers={
                    "User-Agent": random_ua(),
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-GB,en;q=0.9",
                },
            )
            if resp.status_code != 200:
                return {
                    "ok": False, "query": query, "provider": "ddg",
                    "results": [],
                    "error": f"DuckDuckGo HTML returned HTTP {resp.status_code}",
                    "duration_ms": int((time.time() - t0) * 1000),
                }
            html = resp.text
    except Exception as e:
        return {
            "ok": False, "query": query, "provider": "ddg",
            "results": [],
            "error": f"DuckDuckGo HTML fetch failed: {str(e)[:200]}",
            "duration_ms": int((time.time() - t0) * 1000),
        }

    # Parse the DDG HTML — each result is in a <a class="result__a"> with
    # the snippet in the next sibling. Use loose regexes (DDG markup is
    # stable enough for this).
    from urllib.parse import urlparse, parse_qs, unquote
    results = []
    # Each result block: <a class="result__a" href="...">title</a> ... <a class="result__snippet">snippet</a>
    # The href is often a redirect URL like /l/?uddg=<encoded_real_url>
    block_re = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
        r'.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    for m in block_re.finditer(html):
        if len(results) >= max_results:
            break
        raw_href, raw_title, raw_snippet = m.group(1), m.group(2), m.group(3)
        # Unwrap DDG redirect: /l/?uddg=https%3A%2F%2Fexample.com%2Fpath
        actual_url = raw_href
        if raw_href.startswith("/l/?"):
            try:
                qs = parse_qs(urlparse(raw_href).query)
                uddg = qs.get("uddg", [""])[0]
                if uddg:
                    actual_url = unquote(uddg)
            except Exception as e:
                # Don't silently feed the raw /l/? redirect to the LLM as a
                # "search result" — that produces dead URLs and the LLM cites
                # nothing useful. Log + skip this result.
                logger.warning("DDG redirect unwrap failed for %r: %s", raw_href[:80], e)
                continue
        elif raw_href.startswith("//"):
            actual_url = "https:" + raw_href
        # Strip HTML tags from title/snippet
        title = re.sub(r"<[^>]+>", "", raw_title)
        title = re.sub(r"&\w+;", " ", title).strip()[:200]
        snippet = re.sub(r"<[^>]+>", "", raw_snippet)
        snippet = re.sub(r"&\w+;", " ", snippet).strip()[:400]
        if actual_url and title:
            results.append({"title": title, "url": actual_url, "snippet": snippet})

    return {
        "ok": True, "query": query, "provider": "ddg",
        "results": results,
        "duration_ms": int((time.time() - t0) * 1000),
    }


# ── Public: Deep multi-query research orchestrator (no LLM, no RAG) ─────────

# Source-tier scoring hints — used to rank URLs returned by web_search.
# Higher score = more authoritative source. Same hierarchy as the
# `researcher_principles.py` addendum tells the LLM about, but applied
# DETERMINISTICALLY at retrieval time so the top-ranked URLs are
# inherently higher-quality before the LLM ever sees them.
_DOMAIN_TIER_SCORES: list[tuple[str, int]] = [
    # Tier 1 — official records, registries, governments (score 100)
    ("companieshouse.gov.uk",        100),
    ("gov.uk",                       100),
    ("treasury.gov",                 100),
    ("ofac.treasury.gov",            100),
    ("sanctionssearch.ofac.treas.gov", 100),
    ("eur-lex.europa.eu",            100),
    ("europa.eu",                    100),
    ("un.org",                       100),
    ("nato.int",                     100),
    ("ohchr.org",                    100),
    ("portaldojusticia.pt",          100),
    ("racius.com",                   100),  # Portuguese registry aggregator
    ("registocomercial.pt",          100),
    ("ytj.fi",                       100),  # Finnish business registry
    ("prh.fi",                       100),  # Finnish patent + registry
    ("kbo-bce.fgov.be",              100),  # Belgian crossroads bank
    ("rejestr.io",                   100),  # Polish registry
    ("opencorporates.com",            95),
    ("opensanctions.org",             95),
    ("offshoreleaks.icij.org",        95),
    # Tier 2 — institutional / think tanks (score 80)
    ("sipri.org",                     80),
    ("rand.org",                      80),
    ("rusi.org",                      80),
    ("iiss.org",                      80),
    ("csis.org",                      80),
    ("cfr.org",                       80),
    ("carnegieendowment.org",         80),
    ("worldbank.org",                 80),
    ("imf.org",                       80),
    ("acleddata.com",                 80),
    ("fatf-gafi.org",                 80),
    ("transparency.org",              80),
    ("oecd.org",                      80),
    # Tier 3 — specialist defence trade press (score 65)
    ("janes.com",                     65),
    ("defensenews.com",               65),
    ("breakingdefense.com",           65),
    ("naval-news.com",                65),
    ("navalnews.com",                 65),
    ("shephardmedia.com",             65),
    ("c4isrnet.com",                  65),
    ("army-technology.com",           65),
    ("air-force-technology.com",      65),
    ("naval-technology.com",          65),
    ("armyrecognition.com",           65),
    ("defenceweb.co.za",              65),
    # Tier 4 — quality journalism (score 50)
    ("ft.com",                        50),
    ("reuters.com",                   50),
    ("apnews.com",                    50),
    ("bbc.com",                       50),
    ("bbc.co.uk",                     50),
    ("economist.com",                 50),
    ("lemonde.fr",                    50),
    ("lusa.pt",                       50),
    ("expresso.pt",                   50),
    ("publico.pt",                    50),
    ("jornaldenegocios.pt",           50),
    ("clubofmozambique.com",          50),
    ("bloomberg.com",                 50),
    ("nytimes.com",                   50),
    ("washingtonpost.com",            50),
    # Tier 5 — secondary (score 30) — wikipedia, generic press releases
    ("wikipedia.org",                 30),
    ("crunchbase.com",                40),
    ("linkedin.com",                  45),  # higher than generic — DD valuable
    ("dnb.com",                       45),  # Dun & Bradstreet
]


def _score_url_by_domain_tier(url: str) -> int:
    """Return a domain-tier score for ranking search results. Higher =
    more authoritative source. URLs not in the tier list get score 10
    (uncategorised — treated as low-trust generic web)."""
    if not url:
        return 0
    u = url.lower()
    for hint, score in _DOMAIN_TIER_SCORES:
        if hint in u:
            return score
    return 10


async def deep_research(
    entity: str,
    *,
    primary_url: str = "",
    max_queries: int = 5,
    max_extracts: int = 4,
    timeout: float = 12.0,
    overall_budget: float = 45.0,
) -> dict:
    """Orchestrated multi-query, multi-extract research on an entity.

    Workflow:
      1. Issue `max_queries` parallel web searches with different angles
         (entity / entity+company / entity+headquarters / entity+directors
         / entity+news) so different facets of the OSINT surface are
         covered, not just the most generic Google-style query.
      2. Aggregate + dedup the snippet results across all queries.
      3. Rank URLs by source-tier score (registry > think tank > trade
         press > journalism > generic web).
      4. Extract verbatim content from the top `max_extracts` URLs in
         parallel via extract_url_text.
      5. If a `primary_url` is supplied (the user gave us the entity's
         own website), ALSO run extract_url_deep on it for multi-page
         coverage of that specific domain — homepage marketing copy
         alone is rarely enough.
      6. Return ONE unified result with: all snippets, all extracted text,
         the ranked URL list, the source-tier breakdown.

    NO LLM call, NO RAG ingest. Pure HTTP fetching + structured extraction.
    The chat-path tool result block embeds the entire output verbatim so
    the main chat LLM can read all sources before producing its reply.

    Past incident 2026-04-09 (evening): Antonio asked ARIA to investigate
    Modirum Gespi. ARIA had only one tool (extract_url_deep on the URL
    he provided) and could only see what the company published on its
    own homepage. The actual jurisdiction (Finnish HQ) and operational
    structure (Brazilian defence ops + multi-jurisdiction) were nowhere
    on the company website — they live in Finnish trade press, the
    Finnish business registry, LinkedIn, and news articles. Without web
    search, ARIA was structurally blind to all of that. deep_research
    fixes this by ALWAYS doing snippet-level OSINT discovery alongside
    site extraction.
    """
    t0 = time.time()
    if not entity or not isinstance(entity, str):
        return {
            "ok": False, "entity": "", "error": "empty entity",
            "duration_ms": 0,
        }
    entity = entity.strip()[:200]
    logger.info(
        "deep_research ENTRY entity=%r primary_url=%r max_queries=%d max_extracts=%d",
        entity[:80], primary_url[:120] if primary_url else "", max_queries, max_extracts,
    )

    # ── Step 1: build a small set of search angles ────────────────────
    # Each angle surfaces a different facet of the OSINT surface. Order
    # matters — earlier queries are more important if we have to truncate.
    angles = [
        entity,                                    # generic discovery
        f"{entity} company",                       # corporate identity
        f"{entity} headquarters location",         # jurisdiction
        f"{entity} directors leadership",          # people
        f"{entity} news",                          # recent activity
    ][:max_queries]

    # ── Step 2: parallel web searches with overall budget cap ────────
    # Pre-Phase-3 latency cap 2026-04-09: previously the gather had no
    # overall wall-clock budget — slow providers (Brave timeout, DDG
    # rate-limiting) could push the entire chat turn past 5 minutes.
    # Now we wait at most overall_budget/2 for the search step, and the
    # extraction step gets the remaining budget. Past incident: rolling
    # mean turn latency was 348s with deep_research, vs <90s target.
    logger.info("deep_research firing %d parallel web_search angles: %r", len(angles), angles)
    search_budget = max(8.0, overall_budget * 0.45)
    search_tasks = [web_search(angle, max_results=6, timeout=timeout) for angle in angles]
    try:
        search_results_per_angle = await asyncio.wait_for(
            asyncio.gather(*search_tasks, return_exceptions=True),
            timeout=search_budget,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "deep_research search step exceeded budget %.1fs — using empty results",
            search_budget,
        )
        search_results_per_angle = [TimeoutError("search budget exceeded")] * len(angles)
    for angle, sr in zip(angles, search_results_per_angle):
        if isinstance(sr, Exception):
            logger.warning("deep_research angle=%r RAISED: %s: %s", angle, type(sr).__name__, sr)
        elif isinstance(sr, dict):
            logger.info(
                "deep_research angle=%r ok=%s provider=%s results=%d error=%s",
                angle, sr.get("ok"), sr.get("provider"),
                len(sr.get("results") or []), sr.get("error", ""),
            )

    # ── Step 3: aggregate + dedup snippets across all angles ──────────
    snippets_by_url: dict[str, dict] = {}
    snippet_count_per_provider: dict[str, int] = {}
    snippet_count_per_angle: dict[str, int] = {}
    for angle, sr in zip(angles, search_results_per_angle):
        if isinstance(sr, Exception):
            logger.debug("deep_research search angle %r failed: %s", angle, sr)
            continue
        if not isinstance(sr, dict) or not sr.get("ok"):
            continue
        provider = sr.get("provider", "?")
        snippet_count_per_provider[provider] = snippet_count_per_provider.get(provider, 0)
        for r in sr.get("results") or []:
            url = (r.get("url") or "").strip()
            if not url:
                continue
            # Dedup by URL — keep the first snippet but record which angles
            # surfaced this URL (signal of cross-angle relevance)
            if url not in snippets_by_url:
                snippets_by_url[url] = {
                    "title": r.get("title", ""),
                    "url": url,
                    "snippet": r.get("snippet", ""),
                    "tier_score": _score_url_by_domain_tier(url),
                    "angles": [angle],
                }
            else:
                if angle not in snippets_by_url[url]["angles"]:
                    snippets_by_url[url]["angles"].append(angle)
            snippet_count_per_provider[provider] += 1
            snippet_count_per_angle[angle] = snippet_count_per_angle.get(angle, 0) + 1

    all_snippets = list(snippets_by_url.values())

    # ── Step 4: rank URLs (tier_score + cross-angle bonus) ────────────
    for s in all_snippets:
        # Cross-angle bonus: a URL that appears in 2+ angle results is
        # significantly more on-topic than one that appears in just one
        s["rank_score"] = s["tier_score"] + (len(s["angles"]) - 1) * 15

    all_snippets.sort(key=lambda s: s["rank_score"], reverse=True)
    top_for_extract = [s for s in all_snippets if s["url"] != primary_url][:max_extracts]

    # ── Step 5: parallel verbatim extraction of top URLs ──────────────
    # Latency cap: extraction step gets the remaining wall-clock budget
    # (subtract whatever the search step consumed). Hard floor of 8s so
    # that fast searches don't starve the extraction.
    elapsed_so_far = time.time() - t0
    extract_budget = max(8.0, overall_budget - elapsed_so_far - 1.0)
    extract_tasks = [extract_url_text(s["url"], timeout=timeout) for s in top_for_extract]
    if primary_url:
        # Also do a deep multi-page fetch on the primary URL if provided
        extract_tasks.append(extract_url_deep(primary_url, max_pages=3, timeout=timeout))
    try:
        extracted_results = await asyncio.wait_for(
            asyncio.gather(*extract_tasks, return_exceptions=True),
            timeout=extract_budget,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "deep_research extract step exceeded budget %.1fs — partial results lost",
            extract_budget,
        )
        extracted_results = [TimeoutError("extract budget exceeded")] * len(extract_tasks)

    extracted_pages: list[dict] = []
    for ext in extracted_results:
        if isinstance(ext, Exception):
            # WARNING (was DEBUG) — same lesson as the import-os bug:
            # silent gather-swallowed exceptions hide real failures.
            logger.warning(
                "deep_research extraction RAISED: %s: %s",
                type(ext).__name__, ext,
            )
            continue
        if not isinstance(ext, dict) or not ext.get("extraction_ok"):
            continue
        # Latency cap 2026-04-09: text capped at 2500 chars (was 4000) to
        # cut LLM context cost. Mean turn was 25k tokens at 4000-char
        # extracts × 5 pages — too expensive for sub-90s turns.
        extracted_pages.append({
            "url": ext.get("url", ""),
            "title": ext.get("title", ""),
            "description": ext.get("description", ""),
            "text": (ext.get("text") or "")[:2500],
            "social": (ext.get("social") or [])[:3],
            "emails": (ext.get("emails") or [])[:3],
            "phones": (ext.get("phones") or [])[:2],
            "is_deep": ext.get("deep_mode", False),
            "pages_fetched": ext.get("pages_fetched") or [ext.get("url", "")],
        })

    total_elapsed_ms = int((time.time() - t0) * 1000)
    if total_elapsed_ms > overall_budget * 1000:
        logger.warning(
            "deep_research exceeded overall budget: %dms vs %dms cap",
            total_elapsed_ms, int(overall_budget * 1000),
        )

    return {
        "ok": True,
        "entity": entity,
        "primary_url": primary_url or None,
        "queries_run": angles,
        "snippet_count_per_provider": snippet_count_per_provider,
        "snippet_count_per_angle": snippet_count_per_angle,
        "snippets_total": len(all_snippets),
        "snippets_top": all_snippets[:10],  # was 15 — context-budget cut
        "extracted_pages": extracted_pages,
        "extracted_count": len(extracted_pages),
        "duration_ms": total_elapsed_ms,
        "budget_ms": int(overall_budget * 1000),
    }


# ── Public: Multi-page deep URL extraction (no LLM, no RAG) ──────────────────

# High-value internal link path fragments worth following on a corporate
# site DD. Order matters — earlier entries are higher priority. The follower
# stops after collecting `max_pages` distinct links.
_DD_LINK_FRAGMENTS = (
    "/about", "/about-us", "/who-we-are", "/our-company", "/company",
    "/team", "/leadership", "/management", "/people", "/founders",
    "/board", "/directors",
    "/contact", "/contact-us", "/get-in-touch",
    "/products", "/solutions", "/services", "/portfolio", "/offerings",
    "/history", "/story", "/heritage", "/our-story",
    "/locations", "/offices", "/where-we-are", "/global-presence",
    "/news", "/press", "/media",
    "/clients", "/partners", "/customers",
    "/careers", "/jobs",  # careers pages often reveal locations + headcount
)

# Asset / nav-junk patterns to skip when collecting internal links.
_DD_LINK_SKIP = (
    ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf",
    ".webp", ".woff", ".woff2", ".ico", ".xml", ".rss", ".zip",
    "/login", "/signin", "/signup", "/register", "/cart", "/checkout",
    "/wp-", "/wp-content", "/wp-admin", "/wp-includes",
    "javascript:", "mailto:", "tel:", "#",
)


def _collect_internal_dd_links(homepage_html: str, base_url: str, max_links: int = 5) -> list[str]:
    """Parse the homepage HTML for high-value internal links worth following
    on a corporate DD investigation. Returns a deduplicated list, capped at
    max_links, prioritised by the order of _DD_LINK_FRAGMENTS."""
    if not homepage_html:
        return []
    from urllib.parse import urljoin, urlparse
    base_host = urlparse(base_url).netloc.lower()
    if not base_host:
        return []

    # Extract every href from the HTML (cheap regex — good enough for DD link
    # discovery, doesn't need a real HTML parser)
    hrefs = []
    for m in re.finditer(r'href=["\']([^"\'\s>]+)', homepage_html, re.IGNORECASE):
        hrefs.append(m.group(1))

    # Score each href by how early its path matches a DD fragment
    candidates: dict[str, int] = {}  # url → priority (lower = higher prio)
    for href in hrefs:
        # Skip obviously non-content links
        href_lower = href.lower()
        if any(skip in href_lower for skip in _DD_LINK_SKIP):
            continue
        # Resolve relative URLs
        try:
            full = urljoin(base_url, href)
        except Exception as e:
            logger.debug("urljoin failed for href=%r base=%r: %s", href[:80], base_url[:80], e)
            continue
        parsed = urlparse(full)
        # Same-domain only
        if parsed.netloc.lower() != base_host:
            continue
        # Strip query string + fragment for dedup
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
        if not clean or clean.lower() == base_url.rstrip("/").lower():
            continue
        # Find the best DD fragment match (lowest index = highest priority)
        for idx, frag in enumerate(_DD_LINK_FRAGMENTS):
            if frag in parsed.path.lower():
                if clean not in candidates or candidates[clean] > idx:
                    candidates[clean] = idx
                break
        # Note: links with no DD fragment match are NOT added — we only
        # follow high-value pages, not the full sitemap

    # Sort by priority then return top N
    sorted_links = sorted(candidates.items(), key=lambda kv: kv[1])
    return [url for url, _ in sorted_links[:max_links]]


async def extract_url_deep(url: str, max_pages: int = 5, timeout: float = 15.0) -> dict:
    """Multi-page DD extraction. Fetches the URL plus N high-value internal
    links (about / team / contact / products / leadership / etc.), aggregates
    the structured content, and returns ONE combined result.

    This is the FAST primitive for chat-path "investigate URL" intent (vs
    `extract_url_text` which is single-page only). NO LLM call, NO RAG ingest.

    Past incident 2026-04-09 (afternoon): ARIA's single-page extract_url
    on modirumgespi.com only saw the homepage meta tags + skeleton, missed
    the company's actual jurisdiction (Brazilian defence operations + mixed
    locations), and the LLM confabulated "Portuguese OEM" from the
    `hreflang="pt-pt"` language-variant attribute. Multi-page extraction
    surfaces /about / /contact / /locations / /team etc. where the real
    jurisdiction + leadership info typically lives.

    Returns the same shape as extract_url_text plus a `pages_fetched` list
    showing every URL that contributed to the result.
    """
    t0 = time.time()
    # Step 1: fetch the homepage and extract structured content
    homepage = await extract_url_text(url, timeout=timeout)
    if not homepage.get("extraction_ok"):
        return {
            **homepage,
            "pages_fetched": [url],
            "deep_mode": True,
        }

    # Step 2: parse homepage for high-value internal links
    # Re-fetch the raw HTML so we can scan the hrefs (extract_url_text
    # only returns the cleaned text, not the original markup)
    from .security import sanitise_url
    sanitised = sanitise_url(url)
    raw_html = ""
    if sanitised:
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(sanitised, headers={
                    "User-Agent": random_ua(),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                })
                if resp.status_code == 200:
                    raw_html = resp.text
        except Exception as e:
            logger.debug("extract_url_deep raw HTML re-fetch failed: %s", e)

    internal_links = _collect_internal_dd_links(raw_html, sanitised or url, max_links=max_pages - 1)

    # Step 3: fetch each internal link in parallel (capped concurrency)
    pages_fetched = [url]
    aggregate_text_parts = [f"=== HOMEPAGE: {url} ===\n{homepage.get('text', '')}"]
    aggregate_emails = set(homepage.get("emails") or [])
    aggregate_phones = set(homepage.get("phones") or [])
    aggregate_social = set(homepage.get("social") or [])
    aggregate_jsonld = list(homepage.get("structured") or [])
    aggregate_headings = list(homepage.get("headings") or [])

    if internal_links:
        # Run sub-fetches in parallel — much faster than sequential
        sub_results = await asyncio.gather(
            *[extract_url_text(link, timeout=timeout) for link in internal_links],
            return_exceptions=True,
        )
        for link, result in zip(internal_links, sub_results):
            if isinstance(result, Exception):
                logger.debug("extract_url_deep sub-fetch failed for %s: %s", link, result)
                continue
            if not isinstance(result, dict) or not result.get("extraction_ok"):
                continue
            pages_fetched.append(link)
            aggregate_text_parts.append(f"=== {link} ===\n{result.get('text', '')}")
            aggregate_emails.update(result.get("emails") or [])
            aggregate_phones.update(result.get("phones") or [])
            aggregate_social.update(result.get("social") or [])
            aggregate_jsonld.extend(result.get("structured") or [])
            for h in (result.get("headings") or []):
                if h not in aggregate_headings:
                    aggregate_headings.append(h)

    # Cap the aggregated text to keep prompt budget sane
    full_text = "\n\n".join(aggregate_text_parts)[:24000]

    # ── Track C: LLM-free structured + fact extraction (2026-04-18) ─
    # Run the two zero-LLM extractors over the aggregated text so the
    # caller gets typed structured data (tables, schema.org, amounts,
    # dates, reg numbers, named executives) without a round-trip to
    # DeepSeek/Claude. Every turn previously spent 2-5s of LLM time
    # pattern-matching facts a regex could have handled — this closes
    # that gap.
    extractor_tables: list = []
    extractor_json_ld: list = []
    extractor_meta: dict = {}
    extractor_schema_types: list = []
    extractor_facts: dict = {}
    try:
        from .extractors import structured as _struct
        from .extractors import facts as _facts
        # structured.extract takes the raw HTML of the HOMEPAGE — the
        # richest single page for tables + schema markup. Sub-pages'
        # structured data is typically thinner.
        if raw_html:
            try:
                s_out = _struct.extract(raw_html, base_url=url)
                extractor_tables = s_out.get("tables", [])
                extractor_json_ld = s_out.get("json_ld", [])
                extractor_meta = {
                    "opengraph": s_out.get("opengraph", {}),
                    "twitter": s_out.get("twitter", {}),
                    "meta": s_out.get("meta", {}),
                }
                extractor_schema_types = s_out.get("schema_org", [])
            except Exception as _e:
                logger.debug("structured extractor failed: %s", _e)
        # facts.extract runs over the aggregated TEXT across all pages
        # — more signal than single-page since /about/team/contact
        # often each carry a different fragment of the company story.
        try:
            extractor_facts = _facts.extract(full_text, base_url=url)
        except Exception as _e:
            logger.debug("facts extractor failed: %s", _e)
    except ImportError:
        pass  # extractors package not deployed — graceful degrade

    # ── Track C: auto-ingest to RAG so future turns don't re-crawl ──
    # Behind ARIA_DEEP_EXTRACT_AUTO_INGEST env var (default ON). When
    # enabled, every extract_url_deep call persists the aggregated text
    # + extracted facts as a chunked document in the RAG store so next
    # turn's question on the same entity retrieves from memory instead
    # of firing another crawl.
    _auto_ingest = (os.getenv("ARIA_DEEP_EXTRACT_AUTO_INGEST", "1") or "1").strip() != "0"
    if _auto_ingest and full_text and len(full_text) > 300:
        try:
            from . import rag_store as _rag
            from urllib.parse import urlparse as _up
            host = _up(url).netloc.replace("www.", "") if url else "unknown"
            await _rag.ingest_document(
                full_text,
                source=f"extract_url_deep:{host}",
                source_type="crawl",
                title=homepage.get("title", "")[:200],
                url=url,
                extra_metadata={
                    "pages_fetched_count": len(pages_fetched),
                    "schema_org": extractor_schema_types[:10],
                    "has_tables": bool(extractor_tables),
                    "fact_counts": {
                        k: len(v) if isinstance(v, list) else 0
                        for k, v in extractor_facts.items()
                    },
                },
            )
        except Exception as _e:
            logger.debug("extract_url_deep RAG auto-ingest failed: %s", _e)

    return {
        "url": url,
        "extraction_ok": True,
        "deep_mode": True,
        "pages_fetched": pages_fetched,
        "pages_count": len(pages_fetched),
        "text": full_text,
        "title": homepage.get("title", ""),
        "description": homepage.get("description", ""),
        "headings": aggregate_headings[:60],
        "emails": sorted(aggregate_emails)[:30],
        "phones": sorted(aggregate_phones)[:20],
        "social": sorted(aggregate_social)[:20],
        "structured": aggregate_jsonld,
        # Track C additions — structured + regex-extracted facts:
        "tables": extractor_tables,
        "json_ld": extractor_json_ld,
        "meta_structured": extractor_meta,
        "schema_org_types": extractor_schema_types,
        "facts": extractor_facts,
        "duration_ms": int((time.time() - t0) * 1000),
    }


# ── Public: Fast URL text extraction (no LLM, no RAG) ────────────────────────

async def extract_url_text(url: str, timeout: float = 15.0) -> dict:
    """Fetch a URL and return STRUCTURED extracted text. NO LLM call, NO RAG ingest.

    This is the FAST primitive for the chat-path URL handling. It exists
    because read_article() and crawl_website() both make per-page LLM calls
    AND chromadb RAG ingests, which together can take 30-150 seconds —
    far too slow for inline use in a chat reply.

    Past incident 2026-04-09: ARIA fabricated a "Portuguese consultancy"
    profile for modirumgespi.com (an AI-defence systems integrator) because
    the chat-path auto-crawl on the URL was taking 90+ seconds and
    timing out before returning useful content. The LLM then fell back
    to session memory + general knowledge and confabulated registry data.
    The fix is this fast extractor — chat injects its result into the
    message envelope as `[CRAWLED PAGE: ...]` so the main LLM has the
    verbatim site content and can quote from it (or refuse per clauses
    9, 12, 14 if empty).

    Returns the same shape as `_extract_structured_html` plus a `url`
    field, an `extraction_ok` boolean, and an `error` field on failure.
    On any error returns `extraction_ok=False` so callers can tell the
    LLM "the fetch failed — refuse per clause 9".
    """
    from .security import sanitise_url
    url = sanitise_url(url)
    if not url:
        return {"url": url, "extraction_ok": False, "error": "invalid url", "text": ""}

    t0 = time.time()
    html = ""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": random_ua(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
            })
            if resp.status_code == 200:
                html = resp.text
            elif resp.status_code in (401, 402, 403):
                logger.info("extract_url_text: %s returned %d, trying archive", url[:80], resp.status_code)
                html = await _try_archive_fallbacks(url, timeout=timeout)
            else:
                return {
                    "url": url, "extraction_ok": False,
                    "error": f"HTTP {resp.status_code}", "text": "",
                    "duration_ms": int((time.time() - t0) * 1000),
                }
    except Exception as e:
        logger.debug("extract_url_text fetch failed for %s: %s", url[:80], e)
        try:
            html = await _try_archive_fallbacks(url, timeout=timeout)
        except Exception:
            html = ""
        if not html:
            return {
                "url": url, "extraction_ok": False,
                "error": str(e)[:200], "text": "",
                "duration_ms": int((time.time() - t0) * 1000),
            }

    if not html or len(html) < 100:
        return {
            "url": url, "extraction_ok": False,
            "error": "fetched content too short or empty", "text": "",
            "duration_ms": int((time.time() - t0) * 1000),
        }

    extracted = _extract_structured_html(html)
    text = extracted.get("text", "") or ""
    if not text or len(text) < 50:
        # Static fetcher returned thin content (likely JS-rendered site).
        # Surface what little we got via meta tags + JSON-LD so the LLM
        # has SOMETHING to quote from, plus an explicit warning.
        meta_bits = []
        if extracted.get("title"):
            meta_bits.append(f"TITLE: {extracted['title']}")
        if extracted.get("description"):
            meta_bits.append(f"DESCRIPTION: {extracted['description']}")
        for jsd in (extracted.get("structured") or [])[:3]:
            try:
                meta_bits.append(f"JSON-LD: {json.dumps(jsd)[:600]}")
            except Exception:
                pass
        if meta_bits:
            text = "\n\n".join(meta_bits)
        else:
            return {
                "url": url, "extraction_ok": False,
                "error": "fetched but extraction returned no usable text — likely a JS-rendered SPA",
                "text": "",
                "duration_ms": int((time.time() - t0) * 1000),
            }

    return {
        "url": url,
        "extraction_ok": True,
        "text": text[:8000],
        "title": extracted.get("title", ""),
        "description": extracted.get("description", ""),
        "headings": extracted.get("headings") or [],
        "social": extracted.get("social") or [],
        "emails": extracted.get("emails") or [],
        "phones": extracted.get("phones") or [],
        "structured": extracted.get("structured") or [],
        "duration_ms": int((time.time() - t0) * 1000),
    }


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

    # Truncate heavy pages to prevent LLM timeout (past incident 2026-04-16:
    # UCDP downloads page returned 8,000+ chars causing synthesis timeout)
    if len(body) > 6000:
        body = body[:6000] + "\n\n[TRUNCATED — original content was " + str(len(body)) + " chars]"

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

    # Feed brain with article learning outcome
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="knowledge_ingestor",
            summary=f"Article read: {url[:80]} → {facts_learned} facts, {hyp_generated} hypotheses",
            detail=body[:2000] if body else "",
            success=facts_learned > 0,
            confidence="ASSESSED",
        )
    except Exception:
        pass

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

    # Signal brain that a document was read and learned from
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="knowledge_ingestor",
            summary=f"Document read: {filename} ({len(content)} chars, {len(chunks)} chunks) → {total_facts} facts, {total_hyp} hypotheses",
            success=total_facts > 0,
            confidence="CONFIRMED" if total_facts > 0 else "ASSESSED",
        )
    except Exception:
        pass

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
        # Yield control before each article so chat replies and other
        # interactive handlers can interleave with the heavy research work.
        # Without this, a long article-processing run holds the event loop
        # for the entire batch (~30-90s) and starves chat traffic.
        await asyncio.sleep(0.1)

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
