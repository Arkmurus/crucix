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
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from defusedxml import ElementTree as ET
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
_INTEL_SIGNALS_KEY = "crucix:news_monitor:intel_signals"
_POLL_STATE_KEY = "crucix:news_monitor:poll_state"
_MAX_ARTICLES = 1000
_MAX_INTEL_SIGNALS = 500
_MAX_SEEN_URLS = 50000
_GOLDEN_POLL_STALE_S = int(os.getenv("ARIA_GOLDEN_POLL_STALE_S", "5400"))
_GOLDEN_SIGNAL_STALE_S = int(os.getenv("ARIA_GOLDEN_SIGNAL_STALE_S", str(72 * 3600)))

# ── R-F2630: internal poll budget so the TAIL always runs ─────────────────────
# poll_feeds is sequential over ~76 feeds at _TIMEOUT_S each, and its two most
# valuable operations are LAST: _write_poll_state (the freshness heartbeat) and
# the golden_intel_bridge promotion pass (which sets distribution_ready). Its
# caller caps it (main.py:519 `wait_for(..., timeout=330)`), so once enough feeds
# rotted (42/76 failing => ~630s of timeouts alone) the cap ALWAYS killed the
# tail: last_poll_at froze -> "poll_stale" forever, and the bridge never ran ->
# the promoted-signal store stayed empty -> the API backfilled signals that carry
# no distribution_ready -> the dashboard's "Distribution Ready" column read 0
# permanently. Self-reinforcing: duration scales with FAILURES.
# Raising the cap is the §1 band-aid (it fails again at the next rot). Instead
# budget the LOOP to (budget - reserve) and always leave the tail its reserve —
# the R-F1879 pattern already proven in dd_orchestrator.
_POLL_BUDGET_S = float(os.getenv("ARIA_NEWS_POLL_BUDGET_S", "300"))
_POLL_TAIL_RESERVE_S = float(os.getenv("ARIA_NEWS_POLL_TAIL_RESERVE_S", "45"))
# Truncation without rotation would starve every source after the cut-off: with
# 15s-per-failure the loop only reaches ~17 of 76 feeds, so feeds 18..76 would
# NEVER be polled again. Rotate the start offset per poll so coverage is fair
# over time, and persist it in poll state.
_POLL_ROTATION_KEY = "rotation_offset"

# ── HTTP client ───────────────────────────────────────────────────────────────
_TIMEOUT_S = 15
_MAX_RETRIES = 2

_http_client: Optional[httpx.AsyncClient] = None

_SIGNAL_RULES: list[tuple[str, list[str], str, str]] = [
    (
        "active_tender",
        ["tender", "rfp", "rfq", "procurement", "bid", "solicitation"],
        "Procurement activity may create a near-term commercial window.",
        "Qualify opportunity",
    ),
    (
        "contract_award",
        ["contract award", "awarded", "signed a contract", "order for", "deal with"],
        "A contract award changes competitor position, customer budget, or follow-on demand.",
        "Review competitor impact",
    ),
    (
        "sanctions_change",
        [
            "sanction", "embargo", "blacklist", "designated",
            "asset freeze", "export control",
        ],
        "Compliance status may have changed and should be checked before engagement.",
        "Run compliance review",
    ),
    (
        "conflict_escalation",
        [
            "attack", "conflict", "insurgent", "military operation",
            "offensive", "strike",
        ],
        "Security conditions may affect delivery risk, end-use risk, or market timing.",
        "Assess country risk",
    ),
    (
        "budget_movement",
        [
            "budget", "spending", "appropriation", "allocation",
            "funding", "defence expenditure",
        ],
        "Budget movement can signal upcoming procurement or programme acceleration.",
        "Monitor procurement path",
    ),
    (
        "political_transition",
        ["minister", "cabinet", "election", "appointed", "reshuffle", "inaugurated"],
        "Decision-maker change can reset priorities, approvals, and relationship strategy.",
        "Refresh stakeholder map",
    ),
    (
        "competitor_activity",
        [
            "baykar", "aselsan", "elbit", "norinco", "catic",
            "rosoboronexport", "rheinmetall", "leonardo",
        ],
        "Competitor movement can affect positioning, urgency, and pricing strategy.",
        "Review competitive posture",
    ),
    (
        "programme_signal",
        [
            "programme", "program", "delivery", "modernisation",
            "upgrade", "fleet", "capability",
        ],
        "Programme movement can indicate future sustainment, replacement, or partner demand.",
        "Track programme",
    ),
]

_SOURCE_TIER_POINTS = {
    "tier_1a": 40,
    "tier_1b": 34,
    "tier_2": 28,
    "tier_3": 18,
    "tier_4": 10,
    "tier_5": 0,
}


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
    # R-F2247 — PRIMARY-SOURCE + DIVERSITY feeds (source-diversity review):
    # broaden beyond the Janes-heavy (×9) secondary firehose with an OFFICIAL
    # primary source (US DoD daily contract awards) + new-region press the
    # catalogue under-covered (Eastern Europe/Balkans; UN OCHA conflict, strong
    # Africa/MENA). All free/native RSS (§6), each verified live returning items
    # with the news_monitor UA. New domains — no collusion with existing feeds.
    # ══════════════════════════════════════════════════════════════════════
    # R-F2634: re-tiered tier_1b -> tier_1a. This is an OFFICIAL US DoD primary source
    # (it supplied the "Centcom Completes Another Wave of Strikes Against Iran" signal
    # that corroborated two outlets) — it was mis-tiered below its real authority.
    ("US DoD Daily Contracts", "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=20", "defence_global", "en", "tier_1a",
     ["defence", "procurement", "contracts", "official"]),
    ("ReliefWeb (UN OCHA)", "https://reliefweb.int/updates/rss.xml", "defence_regional", "en", "tier_1b",
     ["conflict", "humanitarian", "africa", "middle_east"]),
    ("Balkan Insight", "https://balkaninsight.com/feed/", "defence_regional", "en", "tier_2",
     ["geopolitics", "eastern_europe", "balkans", "corruption"]),

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

    # ══════════════════════════════════════════════════════════════════════
    # R-F2634 (2026-07-15) — GRADE-A SOURCE REBUILD.
    #
    # A live probe of all 76 configured feeds (same 15s contract poll_feeds uses)
    # found 46 DEAD (61%): 404 x21 · 403 x9 · NOT_XML x9 · DNS_DEAD x2 · TIMEOUT x2.
    # Critically the dead ones were the AUTHORITATIVE layer — ~18 of the 20 tier_1b:
    # Janes x9 (404), RUSI (404), CSIS (404), FT (404), Reuters (401),
    # IISS/Chatham House/Atlantic Council/Bloomberg (403), Carnegie (NOT_XML).
    #
    # WHY they died is the lesson: Janes/FT/Bloomberg/Reuters are PAYWALLED products
    # that killed free RSS on purpose; the think tanks are Cloudflare-blocked. Chasing
    # them is fighting the vendor. Decision-grade intel wants OFFICIAL PRIMARY sources:
    # free, stable, and tier_1a — HIGHER authority than the tier_1b we lost.
    #
    # USP: corroboration needs MULTIPLE INDEPENDENT LIVE sources on one event
    # (verified_intel.SourceIndependenceChecker). A tier_2-only pool can never reach
    # decision-grade. These feeds are the corroboration FUEL.
    #
    # EVERY entry below was PROBED 2026-07-15 and returned 200 + real XML + >0 items.
    # 20 of 34 candidates were REJECTED and deliberately omitted (NATO, SIPRI, EDA,
    # OFAC, State Dept, RUSI-alt, CSIS-alt, Janes-alt, EU Council, Lawfare, ...).
    # NOTHING unverified goes in this list.
    # ══════════════════════════════════════════════════════════════════════

    # ── tier_1a — OFFICIAL / PRIMARY (governments + IGOs). ARIA had ZERO of these. ──
    # NOTE: "US DoD Releases" was NOT added here — it resolves to the SAME URL as the
    # existing "US DoD Daily Contracts" above. Two names on one URL would count the
    # same article twice => evidence_count=2 => FALSE corroboration, the exact
    # never-false-clean failure this work exists to prevent. The existing entry is
    # re-tiered to tier_1a instead (it IS an official primary source, just mis-tiered).
    ("US DoD News", "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=800&Site=945&max=20",
     "defence_global", "en", "tier_1a", ["defence", "official", "primary"]),
    ("US Army News", "https://www.army.mil/rss/static/1.xml",
     "defence_global", "en", "tier_1a", ["defence", "official", "primary", "land"]),
    ("UK MOD Announcements", "https://www.gov.uk/government/organisations/ministry-of-defence.atom",
     "defence_global", "en", "tier_1a", ["defence", "official", "primary", "uk"]),
    ("UN News Peace and Security", "https://news.un.org/feed/subscribe/en/news/topic/peace-and-security/feed/rss.xml",
     "geopolitics", "en", "tier_1a", ["geopolitics", "official", "primary", "conflict"]),
    ("UN News", "https://news.un.org/feed/subscribe/en/news/all/rss.xml",
     "geopolitics", "en", "tier_1a", ["geopolitics", "official", "primary"]),

    # ── tier_1b — specialist outlets that still publish free RSS (replace the dead) ──
    ("Defense News", "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml",
     "defence_global", "en", "tier_1b", ["defence", "procurement", "industry"]),
    # NOT added (already registered above and healthy — a second entry on the same URL
    # would count one article twice => FALSE corroboration):
    #   Breaking Defense, War on the Rocks, Defence Blog.
    # They probed OK precisely BECAUSE they already work. Caught by
    # test_rf2634_no_duplicate_feed_urls during this change.
    ("The War Zone", "https://www.twz.com/feed",
     "defence_global", "en", "tier_1b", ["defence", "capability", "analysis"]),
    ("DefenseScoop", "https://defensescoop.com/feed/",
     "technology", "en", "tier_1b", ["defence", "technology", "procurement"]),
    ("Bellingcat", "https://www.bellingcat.com/feed/",
     "geopolitics", "en", "tier_1b", ["osint", "investigation", "conflict"]),
    ("Crisis Group", "https://www.crisisgroup.org/rss.xml",
     "geopolitics", "en", "tier_1b", ["geopolitics", "conflict", "analysis"]),
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


def _article_text(article: dict) -> str:
    return " ".join(
        str(article.get(k, "") or "") for k in ("title", "summary", "full_text")
    ).strip()


def _parse_epoch(value: Any) -> float | None:
    """Best-effort timestamp parser for freshness calculations."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip()
        if not text:
            return None
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _age_seconds(value: Any, *, now: float | None = None) -> int | None:
    epoch = _parse_epoch(value)
    if epoch is None:
        return None
    ref = time.time() if now is None else now
    return max(0, int(ref - epoch))


def _extract_article_entities(text: str) -> dict:
    try:
        from . import intel_ledger as _il
        return _il._extract_entities(text)  # noqa: SLF001 - shared ledger rules.
    except Exception:
        return {"countries": [], "products": [], "oems": []}


def _classify_article_signal(
    text: str,
    category: str,
    topics: list | str,
) -> tuple[str, str, str]:
    low = text.lower()
    for signal_type, needles, why, action in _SIGNAL_RULES:
        if any(n in low for n in needles):
            return signal_type, why, action
    joined_topics = " ".join(topics) if isinstance(topics, list) else str(topics or "")
    topic_low = f"{category} {joined_topics}".lower()
    if any(n in topic_low for n in ("defence", "procurement", "market_intel", "security")):
        return (
            "market_watch",
            "Defence or security coverage may affect market timing, risk, or positioning.",
            "Monitor signal",
        )
    return (
        "situational_awareness",
        "Contextual reporting retained as source evidence, but no immediate action is implied.",
        "Review if relevant",
    )


def _signal_priority(signal_type: str, entities: dict, tier: str) -> str:
    high_types = {
        "active_tender",
        "contract_award",
        "sanctions_change",
        "conflict_escalation",
    }
    if signal_type in high_types and (entities.get("countries") or entities.get("oems")):
        return "HIGH"
    if tier in ("tier_1a", "tier_1b") and signal_type in high_types | {"budget_movement"}:
        return "HIGH"
    if signal_type == "situational_awareness":
        return "LOW"
    return "MEDIUM"


def _confidence(score: int) -> str:
    if score >= 72:
        return "HIGH"
    if score >= 50:
        return "MEDIUM"
    return "LOW"


def _action_horizon(signal_type: str, priority: str) -> str:
    if signal_type in {"sanctions_change", "conflict_escalation"} or priority == "HIGH":
        return "0-72h"
    if signal_type in {"active_tender", "contract_award", "budget_movement"}:
        return "3-14d"
    return "monitor"


def _quality_label(priority: str, confidence: str, evidence_count: int) -> str:
    if priority == "HIGH" and confidence == "HIGH" and evidence_count >= 2:
        return "decision-grade corroborated"
    if priority == "HIGH" and confidence in {"HIGH", "MEDIUM"}:
        return "decision-grade single-source"
    if confidence == "HIGH":
        return "watch-grade"
    return "context"


def _confidence_rationale(
    *,
    source_tier: str,
    signal_type: str,
    entities: dict,
    evidence_count: int,
) -> str:
    parts = []
    if source_tier in {"tier_1a", "tier_1b"}:
        parts.append("high-trust source tier")
    elif source_tier in {"tier_2", "tier_3"}:
        parts.append("curated source tier")
    else:
        parts.append("unverified source tier")
    if signal_type not in {"situational_awareness", "market_watch"}:
        parts.append(f"actionable {signal_type.replace('_', ' ')} pattern")
    if entities.get("countries") or entities.get("oems") or entities.get("products"):
        parts.append("named entity extracted")
    parts.append("corroborated" if evidence_count >= 2 else "single-source")
    return "; ".join(parts)


def _build_intel_signal(article: dict) -> dict:
    """Promote one raw article into a decision-grade signal for the web UI."""
    text = _article_text(article)
    title = str(article.get("title", "") or "Untitled").strip()
    source = str(article.get("source", "") or "unknown").strip()
    tier = str(article.get("tier", "") or "").strip().lower()
    category = str(article.get("category", "") or "unknown").strip()
    topics = article.get("topics", []) or []
    signal_type, why, action = _classify_article_signal(text, category, topics)
    entities = _extract_article_entities(text)
    entity_hits = (
        len(entities.get("countries") or [])
        + len(entities.get("products") or [])
        + len(entities.get("oems") or [])
    )
    tier_score = _SOURCE_TIER_POINTS.get(tier, 12)
    action_score = 28 if signal_type not in ("situational_awareness", "market_watch") else 12
    entity_score = min(18, entity_hits * 6)
    summary_score = 8 if len(text) >= 180 else 3
    score = max(0, min(100, tier_score + action_score + entity_score + summary_score))
    priority = _signal_priority(signal_type, entities, tier)
    confidence = _confidence(score)
    evidence_count = int(article.get("evidence_count") or article.get("source_count") or 1)
    target = (
        (entities.get("oems") or [None])[0]
        or (entities.get("countries") or [None])[0]
        or source
    )
    action_horizon = _action_horizon(signal_type, priority)
    quality_label = _quality_label(priority, confidence, evidence_count)
    return {
        "id": _article_hash(f"{article.get('url', '')}|{signal_type}|{title}"),
        "signal_type": signal_type,
        "priority": priority,
        "confidence": confidence,
        "score": score,
        "quality_label": quality_label,
        "confidence_rationale": _confidence_rationale(
            source_tier=tier,
            signal_type=signal_type,
            entities=entities,
            evidence_count=evidence_count,
        ),
        "evidence_count": evidence_count,
        "corroboration": "corroborated" if evidence_count >= 2 else "single-source",
        "action_horizon": action_horizon,
        "urgency": "immediate" if action_horizon == "0-72h" else "near-term",
        "title": title[:220],
        "decision_summary": title[:220],
        "why_it_matters": why,
        "recommended_action": action,
        "target": target,
        "source": source,
        "source_tier": tier or "unclassified",
        "category": category,
        "language": article.get("language", "en"),
        "url": article.get("url", ""),
        "published": article.get("published", ""),
        "detected_at": article.get("detected_at") or datetime.now(timezone.utc).isoformat(),
        "entities": entities,
        "evidence": {
            "source": source,
            "source_tier": tier or "unclassified",
            "url": article.get("url", ""),
            "count": evidence_count,
            "corroboration": "corroborated" if evidence_count >= 2 else "single-source",
        },
    }


def _normalise_intel_signal(signal: dict) -> dict:
    """Backfill decision metadata for persisted pre-R-F2392 signals."""
    sig = dict(signal)
    signal_type = str(sig.get("signal_type") or "situational_awareness")
    priority = str(sig.get("priority") or "LOW").upper()
    confidence = str(sig.get("confidence") or "LOW").upper()
    evidence = sig.get("evidence") if isinstance(sig.get("evidence"), dict) else {}
    try:
        evidence_count = int(
            sig.get("evidence_count")
            or evidence.get("count")
            or sig.get("source_count")
            or 1
        )
    except (TypeError, ValueError):
        evidence_count = 1
    evidence_count = max(1, evidence_count)
    corroboration = "corroborated" if evidence_count >= 2 else "single-source"
    source_tier = str(
        sig.get("source_tier")
        or evidence.get("source_tier")
        or "unclassified"
    ).lower()
    entities = sig.get("entities") if isinstance(sig.get("entities"), dict) else {}

    sig["priority"] = priority
    sig["confidence"] = confidence
    sig.setdefault("quality_label", _quality_label(priority, confidence, evidence_count))
    sig.setdefault(
        "confidence_rationale",
        _confidence_rationale(
            source_tier=source_tier,
            signal_type=signal_type,
            entities=entities,
            evidence_count=evidence_count,
        ),
    )
    sig.setdefault("evidence_count", evidence_count)
    sig.setdefault("corroboration", corroboration)
    action_horizon = sig.get("action_horizon") or _action_horizon(signal_type, priority)
    sig["action_horizon"] = action_horizon
    sig.setdefault("urgency", "immediate" if action_horizon == "0-72h" else "near-term")

    normalised_evidence = dict(evidence)
    normalised_evidence.setdefault("source", sig.get("source") or "unknown")
    normalised_evidence.setdefault("source_tier", source_tier or "unclassified")
    normalised_evidence.setdefault("url", sig.get("url") or "")
    normalised_evidence.setdefault("count", evidence_count)
    normalised_evidence.setdefault("corroboration", corroboration)
    sig["evidence"] = normalised_evidence
    return sig


async def _store_intel_signal(signal: dict) -> None:
    await rs.lpush(_INTEL_SIGNALS_KEY, json.dumps(signal, default=str))
    await rs.ltrim(_INTEL_SIGNALS_KEY, 0, _MAX_INTEL_SIGNALS - 1)


async def _persist_backfilled_intel_signals(signals: list[dict]) -> None:
    """Persist backfilled Golden Intel without blocking the dashboard request."""
    for signal in signals:
        try:
            await _store_intel_signal(signal)
        except Exception:
            logger.debug("[news_monitor] intel signal backfill persist failed", exc_info=True)
            return


async def _read_poll_state() -> dict:
    try:
        state = await rs.get_json(_POLL_STATE_KEY)
        return state if isinstance(state, dict) else {}
    except Exception:
        logger.debug("[news_monitor] poll state read failed", exc_info=True)
        return {}


async def _write_poll_state(summary: dict) -> dict:
    """Persist the last news-monitor heartbeat used by Golden Intel freshness.

    R-F2636 — only a FULL poll may refresh the feed's health + freshness.
    `routes/aria.py:26749` calls poll_feeds(categories=...) — a SUBSET poll — and its
    summary used to overwrite this state wholesale. Observed live 2026-07-15:
        full 76-feed poll : feeds_polled=76 feeds_failed=42 ratio=0.55 -> source_failure_degraded
        then a 3-feed poll: feeds_polled=3  feeds_failed=0  ratio=0.00 -> reasons=[] "fresh"
    Three clean feeds ERASED the fact that 42 of 76 sources are dead, and the dashboard
    reported a healthy feed while 73 sources went unpolled — a false-clean of the
    observability surface (same class as R-F2621 / R-F2622 / R-F2625).

    A scoped poll is a TARGETED operation, not a feed refresh: it records itself under
    `last_filtered_poll_at` and touches nothing else. `scope` defaults to "full" so
    existing callers (the boot loop, the autonomous task) are unchanged.
    """
    existing = await _read_poll_state()
    polled_at = str(summary.get("polled_at") or datetime.now(timezone.utc).isoformat())

    if str(summary.get("scope") or "full") != "full":
        # Subset poll: record that it happened, preserve the full-poll truth.
        state = {
            **existing,
            "last_filtered_poll_at": polled_at,
            "last_filtered_feeds_polled": int(summary.get("feeds_polled") or 0),
            "last_filtered_feeds_failed": int(summary.get("feeds_failed") or 0),
        }
        try:
            await rs.set_json(_POLL_STATE_KEY, state)
        except Exception:
            logger.debug("[news_monitor] scoped poll state write failed", exc_info=True)
        return state
    failed = int(summary.get("feeds_failed") or 0)
    total = int(summary.get("feeds_polled") or 0)
    status = "ok"
    if total > 0 and failed >= total:
        status = "failed"
    elif failed > 0:
        status = "degraded"
    state = {
        **existing,
        "status": status,
        "last_poll_at": polled_at,
        "last_success_at": polled_at if status in {"ok", "degraded"} else existing.get("last_success_at"),
        "last_error_at": polled_at if status == "failed" else existing.get("last_error_at"),
        "feeds_polled": total,
        "feeds_failed": failed,
        "articles_fetched": int(summary.get("articles_fetched") or 0),
        "articles_new": int(summary.get("articles_new") or 0),
        "signals_promoted": int(summary.get("signals_promoted") or 0),
        # R-F2630 — persist the per-feed results. The freshness reader builds
        # `failed_feeds` from poll_state["results"] (see :1449), but this writer
        # dropped it, so failed_feeds was ALWAYS [] even with 42 feeds failing —
        # the operator could see THAT sources were dying but never WHICH.
        # Capped: the reader only reads [:100].
        "results": list(summary.get("results") or [])[:120],
        # R-F2630 — a time-boxed poll must say so (R-F1572 honesty), and must
        # carry the rotation offset so the next poll starts where this one
        # stopped instead of re-polling the same head of the list forever.
        "truncated": bool(summary.get("truncated")),
        "feeds_skipped": int(summary.get("feeds_skipped") or 0),
        _POLL_ROTATION_KEY: int(summary.get(_POLL_ROTATION_KEY) or 0),
    }
    try:
        await rs.set_json(_POLL_STATE_KEY, state)
    except Exception:
        logger.debug("[news_monitor] poll state write failed", exc_info=True)
    return state


async def _backfill_intel_signals_from_articles(limit: int) -> list[dict]:
    """Derive Golden Intel from existing raw articles when promotion storage is empty."""
    scan_limit = max(limit * 5, min(_MAX_ARTICLES, 100))
    raw = await rs.lrange(_ARTICLES_KEY, 0, max(0, scan_limit - 1))
    candidates: list[dict] = []
    seen: set[str] = set()
    for r in raw:
        try:
            article = json.loads(r) if isinstance(r, str) else r
        except Exception:
            continue
        if not isinstance(article, dict):
            continue
        signal = _build_intel_signal(article)
        signal_id = str(signal.get("id", ""))
        if signal_id in seen:
            continue
        seen.add(signal_id)
        signal["_backfilled"] = True
        candidates.append(signal)

    priority_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    candidates.sort(
        key=lambda s: (
            priority_rank.get(str(s.get("priority", "LOW")), 2),
            0 if str(s.get("quality_label", "")).startswith("decision-grade") else 1,
            1 if s.get("signal_type") == "situational_awareness" else 0,
            -int(s.get("score", 0) or 0),
            str(s.get("detected_at", "")),
        )
    )
    return candidates[:limit]


async def _promote_article_signal(article: dict) -> bool:
    """Best-effort promotion from raw article to dashboard decision signal."""
    try:
        signal = _build_intel_signal(article)
        await _store_intel_signal(signal)
        return True
    except Exception:
        logger.debug("[news_monitor] intel signal promotion failed", exc_info=True)
        return False


async def _feed_to_brain(article: dict) -> bool:
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

    # R-F2385 — product-grade promotion layer. The raw article still lands in
    # the audit feed, but the user-facing surface now gets a concise signal with
    # priority, confidence, why-it-matters, action, entities, and evidence.
    promoted = await _promote_article_signal(article)

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
                detail=str(article.get("full_text") or article.get("summary", ""))[:6000],  # R-F2203 — richer RAG content
                entity_name=_name[:120],
                source_id=f"vault_source:{_article_hash(article.get('url', ''))}",
                confidence="PROBABLE",
                extra_topics=["vault_source"],
            )
    except Exception:
        logger.debug("[news_monitor] vault-source brain absorb failed", exc_info=True)

    return promoted


# ── Feed fetching ─────────────────────────────────────────────────────────────


async def _fetch_feed(url: str, source_name: str) -> Optional[str]:
    """Fetch a feed URL with retries.

    R-F2218 — redirects are followed MANUALLY and EVERY hop is re-validated with
    security.validate_url(). Pre-fix the shared client had follow_redirects=True and
    only the ORIGINAL url was checked, so a public feed URL that 302's to an internal
    /.internal/private host (or a DNS-rebind) was fetched unchecked — SSRF into the
    fly 6PN network with the response absorbed into the corpus. Per-request
    follow_redirects=False overrides the client default without affecting other calls.
    """
    from . import security as _sec
    client = _get_client()
    last_error = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            cur = url
            for _hop in range(6):   # bounded redirect chain
                resp = await client.get(cur, follow_redirects=False)
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location") or ""
                    if not loc:
                        break
                    nxt = str(httpx.URL(cur).join(loc))
                    _ok, _why = _sec.validate_url(nxt)
                    if not _ok:
                        logger.warning("[news_monitor] blocked unsafe redirect for %s: %s -> %s (%s)",
                                       source_name, cur[:80], nxt[:80], _why)
                        return None
                    cur = nxt
                    continue
                resp.raise_for_status()
                return resp.text
            logger.debug("[news_monitor] too many redirects for %s (%s)", source_name, url)
            return None
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
    url_to_id: dict[str, str] = {}
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
            # R-F2217 — remember which vault entry each URL came from so the poll
            # loop can bump/reset its fail-streak (the tuple shape can't carry the id).
            _sid = e.get("site_id")
            if _sid:
                url_to_id[url] = _sid
        except Exception:
            continue
    global _VAULT_URL_TO_ID
    _VAULT_URL_TO_ID = url_to_id
    return out


# ── R-F2217 — dead-source auto-suspension ────────────────────────────────────
# A vault/user source that keeps failing used to be re-fetched hourly FOREVER
# (marked "failed" per poll but never persisted, so it rotted invisibly). Track
# consecutive failures in the entry's metadata; after N, flip its status to
# "failed" — which _get_vault_feed_sources already excludes — so the poll set
# self-heals. A single successful fetch clears the streak (and promotes a
# "pending" source, R-F2213, to "verified" now that it's confirmed live).
_VAULT_URL_TO_ID: dict[str, str] = {}
_VAULT_FAIL_SUSPEND_THRESHOLD = max(2, int(os.getenv("ARIA_VAULT_FAIL_SUSPEND", "6") or "6"))


def _vault_meta(entry: dict) -> dict:
    try:
        return json.loads(entry.get("metadata_json") or "{}") or {}
    except Exception:
        return {}


def _bump_vault_failstreak(url: str) -> None:
    sid = _VAULT_URL_TO_ID.get(url)
    if not sid:
        return
    try:
        from .agent_signup_vault import get_vault
        v = get_vault()
        entry = v.get(sid)
        if not entry:
            return
        cur = (entry.get("status") or "").lower()
        if cur in ("failed", "cancelled", "expired"):
            return
        streak = int(_vault_meta(entry).get("fail_streak", 0) or 0) + 1
        if streak >= _VAULT_FAIL_SUSPEND_THRESHOLD:
            v.update_status(
                sid, "failed",
                notes=f"auto-suspended after {streak} consecutive poll failures (R-F2217)",
                metadata={"fail_streak": streak, "auto_suspended": True},
            )
            try:
                wire_failure(
                    module="news_monitor",
                    summary=f"Vault source auto-suspended: {sid}",
                    detail=f"{streak} consecutive failures | url={url[:120]}",
                    source_id=f"news_monitor:suspend:{sid}",
                )
            except Exception:
                pass
        else:
            v.update_status(sid, entry.get("status") or "pending", metadata={"fail_streak": streak})
    except Exception:
        logger.debug("[news_monitor] failstreak bump failed for %s", url[:80], exc_info=True)


def _reset_vault_failstreak(url: str) -> None:
    sid = _VAULT_URL_TO_ID.get(url)
    if not sid:
        return
    try:
        from .agent_signup_vault import get_vault
        v = get_vault()
        entry = v.get(sid)
        if not entry:
            return
        cur = (entry.get("status") or "").lower()
        had_streak = int(_vault_meta(entry).get("fail_streak", 0) or 0) > 0
        # Only write when something actually changes: clear a streak, or promote a
        # confirmed-live "pending" source to "verified". Healthy sources = no write.
        if had_streak or cur == "pending":
            new_status = "verified" if cur == "pending" else (entry.get("status") or "verified")
            v.update_status(sid, new_status, metadata={"fail_streak": 0, "auto_suspended": False})
    except Exception:
        logger.debug("[news_monitor] failstreak reset failed for %s", url[:80], exc_info=True)


def _wire_scrape_failure(name: str, url: str, why: str) -> None:
    """R-F2214 §21a — a vault WEBSITE source that yields nothing (probe error or
    no extractable content) used to return silently, so a dead manually-added site
    rotted invisibly. Wire it to the brain so the self-heal/coder loop can see it.
    Best-effort — telemetry must never break the poll path."""
    try:
        wire_failure(
            module="news_monitor",
            summary=f"Vault website scrape empty: {name}",
            detail=f"{why} | url={url[:150]}",
            source_id=f"news_monitor:scrape:{name}",
        )
    except Exception:
        pass


async def _scrape_vault_website(name: str, url: str, category: str, lang: str, tier: str, topics) -> dict:
    """R-F2191 — ingest a vault WEBSITE that is NOT an RSS/Atom feed.

    A manually-added website (vault.html "Add Site", site_type=website) that isn't a
    feed used to be silently dropped (`unknown_format`). Operators trust manual sites
    as a reliable data source, so this scrapes the page via the robust SSRF-guarded
    `researcher.extract_url_text` (Wayback fallback, no LLM) and runs the result through
    the SAME store→ledger→brain-absorb path as feed articles — so the site reliably
    reaches BOTH the dashboard/news (Pipeline 1) AND the brain RAG/knowledge (Pipeline 2).

    Content-hash dedup: an unchanged page is not re-ingested every poll; a changed page
    re-ingests. Returns {"fetched", "new"}.
    """
    from . import researcher as _r
    # 1) Cheap single-page PROBE for change-detection so the poll loop is not deep-crawling
    #    every vault source every cycle — deep extraction fires only on first-ingest or change.
    try:
        probe = await _r.extract_url_text(url, timeout=20.0)
    except Exception as e:
        logger.debug("[news_monitor] vault website probe failed for %s: %s", url, e)
        _wire_scrape_failure(name, url, f"probe error: {str(e)[:120]}")   # R-F2214 §21a
        return {"fetched": 0, "new": 0}

    ptext = str((probe or {}).get("text", "") or "").strip()
    if not probe or not probe.get("extraction_ok") or not ptext:
        _wire_scrape_failure(name, url, "no extractable content")          # R-F2214 §21a
        return {"fetched": 0, "new": 0}

    chash = _article_hash(url + "|" + hashlib.sha256(ptext.encode("utf-8", "ignore")).hexdigest()[:16])
    seen_key = f"scrape:{chash}"
    if await _is_seen(seen_key):
        return {"fetched": 1, "new": 0}      # unchanged since last poll
    await _mark_seen(seen_key)

    # 2) R-F2203 — NEW or CHANGED → richer MULTI-PAGE deep extraction (homepage + high-value
    #    internal pages: about/team/products/contact + structured extractors), so the operator's
    #    curated source actually yields RICH data, not just a single homepage. Best-effort: falls
    #    back to the probe text if deep extraction fails or returns less.
    text = ptext
    title = str((probe.get("title") or name or url))
    try:
        deep = await asyncio.wait_for(_r.extract_url_deep(url, max_pages=4, timeout=15.0), timeout=45.0)
        dtext = str((deep or {}).get("text", "") or "").strip()
        if deep and deep.get("extraction_ok", True) and len(dtext) > len(text):
            text = dtext
            title = str(deep.get("title") or title)
    except Exception:
        pass  # keep the homepage probe text — deep extraction is an enrichment, never required

    article = {
        "url": url,
        "title": title[:200],
        "summary": text[:1200],               # R-F2203 — raised 500 -> 1200
        "full_text": text[:24000],            # R-F2203 — raised 5000 -> 24000 (deep multi-page)
        "source": name,                       # already shaped "vault:<site name>"
        "category": category,
        "language": lang,
        "tier": tier,
        "topics": topics,
        "detected_at": datetime.now(timezone.utc).isoformat(),
    }
    await _store_article(article)
    promoted = await _feed_to_brain(article)  # → intel_ledger (data output) + brain absorb (intel)
    return {"fetched": 1, "new": 1, "signals_promoted": 1 if promoted else 0}


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
    total_promoted = 0
    feed_results = []

    # ── R-F2630: budget the loop so the TAIL (state write + promotion bridge)
    # always runs. Rotate the start offset so truncation cannot starve the feeds
    # after the cut-off.
    _prev_state = await _read_poll_state() or {}
    _rot = int(_prev_state.get(_POLL_ROTATION_KEY) or 0)
    if sources:
        _rot %= len(sources)
        sources = sources[_rot:] + sources[:_rot]
    _loop_deadline = time.monotonic() + max(1.0, _POLL_BUDGET_S - _POLL_TAIL_RESERVE_S)
    _truncated = False
    _attempted = 0

    for name, url, category, lang, tier, topics in sources:
        if time.monotonic() >= _loop_deadline:
            # Out of loop budget. STOP here and let the tail run — a partial poll
            # whose state + promotions land beats a full poll that is killed and
            # records nothing (which is what froze last_poll_at at 11:23 for 3h+).
            _truncated = True
            logger.warning(
                "[news_monitor] R-F2630 poll TIME-BOXED after %d/%d feeds "
                "(budget %.0fs, reserve %.0fs) — tail (state write + promotion "
                "bridge) will still run; next poll rotates to offset %d",
                _attempted, len(sources), _POLL_BUDGET_S, _POLL_TAIL_RESERVE_S,
                (_rot + _attempted) % max(1, len(sources)),
            )
            break
        _attempted += 1
        try:
            xml_text = await _fetch_feed(url, name)
            if not xml_text:
                total_failed += 1
                feed_results.append({"name": name, "status": "failed", "articles": 0})
                # R-F2214 §21a — a feed that returns nothing (404 / timeout / empty
                # body after retries) was silently counted and NEVER reached the
                # brain, so a dead user/vault source rotted invisibly (poll retried
                # it hourly forever with no self-heal signal). Wire it so the gap
                # loop can see it. SCOPED to vault_curated (user/admin-added) sources
                # — the curated NEWS_SOURCES firehose is separately maintained and
                # its transient blips would just add noise; record_gap also dedupes
                # per (gap_type, detail) within a window, so a dead source won't flood.
                if category == "vault_curated":
                    try:
                        wire_failure(
                            module="news_monitor",
                            summary=f"Vault source empty/unreachable: {name}",
                            detail=f"url={url[:150]}",
                            source_id=f"news_monitor:feed:{name}",
                        )
                    except Exception:
                        pass
                    _bump_vault_failstreak(url)   # R-F2217 — dead-source lifecycle
                continue

            feed_type = _detect_feed_type(xml_text)
            if feed_type == "rss":
                articles = await _parse_rss(xml_text, name)
            elif feed_type == "atom":
                articles = _parse_atom(xml_text, name)
            else:
                # R-F2191 — a vault-curated WEBSITE that isn't a feed is SCRAPED rather
                # than dropped, so manually-added websites reliably bring value.
                if category == "vault_curated":
                    sc = await _scrape_vault_website(name, url, category, lang, tier, topics)
                    total_fetched += sc["fetched"]
                    total_new += sc["new"]
                    total_promoted += int(sc.get("signals_promoted") or 0)
                    feed_results.append({"name": name, "status": "scraped", "articles": sc["fetched"], "new": sc["new"], "signals_promoted": sc.get("signals_promoted", 0)})
                    # R-F2217 — a fetched page = live source (clear streak / promote);
                    # a 0-fetch scrape = failure (bump toward auto-suspend).
                    if sc["fetched"] > 0:
                        _reset_vault_failstreak(url)
                    else:
                        _bump_vault_failstreak(url)
                    await asyncio.sleep(0.5)
                    continue
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
                if await _feed_to_brain(article):
                    total_promoted += 1
                new_count += 1

            total_new += new_count
            feed_results.append({"name": name, "status": "ok", "articles": len(articles), "new": new_count})
            # R-F2217 — a feed that parsed (even 0 new = all seen) is LIVE: clear
            # its fail streak / promote pending→verified.
            if category == "vault_curated":
                _reset_vault_failstreak(url)

            # Rate-limit: don't hammer all feeds at once
            await asyncio.sleep(0.5)

        except Exception as e:
            logger.warning("[news_monitor] Feed poll failed for %s: %s", name, e)
            total_failed += 1
            feed_results.append({"name": name, "status": "error", "error": str(e)[:100]})
            # R-F1057 — wire failure to brain so ARIA sees it
            try:
                # R-F2630 §3b — was wire_failure(module=, summary=, detail=,
                # source_id=). wire_failure takes (module, detail, gap_type,
                # source): the summary=/source_id= kwargs raised TypeError into
                # the `except: pass` below, so EVERY feed failure was DARK —
                # 42 per poll, never reaching the brain, despite R-F1057's intent.
                wire_failure(
                    module="news_monitor",
                    detail=f"Feed poll failed: {name}: {str(e)[:250]}",
                    gap_type="source_failure",
                    source=f"news_monitor:feed:{name}",
                )
            except Exception:
                pass
            if category == "vault_curated":
                _bump_vault_failstreak(url)   # R-F2217 — dead-source lifecycle

    summary = {
        # R-F2630 — report the feeds ACTUALLY attempted, not the configured
        # total. Reporting len(sources) on a time-boxed run would understate the
        # failure ratio (feeds_failed/feeds_polled) and hide the source rot.
        "feeds_polled": _attempted,
        "feeds_failed": total_failed,
        "articles_fetched": total_fetched,
        "articles_new": total_new,
        "signals_promoted": total_promoted,
        "results": feed_results,
        "truncated": _truncated,
        "feeds_skipped": max(0, len(sources) - _attempted),
        # R-F2636 — a category-filtered run polled a SUBSET of the source list, so it
        # must NOT overwrite the full-poll health/freshness (3 clean feeds erasing a
        # 42/76 failure ratio is a false-clean). _write_poll_state records it separately.
        "scope": "filtered" if categories else "full",
        # Where the NEXT poll should start, so a truncated run cannot starve the
        # sources after the cut-off.
        _POLL_ROTATION_KEY: ((_rot + _attempted) % len(sources)) if sources else 0,
        "polled_at": datetime.now(timezone.utc).isoformat(),
    }
    state = await _write_poll_state(summary)
    summary["freshness"] = state

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

    # R-F2555 — Golden Intel promotion bridge. After the RSS poll, promote
    # structured findings (public procurement tenders, ...) into the SAME signal
    # store so "Distribution Ready" is not RSS-only. Lazy import avoids a circular
    # dependency; guarded so a bridge error can never break the news poll.
    try:
        from . import golden_intel_bridge
        summary["promotion_bridge"] = await golden_intel_bridge.run_promotion_pass()
    except Exception:
        logger.debug("[news_monitor] golden intel promotion pass failed", exc_info=True)

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
async def get_recent_intel_signals(limit: int = 20) -> dict:
    """Return decision-grade signals promoted from the raw news feed."""
    capped = max(1, min(int(limit or 20), 100))
    raw = await rs.lrange(_INTEL_SIGNALS_KEY, 0, capped - 1)
    backfilled: list[dict] = []
    used_backfill = False
    if not raw:
        try:
            backfilled = await _backfill_intel_signals_from_articles(capped)
            if backfilled:
                used_backfill = True
                asyncio.create_task(_persist_backfilled_intel_signals(backfilled))
        except Exception:
            logger.debug("[news_monitor] intel signal backfill failed", exc_info=True)
    signals = list(backfilled)
    for r in raw:
        try:
            sig = json.loads(r) if isinstance(r, str) else r
            if isinstance(sig, dict):
                signals.append(_normalise_intel_signal(sig))
        except Exception:
            continue
    signals = [_normalise_intel_signal(sig) for sig in signals]

    by_priority: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for sig in signals:
        pri = str(sig.get("priority") or "UNKNOWN").upper()
        typ = str(sig.get("signal_type") or "unknown")
        by_priority[pri] = by_priority.get(pri, 0) + 1
        by_type[typ] = by_type.get(typ, 0) + 1
    poll_state = await _read_poll_state()
    now = time.time()
    newest_signal_at = None
    newest_signal_epoch = None
    for sig in signals:
        for field in ("detected_at", "published"):
            epoch = _parse_epoch(sig.get(field))
            if epoch is not None and (newest_signal_epoch is None or epoch > newest_signal_epoch):
                newest_signal_epoch = epoch
                newest_signal_at = sig.get(field)
    poll_age_s = _age_seconds(poll_state.get("last_success_at"), now=now)
    newest_signal_age_s = int(now - newest_signal_epoch) if newest_signal_epoch is not None else None
    stale_reasons: list[str] = []
    if poll_age_s is None:
        stale_reasons.append("missing_poll_state")
    elif poll_age_s > _GOLDEN_POLL_STALE_S:
        stale_reasons.append("poll_stale")
    poll_total = int(poll_state.get("feeds_polled") or 0)
    poll_failed = int(poll_state.get("feeds_failed") or 0)
    poll_failed_ratio = (poll_failed / poll_total) if poll_total else 0.0
    failed_feed_names: list[str] = []
    for item in (poll_state.get("results") or [])[:100]:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "").lower() in {"failed", "error", "unknown_format"}:
            failed_feed_names.append(str(item.get("name") or "unknown")[:120])
    if poll_total and poll_failed_ratio > 0.15:
        stale_reasons.append("source_failure_degraded")
    if not signals:
        stale_reasons.append("no_signals")
    elif newest_signal_age_s is None:
        stale_reasons.append("missing_signal_timestamp")
    elif newest_signal_age_s > _GOLDEN_SIGNAL_STALE_S:
        stale_reasons.append("signals_stale")
    if poll_state.get("status") == "failed":
        stale_reasons.append("last_poll_failed")
    freshness = {
        "status": "stale" if stale_reasons else "fresh",
        "stale": bool(stale_reasons),
        "stale_reasons": stale_reasons,
        "last_poll_at": poll_state.get("last_poll_at"),
        "last_success_at": poll_state.get("last_success_at"),
        "last_error_at": poll_state.get("last_error_at"),
        "poll_age_s": poll_age_s,
        "poll_stale_after_s": _GOLDEN_POLL_STALE_S,
        "newest_signal_at": newest_signal_at,
        "newest_signal_age_s": newest_signal_age_s,
        "signal_stale_after_s": _GOLDEN_SIGNAL_STALE_S,
        "backfilled": used_backfill or (bool(signals) and all(bool(sig.get("_backfilled")) for sig in signals)),
        "poll": {
            "status": poll_state.get("status"),
            "feeds_polled": poll_total,
            "feeds_failed": poll_failed,
            "failed_ratio": round(poll_failed_ratio, 4),
            "failed_feeds": failed_feed_names[:20],
            "failure_budget_ratio": 0.15,
            "articles_fetched": poll_state.get("articles_fetched"),
            "articles_new": poll_state.get("articles_new"),
            "signals_promoted": poll_state.get("signals_promoted"),
        },
    }
    return {
        "signals": signals,
        "count": len(signals),
        "by_priority": by_priority,
        "by_type": by_type,
        "freshness": freshness,
        "schema_version": "rf2385.v1",
    }


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
        "poll_state": await _read_poll_state(),
    }
    _stats_cache = result
    _stats_cache_ts = now
    return result
