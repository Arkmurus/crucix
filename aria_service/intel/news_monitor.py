"""R-F1049 — News Feed Monitor: RSS/Atom feed ingestion + news API integration.

Monitors hundreds of news sources across defence, geopolitics, finance, and
regional markets. Feeds every article into ARIA's brain for analysis.

Architecture
════════════
  1. RSS/Atom feed polling — periodic fetch of configured feeds
  2. News API search — NewsAPI / GNews / Bing News for real-time queries
  3. Article extraction — extract title, body, date, source from each item
  4. Dedup — skip articles already seen (by URL hash)
  5. Brain feed — each new article → brain_hook + intel_ledger + knowledge
  6. Alerting — flag articles matching configured keywords/entities

Source catalogue
════════════════
Organised by category for easy maintenance. Each entry:
  (name, url, category, language, tier, topics)

Categories:
  - defence_global     — Global defence news
  - defence_regional   — Regional defence news (Africa, MENA, LATAM, Asia)
  - geopolitics        — Geopolitical analysis & think tanks
  - finance            — Financial news & markets
  - technology         — Defence technology & cyber
  - press_releases     — Official press releases
  - regional_news      — Regional general news (Lusophone, Arabic, Turkish)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from . import redis_store as rs
from .engine_wiring import wire_success, wire_failure
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.news_monitor")

# ── In-memory stats cache (avoids cold-start 5xx flaps) ────────────────────
_stats_cache: dict[str, Any] = {}
_stats_cache_ts: float = 0
_STATS_CACHE_TTL = 30.0  # seconds

# ── Redis keys ────────────────────────────────────────────────────────────────
_SEEN_URLS_KEY = "crucix:news_monitor:seen_urls"
_FEED_STATE_KEY = "crucix:news_monitor:feed_state"
_ARTICLES_KEY = "crucix:news_monitor:articles"
_MAX_ARTICLES = 1000
_MAX_SEEN_URLS = 50000

# ── HTTP client ───────────────────────────────────────────────────────────────
_TIMEOUT_S = 15
_MAX_RETRIES = 2

_http_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(  # no-breaker: a feed poller hits many INDEPENDENT feeds with per-feed bounded retries + failure marking; a single global breaker would wrongly cut all feeds when one is down (R-F2046)
            timeout=_TIMEOUT_S,
            follow_redirects=True,
            headers={
                "User-Agent": "ARIA-NewsMonitor/1.0 (Arkmurus Research; +https://arkmurus.com)",
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
            },
        )
    return _http_client


# ── Source catalogue ──────────────────────────────────────────────────────────
# Each entry: (name, feed_url, category, language, tier, topics)
NEWS_SOURCES: list[tuple[str, str, str, str, str, list[str]]] = [

    # ══════════════════════════════════════════════════════════════════════
    # GLOBAL DEFENCE NEWS
    # ══════════════════════════════════════════════════════════════════════
    ("Janes Defence", "https://www.janes.com/rss", "defence_global", "en", "tier_1b",
     ["defence", "market_intel", "procurement"]),
    ("Janes Industry", "https://www.janes.com/rss/industry", "defence_global", "en", "tier_1b",
     ["defence", "market_intel", "industry"]),
    ("Defense News", "https://www.defensenews.com/arc/outboundfeeds/rss/", "defence_global", "en", "tier_2",
     ["defence", "market_intel", "procurement"]),
    ("Janes Defence Weekly", "https://www.janes.com/rss/defence-weekly", "defence_global", "en", "tier_1b",
     ["defence", "geopolitics"]),
    ("Shephard Media", "https://www.shephardmedia.com/rss/", "defence_global", "en", "tier_2",
     ["defence", "market_intel", "naval", "air"]),
    ("Naval News", "https://www.navalnews.com/feed/", "defence_global", "en", "tier_2",
     ["defence", "naval", "market_intel"]),
    ("Airforce Technology", "https://www.airforce-technology.com/feed/", "defence_global", "en", "tier_2",
     ["defence", "air", "market_intel"]),
    ("Army Technology", "https://www.army-technology.com/feed/", "defence_global", "en", "tier_2",
     ["defence", "land", "market_intel"]),
    ("Naval Technology", "https://www.naval-technology.com/feed/", "defence_global", "en", "tier_2",
     ["defence", "naval", "market_intel"]),
    ("UK Defence Journal", "https://ukdefencejournal.org.uk/feed/", "defence_global", "en", "tier_2",
     ["defence", "uk", "market_intel"]),
    ("European Defence Review", "https://www.edrmagazine.eu/feed", "defence_global", "en", "tier_2",
     ["defence", "europe", "market_intel"]),
    ("Defence Blog", "https://defence-blog.com/feed/", "defence_global", "en", "tier_2",
     ["defence", "market_intel"]),
    ("Military Aerospace", "https://www.militaryaerospace.com/rss", "defence_global", "en", "tier_2",
     ["defence", "aerospace", "technology"]),
    ("Asian Military Review", "https://www.asianmilitaryreview.com/feed/", "defence_global", "en", "tier_2",
     ["defence", "asia", "market_intel"]),

    # ══════════════════════════════════════════════════════════════════════
    # REGIONAL DEFENCE NEWS
    # ══════════════════════════════════════════════════════════════════════

    # Africa
    ("Africa Defence Forum", "https://adf-magazine.com/feed/", "defence_regional", "en", "tier_2",
     ["defence", "africa", "market_intel"]),
    ("DefenceWeb Africa", "https://www.defenceweb.co.za/feed/", "defence_regional", "en", "tier_2",
     ["defence", "africa", "market_intel"]),
    ("Janes Africa", "https://www.janes.com/rss/africa", "defence_regional", "en", "tier_1b",
     ["defence", "africa", "geopolitics"]),

    # Middle East
    ("Janes Middle East", "https://www.janes.com/rss/middle-east", "defence_regional", "en", "tier_1b",
     ["defence", "middle_east", "geopolitics"]),
    ("Middle East Defence", "https://www.middleeastdefence.com/feed/", "defence_regional", "en", "tier_2",
     ["defence", "middle_east", "market_intel"]),

    # Latin America
    ("Janes Latin America", "https://www.janes.com/rss/latin-america", "defence_regional", "en", "tier_1b",
     ["defence", "latin_america", "geopolitics"]),
    ("Dialogo Americas", "https://dialogo-americas.com/feed/", "defence_regional", "en", "tier_2",
     ["defence", "latin_america", "security"]),

    # Asia-Pacific
    ("Janes Asia-Pacific", "https://www.janes.com/rss/asia-pacific", "defence_regional", "en", "tier_1b",
     ["defence", "asia", "geopolitics"]),

    # Europe
    ("Janes Europe", "https://www.janes.com/rss/europe", "defence_regional", "en", "tier_1b",
     ["defence", "europe", "geopolitics"]),

    # ══════════════════════════════════════════════════════════════════════
    # GEOPOLITICS & THINK TANKS
    # ══════════════════════════════════════════════════════════════════════
    ("IISS", "https://www.iiss.org/rss/", "geopolitics", "en", "tier_1b",
     ["geopolitics", "defence", "analysis"]),
    ("RUSI", "https://rusi.org/rss", "geopolitics", "en", "tier_1b",
     ["geopolitics", "defence", "security"]),
    ("CSIS", "https://www.csis.org/rss", "geopolitics", "en", "tier_1b",
     ["geopolitics", "defence", "analysis"]),
    ("Chatham House", "https://www.chathamhouse.org/rss", "geopolitics", "en", "tier_1b",
     ["geopolitics", "international_relations"]),
    ("Carnegie Endowment", "https://carnegieendowment.org/rss", "geopolitics", "en", "tier_1b",
     ["geopolitics", "analysis"]),
    ("Atlantic Council", "https://www.atlanticcouncil.org/feed/", "geopolitics", "en", "tier_1b",
     ["geopolitics", "defence", "analysis"]),
    ("Foreign Policy", "https://foreignpolicy.com/feed/", "geopolitics", "en", "tier_2",
     ["geopolitics", "analysis"]),
    ("War on the Rocks", "https://warontherocks.com/feed/", "geopolitics", "en", "tier_2",
     ["geopolitics", "defence", "analysis"]),
    ("The Diplomat", "https://thediplomat.com/feed/", "geopolitics", "en", "tier_2",
     ["geopolitics", "asia", "analysis"]),
    ("EurasiaNet", "https://eurasianet.org/rss", "geopolitics", "en", "tier_2",
     ["geopolitics", "eurasia", "analysis"]),

    # ══════════════════════════════════════════════════════════════════════
    # FINANCIAL NEWS
    # ══════════════════════════════════════════════════════════════════════
    ("Financial Times Defence", "https://www.ft.com/companies/defence?format=rss", "finance", "en", "tier_1b",
     ["finance", "defence", "market_intel"]),
    ("Reuters Defence", "https://www.reuters.com/companies/aerospace-defense/rss", "finance", "en", "tier_1b",
     ["finance", "defence", "market_intel"]),
    ("Bloomberg Defence", "https://www.bloomberg.com/defence/rss", "finance", "en", "tier_1b",
     ["finance", "defence", "market_intel"]),

    # ══════════════════════════════════════════════════════════════════════
    # DEFENCE TECHNOLOGY & CYBER
    # ══════════════════════════════════════════════════════════════════════
    ("C4ISRNet", "https://www.c4isrnet.com/arc/outboundfeeds/rss/", "technology", "en", "tier_2",
     ["technology", "c4isr", "defence"]),
    ("Breaking Defense", "https://breakingdefense.com/feed/", "technology", "en", "tier_2",
     ["technology", "defence", "market_intel"]),
    ("The War Zone", "https://www.twz.com/rss", "technology", "en", "tier_2",
     ["technology", "defence", "military"]),
    ("UK Defence Journal Tech", "https://ukdefencejournal.org.uk/category/technology/feed/", "technology", "en", "tier_2",
     ["technology", "defence", "uk"]),

    # ══════════════════════════════════════════════════════════════════════
    # PRESS RELEASES
    # ══════════════════════════════════════════════════════════════════════
    ("PRNewswire Defence", "https://www.prnewswire.com/rss/defence-aerospace/", "press_releases", "en", "tier_2",
     ["press_release", "defence", "market_intel"]),
    ("BusinessWire Defence", "https://www.businesswire.com/portal/site/home/rss/defence", "press_releases", "en", "tier_2",
     ["press_release", "defence", "market_intel"]),
    ("GlobeNewswire Defence", "https://www.globenewswire.com/Rss/industry/defence", "press_releases", "en", "tier_2",
     ["press_release", "defence", "market_intel"]),

    # ══════════════════════════════════════════════════════════════════════
    # REGIONAL NEWS — LUSOPHONE AFRICA
    # ══════════════════════════════════════════════════════════════════════
    ("Angola Press (ANGOP)", "https://www.angop.ao/rss", "regional_news", "pt", "tier_2",
     ["lusophone", "angola", "africa"]),
    ("O País Angola", "https://opais.co.ao/feed/", "regional_news", "pt", "tier_2",
     ["lusophone", "angola", "news"]),
    ("Novo Jornal Angola", "https://novojornal.co.ao/feed/", "regional_news", "pt", "tier_2",
     ["lusophone", "angola", "news"]),
    ("Notícias Mozambique", "https://www.noticias.co.mz/rss", "regional_news", "pt", "tier_2",
     ["lusophone", "mozambique", "africa"]),
    ("O País Mozambique", "https://opais.co.mz/feed/", "regional_news", "pt", "tier_2",
     ["lusophone", "mozambique", "news"]),
    ("Carta de Moçambique", "https://cartamz.com/feed/", "regional_news", "pt", "tier_2",
     ["lusophone", "mozambique", "analysis"]),
    ("Folha de São Paulo", "https://feeds.folha.uol.com.br/", "regional_news", "pt", "tier_2",
     ["lusophone", "brazil", "news"]),
    ("O Globo Brazil", "https://oglobo.globo.com/rss", "regional_news", "pt", "tier_2",
     ["lusophone", "brazil", "news"]),
    ("DefesaNet Brazil", "https://www.defesanet.com.br/feed/", "regional_news", "pt", "tier_2",
     ["lusophone", "brazil", "defence"]),
    ("Tecnodefesa Brazil", "https://tecnodefesa.com.br/feed/", "regional_news", "pt", "tier_2",
     ["lusophone", "brazil", "defence", "technology"]),

    # ══════════════════════════════════════════════════════════════════════
    # REGIONAL NEWS — MIDDLE EAST & NORTH AFRICA
    # ══════════════════════════════════════════════════════════════════════
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "regional_news", "en", "tier_2",
     ["middle_east", "geopolitics", "news"]),
    ("Al Arabiya", "https://english.alarabiya.net/rss", "regional_news", "en", "tier_2",
     ["middle_east", "geopolitics", "news"]),
    ("The National UAE", "https://www.thenationalnews.com/rss", "regional_news", "en", "tier_2",
     ["middle_east", "uae", "news"]),
    ("Arab News", "https://www.arabnews.com/rss", "regional_news", "en", "tier_2",
     ["middle_east", "saudi", "news"]),
    ("Times of Israel", "https://www.timesofisrael.com/feed/", "regional_news", "en", "tier_2",
     ["middle_east", "israel", "news"]),
    ("Middle East Eye", "https://www.middleeasteye.net/rss", "regional_news", "en", "tier_2",
     ["middle_east", "geopolitics", "analysis"]),

    # ══════════════════════════════════════════════════════════════════════
    # REGIONAL NEWS — TURKEY
    # ══════════════════════════════════════════════════════════════════════
    ("Daily Sabah", "https://www.dailysabah.com/rss", "regional_news", "en", "tier_2",
     ["turkey", "news", "defence"]),
    ("Hurriyet Daily News", "https://www.hurriyetdailynews.com/rss", "regional_news", "en", "tier_2",
     ["turkey", "news", "geopolitics"]),
    ("Defence Turkey", "https://www.defenceturkey.com/feed/", "regional_news", "en", "tier_2",
     ["turkey", "defence", "market_intel"]),

    # ══════════════════════════════════════════════════════════════════════
    # REGIONAL NEWS — AFRICA (ENGLISH)
    # ══════════════════════════════════════════════════════════════════════
    ("AllAfrica", "https://allafrica.com/rss", "regional_news", "en", "tier_2",
     ["africa", "news", "geopolitics"]),
    ("Africa Intelligence", "https://www.africaintelligence.com/rss", "regional_news", "en", "tier_2",
     ["africa", "intelligence", "analysis"]),
    ("The East African", "https://www.theeastafrican.co.ke/rss", "regional_news", "en", "tier_2",
     ["africa", "east_africa", "news"]),
    ("Daily Maverick", "https://www.dailymaverick.co.za/rss", "regional_news", "en", "tier_2",
     ["africa", "south_africa", "analysis"]),

    # ══════════════════════════════════════════════════════════════════════
    # REGIONAL NEWS — LATIN AMERICA
    # ══════════════════════════════════════════════════════════════════════
    ("MercoPress", "https://en.mercopress.com/rss", "regional_news", "en", "tier_2",
     ["latin_america", "news", "geopolitics"]),
    ("Buenos Aires Times", "https://www.batimes.com.ar/rss", "regional_news", "en", "tier_2",
     ["latin_america", "argentina", "news"]),
    ("America Economia", "https://www.americaeconomia.com/rss", "regional_news", "es", "tier_2",
     ["latin_america", "business", "news"]),

    # ══════════════════════════════════════════════════════════════════════
    # REGIONAL NEWS — ASIA
    # ══════════════════════════════════════════════════════════════════════
    ("Nikkei Asia", "https://asia.nikkei.com/rss", "regional_news", "en", "tier_2",
     ["asia", "business", "geopolitics"]),
    ("South China Morning Post", "https://www.scmp.com/rss", "regional_news", "en", "tier_2",
     ["asia", "china", "news"]),
    ("The Hindu", "https://www.thehindu.com/rss", "regional_news", "en", "tier_2",
     ["asia", "india", "news"]),
    ("Janes India", "https://www.janes.com/rss/india", "defence_regional", "en", "tier_1b",
     ["defence", "india", "geopolitics"]),
]

# ── Feed parsing ──────────────────────────────────────────────────────────────


async def _parse_rss(xml_text: str, source_name: str) -> list[dict]:
    """Parse RSS 2.0 XML into article dicts."""
    articles = []
    try:
        # R-F2108: offload XML parsing to a thread — multi-MB RSS feeds can take
        # 100-500ms to parse with ET.fromstring on the event loop.
        root = await asyncio.to_thread(ET.fromstring, xml_text)
    except ET.ParseError as e:
        logger.warning("[news_monitor] RSS parse failed for %s: %s", source_name, e)
        return []

    # RSS 2.0: /rss/channel/item
    for item in root.iter("item"):
        try:
            title = _get_text(item, "title")
            link = _get_text(item, "link")
            description = _get_text(item, "description")
            pub_date = _get_text(item, "pubDate")
            guid = _get_text(item, "guid") or link

            if not title or not link:
                continue

            articles.append({
                "title": title.strip(),
                "url": link.strip(),
                "summary": (description or "")[:500],
                "published": pub_date or "",
                "guid": guid,
                "source": source_name,
            })
        except Exception as e:
            logger.debug("[news_monitor] RSS item parse error: %s", e)
            continue

    return articles


def _parse_atom(xml_text: str, source_name: str) -> list[dict]:
    """Parse Atom XML into article dicts."""
    articles = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("[news_monitor] Atom parse failed for %s: %s", source_name, e)
        return []

    # Atom: /feed/entry
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
        try:
            title = _get_text(entry, "title", ns)
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            link = link_el.get("href") if link_el is not None else None
            summary = _get_text(entry, "summary", ns) or _get_text(entry, "content", ns)
            published = _get_text(entry, "published", ns) or _get_text(entry, "updated", ns)
            guid = _get_text(entry, "id", ns) or link

            if not title or not link:
                continue

            articles.append({
                "title": title.strip(),
                "url": link.strip(),
                "summary": (summary or "")[:500],
                "published": published or "",
                "guid": guid,
                "source": source_name,
            })
        except Exception as e:
            logger.debug("[news_monitor] Atom entry parse error: %s", e)
            continue

    return articles


def _get_text(element: ET.Element, tag: str, ns: Optional[dict] = None) -> str:
    """Get text content of a child element."""
    if ns:
        child = element.find(f"{{{ns.get('atom', '')}}}{tag}")
    else:
        child = element.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _detect_feed_type(xml_text: str) -> str:
    """Detect if XML is RSS 2.0 or Atom."""
    if "<rss" in xml_text[:200]:
        return "rss"
    if "<feed" in xml_text[:200]:
        return "atom"
    return "unknown"


# ── Article processing ────────────────────────────────────────────────────────


def _article_hash(url: str) -> str:
    """Stable hash for dedup."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


async def _is_seen(url: str) -> bool:
    """Check if URL has been processed before."""
    h = _article_hash(url)
    # R-F1068: rs.sismember doesn't exist — use get_json on a JSON key
    seen_data = await rs.get_json(_SEEN_URLS_KEY)
    if isinstance(seen_data, dict):
        return h in seen_data
    return False


async def _mark_seen(url: str) -> None:
    """Mark URL as processed."""
    h = _article_hash(url)
    # R-F1068: rs.sadd doesn't exist — use get_json/set_json on a dict key
    seen_data = await rs.get_json(_SEEN_URLS_KEY)
    if not isinstance(seen_data, dict):
        seen_data = {}
    seen_data[h] = time.time()
    # Trim to max size
    if len(seen_data) > _MAX_SEEN_URLS:
        # Remove oldest entries
        sorted_items = sorted(seen_data.items(), key=lambda x: x[1])
        seen_data = dict(sorted_items[-_MAX_SEEN_URLS:])
    await rs.set_json(_SEEN_URLS_KEY, seen_data)


async def _store_article(article: dict) -> None:
    """Store article in Redis list for dashboard display."""
    await rs.lpush(_ARTICLES_KEY, json.dumps(article, default=str))
    await rs.ltrim(_ARTICLES_KEY, 0, _MAX_ARTICLES - 1)


async def _feed_to_brain(article: dict) -> None:
    """Feed article to ARIA's brain for analysis.

    R-F2001: also feeds into intel_ledger so signal_correlator can
    correlate news articles with other signals by country. Best-effort
    and non-fatal — if the ledger feed fails, the article is still stored
    and the brain signal is still fired. Only articles with extractable
    country mentions produce ledger signals (honest by construction).
    """
    try:
        wire_success(
            module="news_monitor",
            summary=f"News: {article['title'][:200]}",
            detail=f"Source: {article['source']}. {article['summary'][:400]}",
            entity_name=article["source"],
            source_id=f"news_monitor:{_article_hash(article['url'])}",
        )
    except Exception as e:
        logger.debug("[news_monitor] brain feed failed: %s", e)

    # R-F2001: feed into intel_ledger so signal_correlator sees news
    try:
        from . import intel_ledger as _il
        _summary = f"{article['title']}"
        _desc = article.get("summary", "")[:300]
        if _desc:
            _summary = f"{_summary} — {_desc}"
        await _il.add_signal({
            "summary": _summary[:500],
            "source": f"news_monitor:{article.get('source', 'unknown')}",
            "type": "news",
            "url": article.get("url", ""),
            "tags": [
                article.get("category", ""),
                str(article.get("topics", "")),
            ],
            "timestamp": article.get("detected_at", ""),
        })
    except Exception:
        logger.debug("[news_monitor] intel_ledger feed failed", exc_info=True)

    # R-F2190: VAULT sources feed CONTENT into the brain (RAG/knowledge), not just the
    # correlator ledger — so a manually-added website (vault.html "Add Site") becomes
    # searchable by chat + intelligence grounding. Scoped to vault-curated articles
    # (source "vault:…" / category "vault_curated") so the entire global news firehose
    # is NOT absorbed (cost + signal-to-noise). This closes Pipeline 2 of the vault
    # business review: add site → aria intel.
    try:
        _src = str(article.get("source", "") or "")
        if article.get("category") == "vault_curated" or _src.startswith("vault:"):
            from . import brain_hook as _bh
            _name = _src[6:] if _src.startswith("vault:") else _src
            await _bh.absorb(
                module="news_monitor",
                summary=str(article.get("title", ""))[:200],
                detail=str(article.get("summary", ""))[:1000],
                entity_name=_name[:120],
                source_id=f"vault_source:{_article_hash(article.get('url', ''))}",
                confidence="PROBABLE",
                extra_topics=["vault_source"],
            )
    except Exception:
        logger.debug("[news_monitor] vault-source brain absorb failed", exc_info=True)


# ── Feed fetching ─────────────────────────────────────────────────────────────


async def _fetch_feed(url: str, source_name: str) -> Optional[str]:
    """Fetch a feed URL with retries."""
    client = _get_client()
    last_error = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = await client.get(url)  # no-ssrf-check: feed URLs are curated NEWS_SOURCES or vault URLs pre-validated via security.validate_url() in _get_vault_feed_sources (R-F2046)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug("[news_monitor] Feed not found: %s (%s)", source_name, url)
                return None
            last_error = e
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(1 * (attempt + 1))
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_error = e
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(2 * (attempt + 1))
        except Exception as e:
            last_error = e
            break

    if last_error:
        logger.debug("[news_monitor] Failed to fetch %s (%s): %s", source_name, url, last_error)
    return None


# ── Main polling function ─────────────────────────────────────────────────────


@fail_wire(module="news_monitor", gap_type="source_failure")
def _get_vault_feed_sources() -> list[tuple]:
    """R-F2046 — admin-curated vault sites of FEED type, shaped as NEWS_SOURCES
    tuples so poll_feeds ingests them through the exact same fetch→parse→ledger
    path (zero duplicated logic). The Agent Signup Vault is the controlled
    data-point catalogue (admin/dev add via vault.html, R-F2048); a site added
    there with site_type rss/website becomes a LIVE ingestion feed that flows
    into the dashboard via the existing correlate_signals chain. portal/api
    types (need credentials) are skipped. Synchronous (small sqlite read) — same
    cost class as the XML parsing already done inline in poll_feeds.
    """
    try:
        from .agent_signup_vault import get_vault
        entries = get_vault().list(limit=500) or []
    except Exception as e:
        logger.debug("[news_monitor] vault feed-source read failed: %s", e)
        return []
    out: list[tuple] = []
    for e in entries:
        try:
            st = (e.get("site_type") or "").lower()
            status = (e.get("status") or "").lower()
            url = e.get("site_url") or ""
            if st not in ("rss", "website"):
                continue                      # portals/api need creds — not feeds
            if status in ("failed", "expired", "cancelled"):
                continue
            if not url.startswith(("http://", "https://")):
                continue
            # R-F2046 — SSRF guard at entry. Vault URLs are admin-added
            # (semi-untrusted), unlike the curated NEWS_SOURCES, so validate each
            # against the SSRF blocklist (internal/private IPs, bad schemes)
            # BEFORE it can ever reach the feed fetcher.
            from . import security as _sec
            _ok, _why = _sec.validate_url(url)
            if not _ok:
                logger.warning("[news_monitor] vault source rejected (unsafe URL %s): %s", url[:80], _why)
                continue
            name = e.get("site_name") or e.get("site_id") or url
            # 6-field NEWS_SOURCES shape: (name, url, category, lang, tier, topics)
            out.append((f"vault:{name}", url, "vault_curated", "en", "tier_2", ["custom"]))
        except Exception:
            continue
    return out


async def poll_feeds(
    categories: Optional[list[str]] = None,
    max_articles_per_feed: int = 10,
) -> dict:
    """Poll all configured news feeds.

    Args:
        categories: If provided, only poll feeds in these categories.
        max_articles_per_feed: Max articles to process per feed per poll.

    Returns:
        dict with counts of fetched, new, and failed feeds.
    """
    sources = list(NEWS_SOURCES)
    if categories:
        sources = [s for s in sources if s[2] in categories]
    else:
        # R-F2046 — also poll admin-curated vault feed sites (no category filter
        # applies to them; they ride the same loop below).
        sources = sources + _get_vault_feed_sources()

    total_fetched = 0
    total_new = 0
    total_failed = 0
    feed_results = []

    for name, url, category, lang, tier, topics in sources:
        try:
            xml_text = await _fetch_feed(url, name)
            if not xml_text:
                total_failed += 1
                feed_results.append({"name": name, "status": "failed", "articles": 0})
                continue

            feed_type = _detect_feed_type(xml_text)
            if feed_type == "rss":
                articles = await _parse_rss(xml_text, name)
            elif feed_type == "atom":
                articles = _parse_atom(xml_text, name)
            else:
                logger.debug("[news_monitor] Unknown feed type for %s", name)
                total_failed += 1
                feed_results.append({"name": name, "status": "unknown_format", "articles": 0})
                continue

            total_fetched += len(articles)
            new_count = 0
            for article in articles[:max_articles_per_feed]:
                if await _is_seen(article["url"]):
                    continue
                article["category"] = category
                article["language"] = lang
                article["tier"] = tier
                article["topics"] = topics
                article["detected_at"] = datetime.now(timezone.utc).isoformat()
                await _mark_seen(article["url"])
                await _store_article(article)
                await _feed_to_brain(article)
                new_count += 1

            total_new += new_count
            feed_results.append({"name": name, "status": "ok", "articles": len(articles), "new": new_count})

            # Rate-limit: don't hammer all feeds at once
            await asyncio.sleep(0.5)

        except Exception as e:
            logger.warning("[news_monitor] Feed poll failed for %s: %s", name, e)
            total_failed += 1
            feed_results.append({"name": name, "status": "error", "error": str(e)[:100]})
            # R-F1057 — wire failure to brain so ARIA sees it
            try:
                wire_failure(
                    module="news_monitor",
                    summary=f"Feed poll failed: {name}",
                    detail=str(e)[:300],
                    source_id=f"news_monitor:feed:{name}",
                )
            except Exception:
                pass

    summary = {
        "feeds_polled": len(sources),
        "feeds_failed": total_failed,
        "articles_fetched": total_fetched,
        "articles_new": total_new,
        "results": feed_results,
        "polled_at": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(
        "[news_monitor] Polled %d feeds: %d articles fetched, %d new, %d failed",
        len(sources), total_fetched, total_new, total_failed,
    )

    # R-F2009c: wire success so the brain knows the monitor is alive even
    # when no new articles are found. Pre-fix the brain signal only fired
    # on new articles or failures, so a clean cycle with 0 new articles
    # left the module appearing stale (190h) while it was actually running.
    try:
        wire_success(
            module="news_monitor",
            summary=f"News poll: {total_new} new / {total_fetched} fetched / {total_failed} failed feeds",
            source_id=f"news_monitor:poll:{summary['polled_at']}",
        )
    except Exception:
        pass

    return summary


# ── Dashboard data ────────────────────────────────────────────────────────────


@fail_wire(module="news_monitor", gap_type="source_failure")
async def get_recent_articles(limit: int = 50) -> list[dict]:
    """Get most recent articles for dashboard display."""
    raw = await rs.lrange(_ARTICLES_KEY, 0, limit - 1)
    articles = []
    for r in raw:
        try:
            articles.append(json.loads(r) if isinstance(r, str) else r)
        except Exception:
            continue
    return articles


@fail_wire(module="news_monitor", gap_type="source_failure")
async def get_stats() -> dict:
    """Get news monitor statistics (cached 30s to avoid cold-start flaps)."""
    global _stats_cache, _stats_cache_ts
    now = time.time()
    if _stats_cache and (now - _stats_cache_ts) < _STATS_CACHE_TTL:
        return dict(_stats_cache)

    articles = await get_recent_articles(1000)
    by_category: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for a in articles:
        cat = a.get("category", "unknown")
        by_category[cat] = by_category.get(cat, 0) + 1
        src = a.get("source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1

    result = {
        "total_sources": len(NEWS_SOURCES),
        "recent_articles": len(articles),
        "by_category": by_category,
        "top_sources": dict(sorted(by_source.items(), key=lambda x: x[1], reverse=True)[:20]),
        "categories": sorted(set(s[2] for s in NEWS_SOURCES)),
    }
    _stats_cache = result
    _stats_cache_ts = now
    return result
