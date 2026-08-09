"""ARIA Research Engine — Active 

learning through article reading and hypothesis validation.

ARIA doesn't just respond to questions — she actively reads defence/security articles,
extracts intelligence, cross-references with existing knowledge, validates or challenges
her own hypotheses, and grows her domain expertise over time.

Three modes of learning:
1. AUTONOMOUS — scans 30+ RSS feeds + web searches every 6 hours
2. ON-DEMAND — reads any article URL you give her
3. WHATSAPP — reads articles shared via WhatsApp links

This is what makes ARIA a learning analyst, not a chatbot."""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

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
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.researcher")


def _extractor_unavailable(which: str, exc: Exception, url: str = "") -> None:
    """R-F3564 — a zero-LLM extractor produced nothing; say so.

    These extractors feed the DD evidence path. When one is unavailable the
    caller keeps its pre-initialised empty lists, which are byte-identical to a
    genuinely empty page — so without this the run records an unverified absence
    as though it were a checked one.
    """
    try:
        from .engine_wiring import wire_failure
        wire_failure(
            module="researcher",
            detail=f"{which} extractor unavailable for {url or 'unknown url'}: "
                   f"{type(exc).__name__}: {exc}"[:400],
            gap_type="source_failure",
            source="researcher.extract_url_deep",
        )
    except Exception:                       # noqa: BLE001
        pass


def _search_hit_to_dict(hit: Any) -> dict:
    """Normalize web-search hits at DD/research boundaries.

    ``web_search.search_multilingual()`` returns SearchResult dataclasses while
    legacy DD/search helpers return dicts. Keep this adapter local so callers
    can harden ingestion without changing the global search contract.
    """
    if isinstance(hit, dict):
        return hit
    return {
        "title": getattr(hit, "title", "") or "",
        "link": getattr(hit, "url", "") or "",
        "url": getattr(hit, "url", "") or "",
        "snippet": getattr(hit, "snippet", "") or "",
        "source": getattr(hit, "source", "") or "",
        "_credibility_tier": getattr(hit, "credibility_tier", ""),
        "_relevance_score": getattr(hit, "relevance_score", None),
        "_language": getattr(hit, "language", None),
    }

# ── GLOBAL Defence & Security Research Sources ───────────────────────────────

RESEARCH_FEEDS = [
    # ── Global Defence Procurement ────────────────────────────────────────
    {"name": "Defense News", "url": "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml", "category": "defence_procurement"},
    {"name": "Janes", "url": "https://www.janes.com/defence-intelligence-insights/defence-news", "category": "defence_industry"},  # 2026-05-01: /osint-insights/* 301'd to /defence-intelligence-insights/*
    # F76 prune 2026-04-28: Defense One /rss/ → persistent 404 across
    # every research cycle today. URL had no working alternative. Removing
    # to stop ~6 wasted req/hour. Re-enable if Defense One restores RSS.
    # {"name": "Defense One", "url": "https://www.defenseone.com/rss/", "category": "defence_policy"},
    {"name": "The Defense Post", "url": "https://www.thedefensepost.com/feed/", "category": "defence_news"},
    # F76 prune 2026-04-28: armyrecognition.com /rss → persistent 404.
    # {"name": "Army Recognition", "url": "https://www.armyrecognition.com/rss", "category": "land_systems"},
    {"name": "Naval News", "url": "https://www.navalnews.com/feed/", "category": "naval"},
    {"name": "Air Force Technology", "url": "https://www.airforce-technology.com/feed/", "category": "aerospace"},
    {"name": "Army Technology", "url": "https://www.army-technology.com/feed/", "category": "land_systems"},
    {"name": "Naval Technology", "url": "https://www.naval-technology.com/feed/", "category": "naval"},
    # F76 prune 2026-04-28: shephardmedia.com /feed/ → 404. The working
    # endpoint /news/defence-notes/feed/ is already in the list below as
    # "Defence Notes", so we don't lose Shephard coverage.
    # {"name": "Shephard Media", "url": "https://www.shephardmedia.com/feed/", "category": "defence_industry"},

    {"name": "C4ISRNet", "url": "https://www.c4isrnet.com/arc/outboundfeeds/rss/?outputType=xml", "category": "c4isr"},
    {"name": "Forecast International", "url": "https://dsm.forecastinternational.com/rss", "category": "defence_industry"},
    {"name": "Defence Notes", "url": "https://www.shephardmedia.com/news/defence-notes/feed/", "category": "defence_news"},

    # ── Arms Trade & Policy ───────────────────────────────────────────────
    {"name": "SIPRI Blog", "url": "https://www.sipri.org/rss", "category": "arms_trade"},  # old /rss.xml → 404
    # F76 prune 2026-04-28: DSCA dsca-rss-really-simple-syndication → 403
    # (Cloudflare or auth-required). Retained as comment so future operator
    # can search; replacement candidate is the DSCA Major Arms Sales JSON
    # at https://www.dsca.mil/major-arms-sales (HTML scrape).
    # {"name": "DSCA Major Arms Sales", "url": "https://www.dsca.mil/dsca-rss-really-simple-syndication", "category": "fms"},
    # F76 prune 2026-04-28: IISS military-balance/feed → 403 (auth wall).
    # IISS RSS was deprecated; primary access now via paid API.
    # {"name": "IISS", "url": "https://www.iiss.org/online-analysis/military-balance/feed", "category": "strategic_studies"},
    {"name": "RUSI", "url": "https://www.rusi.org/rusi-rss-feeds", "category": "defence_research"},  # old /rss.xml → 404
    {"name": "War on the Rocks", "url": "https://warontherocks.com/feed/", "category": "strategy"},
    # F76 prune 2026-04-28: Chatham House /rss.xml → 403. Reachable via
    # newsletter only; no public RSS at present.
    # {"name": "Chatham House", "url": "https://www.chathamhouse.org/rss.xml", "category": "geopolitics"},
    # F76 prune 2026-04-28: csis.org /rss → 404 (page exists, RSS retired).
    # {"name": "CSIS", "url": "https://www.csis.org/rss", "category": "strategy"},
    # F76 prune 2026-04-28: rand.org /pubs/rss.xml → 404.
    # {"name": "RAND", "url": "https://www.rand.org/pubs/rss.xml", "category": "defence_research"},
    {"name": "ISW", "url": "https://understandingwar.org/feed", "category": "conflict_intelligence"},
    {"name": "UCDP", "url": "https://ucdp.uu.se/apidocs/", "category": "conflict_data"},
    {"name": "CrisisWatch", "url": "https://www.crisisgroup.org/rss-0", "category": "conflict_early_warning"},  # 2026-05-01: /rss/crisiswatch 301'd to /rss-0
    {"name": "Crisis Group Africa", "url": "https://www.crisisgroup.org/rss/1", "category": "africa_security"},

    # ── Regional: Africa ──────────────────────────────────────────────────
    {"name": "DefenceWeb", "url": "https://www.defenceweb.co.za/feed/", "category": "africa_defence"},
    {"name": "ISS Africa", "url": "https://issafrica.org/iss-today/feed", "category": "africa_security"},
    {"name": "DW Africa", "url": "https://rss.dw.com/xml/rss-en-africa", "category": "africa_news"},
    # F76 prune 2026-04-28: africa-confidential.com /rss → 404 (paywall,
    # no public RSS). Newsletter-only.
    # {"name": "Africa Confidential", "url": "https://www.africa-confidential.com/rss", "category": "africa_intelligence"},
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
    # R-F64 (2026-05-09): direct breakingdefense.com/feed/ → 403 from cloud IPs
    # (Cloudflare WAF blocks fly.io egress, mirrors seenode R-F61 fix on the Node
    # side). Switch to Google News RSS scoped to site:breakingdefense.com — same
    # editorial coverage, GoogleBot-style fetch path.
    {"name": "Breaking Defense", "url": "https://news.google.com/rss/search?q=site%3Abreakingdefense.com&hl=en-US&gl=US&ceid=US:en", "category": "defence_procurement"},

    # ── Regional: Latin America (Spanish) ────────────────────────────────
    # F76 prune 2026-04-28: infodefensa.com /feed → 404. The site has
    # consolidated to non-RSS aggregators — Defensa.com + Zona Militar
    # below cover the same Spanish-language LatAm intel space.
    # {"name": "Infodefensa", "url": "https://www.infodefensa.com/feed", "category": "latam_defence"},
    {"name": "Defensa.com", "url": "https://www.defensa.com/rss", "category": "latam_defence"},
    {"name": "Zona Militar", "url": "https://www.zona-militar.com/feed/", "category": "latam_defence"},

    # ── Regional: Latin America (Spanish) ────────────────────────────────
    {"name": "Dialogo Americas", "url": "https://dialogo-americas.com/feed/", "category": "latam_security"},
    # BN Americas RSS removed — /rss/infrastructure → 404, paywall site
    # {"name": "BN Americas Defence", "url": "https://www.bnamericas.com/en/rss/infrastructure", "category": "latam_procurement"},

    # ── Export Controls & Compliance ──────────────────────────────────────
    {"name": "BIS Federal Register", "url": "https://www.bis.doc.gov/index.php/component/rssfeed/feed/2-federal-register-notices?format=feed", "category": "export_controls"},

    # ── Russian-language (for lang:ru mastery lift; sources chosen for
    # language exposure, not as primary source-of-truth). Propaganda-tier
    # content is handled separately via Clause 13 at claim-verification
    # time; these sources are for ingesting Cyrillic script so the script
    # detector in student.detect_topics() can emit `lang:ru` tags.
    {"name": "TASS Defence (RU)", "url": "https://tass.ru/rss/v2.xml", "category": "russia_defence"},
    {"name": "Kommersant Defence (RU)", "url": "https://www.kommersant.ru/RSS/news.xml", "category": "russia_industry"},

    # ── Chinese-language (for lang:zh mastery lift; same caveats as ru).
    # SCMP carries a neutral-to-critical perspective on PLA / Chinese
    # defence industry. Global Times huanqiu RSS retired — 404.
    # F76 prune 2026-04-28: huanqiu.com /rss/mil.xml → 404.
    # {"name": "Global Times Military (ZH)", "url": "https://www.huanqiu.com/rss/mil.xml", "category": "china_defence"},
    {"name": "SCMP China", "url": "https://www.scmp.com/rss/91/feed", "category": "china_analysis"},
]

# ── Legal & Regulatory Sources (R-F1523, R-F1525) ─────────────────
# These feeds feed ARIA's legal mastery across sanctions, export control,
# contract law, and international trade law. ARIA fetches these alongside
# defence feeds every research cycle and scores them on legal-specific terms.
#
# R-F1525: populated with verified-working feeds. Each was tested live from
# fly.io egress (2026-06-12). If a feed starts 404ing, replace it.
#
# To add a source, add a dict with:
#   name:     Human-readable label
#   url:      RSS/Atom feed URL
#   category: One of: sanctions_law, export_control, trade_law,
#             contract_law, swiss_law, uae_law, eu_law, international_law
LEGAL_FEEDS: list[dict] = [
    # ── US Export Controls & Sanctions ────────────────────────────────
    {"name": "BIS Federal Register", "url": "https://www.bis.doc.gov/index.php/component/rssfeed/feed/2-federal-register-notices?format=feed", "category": "export_control"},
    {"name": "BIS News", "url": "https://www.bis.doc.gov/index.php/component/rssfeed/feed/1-news?format=feed", "category": "export_control"},
    {"name": "State Dept Arms Control", "url": "https://www.state.gov/feed/?f=topics%3A170", "category": "sanctions_law"},
    {"name": "State Dept Treaty Actions", "url": "https://www.state.gov/feed/?f=topics%3A76", "category": "international_law"},

    # ── UK Sanctions & Export Control ─────────────────────────────────
    {"name": "UK Sanctions Notices", "url": "https://www.gov.uk/government/publications?keywords=sanctions&format=atom", "category": "sanctions_law"},
    {"name": "UK Export Control", "url": "https://www.gov.uk/government/publications?keywords=export+control&format=atom", "category": "export_control"},
    {"name": "UK Trade Remedies", "url": "https://www.gov.uk/government/publications?keywords=trade+remedy&format=atom", "category": "trade_law"},

    # ── International Trade & Arbitration ─────────────────────────────
    {"name": "WTO News", "url": "https://www.wto.org/english/news_e/news_e.rss", "category": "trade_law"},
    {"name": "ICC Arbitration", "url": "https://iccwbo.org/feed/", "category": "contract_law"},
]

# Relevance anchor terms — at least one must match in title+description before
# procurement/regional/legal boosts apply during article scoring. Without this
# gate, a hotel paper with "billion-dollar deal in Angola" picks up +13 score
# with zero defence or legal content (live incident 2026-04-27).
#
# R-F1525: expanded from defence-only to include legal/regulatory anchors so
# sanctions, export control, trade law, and arbitration articles pass the gate.
# Legal articles are scored on their own terms in the scoring step below.
#
# 2026-04-27 v2 — F9 fix: original list used substring matching, so short
# abbreviations false-positived (`isr` matched 'disruption'/'disregard',
# 'mod' matched 'modern'/'module'). Train-crash article got selected for
# deep reading + RAG ingest because its description contained 'disruption'.
# Now: short anchors are word-bounded via regex, longer multi-char terms
# stay as substring matches (lower false-positive risk).
_DEFENCE_ANCHOR_SUBSTRINGS = (
    # ── Defence & Security ────────────────────────────────────────────
    "defence", "defense", "military", "weapon", "weapons",
    "nato", "naval", "air force", "airforce",
    "fighter", "missile", "drone",
    "artillery", "howitzer", "submarine", "frigate", "corvette",
    "helicopter", "warship", "armoured", "armored", "ammunition",
    "munitions", "soldier", "troops", "regiment", "brigade",
    "procurement", "tender",
    "ministry of defence", "ministry of defense", "general staff",
    "battalion",
    "intelligence service", "sigint", "humint", "osint",
    "stanag", "interoperability", "c4isr",
    # ── Export Controls & Sanctions ───────────────────────────────────
    "export control", "sanctions", "embargo", "dual-use",
    "export licence", "export license", "trade control",
    "ofac", "bis ear", "ear", "itar",
    "sanctions regime", "sanctions evasion", "sanctions compliance",
    "restricted party", "denied party", "specially designated",
    "sdn list", "consolidated list",
    # ── Trade Law & Remedies ──────────────────────────────────────────
    "trade law", "trade remedy", "anti-dumping", "countervailing",
    "safeguard measure", "trade barrier", "market access",
    "wto dispute", "wto ruling", "dispute settlement",
    "trade agreement", "free trade agreement", "fta",
    # ── International Law & Arbitration ───────────────────────────────
    "arbitration", "international court", "icc ruling",
    "investment treaty", "bilateral investment", "bit",
    "contract law", "force majeure", "choice of law",
    "jurisdiction clause", "dispute resolution",
    # ── Regulatory & Compliance ───────────────────────────────────────
    "compliance", "regulation", "regulatory",
    "anti-money laundering", "aml", "know your customer", "kyc",
    "bribery", "anti-corruption", "fcca", "uk bribery act",
    "data protection", "gdpr", "privacy regulation",
    "competition law", "antitrust", "merger control",
    # ── Legal Sources & Instruments ───────────────────────────────────
    "eur-lex", "federal register", "official journal",
    "executive order", "statutory instrument",
    "notice of proposed rulemaking", "nprm",
    "public consultation", "comment period",
)
# Word-bounded — short tokens that would false-positive as substrings.
# `arms` matches in 'farmstand', `army` in 'armyworm', `mod` in 'modern'/
# 'module', `isr` in 'disruption', `tank` in 'thank', etc.
_DEFENCE_ANCHOR_WORDS = re.compile(
    r"\b(?:arms|army|navy|tank|uav|ucav|rfp|rfi|fms|mod|isr"
    r"|combat|tactical|deployment|strategic command"
    r"|wto|icc|ofac|bis|ear|itar|aml|kyc|gdpr|fcca"
    r"|sanction|embargo|arbitration|compliance|antitrust)\b",
    re.IGNORECASE,
)


def _shorten_for_search(text: str, max_chars: int = 60) -> str:
    """Trim a long string to a search-engine-shaped query.

    Hypotheses and analysis fragments can be 200+ chars; passing them
    verbatim to news APIs returns nothing useful (and on Brave/Google
    counts toward the per-query character limit). We keep the first
    `max_chars` chars but cut on a word boundary so the query stays
    grammatical. Punctuation that confuses search APIs is also stripped.
    """
    if not text:
        return ""
    s = text.strip()
    # Drop quotes and punctuation that don't belong in a search query
    s = re.sub(r"[\"'`]", "", s)
    s = re.sub(r"[,;:!?]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars].rsplit(" ", 1)[0]
    return cut or s[:max_chars]


# F60 fix 2026-04-28: hypothesis statements like "The ARIA collection
# pipeline is subscribed to or receiving evidence" passed verbatim to
# search APIs return junk because every other word is a stopword.
# Extract the substantive nouns/verbs/proper-nouns and drop the rest.
_QUERY_STOPWORDS = frozenset({
    # Articles / determiners
    "the", "a", "an", "this", "that", "these", "those",
    # Auxiliaries / common verbs that filler hypothesis statements
    "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "have", "has", "had",
    "will", "would", "shall", "should", "may", "might", "can", "could",
    "shows", "showing", "appears", "seems", "indicates", "suggests",
    "suggesting", "subscribed", "receiving",
    # Connectors / prepositions
    "to", "of", "in", "on", "at", "for", "with", "by", "as", "from",
    "into", "onto", "over", "under", "about", "across", "between",
    "and", "or", "but", "if", "then", "than", "while", "because",
    "though", "although", "however",
    # Pronouns
    "it", "its", "their", "them", "they", "we", "our", "us", "i",
    "he", "she", "him", "her", "his", "hers",
    # Generic search-noise that doesn't disambiguate defence intent
    "evidence", "data", "information",
})


def _extract_query_keywords(text: str, max_words: int = 8) -> str:
    """Distill a long hypothesis / analysis sentence into 4-8 keywords.

    Drops articles, auxiliaries, generic verbs, and connectors; keeps
    capitalised tokens (proper nouns) and any non-stopword. Used by
    validate_hypothesis where the hypothesis text would otherwise be
    truncated mid-sentence to look like ``"The ARIA collection pipeline
    is subscribed to or receiving"``.
    """
    if not text:
        return ""
    s = re.sub(r"[\"'`]", "", text.strip())
    s = re.sub(r"[,;:!?.\-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""
    keywords: list[str] = []
    for word in s.split():
        # Stopword check first — even a capitalised "The" at the start
        # of a sentence is junk in a search query.
        if word.lower() in _QUERY_STOPWORDS:
            continue
        # Keep everything else: proper nouns ("ARIA", "NATO"), domain
        # terms ("defence", "procurement"), numbers and model designators.
        keywords.append(word)
        if len(keywords) >= max_words:
            break
    return " ".join(keywords[:max_words])


def _has_defence_anchor(text: str) -> bool:
    """True iff text contains at least one relevance anchor (defence, legal,
    regulatory, or trade — substring or word-bounded). R-F1525 expanded from
    defence-only to include legal/regulatory terms so sanctions, export control,
    trade law, and arbitration articles pass the scoring gate."""
    lower = text.lower()
    if any(s in lower for s in _DEFENCE_ANCHOR_SUBSTRINGS):
        return True
    return bool(_DEFENCE_ANCHOR_WORDS.search(text))


# Backwards-compat alias for existing tests / callers that imported the
# tuple directly. New callers should use _has_defence_anchor().
_DEFENCE_ANCHOR_TERMS = _DEFENCE_ANCHOR_SUBSTRINGS

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
    # R-F1523: legal & regulatory search queries for autonomous legal research
    "sanctions export control regulation update 2026",
    "OFAC sanctions enforcement action 2026",
    "EU sanctions regime Russia Iran 2026",
    "UK ECJU open general export licence update 2026",
    "Swiss law international sanctions implementation 2026",
    "UAE trade sanctions compliance framework 2026",
    "WTO dispute settlement ruling 2026",
    "ICC arbitration award enforcement 2026",
    "international trade law export controls 2026",
    "arms trade treaty implementation 2026",
    "defence contract law dispute resolution 2026",
    "BIS EAR export control reform 2026",
]

# ── Hypothesis Tracker ───────────────────────────────────────────────────────

HYPOTHESIS_KEY = "crucix:aria:hypotheses"
ARTICLES_READ_KEY = "crucix:aria:articles_read"

# After this many validation attempts that didn't flip the hypothesis off
# OPEN, validate_hypothesis() forces it to INSUFFICIENT_EVIDENCE so the
# 200-cap backlog drains. 5 means a stuck hypothesis ages out after ~5
# cycles of being in the per-cycle pick window (oldest-first). Combined
# with the per-cycle pick quota in main.py (R-F32 2026-05-03 raised that
# to 8), drain ≈ picks/cap = 8/5 = 1.6 hypotheses/cycle, comfortably
# above the ~1/cycle generation rate so the backlog actually shrinks.
_HYPOTHESIS_ATTEMPT_CAP = 5

# R-F161 (2026-05-10) — TIME-based stale cap. The R-F32 attempt-cap above
# only ages hypotheses that get *picked*. Hypotheses that sit at the back
# of the OPEN queue and never get picked stay OPEN indefinitely. Live
# evidence 2026-05-10: backlog grew 118→122 over ~5h despite 50% verifier
# resolution rate, with malformed hypothesis text ("Iraqs denial defence
# Bahadlis job scope indicate deliberate") burning verifier cycles.
#
# Fix: any hypothesis with status='OPEN' AND created_at older than this
# threshold gets auto-promoted to status='STALE' on the next _load_hypotheses
# call. STALE entries are excluded from validate_hypothesis picker (which
# filters for status=='OPEN'). They remain in the store for audit but stop
# burning verification compute. Operator can manually promote a STALE entry
# back to OPEN if they decide it's worth re-investigating.
#
# 14 days = generous enough that legitimate slow-moving hypotheses (waiting
# for natural-language news to surface evidence) still get a fair attempt
# window, but tight enough that fragment-pollution drains within ~2 weeks.
_HYPOTHESIS_STALE_DAYS = 14


def _is_stale_hypothesis(h: dict, max_age_days: int = _HYPOTHESIS_STALE_DAYS) -> bool:
    """R-F161 — return True if this OPEN hypothesis is older than the
    stale threshold. Uses created_at (YYYY-MM-DD) for the comparison —
    other date fields are optional and may be absent on legacy entries."""
    if not isinstance(h, dict):
        return False
    if h.get("status") != "OPEN":
        return False
    created_at = h.get("created_at") or ""
    if not created_at or not isinstance(created_at, str):
        return False
    try:
        # created_at is stored as YYYY-MM-DD (per line 1443 of this file);
        # parse defensively so a legacy ISO timestamp also works.
        from datetime import datetime as _dt, timezone as _tz
        if "T" in created_at:
            _created = _dt.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            _created = _dt.strptime(created_at[:10], "%Y-%m-%d").replace(tzinfo=_tz.utc)
        _age_days = (_dt.now(_tz.utc) - _created).total_seconds() / 86400
        return _age_days > max_age_days
    except Exception:
        return False


_INVALID_HYPOTHESIS_TEXTS = {"", "null", "none", "undefined", "nan", "n/a"}


def _is_valid_hypothesis_text(text: object) -> bool:
    """Filter out hypothesis entries whose text is missing, None, the
    literal string 'null'/'None'/'undefined', or shorter than ~10 chars
    (likely a parser artifact, not a real claim).

    F90 fix 2026-04-29: live evidence at 15:04:27 showed an autonomous
    research cycle running `news.google.com/rss/search?q=null+evidence+2026`
    because validate_hypothesis got handed a hypothesis whose text was
    literally 'null' — almost certainly a JSON-null leaking through
    `str()` somewhere upstream. Filter at load + insert so existing
    poison data drops out of the rotation and new bad entries can't
    accumulate.
    """
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if stripped.lower() in _INVALID_HYPOTHESIS_TEXTS:
        return False
    if len(stripped) < 10:
        return False
    return True


async def _load_hypotheses() -> list[dict]:
    data = await rs.get_json(HYPOTHESIS_KEY)
    raw = data or []
    # F90 fix: prune invalid hypothesis entries on read so the
    # validation loop never picks "null", "" or stub text. If we
    # filtered any out, persist the cleaned list back so the next
    # boot sees a clean slate.
    cleaned = [h for h in raw if _is_valid_hypothesis_text(h.get("hypothesis"))]

    # R-F161 — opportunistic stale-marking on every load. Mutate in-place,
    # tracking how many flipped from OPEN→STALE this cycle. Persist if
    # anything changed (combined with F90 prune below into a single write).
    _stale_marked = 0
    for h in cleaned:
        if _is_stale_hypothesis(h):
            h["status"] = "STALE"
            h["staled_at"] = datetime.now(timezone.utc).isoformat()
            h["staled_reason"] = (
                f"Auto-staled by R-F161: created_at {h.get('created_at','?')} "
                f"is older than {_HYPOTHESIS_STALE_DAYS} days and validation "
                f"never resolved it. Backlog drain — re-OPEN manually if worth "
                f"re-investigating."
            )
            _stale_marked += 1

    if len(cleaned) != len(raw) or _stale_marked > 0:
        try:
            if len(cleaned) != len(raw):
                logger.info(
                    "[hypotheses] filtered %d invalid entries on load (kept %d of %d)",
                    len(raw) - len(cleaned), len(cleaned), len(raw),
                )
            if _stale_marked > 0:
                logger.info(
                    "[hypotheses] R-F161 marked %d entries STALE on load (>%dd OPEN without resolution)",
                    _stale_marked, _HYPOTHESIS_STALE_DAYS,
                )
            await rs.set_json(HYPOTHESIS_KEY, cleaned[:200])
        except Exception as e:
            logger.debug("hypothesis cleanup persist failed: %s", e)
    return cleaned


async def _save_hypotheses(hypotheses: list[dict]) -> None:
    # Same filter applied on save so an in-process bug that adds a bad
    # entry can't pollute the persisted store on flush.
    cleaned = [h for h in hypotheses if _is_valid_hypothesis_text(h.get("hypothesis"))]
    await rs.set_json(HYPOTHESIS_KEY, cleaned[:200])


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
    """Fetch RSS feed and extract article titles + links.

    Per-feed circuit breaker (F14 fix, 2026-04-27): the live log shows
    ~12 RSS sources returning 404/403/410 every research cycle (DSCA,
    IISS, ChathamHouse, CSIS, RAND, AfricaConfidential, infodefensa,
    huanqiu, etc.). Without a breaker, each cycle wastes a request on
    every dead feed. After 5 consecutive failures the breaker opens for
    1 hour; the operator can verify if the feed URL has moved during
    that window.
    """
    from urllib.parse import urlparse as _up
    from .circuit_breaker import get_breaker
    host = (_up(url).hostname or url)[:80]
    cb = get_breaker(f"rss:{host}", failure_threshold=5, cooldown_seconds=3600)
    if cb.is_open():
        return []
    articles = []
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": random_ua()})
            if resp.status_code != 200:
                cb.record_failure()
                return []
            cb.record_success()
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
        cb.record_failure()
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

    Each mirror is wrapped in a circuit breaker (F21 fix, 2026-04-27):
    archive.is rate-limits aggressively (429 every paywalled OUP DOI in
    the live log). After 3 consecutive failures the breaker opens for
    15 minutes; we skip that mirror and fall through to the next.
    """
    from urllib.parse import quote_plus as _q
    from .circuit_breaker import get_breaker, classify_status

    # 1. archive.is
    # F78a 2026-04-29: bumped cooldown 900→3600 because archive.is
    # rate-limits aggressively and the live HALF_OPEN probe gets a 429
    # almost immediately every cycle (06:33:37 prod log). 15-min cycle
    # wasted ~96 probes/day; 1-hour cycle cuts that to ~24 with no
    # loss of recovery responsiveness.
    cb_archive = get_breaker("archive_is", failure_threshold=3, cooldown_seconds=3600)
    if not cb_archive.is_open():
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(
                    f"https://archive.is/newest/{url}",
                    headers={"User-Agent": random_ua()},
                )
                if resp.status_code == 200 and len(resp.text) > 2000:
                    cb_archive.record_success()
                    return resp.text
                # R-F1834: classify (archive.is 429s every probe from the DC IP →
                # rate_limit, so the backoff escalates instead of flapping at 3600s)
                cb_archive.record_failure(reason=classify_status(resp.status_code))
        except httpx.HTTPError:
            cb_archive.record_failure(reason="timeout")

    # 2. Wayback Machine — get the most recent snapshot URL via the availability API
    # F78b 2026-04-29: cooldown 900→3600 (same reason as archive.is)
    # AND distinguish "API succeeded but no archived snapshot for this
    # URL" from "API itself failed". The wayback availability endpoint
    # legitimately answers 200 + empty `archived_snapshots` when no
    # snapshot exists — that's a valid response, not a backend failure.
    # Previously we recorded that as a failure, so the breaker flapped
    # on every research cycle that asked about a never-archived URL.
    cb_wayback = get_breaker("wayback", failure_threshold=3, cooldown_seconds=3600)
    if not cb_wayback.is_open():
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                avail = await client.get(
                    "https://archive.org/wayback/available",
                    params={"url": url},
                )
                if avail.status_code == 200:
                    cb_wayback.record_success()
                    snap = (avail.json().get("archived_snapshots", {}) or {}).get("closest", {})
                    snap_url = snap.get("url") if snap.get("available") else None
                    if snap_url:
                        snap_resp = await client.get(snap_url)
                        if snap_resp.status_code == 200 and len(snap_resp.text) > 2000:
                            return snap_resp.text
                else:
                    cb_wayback.record_failure(reason=classify_status(avail.status_code))
        except (httpx.HTTPError, ValueError, KeyError):
            cb_wayback.record_failure(reason="timeout")

    return ""


# Domains that consistently return 403 (paywall/auth required) on direct
# fetch -- skip them up-front rather than wasting an HTTP round-trip + a
# downstream archive.is fallback that also rate-limits.
# Live evidence 2026-04-27 18:30:48 / 18:30:59: every academic.oup.com URL
# returned 403 then archive.is gave 429.
_KNOWN_PAYWALL_DOMAINS = (
    "academic.oup.com",
    "www.sciencedirect.com",
    "onlinelibrary.wiley.com",
    "link.springer.com",
    "pubs.aip.org",
    "www.tandfonline.com",
    "iopscience.iop.org",
    "journals.sagepub.com",
    # F32 fix 2026-04-27: SSRN consistently 403s on direct fetch; the
    # archive.is fallback then 429s. Live evidence: 20:14:43 wasted
    # the full fetch chain on /sol3/papers.cfm + archive.is + wayback.
    "papers.ssrn.com",
    "www.ssrn.com",
    "ssrn.com",
    # R-F150 2026-05-10: tearline.mil rate-limits aggressively on its own
    # — fetched once and immediately 429s on retry. Live evidence
    # 2026-05-10 11:28:47: GET /snapshot/russia-planned-naval-base...
    # → 301 → 429 (Too Many Requests). Skip direct fetch; archive
    # fallback can still try (tearline.mil snapshots are indexed by
    # archive.is when relevant).
    "www.tearline.mil",
    "tearline.mil",
)

# F91 fix 2026-04-29: DOI prefixes that consistently resolve via
# doi.org redirects to one of the known paywall hosts above. The
# original `_is_known_paywall` only checks hostname, so a URL coming
# in as `doi.org/10.2139/ssrn.6340699` slips through (host is doi.org,
# not ssrn.com) and we burn the full fetch chain: doi.org → ssrn.com
# → 403 → archive.is 429 → wayback 503. Live evidence 2026-04-29
# 15:04:32 — exactly that wasted cascade. Recognising the SSRN /
# Elsevier / Wiley / Springer / OUP DOI prefixes lets us skip before
# the first GET. Map: registrant prefix → which paywall it lands on.
_KNOWN_PAYWALL_DOI_PREFIXES = (
    "10.2139/",      # SSRN
    "10.1016/",      # Elsevier (sciencedirect.com)
    "10.1002/",      # Wiley
    "10.1007/",      # Springer
    "10.1093/",      # OUP (academic.oup.com)
    "10.1080/",      # Taylor & Francis (tandfonline.com)
    "10.1063/",      # AIP (pubs.aip.org)
    "10.1088/",      # IOP (iopscience.iop.org)
    "10.1177/",      # SAGE (journals.sagepub.com)
)


def _is_known_paywall(url: str) -> bool:
    """True if the URL is on the known-paywall skip list — either by
    hostname or by DOI prefix that resolves to a known-paywall host.

    The DOI-prefix check matters because doi.org is itself a redirector;
    a URL like https://doi.org/10.2139/ssrn.6340699 has hostname
    `doi.org` (not on the list) but resolves to papers.ssrn.com (which
    is). Without the prefix check we fire a full fetch chain on every
    such URL even though the destination is known-paywalled.
    """
    try:
        from urllib.parse import urlparse as _up
        parsed = _up(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path or ""
    except Exception:
        return False
    if any(host == d or host.endswith("." + d) for d in _KNOWN_PAYWALL_DOMAINS):
        return True
    # F91: detect DOI-host URLs whose path begins with a known-paywall
    # registrant prefix. Path shape is `/10.NNNN/suffix` so we strip
    # the leading slash before matching.
    if host in ("doi.org", "dx.doi.org"):
        clean_path = path.lstrip("/")
        if any(clean_path.startswith(p) for p in _KNOWN_PAYWALL_DOI_PREFIXES):
            return True
    return False


# Domains that almost never carry defence-relevant content. F26 fix
# 2026-04-27: hypothesis validation pulled medical/literature/legal DOIs
# from CrossRef (keyword-match across all DOIs) and Lightpanda-rendered
# them into RAG. Defence research must skip these up-front.
_IMPLAUSIBLE_DEFENCE_DOMAINS = (
    # Medical research
    "casemedicalresearch.com", "pubmed.ncbi.nlm.nih.gov", "nejm.org",
    "thelancet.com", "bmj.com", "jamanetwork.com", "nih.gov",
    # Literature / humanities aggregators
    "bloomsburycollections.com", "jstor.org", "muse.jhu.edu",
    "projectmuse.org", "modernlanguagesopen.org",
    # Pure math / chemistry
    "chemrxiv.org", "arxiv.org/abs/math",
    # Generic non-defence retail / lifestyle
    "amazon.com", "ebay.com", "etsy.com", "pinterest.com",
    # Recipe / cooking / hospitality
    "allrecipes.com", "tripadvisor.com", "booking.com",
)


def _is_plausible_defence_domain(url: str) -> bool:
    """Return False when the URL is on the skip list. Returns True for
    everything else — this is a denylist, not an allowlist, so unknown
    domains still pass through. The caller decides what to do with the
    filtered result list."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse as _up
        host = (_up(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    return not any(host == d or host.endswith("." + d) for d in _IMPLAUSIBLE_DEFENCE_DOMAINS)


@fail_wire(module="researcher", gap_type="source_failure")
async def extract_structured_html_async(html: str) -> dict:
    """R-F3475 — run _extract_structured_html OFF the event loop.

    LIVE 2026-07-30: R-F3464's stall attribution caught this on the loop thread:
        trafilatura/main_extractor.py:_extract:610
        trafilatura/main_extractor.py:extract_content:657
        trafilatura/core.py:trafilatura_sequence:103

    Unlike the three stall causes fixed before it — a module import (R-F3467), a
    sqlite commit (R-F3468) and a DNS resolve (R-F3473), all blocking syscalls —
    this one is pure CPU: trafilatura parses the DOM and runs main-content
    detection, and the function below then runs a dozen regex passes over the
    whole document (R-F2204 raised the output cap to 30k chars, so inputs are
    large by design). CPU-bound work cannot be made non-blocking in place; it has
    to move to a thread.

    Every async caller must use THIS, not the sync function. A guard test
    (test_rf3475_html_extraction_offload.py) fails the build if a new async
    caller calls the sync one directly.
    """
    return await asyncio.to_thread(_extract_structured_html, html)


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

    # ── R-F2204: trafilatura clean main-content (readability-grade) ──
    # The regex <p> path above misses body text on modern div/React/Tailwind sites
    # (content lives in <div>, not <p>) — yielding thin/empty extraction exactly where
    # the operator's curated sources live. trafilatura does proper main-content detection
    # (+ tables). Best-effort + import-guarded: if it's unavailable or yields LESS than the
    # regex paragraphs, we keep the regex result. When richer, it REPLACES `paragraphs`
    # so all downstream consumers (text body + the `paragraphs` field) get the clean text.
    try:
        import trafilatura as _traf
        _main = (_traf.extract(html, include_tables=True, include_comments=False,
                               favor_recall=True, no_fallback=False) or "").strip()
        if _main and len(_main) > len(" ".join(paragraphs)) + 200:
            paragraphs = [_main]
    except Exception:
        pass

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

    text = "\n\n".join(text_parts)[:30000]   # R-F2204 — raised 8000 -> 30000 (don't truncate rich sources)

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


async def _fetch_article_text(url: str, timeout: float = 0) -> str:
    """Fetch a URL and return STRUCTURED extracted content as a string.

    Uses _extract_structured_html() so the LLM sees title + headings + body +
    contact info + social links instead of a blob of regex-stripped text.
    Falls back to archive.is + Wayback Machine on paywalls and 4xx errors.
    R-F1102: default timeout raised to 30s (was 15), configurable via
    ARIA_FETCH_TIMEOUT env var.
    """
    if timeout <= 0:
        timeout = float(os.getenv("ARIA_FETCH_TIMEOUT", "30.0"))
    from .security import (
        sanitise_url, scan_content, strip_dangerous_content, is_internal_ref,
    )
    # R-F3355 — ARIA's OWN memory reaches this fetcher. `_web_search` maps every
    # SearchResult into the pipeline as `"link": r.url` (researcher.py:1632)
    # WITHOUT filtering the `memory://<sha1>` pointers `web_search` mints for
    # RAG hits that have no URL (web_search.py:1188). `research_and_learn` then
    # fetches that "link" (researcher.py:4079). The fetch can never succeed —
    # it is an identifier, not a locator — so this is pure waste, and every
    # attempt logged a WARNING that consumed a slot in the 200-entry error
    # ledger shared with real errors (43-44% of it, measured live 2026-07-28).
    # Short-circuit BEFORE sanitise_url so no work and no log happen at all.
    # Behaviour is unchanged: sanitise_url already returned None for these and
    # this function already returned "" — and research_and_learn already
    # handles an empty body by using the item's title + snippet, so the RAG hit
    # is still processed, just not pointlessly fetched.
    if is_internal_ref(url):
        return ""
    url = sanitise_url(url)
    if not url:
        return ""

    # Skip URLs we already know are paywalled — they 403 the direct
    # fetch and the archive.is fallback then 429s. F22 fix 2026-04-27.
    if _is_known_paywall(url):
        logger.debug("Article %s on known-paywall domain — skipping fetch", url[:80])
        return ""

    html = ""
    try:
        # R-F1851 (DD stage 2) — SSRF guard. `url` is a discovered/user-supplied page
        # URL; sanitise_url is parse-time only (no DNS), so route through safe_get
        # which DNS-resolves the host and revalidates every redirect hop (raw
        # follow_redirects=True could open-redirect to an internal service).
        from . import url_safety as _us
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await _us.safe_get(client, url, headers={
                "User-Agent": random_ua(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
            })
            if resp.status_code == 200:
                html = resp.text
            elif resp.status_code in (401, 402, 403, 404, 410, 429, 451, 500, 502, 503, 504):
                # R-F126 (2026-05-10): widen wayback fallback (mirrors
                # the same fix in extract_url_text) — 404/410/5xx
                # commonly occur on rotated CMS URLs of post-event PR.
                # R-F187 (2026-05-11): added 429 — dominant rate-limit
                # response across providers; pre-R-F187 the article fell
                # through to return "" leaving caller blind to the cause.
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

    # ── Playwright JS-rendering fallback ──────────────────────────────
    # If Lightpanda also returned thin content (or wasn't available),
    # try the full Chromium Playwright engine for complex SPAs.
    if _headless.is_thin_content(html):
        try:
            from .scraper.playwright_engine import fetch as _pw_fetch, is_available as _pw_avail
            # ── R-F3714 — `is_available` is ASYNC (playwright_engine.py:334) ──
            #
            # THE DEFECT: this called it WITHOUT await. A coroutine object is
            # always truthy, so the guard passed unconditionally and every thin
            # page entered `_pw_fetch(..., timeout=30.0, wait_for="networkidle")`
            # — including on hosts with no Chromium at all, where the launch
            # failure is swallowed below. Up to 30s per thin page, on the DD's
            # article-fetch path, spent proving something the guard was supposed
            # to answer for free. It also emitted a RuntimeWarning
            # ("coroutine ... was never awaited") on every call.
            #
            # The mistake is invisible on inspection because the sibling guard
            # in the Lightpanda engine IS synchronous, so the two read
            # identically at the call site. §3b exists for exactly this: check
            # whether the callee is async before deciding to await it.
            if await _pw_avail():
                logger.info("Still thin after Lightpanda (%d chars) — trying Playwright",
                            len(html))
                pw_result = await _pw_fetch(url, timeout=30.0, wait_for="networkidle")
                if pw_result.ok and pw_result.html and len(pw_result.html) > len(html):
                    logger.info("Playwright rendered %s: %d chars (was %d)",
                                url[:80], len(pw_result.html), len(html))
                    html = pw_result.html
                elif pw_result.blocked:
                    logger.info("Playwright blocked by bot-detection on %s: %s",
                                url[:80], pw_result.block_reason)
        except Exception as _pw_e:
            logger.debug("Playwright fallback failed for %s: %s", url[:80], _pw_e)

    scan = scan_content(html, source=url[:100])
    if not scan["safe"]:
        # We do NOT block — strip_dangerous_content sanitises and we
        # continue with the cleaned HTML. Old log said "Blocked" which
        # made it look like we dropped the article when we didn't.
        logger.info("Sanitised suspicious HTML from %s: %s", url[:80],
                    [t["type"] for t in scan["threats"]])
        html = strip_dangerous_content(html)

    # ── STRUCTURED EXTRACTION (replaces the old blob slice) ──
    # R-F719 (2026-05-19): wedge stack /data/wedge_stacks/wedge_675_1779182544.log
    # captured the main thread in _extract_structured_html doing regex
    # walks over large HTML payloads (re.finditer over <tr>/<td>/email
    # patterns on multi-hundred-KB articles). With R-F714 having killed
    # the persistence-path wedges, this researcher pipeline is the new
    # tallest tree — 6-9s circuit trips on absorb(web_search) during
    # research cycles trace here. Move the CPU-bound regex walk into a
    # worker thread; the surrounding httpx fetches stay on the loop.
    extracted = await asyncio.to_thread(_extract_structured_html, html)
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
    # R-F186 (2026-05-11) — expanded fallback to 11 languages to match
    # the web_search._detect_query_languages claim. Pre-R-F186 the
    # legacy researcher path was limited to 4 langs, so Turkish /
    # Russian / Chinese / Japanese / Korean / Hindi / German queries
    # silently skipped the language-specific Google News profiles.
    "tr": {"hl": "tr", "gl": "TR", "ceid": "TR:tr",
           "translate": {"defence procurement": "savunma tedarik",
                         "tender": "ihale", "contract": "sözleşme",
                         "armed forces": "silahlı kuvvetler", "ministry of defence": "savunma bakanlığı"}},
    "ru": {"hl": "ru", "gl": "RU", "ceid": "RU:ru",
           "translate": {"defence procurement": "оборонный заказ",
                         "tender": "тендер", "contract": "контракт",
                         "armed forces": "вооружённые силы", "ministry of defence": "министерство обороны"}},
    "zh": {"hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans",
           "translate": {"defence procurement": "国防采购",
                         "tender": "招标", "contract": "合同",
                         "armed forces": "武装部队", "ministry of defence": "国防部"}},
    "ja": {"hl": "ja", "gl": "JP", "ceid": "JP:ja",
           "translate": {"defence procurement": "防衛調達",
                         "tender": "入札", "contract": "契約",
                         "armed forces": "軍隊", "ministry of defence": "防衛省"}},
    "ko": {"hl": "ko", "gl": "KR", "ceid": "KR:ko",
           "translate": {"defence procurement": "국방조달",
                         "tender": "입찰", "contract": "계약",
                         "armed forces": "군대", "ministry of defence": "국방부"}},
    "hi": {"hl": "hi", "gl": "IN", "ceid": "IN:hi",
           "translate": {"defence procurement": "रक्षा खरीद",
                         "tender": "निविदा", "contract": "अनुबंध",
                         "armed forces": "सशस्त्र बल", "ministry of defence": "रक्षा मंत्रालय"}},
    "de": {"hl": "de", "gl": "DE", "ceid": "DE:de",
           "translate": {"defence procurement": "Rüstungsbeschaffung",
                         "tender": "Ausschreibung", "contract": "Vertrag",
                         "armed forces": "Streitkräfte", "ministry of defence": "Verteidigungsministerium"}},
    # R-W6 (2026-05-11): expanded from 11 → 40+ languages. Previously
    # the _COUNTRY_LANGS map promised codes (uk, ka, th, vi, id, ms, tl,
    # ur, bn, fa, sw, am, ti, nl, pl, it, etc.) that had no _LANG_PROFILES
    # entry — _translate_query silently no-op'd and queries went out in
    # English regardless. Each new entry has the same shape (hl/gl/ceid
    # + 5 defence translations). Locale codes verified against Google
    # News supported list.
    "uk": {"hl": "uk", "gl": "UA", "ceid": "UA:uk",
           "translate": {"defence procurement": "оборонні закупівлі",
                         "tender": "тендер", "contract": "контракт",
                         "armed forces": "збройні сили", "ministry of defence": "міністерство оборони"}},
    "pl": {"hl": "pl", "gl": "PL", "ceid": "PL:pl",
           "translate": {"defence procurement": "zamówienia obronne",
                         "tender": "przetarg", "contract": "kontrakt",
                         "armed forces": "siły zbrojne", "ministry of defence": "ministerstwo obrony"}},
    "it": {"hl": "it", "gl": "IT", "ceid": "IT:it",
           "translate": {"defence procurement": "appalti per la difesa",
                         "tender": "gara d'appalto", "contract": "contratto",
                         "armed forces": "forze armate", "ministry of defence": "ministero della difesa"}},
    "nl": {"hl": "nl", "gl": "NL", "ceid": "NL:nl",
           "translate": {"defence procurement": "defensie-aanbestedingen",
                         "tender": "aanbesteding", "contract": "contract",
                         "armed forces": "strijdkrachten", "ministry of defence": "ministerie van defensie"}},
    "sv": {"hl": "sv", "gl": "SE", "ceid": "SE:sv",
           "translate": {"defence procurement": "försvarsupphandling",
                         "tender": "upphandling", "contract": "kontrakt",
                         "armed forces": "försvarsmakten", "ministry of defence": "försvarsdepartementet"}},
    "no": {"hl": "no", "gl": "NO", "ceid": "NO:no",
           "translate": {"defence procurement": "forsvarsanskaffelse",
                         "tender": "anbud", "contract": "kontrakt",
                         "armed forces": "forsvaret", "ministry of defence": "forsvarsdepartementet"}},
    "da": {"hl": "da", "gl": "DK", "ceid": "DK:da",
           "translate": {"defence procurement": "forsvarsindkøb",
                         "tender": "udbud", "contract": "kontrakt",
                         "armed forces": "forsvaret", "ministry of defence": "forsvarsministeriet"}},
    "fi": {"hl": "fi", "gl": "FI", "ceid": "FI:fi",
           "translate": {"defence procurement": "puolustushankinta",
                         "tender": "tarjouspyyntö", "contract": "sopimus",
                         "armed forces": "puolustusvoimat", "ministry of defence": "puolustusministeriö"}},
    "cs": {"hl": "cs", "gl": "CZ", "ceid": "CZ:cs",
           "translate": {"defence procurement": "obranné zakázky",
                         "tender": "veřejná zakázka", "contract": "smlouva",
                         "armed forces": "ozbrojené síly", "ministry of defence": "ministerstvo obrany"}},
    "sk": {"hl": "sk", "gl": "SK", "ceid": "SK:sk",
           "translate": {"defence procurement": "obranné obstarávanie",
                         "tender": "verejná súťaž", "contract": "zmluva",
                         "armed forces": "ozbrojené sily", "ministry of defence": "ministerstvo obrany"}},
    "hu": {"hl": "hu", "gl": "HU", "ceid": "HU:hu",
           "translate": {"defence procurement": "védelmi beszerzés",
                         "tender": "pályázat", "contract": "szerződés",
                         "armed forces": "fegyveres erők", "ministry of defence": "honvédelmi minisztérium"}},
    "ro": {"hl": "ro", "gl": "RO", "ceid": "RO:ro",
           "translate": {"defence procurement": "achiziții pentru apărare",
                         "tender": "licitație", "contract": "contract",
                         "armed forces": "forțele armate", "ministry of defence": "ministerul apărării"}},
    "bg": {"hl": "bg", "gl": "BG", "ceid": "BG:bg",
           "translate": {"defence procurement": "отбранителни поръчки",
                         "tender": "обществена поръчка", "contract": "договор",
                         "armed forces": "въоръжени сили", "ministry of defence": "министерство на отбраната"}},
    "hr": {"hl": "hr", "gl": "HR", "ceid": "HR:hr",
           "translate": {"defence procurement": "javne nabave za obranu",
                         "tender": "natječaj", "contract": "ugovor",
                         "armed forces": "oružane snage", "ministry of defence": "ministarstvo obrane"}},
    "sr": {"hl": "sr", "gl": "RS", "ceid": "RS:sr",
           "translate": {"defence procurement": "одбрамбене набавке",
                         "tender": "тендер", "contract": "уговор",
                         "armed forces": "оружане снаге", "ministry of defence": "министарство одбране"}},
    "el": {"hl": "el", "gl": "GR", "ceid": "GR:el",
           "translate": {"defence procurement": "αμυντικές προμήθειες",
                         "tender": "διαγωνισμός", "contract": "σύμβαση",
                         "armed forces": "ένοπλες δυνάμεις", "ministry of defence": "υπουργείο εθνικής άμυνας"}},
    "he": {"hl": "iw", "gl": "IL", "ceid": "IL:iw",
           "translate": {"defence procurement": "רכש ביטחוני",
                         "tender": "מכרז", "contract": "חוזה",
                         "armed forces": "צה\"ל", "ministry of defence": "משרד הביטחון"}},
    "fa": {"hl": "fa", "gl": "IR", "ceid": "IR:fa",
           "translate": {"defence procurement": "تدارکات دفاعی",
                         "tender": "مناقصه", "contract": "قرارداد",
                         "armed forces": "نیروهای مسلح", "ministry of defence": "وزارت دفاع"}},
    "ur": {"hl": "ur", "gl": "PK", "ceid": "PK:ur",
           "translate": {"defence procurement": "دفاعی خریداری",
                         "tender": "ٹینڈر", "contract": "معاہدہ",
                         "armed forces": "مسلح افواج", "ministry of defence": "وزارت دفاع"}},
    "bn": {"hl": "bn", "gl": "BD", "ceid": "BD:bn",
           "translate": {"defence procurement": "প্রতিরক্ষা ক্রয়",
                         "tender": "দরপত্র", "contract": "চুক্তি",
                         "armed forces": "সশস্ত্র বাহিনী", "ministry of defence": "প্রতিরক্ষা মন্ত্রণালয়"}},
    "th": {"hl": "th", "gl": "TH", "ceid": "TH:th",
           "translate": {"defence procurement": "การจัดซื้อด้านกลาโหม",
                         "tender": "การประมูล", "contract": "สัญญา",
                         "armed forces": "กองทัพ", "ministry of defence": "กระทรวงกลาโหม"}},
    "vi": {"hl": "vi", "gl": "VN", "ceid": "VN:vi",
           "translate": {"defence procurement": "mua sắm quốc phòng",
                         "tender": "đấu thầu", "contract": "hợp đồng",
                         "armed forces": "lực lượng vũ trang", "ministry of defence": "bộ quốc phòng"}},
    "id": {"hl": "id", "gl": "ID", "ceid": "ID:id",
           "translate": {"defence procurement": "pengadaan pertahanan",
                         "tender": "tender", "contract": "kontrak",
                         "armed forces": "angkatan bersenjata", "ministry of defence": "kementerian pertahanan"}},
    "ms": {"hl": "ms", "gl": "MY", "ceid": "MY:ms",
           "translate": {"defence procurement": "perolehan pertahanan",
                         "tender": "tender", "contract": "kontrak",
                         "armed forces": "angkatan tentera", "ministry of defence": "kementerian pertahanan"}},
    "tl": {"hl": "tl", "gl": "PH", "ceid": "PH:tl",
           "translate": {"defence procurement": "pagkuha ng depensa",
                         "tender": "tender", "contract": "kontrata",
                         "armed forces": "sandatahang lakas", "ministry of defence": "kagawaran ng tanggulan"}},
    "sw": {"hl": "sw", "gl": "KE", "ceid": "KE:sw",
           "translate": {"defence procurement": "ununuzi wa ulinzi",
                         "tender": "zabuni", "contract": "mkataba",
                         "armed forces": "jeshi", "ministry of defence": "wizara ya ulinzi"}},
    "am": {"hl": "am", "gl": "ET", "ceid": "ET:am",
           "translate": {"defence procurement": "የመከላከያ ግዢ",
                         "tender": "ጨረታ", "contract": "ውል",
                         "armed forces": "የመከላከያ ኃይል", "ministry of defence": "የመከላከያ ሚኒስቴር"}},
    "ka": {"hl": "ka", "gl": "GE", "ceid": "GE:ka",
           "translate": {"defence procurement": "თავდაცვის შესყიდვები",
                         "tender": "ტენდერი", "contract": "ხელშეკრულება",
                         "armed forces": "შეიარაღებული ძალები", "ministry of defence": "თავდაცვის სამინისტრო"}},
    # Locale-only entries (translations left to English fallback) — Google
    # News still serves more relevant local content because of `hl/gl/ceid`
    # even if the query stays English.
    "lv": {"hl": "lv", "gl": "LV", "ceid": "LV:lv", "translate": {}},
    "lt": {"hl": "lt", "gl": "LT", "ceid": "LT:lt", "translate": {}},
    "et": {"hl": "et", "gl": "EE", "ceid": "EE:et", "translate": {}},
    "sl": {"hl": "sl", "gl": "SI", "ceid": "SI:sl", "translate": {}},
    "mk": {"hl": "mk", "gl": "MK", "ceid": "MK:mk", "translate": {}},
    "sq": {"hl": "sq", "gl": "AL", "ceid": "AL:sq", "translate": {}},
    "ti": {"hl": "ti", "gl": "ER", "ceid": "ER:ti", "translate": {}},
    "ps": {"hl": "ps", "gl": "AF", "ceid": "AF:ps", "translate": {}},
    "si": {"hl": "si", "gl": "LK", "ceid": "LK:si", "translate": {}},
    "ne": {"hl": "ne", "gl": "NP", "ceid": "NP:ne", "translate": {}},
    "my": {"hl": "my", "gl": "MM", "ceid": "MM:my", "translate": {}},
    "km": {"hl": "km", "gl": "KH", "ceid": "KH:km", "translate": {}},
    "lo": {"hl": "lo", "gl": "LA", "ceid": "LA:lo", "translate": {}},
    "bs": {"hl": "bs", "gl": "BA", "ceid": "BA:bs", "translate": {}},
    "is": {"hl": "is", "gl": "IS", "ceid": "IS:is", "translate": {}},
}

# Country → relevant languages to search in
# R-5002 (2026-05-11) — extended coverage. Was missing Panama, Russia,
# Turkey, China, Iran, Ukraine, and most of LatAm + East Asia / CIS.
# Operator's WhatsApp DD on lngtradinginternationalpanamasa.com missed
# Spanish-language press because "panama" wasn't in this map.
_COUNTRY_LANGS = {
    # Lusophone
    "angola": ["pt"], "mozambique": ["pt"], "guinea-bissau": ["pt"],
    "cape verde": ["pt"], "brazil": ["pt"], "portugal": ["pt"],
    "são tomé": ["pt"], "east timor": ["pt"], "timor-leste": ["pt"],
    "macau": ["pt", "zh"],
    # Francophone
    "senegal": ["fr"], "mali": ["fr"], "burkina faso": ["fr"],
    "niger": ["fr"], "chad": ["fr"], "ivory coast": ["fr"],
    "côte d'ivoire": ["fr"], "cameroon": ["fr"], "morocco": ["fr", "ar"],
    "algeria": ["fr", "ar"], "tunisia": ["fr", "ar"],
    "france": ["fr"], "belgium": ["fr"], "switzerland": ["fr", "de"],
    "luxembourg": ["fr"], "djibouti": ["fr", "ar"],
    "madagascar": ["fr"], "rwanda": ["fr"], "burundi": ["fr"],
    "togo": ["fr"], "benin": ["fr"], "guinea": ["fr"],
    "congo": ["fr"], "democratic republic of congo": ["fr"],
    "central african republic": ["fr"], "mauritania": ["fr", "ar"],
    "haiti": ["fr"], "quebec": ["fr"],
    # Hispanophone (LatAm + Spain — was severely under-covered)
    "spain": ["es"], "colombia": ["es"], "peru": ["es"], "mexico": ["es"],
    "venezuela": ["es"], "panama": ["es"], "argentina": ["es"],
    "chile": ["es"], "ecuador": ["es"], "bolivia": ["es"],
    "paraguay": ["es"], "uruguay": ["es"], "costa rica": ["es"],
    "honduras": ["es"], "guatemala": ["es"], "nicaragua": ["es"],
    "cuba": ["es"], "dominican republic": ["es"], "el salvador": ["es"],
    "puerto rico": ["es"], "equatorial guinea": ["es", "pt"],
    # Arabic-speaking
    "egypt": ["ar"], "saudi arabia": ["ar"], "uae": ["ar"], "iraq": ["ar"],
    "jordan": ["ar"], "lebanon": ["ar", "fr"], "libya": ["ar"],
    "yemen": ["ar"], "syria": ["ar"], "qatar": ["ar"], "bahrain": ["ar"],
    "oman": ["ar"], "kuwait": ["ar"], "palestine": ["ar"],
    "sudan": ["ar"], "somalia": ["ar"],
    # CIS / Russophone
    "russia": ["ru"], "belarus": ["ru"], "kazakhstan": ["ru"],
    "kyrgyzstan": ["ru"], "tajikistan": ["ru"], "turkmenistan": ["ru"],
    "uzbekistan": ["ru"], "armenia": ["ru"], "azerbaijan": ["ru"],
    "moldova": ["ru", "ro"], "ukraine": ["uk", "ru"],
    "georgia": ["ka", "ru"],
    # East Asia
    "china": ["zh"], "taiwan": ["zh"], "hong kong": ["zh"],
    "singapore": ["zh"],
    "japan": ["ja"], "korea": ["ko"], "south korea": ["ko"],
    "north korea": ["ko"],
    # South / Southeast Asia
    "thailand": ["th"], "vietnam": ["vi"], "indonesia": ["id"],
    "malaysia": ["ms"], "philippines": ["tl"],
    "myanmar": ["my"], "cambodia": ["km"], "laos": ["lo"],
    # South Asia
    "india": ["hi"], "pakistan": ["ur"], "bangladesh": ["bn"],
    "sri lanka": ["si"], "nepal": ["ne"],
    # Turkic
    "turkey": ["tr"], "türkiye": ["tr"],
    # Persian
    "iran": ["fa"], "afghanistan": ["fa", "ps"],
    # Sub-Saharan Africa (English-second languages also tracked)
    "ethiopia": ["am"], "eritrea": ["ti"], "tanzania": ["sw"],
    "kenya": ["sw"], "uganda": ["sw"],
    # Central / Northern Europe
    "germany": ["de"], "austria": ["de"], "netherlands": ["nl"],
    "poland": ["pl"], "czech republic": ["cs"], "slovakia": ["sk"],
    "hungary": ["hu"], "romania": ["ro"], "bulgaria": ["bg"],
    "greece": ["el"], "serbia": ["sr"], "croatia": ["hr"],
    "slovenia": ["sl"], "bosnia": ["bs"], "montenegro": ["sr"],
    "macedonia": ["mk"], "albania": ["sq"],
    "italy": ["it"], "denmark": ["da"], "sweden": ["sv"],
    "norway": ["no"], "finland": ["fi"], "iceland": ["is"],
    "estonia": ["et"], "latvia": ["lv"], "lithuania": ["lt"],
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


async def _query_internal_index(query: str) -> list[dict]:
    """R-F504 (2026-05-14) — query ARIA's own curated search index.

    The internal index is the independence path: when external backends
    are circuit-open (Brave key issue 2026-05-10) or homograph-poisoned
    (Modirum-Gespi failure 2026-05-14 where academic Semantic Scholar
    dominated 20/20 results), the curated corpus carries the load.

    Returns the same dict shape as the external `_web_search` branch so
    callers can merge them transparently. Empty list on any error —
    crawler bootstrap, missing index, or db not connected.
    """
    try:
        from aria_service.search_engine import internal_search as _isi
        hits = await _isi.search(query, max_results=15,
                                 language=None, min_credibility=6)
    except Exception as e:
        logger.debug("internal index query failed for %r: %s", query[:60], e)
        return []
    out: list[dict] = []
    for h in hits or []:
        out.append({
            "title": h.get("title") or "",
            "link": h.get("url") or "",
            "snippet": h.get("snippet") or "",
            "source": h.get("source") or "aria_internal_index",
            "_credibility_tier": h.get("credibility_tier"),
            "_relevance_score": h.get("relevance_score"),
            "_language": h.get("language") or None,
            "_internal": True,
        })
    return out


async def _web_search(
    query: str, timeout: float = 10.0, *, raise_on_timeout: bool = False,
    screening: bool = False,
) -> list[dict]:
    """ARIA's independent multi-backend web search.

    Uses ARIA's own search engine (web_search.py) which queries multiple
    backends in parallel (Brave, SearXNG, Google News, Bing News),
    deduplicates, applies credibility scoring, and returns ranked results.

    R-F504 (2026-05-14): also queries ARIA's curated internal index
    (aria_service.search_engine.internal_search) in parallel and merges
    results — dedup by URL, internal hits preferred on tie. This closes
    the "Brave is OPEN → academic noise dominates" failure mode.

    Falls back to legacy Google News RSS if both engines fail.

    INDEPENDENCE: ARIA does not depend on any single search provider.
    If Brave is down, SearXNG covers. If SearXNG is down, internal index
    covers. If all external fail, internal index still answers from
    curated corpus. ARIA always returns results.
    """
    try:
        from . import web_search as ws

        # Detect languages for multilingual search
        extra_langs = _detect_target_languages(query)
        languages = ["en"] + list(extra_langs)

        # R-F504: query both ARIA's external multi-backend search AND
        # her own curated internal index in parallel. Both are async +
        # independent — gather lets the slower one not block the other.
        # R-F2846 — screening callers skip the 94s cross-encoder re-rank.
        ext_task = ws.search_multilingual(
            query, languages=languages, max_results=30, screening=screening,
        )
        int_task = _query_internal_index(query)
        # R-F2832 — BOUND THE PRIMARY PATH. This gather had no wait_for and no
        # asyncio.timeout, so the `timeout` argument above was a FALSE CONTRACT:
        # applied only to the legacy/RSS fallbacks below, never to the path that
        # actually runs. Measured against real backends (P2-G, 2026-07-21) on five
        # adverse-media queries: 36.07 / 45.00 / 52.90s (min/median/max, mean
        # 44.01s) for a DECLARED 10s — 5/5 calls over, by 3.6x-5.3x.
        #
        # Nine production call sites depend on this timeout (deep_researcher.py x6,
        # researcher.py x3) and eight do not even pass one, trusting the 10s default.
        #
        # Why it discards evidence: run_adverse_media_deep_search checks its deadline
        # BEFORE each template and then runs one unbounded search to completion, so
        # 180s budget + 52.9s overrun = 232.9s > the 210s wait_for backstop. The
        # backstop fires and the PARTIAL findings are thrown away. Bounding this call
        # makes the honest-partial path reachable — it raises COVERAGE, it does not
        # relax any threshold.
        try:
            ext_raw, internal_results = await asyncio.wait_for(
                asyncio.gather(ext_task, int_task, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[R-F2832] web search exceeded its %.1fs budget for %r — "
                "backends did not answer in time", timeout, query[:80],
            )
            # HONESTY: a timed-out search must be distinguishable from one that ran
            # and found nothing. run_adverse_media_deep_search counts
            # `_templates_searched` only for calls that RETURN, and accounts a raised
            # exception as a breaker skip. If a timeout returned [] here, a sweep in
            # which every backend timed out would report 30/30 templates searched
            # with zero findings — indistinguishable from a genuinely clean entity,
            # which is the exact false clean R-F2791 exists to prevent.
            #
            # But only 1 of 5 sampled call sites has a local try/except, so raising
            # unconditionally would convert a slow search into an outage across the
            # research stack. Callers whose accounting depends on the distinction
            # opt in; everyone else is merely BOUNDED, which is the robustness win.
            if raise_on_timeout:
                raise
            ext_raw, internal_results = [], []
        if isinstance(ext_raw, Exception):
            logger.warning("external search_multilingual raised: %s",
                           ext_raw)
            ext_raw = []
        if isinstance(internal_results, Exception):
            internal_results = []

        # Convert SearchResult objects to the dict format the rest of
        # the pipeline expects (title, link, snippet, source)
        ext_results: list[dict] = []
        for r in ext_raw or []:
            ext_results.append({
                "title": r.title,
                "link": r.url,
                "snippet": r.snippet,
                "source": r.source,
                "_credibility_tier": r.credibility_tier,
                "_relevance_score": r.relevance_score,
                "_language": r.language if r.language != "en" else None,
            })

        # Merge with the internal index hits. Dedupe by URL — when both
        # branches have the same URL, keep the internal entry because
        # it has fetched body content and known source_tier metadata.
        results: list[dict] = []
        seen: set[str] = set()
        for r in internal_results:
            link = (r.get("link") or "").strip()
            if not link or link in seen:
                continue
            seen.add(link)
            results.append(r)
        for r in ext_results:
            link = (r.get("link") or "").strip()
            if not link or link in seen:
                continue
            seen.add(link)
            results.append(r)

        # R-F507 (2026-05-14) — let discovered domains flow into the
        # index registry. Every external URL whose domain ARIA doesn't
        # already know lands at tier 4 ("discovered") so the next
        # crawl cycle can index it. Pay-once-remember-forever per
        # [[feedback_pay_once_remember_forever]].
        try:
            from aria_service.crawler.on_demand import (
                auto_register_domain, looks_like_entity_query,
                background_ensure,
            )
            from aria_service.crawler.politeness import domain_of as _dom
            _seen_doms: set[str] = set()
            for r in ext_results:
                d = _dom(r.get("link") or "")
                if d and d not in _seen_doms:
                    _seen_doms.add(d)
                    try:
                        await auto_register_domain(d)
                    except Exception:
                        pass
            # If the internal index returned NOTHING for this query and
            # the query looks like an entity worth researching, fire a
            # bounded background crawl. Future queries for the same
            # entity hit warm.
            if not internal_results and looks_like_entity_query(query):
                try:
                    asyncio.create_task(background_ensure(query))
                except Exception:
                    pass
        except Exception as _exc:
            logger.debug("R-F507 auto-register/background_ensure path "
                         "failed (non-fatal): %s", _exc)

        if results:
            ext_count = sum(1 for r in results if not r.get("_internal"))
            int_count = sum(1 for r in results if r.get("_internal"))
            logger.info(
                "ARIA search: %d results for %r (external=%d, internal=%d)",
                len(results), query[:60], ext_count, int_count,
            )
            return results

    except asyncio.TimeoutError:
        # R-F2832 — must escape. Falling through to the RSS fallback below would
        # spend MORE unbounded time after we already blew the budget, and would
        # convert a timeout into a silent empty result at the caller.
        raise
    except Exception as e:
        logger.warning("ARIA search engine failed, falling back to Google News RSS: %s", e)

    # ── R-F1597: DDG fallback BEFORE the legacy Google-News-only path ──
    # The multi-backend engine above (web_search.py) leans on backends that
    # are dead/gated in this deployment: _search_brave returns [] (no key —
    # Brave declined, §18), _search_searxng returns [] (no instances). So a
    # DD on a foreign company (e.g. deltaguard.org) got ZERO external results
    # even though the WORKING, breaker-free DDG search in web_search() returns
    # hits (verified live: 5 results). Route the DD search through it before
    # giving up. Operator 2026-06-15: "you could fetch adverse media many ways
    # but you are not bringing any results." Additive — only fires when the
    # primary path returned nothing.
    try:
        _ddg = await web_search(query, max_results=20, timeout=timeout)
        _items = (_ddg or {}).get("results") or []
        _conv = [
            {
                "title": it.get("title", ""),
                "link": it.get("url") or it.get("link") or "",
                "snippet": it.get("snippet") or it.get("body") or "",
                "source": it.get("source") or (_ddg or {}).get("provider") or "duckduckgo",
            }
            for it in _items
            if (it.get("url") or it.get("link"))
        ]
        if _conv:
            logger.info("R-F1597: DDG fallback returned %d results for %r",
                        len(_conv), query[:60])
            return _conv
    except Exception as _ddg_err:
        logger.debug("R-F1597 DDG fallback failed (non-fatal): %s", _ddg_err)

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
            # F93 fix 2026-04-29: bumped 2000 → 4000. Schema has 10
            # nested arrays (entities/products/countries/classifications/
            # licensing/EUC/offsets/sanctions/diversion/re-export/facts);
            # 2000 truncated DeepSeek mid-array ~char 7919, dropping a
            # comma between adjacent objects. Live evidence
            # 2026-04-29 15:08-15:09: 3 consecutive failures on the same
            # 17-chunk email document → "Document read: ... → 0 facts"
            # (91 seconds wasted). 4000 fits the full schema with
            # margin.
            max_tokens=4000,
            timeout=90.0,
        )
        # F93 fix 2026-04-29: switch from raw json.loads() to the
        # multi-strategy parse_llm_json. _analyse_article already uses
        # this for the same reason; the parallel compliance path was
        # never migrated. parse_llm_json now also handles missing-
        # comma-between-adjacent-objects (Strategy 6) which is the
        # specific failure mode this function was hitting.
        from .llm_json import parse_llm_json
        parsed = parse_llm_json(result.text, source='researcher')
        if parsed is not None:
            return parsed
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

    # R-F2769 — route article fact-extraction (the DD token bulk: up to ~24 calls
    # per run) to the CHEAP Claude model (Haiku) via the model-routing policy.
    # A non-Claude provider ignores a claude id, so this is a safe no-op until the
    # switch flips; post-switch it roughly halves the per-DD Claude cost. Extraction
    # is a mechanical high-recall task Haiku handles well.
    try:
        from ..llm import tier_router as _tr2769
        _extract_model = _tr2769.claude_model_for_intent("research_extraction")
    except Exception:
        _extract_model = ""
    try:
        # R-F2914 — attribute this spend: it is the article-extraction bulk
        # (~24 calls/run) and was landing in the 74% "uncategorized" bucket.
        from . import cost_tracker as _ct2914
        with _ct2914.feature("research_extraction"):
            result = await llm.complete(
                "You are ARIA — a global defence procurement intelligence analyst. "
                "EXTRACT EXHAUSTIVELY. A senior analyst extracts 10-20 facts from a "
                "substantive page, not 1-2. Be specific: names, amounts, dates, "
                "countries, contract IDs. Rigorous confidence levels. Return strict JSON.",
                extract_prompt,
                max_tokens=3000,  # bumped from 1500 to fit ~15-20 facts
                timeout=90.0,
                model=_extract_model,   # R-F2769 — Haiku (ignored by non-Claude)
            )
        # Use the multi-strategy LLM JSON repair instead of plain json.loads.
        # The al-monitor.com Iran-FM article observed 2026-04-27 fails every
        # spider re-run with "Expecting ',' delimiter" because Claude inlines
        # an unescaped quote from the article body into the JSON output. The
        # plain parse silently returns None, the spider re-tries the same URL
        # next pass, and the LLM call burns budget every cycle. Repair pipeline
        # handles control-char escapes, unquoted keys, single quotes,
        # truncation, and trailing commas before nuclear-stripping.
        from .llm_json import parse_llm_json
        parsed = parse_llm_json(result.text, source='researcher')
        if parsed is not None:
            return parsed
    except Exception as e:
        logger.warning(f"Article analysis failed: {e}")
    return None


async def _process_analysis(parsed: dict, source: str, hypotheses: list[dict]) -> tuple[int, int]:
    """Process LLM analysis — store facts and update hypotheses. Returns (facts_learned, hyp_generated)."""
    facts_learned = 0
    hyp_generated = 0

    if parsed.get("skip"):
        return 0, 0

    # Collect all facts from this article so we can batch the RAG
    # encode pass (F23 + F24 fix 2026-04-27 — was 14 separate model.encode
    # calls per article from store_fact's per-fact rag_store.ingest_fact
    # tail. F23 added add_facts_batch but the F24 audit caught that
    # store_fact still ran ingest_fact too, doubling the work. Now we
    # pass skip_rag_ingest=True so store_fact does dedup/contradiction
    # detection only, and a single add_facts_batch call handles RAG.).
    rag_batch: list[dict] = []
    # F83 2026-04-29: parallel accumulator for the in-memory semantic
    # index — paired with store_fact(skip_semantic_index=True) below
    # so the per-fact model.encode tail is collapsed into one batch
    # call at the end of the loop. Was 15 separate "Batches: 1/1" log
    # lines per article; now one.
    semantic_batch: list[tuple[str, str, dict | None]] = []
    for fact in (parsed.get("facts") or []):
        topic = fact.get("topic", "")
        content = fact.get("content", "")
        confidence = fact.get("confidence", "ASSESSED")
        if topic and content and len(content) > 20:
            sf_result = await store_fact(
                topic,
                f"{content} [Source: {source}]",
                f"research:{source}",
                confidence,
                skip_rag_ingest=True,
                skip_semantic_index=True,
            )
            facts_learned += 1
            rag_batch.append({
                "topic": topic,
                "content": f"{content} [Source: {source}]",
                "confidence": confidence,
                "source": f"research:{source}",
            })
            fid = (sf_result or {}).get("fact_id")
            if fid:
                semantic_batch.append((
                    fid,
                    f"{topic} {content}",
                    {"confidence": confidence},
                ))
    if rag_batch:
        try:
            from . import rag_store as _rag
            await _rag.add_facts_batch(rag_batch)
        except Exception as e:
            logger.debug("rag_store.add_facts_batch failed: %s", e)
    if semantic_batch:
        try:
            from . import semantic_search as _ss
            import asyncio as _aio
            # to_thread because index_facts_batch runs sync model.encode
            # which holds the GIL; matches the per-fact path's offload.
            await _aio.to_thread(_ss.index_facts_batch, semantic_batch)
        except Exception as e:
            logger.debug("semantic_search.index_facts_batch failed: %s", e)

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

    # ── R-F3295 — THE MODEL DECIDES THIS SHAPE, SO IT CANNOT BE ASSUMED ──────
    #
    # `validates` / `challenges` come straight out of the LLM's article analysis
    # and were used as strings. When the model answers with a LIST — an entirely
    # ordinary reply to "which hypotheses does this validate?" — `.lower()` raised
    #     AttributeError: 'list' object has no attribute 'lower'
    #
    # This runs in investigate()'s ARTICLE LOOP, before synthesis, so the error
    # escaped investigate() and dd_orchestrator.py:6692 turned it into a data-gap
    # string, discarding every article read and every fact learned. Live on the
    # AZURE PARKING LTD DD: the gap present, articles_read and facts_learned
    # absent, and the digital layer contributing nothing.
    #
    # Coerced rather than skipped. Dropping non-str input would stop the crash and
    # silently lose every hypothesis match, trading a loud failure for a quiet one.
    def _as_phrases(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            return [v for v in value if isinstance(v, str) and v.strip()]
        return []

    for phrase in _as_phrases(parsed.get("validates")):
        for h in hypotheses:
            if phrase.lower() in str(h.get("hypothesis", "")).lower():
                h["evidence_count"] = h.get("evidence_count", 0) + 1
                if h["evidence_count"] >= 3:
                    h["status"] = "STRENGTHENED"

    for phrase in _as_phrases(parsed.get("challenges")):
        for h in hypotheses:
            if phrase.lower() in str(h.get("hypothesis", "")).lower():
                h["status"] = "CHALLENGED"

    return facts_learned, hyp_generated


# ── Public: Web search (Brave API preferred, DuckDuckGo HTML fallback) ──────

@fail_wire(module="researcher", gap_type="source_failure")
async def web_search(query: str, max_results: int = 8, timeout: float = 15.0) -> dict:
    """Perform a web search and return structured results.

    R-F1660 (2026-06-18) — Brave provider REMOVED. Operator sovereignty
    directive ("no Brave API... not an option"; ARIA must be independent,
    not add third-party dependencies — see memory aria_sovereignty_no_new
    _dependencies). The Brave block was vestigial anyway (no key set; the
    web_search.py _search_brave is already a permanent R-F320 stub). This
    function now goes straight to the keyless backends.

    Tries providers in order:
      1. DuckDuckGo HTML scraping (free, no key)
      2. _multi_backend_fallback (Google News / Bing News RSS, all keyless)

    Returns:
      {
        "ok": bool,
        "query": str,
        "provider": "ddg" | "none",
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
    # Cap at 200 chars (not 300). Brave's ~400-char hard limit returns
    # HTTP 422; Semantic Scholar tolerates more but 429s under load.
    # 200 chars is the safe ceiling — beyond that the query is almost
    # certainly a hypothesis leak from upstream. Log upstream leaks so
    # we can chase the root cause instead of silently truncating.
    # Added 2026-04-21 after operator flagged the same class of bug as
    # "generate digest → deep_research with whole prompt as entity".
    raw_len = len(query.strip())
    query = query.strip()
    if raw_len > 200:
        logger.warning(
            "web_search upstream leak — query was %d chars, truncating to 200 "
            "at word boundary. Caller should have pre-chunked: %r",
            raw_len, query[:60],
        )
        # Word-boundary truncation — don't slice mid-token and create garbage
        truncated = query[:200].rsplit(" ", 1)[0] if " " in query[:200] else query[:200]
        query = truncated

    # ── R-F1660: Brave provider removed (sovereignty — no third-party
    # search API). Straight to the keyless backends below.
    logger.info("web_search ENTRY query=%r", query[:80])

    # ── Provider 1: DuckDuckGo HTML scraping ──────────────────────────
    # Free, no key required. The HTML endpoint at html.duckduckgo.com
    # returns standard HTML with anchor tags we can parse out. Fragile
    # against DDG layout changes but works as of 2026-04-09.
    # R-F1790: this raw DDG path had NO circuit breaker — only
    # web_search._search_duckduckgo was protected. Share the SAME breaker
    # name so both DDG paths back off together when DDG rate-limits (202/429).
    from .circuit_breaker import get_breaker as _get_breaker
    _ddg_cb = _get_breaker("search:duckduckgo", failure_threshold=5, cooldown_seconds=600)
    if _ddg_cb.is_open():
        logger.info("web_search DDG breaker OPEN — skipping to multi-backend for %r", query[:80])
        return await _multi_backend_fallback(query, max_results, t0, reason="ddg_breaker_open")
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
                # R-F1790: record the failure so the shared DDG breaker trips.
                _ddg_cb.record_failure(reason="rate_limit" if resp.status_code in (202, 429) else "server")
                logger.warning(
                    "web_search DDG returned HTTP %d for %r — falling through to multi-backend",
                    resp.status_code, query[:80],
                )
                return await _multi_backend_fallback(query, max_results, t0,
                                                    reason=f"ddg_http_{resp.status_code}")
            _ddg_cb.record_success()  # R-F1790: 200 = backend healthy
            html = resp.text
    except Exception as e:
        _ddg_cb.record_failure(reason="timeout")  # R-F1790
        logger.warning(
            "web_search DDG fetch failed for %r: %s — falling through to multi-backend",
            query[:80], e,
        )
        return await _multi_backend_fallback(query, max_results, t0,
                                            reason=f"ddg_exception:{str(e)[:80]}")

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

    if not results:
        # DDG parsed clean but returned zero results — try the multi-
        # backend search engine before giving up. Brave + SearXNG +
        # Google News + Bing News in parallel beats a single-source zero.
        logger.info(
            "web_search DDG parsed zero results for %r — trying multi-backend",
            query[:80],
        )
        return await _multi_backend_fallback(query, max_results, t0,
                                            reason="ddg_zero_results")

    return {
        "ok": True, "query": query, "provider": "ddg",
        "results": results,
        "duration_ms": int((time.time() - t0) * 1000),
    }


async def _multi_backend_fallback(
    query: str, max_results: int, t0: float, *, reason: str = "",
) -> dict:
    """Last-resort multi-backend fallback for web_search.

    Calls intel.web_search.search() which queries Brave + SearXNG +
    Google News in parallel, deduplicates, and applies tier scoring.
    Used when researcher.web_search's primary providers (Brave + DDG)
    both fail or return empty. This is the wire that keeps the rich
    multi-backend engine actually used in production.
    """
    try:
        from . import web_search as _ws
        results = await _ws.search(query, max_results=max_results, min_credibility=6)
        return {
            "ok": bool(results),
            "query": query,
            "provider": f"multi_backend (after {reason})" if reason else "multi_backend",
            "results": [
                {"title": r.title, "url": r.url, "snippet": r.snippet}
                for r in results
            ],
            "duration_ms": int((time.time() - t0) * 1000),
            "fallback_reason": reason,
        }
    except Exception as e:
        logger.warning("multi_backend fallback failed for %r: %s", query[:80], e)
        return {
            "ok": False, "query": query,
            "provider": f"multi_backend_failed (after {reason})",
            "results": [],
            "error": f"All providers exhausted. Last error: {str(e)[:200]}",
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


@fail_wire(module="researcher", gap_type="source_failure")
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

    # R-F329 (2026-05-11): strip question-modifier suffixes from the
    # entity phrase before templating angles. Live failure 21:55 — the
    # operator asked "investigate Modirum Gespi people and network" and
    # the chat handler passed entity="Modirum Gespi people and network",
    # so every angle became "Modirum Gespi people and network <suffix>"
    # — no search engine indexes that phrase. Now we detect tail-end
    # question modifiers and split them into (entity, intent_hint).
    _question_modifier_tail_re = re.compile(
        r"\b("
        r"people\s+(?:and\s+network|and\s+leadership|and\s+directors|"
        r"and\s+officers|and\s+management)"
        r"|directors?(?:\s+and\s+(?:officers|leadership|management))?"
        r"|leadership(?:\s+team)?"
        r"|officers?"
        r"|management(?:\s+team)?"
        r"|board(?:\s+of\s+directors)?"
        r"|ubo|beneficial\s+owners?"
        r"|shareholders?"
        r"|owners?"
        r"|founders?"
        r"|executives?"
        r"|key\s+personnel"
        r"|c-suite"
        r"|key\s+staff"
        r"|network"
        r")\s*$",
        re.IGNORECASE,
    )
    _intent_hint = ""
    _entity_stripped = entity
    _tail_match = _question_modifier_tail_re.search(_entity_stripped)
    if _tail_match:
        _intent_hint = _tail_match.group(1).lower().strip()
        _entity_stripped = (
            _entity_stripped[:_tail_match.start()].strip()
        ).rstrip(",;:- ")
        if _entity_stripped:
            logger.info(
                "R-F329: split entity %r → entity=%r + intent=%r",
                entity[:80], _entity_stripped[:80], _intent_hint,
            )
            entity = _entity_stripped

    logger.info(
        "deep_research ENTRY entity=%r primary_url=%r max_queries=%d max_extracts=%d intent_hint=%r",
        entity[:80], primary_url[:120] if primary_url else "", max_queries, max_extracts, _intent_hint,
    )

    # ── Step 0: FREE memory-first recall from prior Brave Answers ──────
    # Added 2026-04-21. Every Brave Answers call writes to rag_store with
    # source_type="brave_answer" (see intel/brave_answers.py). A semantic
    # lookup here surfaces prior answers about this entity into the
    # current investigation without a paid API call. Over time ARIA's
    # Brave corpus becomes a free acceleration layer — the super-AI
    # "remembers everything" doctrine: pay once, recall forever.
    #
    # Non-fatal: if rag_store is unreachable or empty, deep_research
    # continues with the normal flow unchanged.
    prior_brave_knowledge: list[dict] = []
    try:
        from . import rag_store as _rag
        _prior = await _rag.search(entity, top_k=3, source_type="brave_answer")
        for h in (_prior or []):
            if float(h.get("similarity") or 0.0) >= 0.70:
                prior_brave_knowledge.append({
                    "text": h.get("text", ""),
                    "similarity": h.get("similarity"),
                    "ingested_at": h.get("ingested_at"),
                    "source_id": h.get("source"),
                })
        if prior_brave_knowledge:
            logger.info(
                "deep_research memory-first hit: %d prior Brave Answers for entity=%r",
                len(prior_brave_knowledge), entity[:60],
            )
    except Exception as e:
        logger.debug("deep_research memory-first RAG lookup failed: %s", e)

    # ── Step 1: build a small set of search angles ────────────────────
    # Each angle surfaces a different facet of the OSINT surface. Order
    # matters — earlier queries are more important if we have to truncate.
    _base_angles: list[str] = [
        entity,                                    # generic discovery
        f"{entity} company",                       # corporate identity
        f"{entity} headquarters location",         # jurisdiction
        f"{entity} directors leadership",          # people
        f"{entity} news",                          # recent activity
    ]

    # ── R-F2426: adverse-media / sanctions angles ─────────────────────
    # Root cause of the adverse-media 0.0-grounding population: the base
    # angles above are all CORPORATE-generic (company / HQ / directors /
    # news). For a query like "Wagner Group adverse media" the retrieval
    # never runs the query that actually surfaces the evidence — proven
    # live: "Wagner Group headquarters location" / "… directors leadership"
    # return corporate noise, while "Wagner Group adverse media war crimes"
    # returns Europol / OFAC / news sources. The sanctions/adverse facet
    # of the OSINT surface was structurally unqueried. When adverse-media /
    # sanctions intent is signalled (in the entity phrase itself — the chat
    # handler folds "adverse media"/"sanctions" into the deep_research
    # entity — or, when the flag forces it, for every run), add targeted
    # angles built from the CLEANED entity (adverse nouns stripped so the
    # angle isn't "<entity> adverse media sanctions OFAC"). Env-gated
    # (default OFF → base angles byte-for-byte unchanged); best-effort.
    _adverse_terms = (
        "adverse media", "adverse", "sanction", "sanctioned", "ofac", "ofsi",
        "war crime", "human rights", "corruption", "bribery", "fraud",
        "money laundering", "laundering", "terrorism", "terrorist",
        "investigation", "allegation", "misconduct", "controversy",
        "designated", "designation", "criminal", "lawsuit", "litigation",
    )
    _adverse_flag = (os.getenv("ARIA_DEEP_RESEARCH_ADVERSE_ANGLES", "") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    _entity_low = entity.lower()
    _adverse_signalled = any(t in _entity_low for t in _adverse_terms)
    if _adverse_flag:
        # Strip adverse nouns from the entity so the targeted angles carry a
        # clean subject (e.g. "Wagner Group adverse media" → "Wagner Group").
        _clean = entity
        for _t in ("adverse media", "adverse-media", "adverse", "sanctions",
                   "sanction", "sanctioned", "ofac", "ofsi", "screening",
                   "war crimes", "human rights", "allegations", "investigation"):
            _clean = re.sub(rf"\b{re.escape(_t)}\b", " ", _clean, flags=re.IGNORECASE)
        _clean = re.sub(r"\s{2,}", " ", _clean).strip(" .,:;-")
        if not _clean or len(_clean) < 2:
            _clean = entity
        _adverse_angles = [
            f'"{_clean}" sanctions OFAC EU designation',
            f'"{_clean}" adverse media allegations',
            f'"{_clean}" investigation OR lawsuit OR fraud OR corruption OR "war crimes"',
        ]
        if _adverse_signalled:
            # The entity phrase IS an adverse/sanctions request — these are the
            # discovery angles; PREPEND so they survive max_queries truncation.
            _base_angles = _adverse_angles + _base_angles
        else:
            # Benign lookup with the flag on — APPEND so the corporate angles
            # keep priority within the query budget (adverse angles are a
            # supplementary DD-doctrine sweep, not the point of the query).
            _base_angles = _base_angles + _adverse_angles
        logger.info(
            "R-F2426: adverse-media angles added (signalled=%s, position=%s) "
            "for entity=%r → %r",
            _adverse_signalled, "prepend" if _adverse_signalled else "append",
            entity[:80], _adverse_angles,
        )

    # R-F331 (2026-05-11): when the intent hint signals a people /
    # network / officers question, add LinkedIn site-restricted angles.
    # These are the highest-yield queries for people data — LinkedIn
    # company profile + People tab is where real organisational
    # structure lives. Surface web search of `site:linkedin.com X` is
    # public and free.
    _people_intents = (
        "people", "network", "directors", "officers", "leadership",
        "management", "board", "ubo", "shareholders", "owners",
        "founders", "executives", "key personnel", "c-suite", "key staff",
        "beneficial",
    )
    if any(_kw in _intent_hint for _kw in _people_intents):
        _base_angles.extend([
            f"site:linkedin.com/company {entity}",
            f"site:linkedin.com/in {entity} director OR CEO OR founder",
            f"{entity} CEO OR founder",
            f"{entity} board of directors",
        ])
        logger.info(
            "R-F331: people-intent detected (%r) — adding LinkedIn + "
            "people-specific angles", _intent_hint,
        )

    # R-F330 (2026-05-11): memory-first query expansion. Before issuing
    # the templated angles, query RAG for what we ALREADY know about
    # this entity (CEO names, parent company, predecessor / rebrand,
    # subsidiaries). Append those as additional search angles so the
    # 21:55 "we knew Ocellott + Elias Silvola from the 21:48 run but
    # didn't use them at 21:55" gap closes — pay-once-remember-forever
    # extended to query generation.
    _memory_expansions: list[str] = []
    try:
        from . import rag_store as _rag_ex
        _mem_hits = await _rag_ex.search(entity, top_k=5)
        _seen_terms: set[str] = set()
        for _h in _mem_hits or []:
            _text = (_h.get("text") or "")[:1500]
            if not _text:
                continue
            # Extract people-name candidates (CamelCase 2+ words)
            for _m in re.finditer(
                r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z'\-]+){1,3})\b", _text,
            ):
                _name = _m.group(1).strip()
                # Filter common stopwords / dates / non-names
                if len(_name) < 6 or len(_name) > 60:
                    continue
                if _name.lower() in _seen_terms:
                    continue
                if _name.split()[0].lower() in {
                    "the", "this", "modirum", "north", "south", "east", "west",
                    "united", "european", "russian", "chinese", "indian",
                    "africa", "asia", "europe", "january", "february", "march",
                    "april", "may", "june", "july", "august", "september",
                    "october", "november", "december",
                }:
                    continue
                # Skip if name is a substring of the entity (no new info)
                if _name.lower() in entity.lower():
                    continue
                _seen_terms.add(_name.lower())
                _memory_expansions.append(f"{entity} {_name}")
                if len(_memory_expansions) >= 3:
                    break
            if len(_memory_expansions) >= 3:
                break
        if _memory_expansions:
            logger.info(
                "R-F330: memory-first query expansion added %d angles "
                "from RAG: %r",
                len(_memory_expansions),
                [a[:80] for a in _memory_expansions],
            )
    except Exception as _mexp_e:
        logger.debug("R-F330 memory expansion failed: %s", _mexp_e)

    # R-F766 (2026-05-20) — entity-name variant fanout. Live bug
    # 2026-05-20 transcript: deep_research on 'Efdal Colpan' ran 4
    # queries on the ASCII spelling -> 0 results. The gap-analysis
    # section itself listed better variants (Çolpan, Cholpan,
    # Djolpan) but never auto-ran them, forcing the user to ask for
    # the variants in a follow-up turn (also asking permission per
    # the Clause-37 bug R-F764 fixes). R-F766 generates plausible
    # romanisation/transliteration variants for non-English-shaped
    # names and adds one search angle per variant. Cheap pre-check
    # `likely_needs_variants` skips the fanout for plain Western
    # names (John Smith) where the variant generation would waste
    # max_queries budget.
    _variant_angles: list[str] = []
    try:
        from .name_variants import generate_variants, likely_needs_variants
        if likely_needs_variants(entity):
            # Take up to 3 NEW variants (excluding the original which is
            # already covered by _base_angles[0]).
            _variants = generate_variants(entity, limit=4)
            for v in _variants[1:]:  # skip original
                _variant_angles.append(v)
            if _variant_angles:
                logger.info(
                    "R-F766: name-variant fanout added %d angles for "
                    "entity=%r: %r",
                    len(_variant_angles), entity[:80],
                    [a[:80] for a in _variant_angles],
                )
    except Exception as _v_e:
        logger.debug("R-F766 name-variant fanout failed: %s", _v_e)

    # Merge: base + variant fanout + memory expansions, capped at max_queries.
    # Variant fanout comes BEFORE memory expansions because (a) memory
    # expansions are derived from prior RAG hits which presuppose the
    # canonical spelling was searchable in the first place — for a
    # transliterated name with zero prior coverage, the variants ARE the
    # discovery angles, and (b) memory expansions already include "<entity>
    # <name>" combos which we don't want to crowd out the variant angles.
    angles = (_base_angles + _variant_angles + _memory_expansions)[:max_queries]

    # ── Step 2: parallel web searches with overall budget cap ────────
    # Pre-Phase-3 latency cap 2026-04-09: previously the gather had no
    # overall wall-clock budget — slow providers (Brave timeout, DDG
    # rate-limiting) could push the entire chat turn past 5 minutes.
    # Now we wait at most overall_budget/2 for the search step, and the
    # extraction step gets the remaining budget. Past incident: rolling
    # mean turn latency was 348s with deep_research, vs <90s target.
    logger.info("deep_research firing %d parallel web_search angles: %r", len(angles), angles)
    search_budget = max(8.0, overall_budget * 0.45)
    # R-W1 (2026-05-11): use the multi-backend search_multilingual
    # aggregator instead of single-backend web_search. The previous code
    # only hit one provider per angle (Brave-or-DDG), missing
    # Google News, Bing News, Crossref, OpenAlex, Semantic Scholar fan-
    # out — and on dead-Brave nights returned single-backend noise.
    # Wrap the multilingual call into web_search's expected dict shape
    # so downstream aggregation stays untouched.
    from . import web_search as _ws_mod
    async def _search_one_angle(angle: str) -> dict:
        try:
            results_objs = await _ws_mod.search_multilingual(
                angle, languages=None, max_results=8, translate_query=False,
            )
            results = []
            backend_set = set()
            for r in results_objs or []:
                _bk = getattr(r, "backend", "") or getattr(r, "source", "") or "web"
                backend_set.add(_bk)
                results.append({
                    "title": getattr(r, "title", "") or "",
                    "url": getattr(r, "url", "") or "",
                    "snippet": (getattr(r, "snippet", "") or "")[:400],
                    "tier": getattr(r, "source_tier", None) or "UNVERIFIED",
                })
            return {
                "ok": True,
                "provider": ",".join(sorted(backend_set)) or "multilingual",
                "results": results,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)[:200], "provider": "multilingual"}
    search_tasks = [_search_one_angle(angle) for angle in angles]
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
    # R-W9 honesty: initialise per-angle counters AND per-angle status to
    # zero/ok BEFORE iteration so silent (returned 0) and errored (raised)
    # angles are visible in the output. Previous code only added entries
    # on snippet hit, so the operator/LLM couldn't tell whether an angle
    # ran with zero results or never ran at all — same R-F297 failure
    # mode applied to the research output.
    snippet_count_per_angle: dict[str, int] = {a: 0 for a in angles}
    angle_status: dict[str, str] = {a: "ok" for a in angles}
    for angle, sr in zip(angles, search_results_per_angle):
        if isinstance(sr, Exception):
            logger.debug("deep_research search angle %r failed: %s", angle, sr)
            angle_status[angle] = f"errored: {type(sr).__name__}"
            continue
        if not isinstance(sr, dict) or not sr.get("ok"):
            angle_status[angle] = "errored: backend_not_ok"
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
            snippet_count_per_angle[angle] += 1
        # If the angle came back ok but with 0 results, mark it silent
        if angle_status[angle] == "ok" and snippet_count_per_angle[angle] == 0:
            angle_status[angle] = "silent"

    all_snippets = list(snippets_by_url.values())

    # ── Step 3.5: 0-result escalation (R-F768, 2026-05-20) ────────────
    # Live bug 2026-05-20 transcript: deep_research on 'Efdal Colpan' ran
    # 4 angles (+R-F766 variants) → 0 snippets. Pre-R-F768 the function
    # returned an empty result and the chat handler proposed retry
    # options for the user to pick — wasting a turn (Clause 37 / R-F764
    # anti-pattern). R-F768 walks the escalation tree IN-TURN:
    #   (a) native-language web_search via search_multilingual when
    #       R-F767's suggest_jurisdictions maps the entity name to a
    #       non-English region (Turkish → tr, Arabic → ar, Slavic →
    #       pl/sk/cz/hu, etc.)
    #   (b) registry-adapter lookup_entity for the same jurisdictions
    #       (limited to first 2 ISO2 + 8s cap each) so MERSIS / EDR /
    #       Companies House / etc. get queried directly when the
    #       open-web returned nothing
    # Caps prevent runaway: native-language 10s, registry 18s total,
    # respecting the overall_budget (default 45s). Each escalation
    # phase appends to escalation_chain so observers can see what
    # was tried.
    escalation_chain: list[str] = []
    escalation_registry: dict[str, dict] = {}
    if not all_snippets:
        _esc_t0 = time.time()
        _esc_budget = max(2.0, overall_budget - (_esc_t0 - t0) - 2.0)
        if _esc_budget < 3.0:
            logger.info(
                "R-F768 escalation: no budget left (%.1fs) — skipping",
                _esc_budget,
            )
        else:
            try:
                from .name_variants import suggest_jurisdictions as _r768_juris
                _jurisdictions = _r768_juris(entity)
            except Exception as _juri_e:
                logger.debug("R-F768 suggest_jurisdictions failed: %s", _juri_e)
                _jurisdictions = []

            # ISO2 → search_multilingual language code. Limited to the
            # jurisdictions that have an aria_service registry adapter
            # AND a search_multilingual language pair.
            _ISO_TO_LANG = {
                "TR": "tr",  "BG": "bg",  "SA": "ar",  "AE": "ar",
                "PL": "pl",  "SK": "sk",  "CZ": "cs",  "HU": "hu",
                "BR": "pt",  "AO": "pt",  "FR": "fr",  "DE": "de",
                "RO": "ro",
            }
            _esc_langs = list({
                _ISO_TO_LANG[j] for j in _jurisdictions if j in _ISO_TO_LANG
            })

            # ── (a) Native-language search ───────────────────────────
            if _esc_langs:
                escalation_chain.append(f"native_search:{','.join(sorted(_esc_langs))}")
                try:
                    from .web_search import search_multilingual as _r768_ml
                    _native_results = await asyncio.wait_for(
                        _r768_ml(entity, languages=_esc_langs, max_results=8),
                        timeout=min(10.0, _esc_budget * 0.5),
                    )
                    for sr in (_native_results or []):
                        _url = getattr(sr, "url", "") or ""
                        if not _url or _url in snippets_by_url:
                            continue
                        try:
                            _tier = _score_url_by_domain_tier(_url)
                        except Exception:
                            _tier = 0
                        snippets_by_url[_url] = {
                            "url": _url,
                            "title": getattr(sr, "title", "") or "",
                            "snippet": (getattr(sr, "description", "") or "")[:300],
                            "tier_score": _tier,
                            "angles": ["__r768_native_escalation__"],
                            "source_provider": "search_multilingual",
                        }
                    all_snippets = list(snippets_by_url.values())
                    logger.info(
                        "R-F768 native-language escalation: %d new snippets in %s "
                        "(entity=%r, jurisdictions=%r)",
                        max(0, len(all_snippets)),
                        _esc_langs, entity[:80], _jurisdictions,
                    )
                except asyncio.TimeoutError:
                    logger.info(
                        "R-F768 native-language escalation timed out (%s)",
                        _esc_langs,
                    )
                except Exception as _ml_e:
                    logger.debug("R-F768 native-language escalation failed: %s", _ml_e)

            # ── (b) Registry adapter lookup ──────────────────────────
            # Try the first 2 jurisdictions only — registry adapters can
            # be slow (15-25s each for some). The 18s hard cap below
            # prevents runaway when both jurisdictions hang.
            _esc_remaining = _esc_budget - (time.time() - _esc_t0)
            if _jurisdictions and _esc_remaining > 5.0:
                _registry_cap = min(18.0, _esc_remaining - 1.0)
                _registry_per_call = _registry_cap / min(2, len(_jurisdictions))
                escalation_chain.append(
                    f"registry:{','.join(_jurisdictions[:2])}"
                )
                try:
                    from . import registry_adapters as _r768_ra
                    for _iso2 in _jurisdictions[:2]:
                        if (time.time() - _esc_t0) > _registry_cap:
                            break
                        try:
                            _reg_result = await asyncio.wait_for(
                                _r768_ra.lookup_entity(entity, _iso2),
                                timeout=_registry_per_call,
                            )
                            if _reg_result:
                                escalation_registry[_iso2] = _reg_result
                                logger.info(
                                    "R-F768 registry escalation [%s]: %r → "
                                    "%r",
                                    _iso2, entity[:80],
                                    (_reg_result.get("profile") or {}).get(
                                        "company_name", "matched"
                                    ),
                                )
                        except asyncio.TimeoutError:
                            logger.info(
                                "R-F768 registry [%s] timed out at %.1fs",
                                _iso2, _registry_per_call,
                            )
                        except Exception as _ra_e:
                            logger.debug(
                                "R-F768 registry [%s] failed: %s", _iso2, _ra_e,
                            )
                except Exception as _ra_setup_e:
                    logger.debug(
                        "R-F768 registry-adapter import failed: %s",
                        _ra_setup_e,
                    )

            if escalation_chain:
                logger.info(
                    "R-F768 escalation complete: chain=%r snippets=%d registry_hits=%d",
                    escalation_chain, len(all_snippets), len(escalation_registry),
                )

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
        "angle_status": angle_status,  # R-W9: per-angle ok/silent/errored
        "snippets_total": len(all_snippets),
        "snippets_top": all_snippets[:10],  # was 15 — context-budget cut
        "extracted_pages": extracted_pages,
        "extracted_count": len(extracted_pages),
        "prior_brave_knowledge": prior_brave_knowledge,
        "duration_ms": total_elapsed_ms,
        "budget_ms": int(overall_budget * 1000),
        # R-F768 (2026-05-20) — observable escalation trail. Empty list
        # when initial angles returned >=1 snippet. List of escalation
        # phase tags (e.g. 'native_search:tr', 'registry:TR,BG') when
        # the cross-tool fallback fired. escalation_registry maps ISO2
        # to the registry-adapter result so the chat handler can
        # render directorships / officers without a second tool call.
        "escalation_chain": escalation_chain,
        "escalation_registry": escalation_registry,
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


@fail_wire(module="researcher", gap_type="source_failure")
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
    # SSRF guard — refuse loopback / RFC1918 / fly-private / internal TLDs
    # before any fetch. See intel/url_safety.py. Applied here at the
    # top-level deep-extraction entry so one guard covers the homepage,
    # the re-fetch for raw HTML (line ~1627), and the derived
    # internal-link crawl later.
    from .url_safety import is_safe_url as _safe_url
    _ok, _reason = _safe_url(url)
    if not _ok:
        logger.warning("extract_url_deep blocked unsafe URL %r: %s", url, _reason)
        return {
            "ok": False, "url": url, "error": f"blocked_unsafe_url:{_reason}",
            "pages_fetched": [], "deep_mode": True,
        }

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
            # R-F1851 (DD stage 2) — the seed `url` was SSRF-checked at entry
            # (line ~3021), but this re-fetch previously followed redirects with no
            # revalidation, so an open redirect on the seed could reach an internal
            # host. Route through safe_get (per-hop revalidation, redirects off).
            from . import url_safety as _us
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                resp = await _us.safe_get(client, sanitised, headers={
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
                # R-F3564 — the OUTER swallow. structured.extract() wires its own
                # per-part failures, but if the whole call dies (or the module is
                # absent) nothing inside it runs, so this branch is the only place
                # the loss can be reported. Silence here left the DD evidence path
                # with empty tables/schema and no reason recorded.
                logger.debug("structured extractor failed: %s", _e)
                _extractor_unavailable("structured", _e, url)
        # facts.extract runs over the aggregated TEXT across all pages
        # — more signal than single-page since /about/team/contact
        # often each carry a different fragment of the company story.
        try:
            extractor_facts = _facts.extract(full_text, base_url=url)
        except Exception as _e:
            logger.debug("facts extractor failed: %s", _e)
            _extractor_unavailable("facts", _e, url)
    except ImportError as _ie:
        # "extractors package not deployed" is a DEPLOY defect, not a graceful
        # degrade: every crawl silently loses reg numbers, officers and tables.
        # Degrading gracefully is right; degrading SILENTLY is what this fixes.
        _extractor_unavailable("extractors_package", _ie, url)

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

@fail_wire(module="researcher", gap_type="source_failure")
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

    # SSRF guard — see url_safety.py. Applied AFTER sanitise_url
    # (which handles URL-encoding / normalisation) and BEFORE any fetch.
    from .url_safety import is_safe_url as _safe_url
    _ok, _reason = _safe_url(url)
    if not _ok:
        logger.warning("extract_url_text blocked unsafe URL %r: %s", url, _reason)
        return {"url": url, "extraction_ok": False,
                "error": f"blocked_unsafe_url:{_reason}", "text": ""}

    t0 = time.time()
    html = ""
    try:
        # R-F1851 (DD stage 2) — SSRF guard. `url` is a discovered/user-supplied page
        # URL; sanitise_url is parse-time only (no DNS), so route through safe_get
        # which DNS-resolves the host and revalidates every redirect hop (raw
        # follow_redirects=True could open-redirect to an internal service).
        from . import url_safety as _us
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await _us.safe_get(client, url, headers={
                "User-Agent": random_ua(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
            })
            if resp.status_code == 200:
                html = resp.text
            elif resp.status_code in (401, 402, 403, 404, 410, 429, 451, 500, 502, 503, 504):
                # R-F126 (2026-05-10): widen wayback fallback. Was only
                # 401/402/403 — but contract-signing PR pages often return
                # 404 / 410 once the org's CMS rotates content; 5xx is
                # R-F187 (2026-05-11): added 429 (dominant rate-limit code)
                # so rate-limited fetches try Wayback before giving up.
                # equally common during high-traffic post-event windows.
                # Trying archive.org's snapshot is the difference between
                # surfacing the contract value vs returning "fetch failed".
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

    # R-W2 (2026-05-11): when static fetch returned thin content (likely
    # JS-rendered SPA), try Lightpanda/headless BEFORE the early-return.
    # extract_url_text used to give up here with "fetched content too
    # short or empty" — extract_url_deep relies on this function, so
    # this single fix unblocks both. Mirrors _fetch_article_text:1029.
    if not html or len(html) < 100:
        try:
            from . import headless as _headless
            if hasattr(_headless, "fetch_rendered_html"):
                logger.info(
                    "R-W2: static fetch thin/empty for %s, trying headless render",
                    url[:80],
                )
                rendered = await _headless.fetch_rendered_html(url, timeout=20)
                if rendered and len(rendered) >= 100:
                    html = rendered
        except Exception as _hl_e:
            logger.debug("R-W2 headless fallback failed for %s: %s", url[:80], _hl_e)

    if not html or len(html) < 100:
        return {
            "url": url, "extraction_ok": False,
            "error": "fetched content too short or empty (R-W2 headless also returned thin)",
            "text": "",
            "duration_ms": int((time.time() - t0) * 1000),
        }

    extracted = await extract_structured_html_async(html)
    text = extracted.get("text", "") or ""
    if not text or len(text) < 50:
        # R-W2: also try headless when html had content but extraction
        # yielded nothing (real SPA where JS-rendered text is needed).
        try:
            from . import headless as _headless
            if hasattr(_headless, "fetch_rendered_html"):
                logger.info(
                    "R-W2: extraction thin for %s, trying headless render",
                    url[:80],
                )
                rendered = await _headless.fetch_rendered_html(url, timeout=20)
                if rendered and len(rendered) > len(html or ""):
                    extracted = await extract_structured_html_async(rendered)
                    text = extracted.get("text", "") or ""
        except Exception as _hl_e:
            logger.debug("R-W2 headless fallback failed for %s: %s", url[:80], _hl_e)

    if not text or len(text) < 50:
        # Even after headless still thin — surface meta tags + JSON-LD
        # so the LLM has SOMETHING to quote from, plus an explicit warning.
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
                "error": "fetched but extraction returned no usable text — likely a JS-rendered SPA (R-W2 headless also returned thin)",
                "text": "",
                "duration_ms": int((time.time() - t0) * 1000),
            }

    return {
        "url": url,
        "extraction_ok": True,
        "text": text[:30000],   # R-F2204 — raised 8000 -> 30000 so trafilatura's rich main text isn't re-truncated
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

# R-F652 (2026-05-17): CDN-asset and static-resource URL filter. The LinkedIn-
# newsletter intake extracts every href from the email HTML and submits each
# one to read_article. That meant ~4 static.licdn.com/aero-v1/sc/h/* URLs per
# newsletter (CSS sprites, fonts) hit the researcher pipeline — each burning
# a DeepSeek call (~5s) and producing 0 facts because the body is binary or
# minified asset data. RAG dedup logged "skipping 3 of 3 chunks (content
# already in RAG)" for each one. Live evidence 2026-05-17 08:01:40 onward.
#
# Hosts + path patterns here are intentionally narrow — only block sites we
# have direct live evidence of leaking through. Add more as they surface.
_STATIC_ASSET_HOSTS = (
    "static.licdn.com",       # LinkedIn images/CSS/fonts
    "static.fbcdn.net",       # Facebook CDN
    "static.xx.fbcdn.net",
    "static.twimg.com",       # Twitter/X CDN
    "abs.twimg.com",
    "pbs.twimg.com",          # Twitter image CDN
    "media.licdn.com",        # LinkedIn user-uploaded media
)
_STATIC_ASSET_PATH_PREFIXES = (
    "/aero-v1/sc/",            # LinkedIn's static-component path
    "/aero-v1/sc/h/",
)
_STATIC_ASSET_EXTENSIONS = (
    ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".webp", ".ico", ".woff", ".woff2", ".ttf", ".otf",
    ".mp4", ".webm", ".mp3", ".wav",
)


def _is_static_asset_url(url: str) -> bool:
    """Return True for URLs that are CDN assets or static resources — these
    are not articles and submitting them to read_article wastes an LLM call.
    Returns False for ambiguous URLs (let the fetcher decide)."""
    if not url:
        return False
    u = url.lower().strip()
    # Path-extension match (cheapest, catches *.css etc.)
    # Strip query string before checking extension
    path_only = u.split("?", 1)[0].split("#", 1)[0]
    if path_only.endswith(_STATIC_ASSET_EXTENSIONS):
        return True
    # Host match
    try:
        from urllib.parse import urlparse
        parsed = urlparse(u)
        host = (parsed.hostname or "").lower()
        path = parsed.path or ""
        if host in _STATIC_ASSET_HOSTS:
            return True
        # Host + path-prefix match (catches the LinkedIn aero-v1 pattern even
        # if a wrapper subdomain is added later)
        for prefix in _STATIC_ASSET_PATH_PREFIXES:
            if path.startswith(prefix):
                return True
    except Exception:
        pass
    return False


@fail_wire(module="researcher", gap_type="source_failure")
async def read_article(llm: LLMProvider, url: str, context: str = "") -> dict:
    """
    Read a specific article URL and extract intelligence.
    Use this when someone shares an article via WhatsApp, chat, or API.
    """
    if not llm or not llm.is_configured:
        return {"error": "LLM not configured"}

    # R-F652: short-circuit static-asset URLs before any fetch or LLM call.
    if _is_static_asset_url(url):
        logger.info("R-F652 read_article: skipped static-asset URL %s", url[:120])
        return {
            "url": url,
            "facts_learned": 0,
            "hypotheses_generated": 0,
            "facts": [],
            "skipped_reason": "static_asset_url",
        }

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
    # R-F1102: ARIA_ARTICLE_MAX_CHARS env var (default 50000) for DD reads.
    _article_max_chars = int(os.getenv("ARIA_ARTICLE_MAX_CHARS", "50000"))
    if len(body) > _article_max_chars:
        body = body[:_article_max_chars] + "\n\n[TRUNCATED — original content was " + str(len(body)) + " chars]"

    existing_kb = await asyncio.to_thread(search_knowledge, body[:200])  # R-F1910 G4: off-loop
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

@fail_wire(module="researcher", gap_type="source_failure")
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
        # Detect source type from filename / source string. Emails get a
        # dedicated `email` source_type so ARIA's RAG EMAIL SEARCH can filter
        # on them — previously they were tagged "document" and ARIA's live
        # diagnostic reported "0 email-tagged chunks found" despite 22 email
        # brain_absorb signals (2026-04-21 incident).
        ext = (filename.rsplit(".", 1)[-1] or "").lower()
        src_lower = source.lower()
        if src_lower.startswith("email:") or "email" in filename.lower():
            source_type = "email"
        elif "pdf" in src_lower or ext == "pdf":
            source_type = "pdf"
        elif ext in ("docx", "doc"):
            source_type = "docx"
        elif ext in ("xlsx", "xls", "csv"):
            source_type = "spreadsheet"
        else:
            source_type = "document"

        rag_result = await rag_store.ingest_document(
            text=content,
            source=f"document:{source}:{filename}",
            source_type=source_type,
            title=filename,
            extra_metadata={"context": (context or "")[:300], "is_email": source_type == "email"},
        )
        if not rag_result.get("ingested"):
            logger.warning(
                "RAG ingest skipped for %s (source=%s, len=%d): %s",
                filename, source_type, len(content or ""),
                rag_result.get("reason") or rag_result.get("error") or "unknown",
            )
    except Exception as e:
        logger.warning("RAG ingest from read_document failed: %s", e, exc_info=True)

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

    # R-F1916 (G2): hard chunk-count ceiling — defense-in-depth so even if a
    # caller bypasses the read_document_ep content cap, the fan-out below (a
    # gather over every chunk, each scheduling an LLM analysis + a search_knowledge
    # thread hop) can't explode into thousands of coroutines and wedge/OOM the
    # single-process brain. ~200 chunks ≈ 900K chars — well past any real document.
    _MAX_CHUNKS = max(1, int(os.getenv("ARIA_DOC_MAX_CHUNKS", "200")))
    if len(chunks) > _MAX_CHUNKS:
        logger.warning(
            "read_document: capping chunk fan-out %d -> %d (content=%d chars, doc=%s)",
            len(chunks), _MAX_CHUNKS, len(content), filename,
        )
        chunks = chunks[:_MAX_CHUNKS]

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

    # ── R-F1675 (surgical): PRE-COMPUTE the per-chunk LLM analysis CONCURRENTLY.
    # This was THE bottleneck behind the all-day WhatsApp doc-review failure: a
    # 62KB contract chunks into ~18 windows and the old loop ran one LLM analysis
    # per chunk SEQUENTIALLY ("# No limit") → ~10-18 min, far beyond the WA poll
    # window, so the review never delivered. Running up to ARIA_DOC_CHUNK_CONCURRENCY
    # (default 4) analyses at once cuts that to ~ceil(N/conc) × per-call. The
    # storage loop below stays SEQUENTIAL + in page order (state mutation:
    # store_fact, hypotheses via _process_analysis, RAG/semantic batches).
    # _analyse_article / _analyse_compliance_document only READ hypotheses
    # (verified — no mutation), so concurrent analysis is safe.
    _doc_sem = asyncio.Semaphore(max(1, int(os.getenv("ARIA_DOC_CHUNK_CONCURRENCY", "4"))))

    async def _rf1675_analyse(_i: int, _chunk: str):
        _dt = f"Document: {filename}\nSource: {source}\n"
        if context:
            _dt += f"Context: {context}\n"
        _dt += f"Content (part {_i + 1}/{len(chunks)}):\n{_chunk}"
        _ekb = await asyncio.to_thread(search_knowledge, _chunk[:200])  # R-F1910 G4: off-loop
        async with _doc_sem:
            if is_compliance:
                return await _analyse_compliance_document(llm, _dt, f"{source}:{filename}", _ekb)
            return await _analyse_article(llm, _dt, f"{source}:{filename}", _ekb, hypotheses)

    _parsed_results = await asyncio.gather(
        *[_rf1675_analyse(i, c) for i, c in enumerate(chunks)],
        return_exceptions=True,
    )
    _parsed_list = [
        (None if isinstance(p, BaseException) else p) for p in _parsed_results
    ]

    # ── Storage phase: SEQUENTIAL + in page order (preserves the original
    # stateful behaviour exactly; only the slow LLM calls were parallelised).
    for i, chunk in enumerate(chunks):
        parsed = _parsed_list[i]

        if is_compliance:
            if parsed and not parsed.get("skip"):
                compliance_results.append(parsed)
                # F48 2026-04-29: collect this chunk's facts so a single
                # add_facts_batch covers the RAG side. Was 1 model.encode
                # per fact (compliance docs often have 10-20 facts/chunk
                # × multi-chunk = ~60 wasted encodes per filing). Same
                # pattern as _process_analysis (F23/F24, 2026-04-27).
                rag_batch: list[dict] = []
                semantic_batch: list[tuple[str, str, dict | None]] = []
                for fact in (parsed.get("facts") or []):
                    topic = fact.get("topic", "")
                    fact_content = fact.get("content", "")
                    confidence = fact.get("confidence", "ASSESSED")
                    if topic and fact_content and len(fact_content) > 20:
                        sf_result = await store_fact(
                            topic,
                            f"{fact_content} [Source: {source}:{filename}]",
                            f"compliance:{source}",
                            confidence,
                            skip_rag_ingest=True,
                            skip_semantic_index=True,
                        )
                        total_facts += 1
                        all_facts.append(fact)
                        rag_batch.append({
                            "topic": topic,
                            "content": f"{fact_content} [Source: {source}:{filename}]",
                            "confidence": confidence,
                            "source": f"compliance:{source}",
                        })
                        fid = (sf_result or {}).get("fact_id")
                        if fid:
                            semantic_batch.append((
                                fid,
                                f"{topic} {fact_content}",
                                {"confidence": confidence},
                            ))
                if rag_batch:
                    try:
                        from . import rag_store as _rag
                        await _rag.add_facts_batch(rag_batch)
                    except Exception as e:
                        logger.debug("rag_store.add_facts_batch (compliance) failed: %s", e)
                if semantic_batch:
                    try:
                        from . import semantic_search as _ss
                        import asyncio as _aio
                        await _aio.to_thread(_ss.index_facts_batch, semantic_batch)
                    except Exception as e:
                        logger.debug("semantic_search.index_facts_batch (compliance) failed: %s", e)

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

@fail_wire(module="researcher", gap_type="source_failure")
async def research_and_learn(llm: LLMProvider, max_articles: int = 30) -> dict:
    """
    ARIA's autonomous research cycle:
    1. Scan 30+ RSS feeds for relevant articles
    2. Run web searches on rotating topics
    3. Read and extract intelligence from the best articles
    4. Cross-reference with existing knowledge
    5. Generate and validate hypotheses
    """
    # R-F195 (2026-05-11) — graceful degrade when no LLM is available.
    # Pre-R-F195 we returned error and skipped the cycle entirely. That
    # meant a cloud-LLM outage stopped ALL learning. Air-gap independence
    # requires the RSS-read + RAG-ingest path to keep running even when
    # no LLM is configured; only the LLM-driven fact extraction
    # downstream is skipped. Knowledge still grows via reading +
    # embedding-only ingestion.
    _llm_available = bool(llm and getattr(llm, "is_configured", False))
    if not _llm_available:
        logger.warning(
            "[research] LLM unavailable — running degraded cycle "
            "(RSS fetch + RAG ingest only, no LLM extraction)"
        )

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

    # R-F1523: also fetch from legal feeds if configured
    if LEGAL_FEEDS:
        for feed in LEGAL_FEEDS:
            articles = await _fetch_rss(feed["url"])
            for a in articles:
                a["source"] = feed["name"]
                a["category"] = feed["category"]
            all_articles.extend(articles)

    logger.info(f"RSS feeds: {len(all_articles)} articles from {len(RESEARCH_FEEDS) + len(LEGAL_FEEDS)} feeds")

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
    # Relevance anchor gate: nothing scores anything without at least one
    # explicit defence or legal/regulatory term in title+description. Generic
    # words like "contract", "billion", "deal", "angola" appear in many off-topic
    # contexts (hospitality, tourism, entertainment) and were lifting hotel
    # papers into the top reading queue (live incident 2026-04-27).
    #
    # R-F1525: added legal scoring path alongside defence scoring. Legal
    # articles (sanctions, export control, trade law, arbitration) are scored
    # on their own terms so they compete fairly with defence articles for the
    # top-N reading slots.
    scored: list[tuple[float, dict]] = []
    for article in all_articles:
        text = f"{article['title']} {article.get('description', '')}".lower()
        if not _has_defence_anchor(text):
            continue
        score = 0
        # ── Defence scoring ────────────────────────────────────────────
        for interest in RESEARCH_INTERESTS:
            words = interest.lower().split()
            matches = sum(1 for w in words if w in text)
            if matches >= 2:
                score += matches * 2
        if any(k in text for k in ["tender", "contract", "procure", "award", "billion", "million", "deal"]):
            score += 5
        if any(c in text for c in ["angola", "mozambique", "guinea-bissau", "cape verde", "lusophone"]):
            score += 8
        if any(c in text for c in ["nigeria", "kenya", "saudi", "uae", "indonesia", "philippines", "poland"]):
            score += 3
        # ── Legal & Regulatory scoring (R-F1525) ───────────────────────
        if any(k in text for k in [
            "sanctions", "embargo", "export control", "export licence",
            "ofac", "bis", "ear", "itar", "dual-use",
            "trade law", "trade remedy", "anti-dumping", "countervailing",
            "wto", "dispute settlement", "arbitration", "icc",
            "compliance", "regulation", "regulatory",
            "anti-money laundering", "aml", "anti-corruption",
            "data protection", "gdpr", "privacy",
            "competition law", "antitrust", "merger control",
            "investment treaty", "bilateral investment",
            "force majeure", "choice of law", "jurisdiction",
        ]):
            score += 5
        if any(c in text for c in ["eu sanctions", "un sanctions", "uk sanctions", "us sanctions"]):
            score += 3
        if any(c in text for c in ["eur-lex", "federal register", "official journal", "executive order"]):
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

        # R-F195: when no LLM, still ingest the article into RAG so
        # mastery + future retrieval benefit. Skip the LLM-driven
        # extraction step. Without this branch, a cloud outage stops
        # all knowledge growth from research_and_learn.
        if not _llm_available:
            if body and len(body) > 200:
                try:
                    from . import rag_store as _rs_r
                    await _rs_r.ingest_document(
                        body[:6000],
                        source=f"research_degraded:{article.get('source', 'unknown')}",
                        source_type="article",
                        title=article.get("title", "")[:200],
                        url=article.get("link", "")[:500],
                        extra_metadata={"degraded_no_llm": True},
                    )
                    facts_learned += 1  # RAG chunk counts as a fact for the cycle
                except Exception as _ie:
                    logger.debug("R-F195 RAG ingest failed: %s", _ie)
            continue

        existing_kb = await asyncio.to_thread(search_knowledge, article["title"])  # R-F1910 G4: off-loop
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

@fail_wire(module="researcher", gap_type="source_failure")
async def validate_hypothesis(llm: LLMProvider, hypothesis_text: str) -> dict:
    """Search for evidence to validate or refute a specific hypothesis.

    Drain rule (added 2026-05-01): every call increments
    `validation_attempts`; after `_HYPOTHESIS_ATTEMPT_CAP` consecutive
    attempts that didn't flip the hypothesis off OPEN, force the status
    to INSUFFICIENT_EVIDENCE so the backlog drains. Without this the
    LLM's natural conservatism ("when in doubt, return OPEN") combined
    with the 200-cap hypothesis store had the OPEN backlog saturated at
    188/200 indefinitely (live observation 2026-05-01 06:34:12).
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

    target["validation_attempts"] = int(target.get("validation_attempts") or 0) + 1
    target["last_validated_at"] = datetime.now(timezone.utc).isoformat()

    async def _exit(payload: dict) -> dict:
        # Single point that persists attempt-counter mutations and
        # applies the drain rule. Every early-exit and the LLM path go
        # through here so the counter never silently de-syncs from
        # disk.
        if (
            target.get("status") == "OPEN"
            and target["validation_attempts"] >= _HYPOTHESIS_ATTEMPT_CAP
        ):
            target["status"] = "INSUFFICIENT_EVIDENCE"
            payload = {**payload, "status": "INSUFFICIENT_EVIDENCE",
                       "new_status": "INSUFFICIENT_EVIDENCE"}
        await _save_hypotheses(hypotheses)
        return payload

    # Hypotheses can be 200+ char descriptions ("The Measure Group Europe
    # LinkedIn page shows negligible engagement and no defence content,
    # suggesting it is either a non-defence entity, a shell page..."). Long
    # queries return junk from search APIs and consume their per-query limits.
    # F10 fix 2026-04-27: truncate to a search-shaped query (~60 chars).
    # F60 fix 2026-04-28: truncation alone left queries like "The ARIA
    # collection pipeline is subscribed to or receiving" — mostly stopwords,
    # zero search signal. Extract the substantive keywords first, then
    # append "evidence 2026" as the recency anchor.
    query = _extract_query_keywords(target["hypothesis"], max_words=8) + " evidence 2026"
    articles = await _web_search(query)
    if not articles:
        return await _exit({"hypothesis": target["hypothesis"], "status": "NO_NEW_EVIDENCE"})

    # F26 fix 2026-04-27: CrossRef and academic search APIs return DOI
    # matches by keyword overlap regardless of domain. A defence hypothesis
    # like "MQ-25A Stingray first flight" pulled medical-research and
    # literature-review papers from casemedicalresearch.com and
    # bloomsburycollections.com, which we then Lightpanda-rendered and
    # ingested into RAG. Filter implausible-domain results before fetch.
    plausible = [a for a in articles if _is_plausible_defence_domain(a.get("link", ""))]
    if not plausible:
        return await _exit({"hypothesis": target["hypothesis"], "status": "NO_PLAUSIBLE_EVIDENCE"})

    evidence_texts = []
    for a in plausible[:3]:
        body = await _fetch_article_text(a.get("link", "")) if a.get("link") else ""
        # R-F861 — content relevance gate. _is_plausible_defence_domain (above)
        # filters by DOMAIN, but crossref/openalex resolve keyword-collision hits
        # to generic academic domains that pass it while the CONTENT is off-topic
        # (live: an arts journal matched "offset", an IPO-underpricing paper
        # matched "forward" for a Finland F-35 offset hypothesis). Require a
        # defence anchor in title+body before using it as evidence so junk
        # neither dilutes the LLM hypothesis evaluation nor wastes deep-read
        # budget + encode load.
        if not _has_defence_anchor(f"{a.get('title', '')} {body[:1500]}"):
            logger.info(
                "R-F861 relevance gate dropped off-topic evidence: %s",
                (a.get("title") or a.get("link") or "?")[:80],
            )
            continue
        evidence_texts.append(f"Title: {a['title']}\n{body[:1500]}")
    if not evidence_texts:
        return await _exit({"hypothesis": target["hypothesis"], "status": "NO_RELEVANT_EVIDENCE"})

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
            return await _exit({**parsed, "hypothesis": target["hypothesis"]})
    except Exception as e:
        # Persist the bumped attempt counter even on exception so a
        # serially-failing hypothesis can still age out via the cap.
        try:
            await _save_hypotheses(hypotheses)
        except Exception:
            pass
        return {"error": str(e)}
    return await _exit({"hypothesis": target["hypothesis"], "status": "EVALUATION_FAILED"})


@fail_wire(module="researcher", gap_type="source_failure")
async def get_hypotheses() -> list[dict]:
    return await _load_hypotheses()


@fail_wire(module="researcher", gap_type="source_failure")
async def get_research_summary(llm: LLMProvider) -> dict:
    hypotheses = await _load_hypotheses()
    kb_size = len((_kb_mod._cache or {}).get("facts", []))
    ledger_size = len((_ledger_mod._cache or {}).get("signals", []))
    open_h = [h for h in hypotheses if h.get("status") == "OPEN"]
    strong_h = [h for h in hypotheses if h.get("status") == "STRENGTHENED"]
    challenged_h = [h for h in hypotheses if h.get("status") == "CHALLENGED"]
    # R-F161 — surface stale + insufficient-evidence buckets so backlog
    # drain is visible in the dashboard. STALE = aged out via R-F161
    # time-cap; INSUFFICIENT_EVIDENCE = aged out via R-F32 attempt-cap.
    stale_h = [h for h in hypotheses if h.get("status") == "STALE"]
    insufficient_h = [h for h in hypotheses if h.get("status") == "INSUFFICIENT_EVIDENCE"]

    return {
        "knowledge_base_facts": kb_size,
        "intel_ledger_signals": ledger_size,
        "hypotheses": {
            "total": len(hypotheses),
            "open": len(open_h),
            "strengthened": len(strong_h),
            "challenged": len(challenged_h),
            "stale": len(stale_h),
            "insufficient_evidence": len(insufficient_h),
        },
        "drain_indicators_R_F161": {
            "stale_threshold_days": _HYPOTHESIS_STALE_DAYS,
            "stale_count": len(stale_h),
            "attempt_cap": _HYPOTHESIS_ATTEMPT_CAP,
            "insufficient_evidence_count": len(insufficient_h),
            "open_actively_being_verified": len(open_h),
        },
        "top_hypotheses": [{"hypothesis": h["hypothesis"], "status": h["status"], "evidence": h.get("evidence_count", 0)} for h in hypotheses[:10]],
    }


# ══════════════════════════════════════════════════════════════════════
# R-F159 (2026-05-10) — Stage B adverse-media deep search
# ══════════════════════════════════════════════════════════════════════
# Per operator priority 2026-05-10: "ARIA needs to really dig in deep
# not just saying so but doing it actually". The dd_disciplines framework
# (R-F152) defined adverse_media as a discipline + provided
# adverse_media_query_templates() that generates 20-50 STRUCTURED queries
# per entity targeting court records, regulators, ICIJ leaks, OCCRP,
# Bellingcat, Tier-1 journalism, news archive, Wayback, sector trade press.
#
# This function executes those templates via the existing multi-backend
# web_search infrastructure, aggregates findings with provenance, and
# tier-classifies per Clause 17. The result is a structured adverse-media
# section operators can plug directly into a DD report.
#
# NOT auto-wired into deep_research or dd_orchestrator yet. Operator
# decision: when does adverse-media run (always? CRITICAL only? on demand?)
# + cost expectations (each entity = 20-50 new searches; circuit breakers
# in place per R-F150 for backends that 202/429).

#: R-F3802 — legal FORM suffixes only. Deliberately NOT `_sanctions_classify.
#: _CORP_SUFFIXES`; see `_adverse_relevance_token_sets` for why the two must differ.
_ADVERSE_LEGAL_FORMS = frozenset({
    "ltd", "limited", "llc", "llp", "lp", "plc", "inc", "incorporated", "corp",
    "corporation", "co", "company", "gmbh", "ag", "kg", "mbh", "bv", "nv", "sa",
    "sas", "sarl", "srl", "spa", "spz", "oy", "ab", "as", "apS", "aps", "pty",
    "pte", "sdn", "bhd", "jsc", "ojsc", "cjsc", "pjsc", "ooo", "oao", "zao",
    "doo", "dd", "ae", "epe", "kk", "yk", "trust", "holdings", "holding", "group",
})


@fail_wire(module="researcher", gap_type="source_failure")
def _adverse_relevance_token_sets(names: list[str]) -> list[set[str]]:
    """R-F2745 — meaningful token set per subject name (entity + directors + UBOs).

    R-F3802 — this used `_sanctions_classify._tokenize_entity_name` and inherited a
    stop list tuned for the OPPOSITE requirement, which silently reopened the exact
    defect R-F2745 exists to prevent.

    That helper drops "ventures", "capital", "investments", "fund", "management" and
    similar as non-distinctive. For SANCTIONS SCREENING that is right and must not be
    touched: run dd_29368fbb8b3d HARD_STOP'd "BATSELA CAPITAL INVESTMENTS L.L.P"
    against OFAC's "D.G.D. INVESTMENTS LTD." on the single shared token
    "investments", and told the operator to consider filing a SAR. Screening needs
    RECALL with distinctiveness guards.

    This gate needs PRECISION. It asks "is this article about MY subject", and there
    the commercial descriptor is exactly what distinguishes one company from another.
    Borrowing the screening tokenizer reduced "Acme Ventures Ltd" to {"acme"}, so
    "Acme Widgets Inc under investigation" satisfied the subset test and a DIFFERENT
    company's adverse media was attributed to the subject — inflating the adverse
    exposure that feeds the evidence grade, on a compliance product.

    So only true legal FORMS are stripped here. The trade-off is deliberate: an
    article naming the company informally ("Acme fined $2m") is now dropped. On a DD
    product, attributing the WRONG company's wrongdoing is far worse than missing a
    loosely-worded mention, which is R-F2745's stated contract ("kept only if the
    article names the subject"). Drops are counted in `_off_subject_dropped`, so the
    cost is observable rather than silent.
    """
    import unicodedata as _ud

    out: list[set[str]] = []
    for n in names:
        normalised = _ud.normalize("NFKD", str(n or ""))
        normalised = "".join(c for c in normalised if not _ud.combining(c))
        tokens = re.sub(r"[^a-zA-Z0-9]+", " ", normalised).lower().split()
        ts = {t for t in tokens
              if len(t) >= 3 and t not in _ADVERSE_LEGAL_FORMS and not t.isdigit()}
        if ts:
            out.append(ts)
    return out


def _result_domain(url: str) -> str:
    """R-F3023 — the registrable host of a result URL, lower-cased, `www.` stripped.
    Returns "" for anything that is not an http(s) URL (including ARIA's own
    `memory://` records, which are not external sources at all)."""
    s = str(url or "").strip()
    if not s.lower().startswith(("http://", "https://")):
        return ""
    try:
        from urllib.parse import urlparse
        host = (urlparse(s).hostname or "").lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _query_site_hosts(query: str) -> list[str]:
    """R-F3023 — the hosts a template CONSTRAINED itself to via `site:`.

    The adverse templates are written as `"{name}" site:bailii.org` (see
    dd_disciplines.py:1258+), so the intended domain is stated in the query itself —
    no hand-maintained class→domain table to drift out of date."""
    return [m.lower().lstrip(".") for m in
            re.findall(r"site:([A-Za-z0-9.\-]+)", str(query or ""))]


#: R-F3516 — host constraints too BROAD to corroborate a specific body.
#:
#: `site:gov.uk` is the whole of UK government, not OFSI. On the live Chemring run
#: (dd_8bd7ac42a488) the OFSI template was `"{name}" site:gov.uk OFSI enforcement`, the
#: backend answered with Companies House pages at
#: `find-and-update.company-information.service.gov.uk` — which ends with `.gov.uk` — and
#: four registry pages were stamped `source_class_corroborated: True` for OFSI. A company
#: register page corroborates precisely nothing about a sanctions-enforcement screen.
#:
#: These yield None (unverifiable), never True. None is not False: the template genuinely
#: constrained itself, we simply cannot tell from the host whether the right BODY answered.
_UNSPECIFIC_CONSTRAINT_HOSTS = frozenset({
    "gov.uk", "gov", "gov.au", "gov.ca", "gov.in", "gov.za", "gc.ca",
    "europa.eu", "int", "org", "com", "net", "co.uk", "org.uk", "ac.uk", "edu",
})


def _domain_corroborates_class(domain: str, source_class: str, query: str = "") -> bool | None:
    """R-F3023 — does the RESULT's domain support the class the TEMPLATE asserted?

    True  — the template constrained itself to a SPECIFIC host and the result came from it.
    False — it constrained itself and the result came from somewhere else (the live
            failure: `site:justice.gov` answered by a doi.org academic paper because
            every news/court backend was silent and only the academic backend replied).
    None  — nothing to corroborate: either the template set no host constraint, or the
            constraint it set is too broad to identify the body it claims (R-F3516).
            None is not False: "unverifiable" must never render as "contradicted".
    """
    hosts = [h for h in _query_site_hosts(query)
             if h not in _UNSPECIFIC_CONSTRAINT_HOSTS]
    if not hosts:
        return None
    d = (domain or "").lower()
    if not d:
        return False
    return any(d == h or d.endswith("." + h) for h in hosts)


def _adverse_hit_names_subject(text: str, name_token_sets: list[set[str]]) -> bool:
    """R-F2745 — does the article actually NAME the subject (or a named director/UBO)?

    The adverse-media templates are entity-anchored, but a web backend returns results
    that match the adverse TOPIC (fraud/sanctions/…) even when the entity is absent —
    or a DIFFERENT same-named entity. Attributing those to the subject fabricates its
    adverse exposure (and inflates the count that feeds the evidence grade). A finding
    is the subject's only if the FULL token set of one of its names appears in the
    title/snippet. If no subject name is usable (all too short to tokenise), the gate
    cannot apply and returns True — preserve behaviour rather than fabricate a drop.
    """
    if not name_token_sets:
        return True
    tt = {t for t in re.split(r"[^0-9a-z]+", (text or "").lower()) if len(t) >= 3}
    return any(ns <= tt for ns in name_token_sets)




# ── R-F2847 — adverse-media budget, calibrated to MEASURED search cost ────────
#
# The loop ran up to 30 templates against a 180s deadline with a hardcoded 10.0s
# per-search bound. Phase-timed on the brain (R-F2846) a real search costs ~12.9s:
#     30 x ~13s = ~390s  vs a 180s deadline      -> impossible
#     bound 10.0s        vs measured 12.9s       -> EVERY search times out
# The live SOCAR run recorded exactly that: templates_run 18, templates_searched 0,
# search_backends_answered False. Adverse media returned zero evidence on every run
# because the budget could never be met — not because the sources were empty.
#
# §1 forbids a band-aid, and simply raising 10s would still leave 30 x 13s in 180s.
# The structural error was that the template count and the per-search bound had NO
# relationship to the deadline they had to fit inside. Both are now derived.
#
# HONESTY OVER THROUGHPUT: completing ~9 of 34 templates AND SAYING SO beats
# attempting 34 and completing 0. R-F2791's templates_searched /
# search_backends_answered exist so a zero-finding sweep is distinguishable from a
# sweep that never ran; templates_capped_at + templates_total_in_set keep the cap
# visible, because an invisible truncation reads as a completed sweep.
# R-F2847 — sized from the MEASURED IN-APP distribution, and capped by the caller's
# backstop. In-container timing of the exact adverse-media call shape:
#     per-call 24.73 / 4.47 / 1.92 / 4.64 s   median 4.55s   max 24.73s
# i.e. searches are FAST once warm and the first is slow (cold connections/caches).
# The cost is not a stable ~13s: it is VARIABLE, 2-25s, and load-dependent.
#
# A bound sized on the median therefore cuts legitimate searches under load — which
# is exactly what a 20.0s bound did: a live run completed 0 of 9 templates because
# every search was cut, then honestly recorded as a breaker skip (templates_searched
# 0). The instrument is wrong: the per-search bound should be a HANG guard, and the
# DEADLINE should bound total work.
#
# CEILING: the caller wraps the sweep in wait_for(deadline + 30s) = 210s
# (dd_orchestrator ~:9145). A template may start at deadline-epsilon, so
# deadline(180) + bound must stay under 210 -> bound <= 30. 25.0 covers the observed
# 24.73s worst case with margin (180 + 25 = 205 < 210).
ADVERSE_SEARCH_TIMEOUT_S = float(os.getenv("ARIA_ADVERSE_SEARCH_TIMEOUT_S", "25.0"))



@fail_wire(module="researcher", gap_type="source_failure")
async def run_adverse_media_deep_search(
    entity_name: str,
    *,
    director_names: list[str] | None = None,
    ubo_names: list[str] | None = None,
    sectors: list[str] | None = None,
    years_back: int = 10,
    max_templates: int = 30,
    max_results_per_template: int = 6,
    deadline_s: float | None = None,
) -> dict:
    """Execute the adverse-media discipline deeply, not just superficially.

    For the given entity (+ optional directors / UBOs / sectors), generates
    structured queries from dd_disciplines.adverse_media_query_templates()
    and executes each via ARIA's multi-backend web_search. Aggregates
    findings with source URL, source_class tag, credibility tier, and
    matched-pattern context.

    Args:
      entity_name:   The primary target.
      director_names: Optional list of named directors (per Layer 2 graph).
      ubo_names:     Optional list of UBO natural persons.
      sectors:       Optional list of sector tags ("defence", "oil", "lng",
                     etc.) — drives sector-specific trade-press templates.
      years_back:    Time-window for news-archive templates (default 10y).
      max_templates: Cap total templates executed (default 30; full set
                     can be 50+ for entity + 3 directors + 2 UBOs).
      max_results_per_template: Cap results per template execution.

    Returns:
      {
        ok: bool,
        entity: str,
        templates_run: int,
        templates_total_in_set: int,
        findings: [
          {source_class, source_url, title, snippet, credibility_tier,
           query_executed, matched_template_purpose}
        ],
        coverage_by_class: {<source_class>: <count>},
        execution_time_seconds: float,
        circuit_breaker_skips: int,
        clause_17_attribution: 'every finding carries source URL + credibility tier; verify against primary sources before quoting in DD report',
      }

    Per Clause 7 (knowing limits): this function executes the discipline,
    it does NOT verify the findings. Each finding requires operator review
    + tier validation before being treated as established fact in a DD.
    """
    if not entity_name or not entity_name.strip():
        return {"ok": False, "error": "entity_name required"}

    started = time.time()

    try:
        from . import dd_disciplines as _dd_disc
    except Exception as e:
        return {"ok": False, "error": f"dd_disciplines module not available: {e}"}

    # Generate template set
    try:
        templates = _dd_disc.adverse_media_query_templates(
            entity_name=entity_name,
            director_names=director_names or [],
            ubo_names=ubo_names or [],
            sectors=sectors or [],
            years_back=years_back,
        )
    except Exception as e:
        # §21a — failure branch must reach the brain sink too (balances the
        # wire_success on the success path below).
        wire_failure(
            module="researcher",
            detail=f"adverse-media template generation failed: {str(e)[:180]}",
            gap_type="engine_failure",
            source="researcher:R-F996",
        )
        return {"ok": False, "error": f"template generation failed: {e}"}

    total_templates = len(templates)
    # Cap to avoid burst-load on backends (circuit breakers will catch
    # overload anyway, but be polite)
    # R-F2847 — NO derived cap. With the per-search bound now ABOVE measured cost
    # (see ADVERSE_SEARCH_TIMEOUT_S) searches actually COMPLETE, and the existing
    # R-F2667 deadline check below stops the loop when the budget is spent —
    # templates finish one by one until then. A count cap sized on the timeout
    # CEILING was over-conservative: it truncated sweeps that would have completed
    # comfortably (caught by R-F2791's 4-template/30s case, which it cut to 1).
    # Worst case is bounded: a template starting at deadline-1s ends ~20s later,
    # inside the caller's 210s wait_for backstop.
    templates_to_run = templates[:max_templates]

    findings: list[dict] = []
    # R-F3516 — two facts, two counters. `_class_asked` is how many templates of this
    # class ran; `_class_answered` is how many findings the class's OWN sources actually
    # returned. Collapsing them into one number is what let a silent source read as a
    # screened-clean one. `coverage_by_class` is retained as the asked count for
    # backward compatibility and re-stated honestly in the result (see below).
    coverage_by_class: dict[str, int] = {}
    _class_asked: dict[str, int] = {}
    _class_answered: dict[str, int] = {}
    breaker_skips = 0
    _templates_done = 0
    _templates_searched = 0   # R-F2791: templates that actually reached the search layer
    _backends_answered = False  # R-F2791: did ANY template get a raw result back?
    _timed_out = False
    # R-F2745 — the subject's names (entity + directors + UBOs). A finding is only the
    # subject's adverse media if the article NAMES one of these, not just the topic.
    _name_token_sets = _adverse_relevance_token_sets(
        [entity_name, *(director_names or []), *(ubo_names or [])]
    )
    _off_subject_dropped = 0

    # Execute templates sequentially with brief throttle to avoid
    # tripping per-host breakers unnecessarily. Full parallel would be
    # faster but riskier on backend rate-limits.
    for tmpl in templates_to_run:
        # R-F2667 — self-bounding deadline. On a high-press entity this 30-template
        # sequential search can exceed the caller's budget; STOP here and return the
        # PARTIAL findings gathered so far (honest partial adverse-media) instead of being
        # cancelled by an outer wait_for and losing everything (the live-DD defect where a
        # BAE Systems follow-up timed out at 180s → 0 findings + an empty error message).
        if deadline_s is not None and (time.time() - started) >= deadline_s:
            _timed_out = True
            break
        _templates_done += 1
        query = tmpl.get("query", "")
        if not query:
            continue
        source_class = tmpl.get("source_class", "unknown")
        purpose = tmpl.get("purpose", "")

        try:
            # R-F2832 — strict: a timed-out template must be accounted as a
            # breaker skip, NOT as a template that was searched and found
            # nothing (R-F2791 `_templates_searched`).
            # R-F2846 — screening=True: this loop inspects EVERY hit, so candidate
            # ORDER is irrelevant, and the re-rank was 79% of each search's cost.
            search_results = await _web_search(
                query, timeout=ADVERSE_SEARCH_TIMEOUT_S,
                raise_on_timeout=True, screening=True,
            )
        except Exception as e:
            logger.debug("[adverse_media] template %r failed: %s", source_class, e)
            breaker_skips += 1
            continue
        # R-F2791 — templates that actually reached the search layer, as opposed to
        # _templates_done which counts templates ENTERED (it is incremented above,
        # before the call). Consumers must never certify a screening on the entered
        # count: a sweep where every backend call failed still reports 30/30 there.
        _templates_searched += 1

        if not search_results:
            continue
        # R-F2791 — OBSERVATIONAL proof that the search infrastructure answered,
        # taken from the run itself rather than a separate health probe (free, and
        # it cannot disagree with what this sweep actually experienced). Recorded
        # BEFORE the subject-name filter below on purpose: a genuinely clean entity
        # gets plenty of raw hits for "<name> fraud" that are then dropped as
        # off-subject — that is a working search with zero findings, which IS valid
        # negative evidence. Zero RAW results across every template is not.
        _backends_answered = True

        # Capture top-N per template (per max_results_per_template)
        for raw_hit in search_results[:max_results_per_template]:
            r = _search_hit_to_dict(raw_hit)
            url = r.get("link") or r.get("url") or ""
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            tier = r.get("_credibility_tier", "")
            if not url or not title:
                continue
            # R-F2745 — drop results that match the adverse TOPIC but do not name the
            # subject (or a director/UBO): they are not the subject's adverse media.
            if not _adverse_hit_names_subject(f"{title} {snippet}", _name_token_sets):
                _off_subject_dropped += 1
                continue
            # ── R-F3023 — the source class describes the QUERY, not the RESULT ──
            #
            # THE DEFECT (live, dd_16db41eb5fa8). `source_class` and
            # `matched_template_purpose` are the TEMPLATE's intent — "UK + Ireland
            # case law involving this party", "regulatory_us_doj". They were written
            # onto the finding verbatim, whatever actually came back. When the news
            # and court backends were all silent and only the academic backend
            # answered, a molecular-biology paper ("Genetically engineered bacterial
            # cells", Toxicology Letters 1995) was stored as
            # `source_class: regulatory_us_doj` / `legal_court_us_federal`. The
            # report then presented academic DOIs as court and DOJ records.
            #
            # The template's intent is still recorded (it is real provenance about
            # what was ASKED), but it is no longer allowed to masquerade as a fact
            # about the result. `source_domain` is what the result IS, and
            # `source_class_corroborated` says plainly whether the two agree — so a
            # consumer can require corroboration before treating a class as true.
            _domain = _result_domain(url)
            _corrob = _domain_corroborates_class(_domain, source_class, query)
            findings.append({
                "source_class": source_class,
                "source_url": url,
                "title": title[:240],
                "snippet": snippet[:400],
                "credibility_tier": tier,
                "query_executed": query[:240],
                "matched_template_purpose": purpose[:240],
                # R-F3023 — result-derived provenance
                "source_domain": _domain,
                "source_class_corroborated": _corrob,
            })
            if _corrob:
                _class_answered[source_class] = _class_answered.get(source_class, 0) + 1

        # ── R-F3516 — coverage counts TEMPLATES ASKED, and said "covered" ────────
        #
        # R-F3023 stopped one line short. It made each FINDING honest — carrying
        # `source_domain` and `source_class_corroborated` — and then this counter went on
        # incrementing off `source_class`, the TEMPLATE's intent, so the sweep's headline
        # coverage claim kept asserting exactly what R-F3023 had just proved unreliable.
        # The producer had no carrier into the number a reader actually reads.
        #
        # THE LIVE HARM (Chemring Group PLC, dd_8bd7ac42a488, a listed defence group).
        # `coverage_by_class` reported leak_database_icij, investigative_journalism_occrp,
        # investigative_journalism_bellingcat, regulatory_us_doj, regulatory_us_sec,
        # regulatory_us_ofac, legal_court_us_federal — while 75 of 92 findings were NOT
        # corroborated and those classes had ZERO corroborated rows between them. What
        # actually came back was the subject's own Companies House pages and ARIA's own
        # `memory://` records. A reader sees "ICIJ, OCCRP, Bellingcat, DOJ, SEC, SFO
        # screened, nothing material" and concludes those sources are clean. They were
        # never heard from. That is a FALSE CLEAN on the adverse-media layer.
        #
        # `templates_asked` is kept — it is true, and it is the honest denominator. But
        # "we asked" and "the source answered" are different facts and must not share one
        # number.
        _class_asked[source_class] = _class_asked.get(source_class, 0) + 1

        # Polite throttle — 100ms between templates
        await asyncio.sleep(0.1)

    # Sort findings by credibility tier (highest first), then by source_class
    _tier_rank = {"tier_1a": 0, "tier_1b": 1, "tier_2": 2, "tier_3": 3, "": 9}
    findings.sort(key=lambda f: (_tier_rank.get(f.get("credibility_tier", ""), 9), f.get("source_class", "")))

    duration = round(time.time() - started, 2)


    # R-F996 — wire to brain
    wire_success(
        module="researcher",
        summary="Research",
        source_id="researcher:R-F996",
    )
    return {
        "ok": True,
        "entity": entity_name,
        "templates_run": _templates_done,  # R-F2667: templates ENTERED (see templates_searched)
        # R-F2791 — the two fields consumers must use to decide whether a
        # zero-finding sweep is valid negative evidence. templates_run alone
        # certified sweeps in which every backend call failed.
        "templates_searched": _templates_searched,
        "search_backends_answered": _backends_answered,
        "templates_total_in_set": total_templates,
        "templates_capped_at": max_templates,
        # R-F2667 — True when the deadline stopped the search early; findings below are a
        # PARTIAL (honest) result, not the full sweep.
        "partial": _timed_out,
        "timed_out": _timed_out,
        "findings": findings,
        "findings_count": len(findings),
        # R-F2745 — results that matched the adverse topic but did not name the subject
        # (a different same-named entity, or generic topic news) were NOT attributed.
        "off_subject_dropped": _off_subject_dropped,
        # R-F3516 — "we asked" and "the source answered" are separate facts.
        #
        # `coverage_by_class` is the ASKED count and keeps its old meaning and shape so
        # no existing consumer silently changes behaviour — but it is no longer the only
        # thing on offer, and it is no longer the one to read when the question is
        # "was this source actually screened?".
        #
        # `classes_answered` counts findings whose OWN domain corroborated the class.
        # `classes_silent` is the difference: templates that ran and whose target source
        # returned nothing attributable to it. A silent source is real negative evidence
        # and must be reported AS silence — presenting it as coverage is a false clean.
        "coverage_by_class": coverage_by_class or dict(_class_asked),
        "classes_asked": dict(_class_asked),
        "classes_answered": dict(_class_answered),
        "classes_silent": sorted(c for c in _class_asked if not _class_answered.get(c)),
        "coverage_note": (
            "classes_asked = templates executed for that source class. "
            "classes_answered = findings whose own domain corroborated the class. "
            "A class in classes_silent was SEARCHED and its own sources returned "
            "nothing attributable to them — that is negative evidence, NOT a clean "
            "screen of that source. Do not read coverage_by_class as confirmation "
            "that a source was reached."
        ),
        "execution_time_seconds": duration,
        "circuit_breaker_skips": breaker_skips,
        "clause_17_attribution": (
            "Every finding carries source URL + credibility tier per Clause 17. "
            "BEFORE quoting in a DD report, the operator must verify each finding "
            "against the primary source. This function executes the discipline "
            "(structured deep search across 30+ source classes) — verification of "
            "individual findings is the operator's responsibility per Clause 14 "
            "(no fabricated verifiable facts) and Clause 17 (multi-source verification)."
        ),
        "framework_note": (
            "Generated by run_adverse_media_deep_search (R-F159) using "
            "dd_disciplines.adverse_media_query_templates (R-F152) routed "
            "through web_search.search_multilingual. Source-class coverage "
            "shows how many distinct query templates returned at least one "
            "result — an empty class means either no findings exist OR the "
            "query format doesn't match content the search backends index."
        ),
    }

