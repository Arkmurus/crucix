"""
Tender Monitor — proactive defence procurement portal crawler.

Crawls public defence procurement portals (TED, SAM.gov, Contracts Finder,
UNGM, AfDB) for relevant tenders and scores them against Arkmurus product
categories and target markets. Equivalent functionality to Janes/IHS Markit
tender monitoring ($100k/year) — built in-house.

Portals:
  1. TED (Tenders Electronic Daily) — EU defence procurement (free REST API)
  2. SAM.gov — US government procurement (API key required)
  3. Contracts Finder — UK government procurement (free REST API)
  4. UNGM — UN procurement (web scrape)
  5. AfDB — African Development Bank procurement (web scrape)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from . import redis_store as rs

logger = logging.getLogger("aria.tender_monitor")

# ── Redis keys ────────────────────────────────────────────────────────────────
_KEY_ALERTS = "crucix:aria:tender_monitor:alerts"       # list of JSON alerts
_KEY_SEEN = "crucix:aria:tender_monitor:seen"            # JSON set of seen IDs
_KEY_STATS = "crucix:aria:tender_monitor:stats"          # last-run stats
_KEY_PORTAL_HEALTH = "crucix:aria:tender_monitor:health" # per-portal health

# ── Constants ─────────────────────────────────────────────────────────────────
_MAX_ALERTS = 500
_HTTP_TIMEOUT = 20.0
_MAX_DESCRIPTION_LEN = 500

# ── Arkmurus product categories ──────────────────────────────────────────────
PRODUCT_CATEGORIES = [
    "armoured_vehicles",
    "ammunition",
    "detonators",
    "explosives",
    "patrol_boats",
    "surveillance_systems",
    "training_services",
    "border_security",
    "communication_systems",
    "personal_protective_equipment",
    "military_logistics",
    "weapons_systems",
]

# Keywords that indicate relevance to Arkmurus capabilities
RELEVANCE_KEYWORDS = {
    "armoured_vehicles": [
        "armoured vehicle", "armored vehicle", "apc", "mrap", "ifv",
        "personnel carrier", "military vehicle", "tactical vehicle",
        "mine-resistant", "mine resistant", "protected vehicle",
    ],
    "ammunition": [
        "ammunition", "ammo", "cartridge", "calibre", "caliber",
        "small arms ammunition", "mortar round", "shell", "munition",
    ],
    "detonators": [
        "detonator", "fuse", "fuze", "primer", "initiator",
        "blasting cap", "detonation",
    ],
    "explosives": [
        "explosive", "tnt", "rdx", "c4", "demolition charge",
        "pyrotechnic", "propellant",
    ],
    "patrol_boats": [
        "patrol boat", "patrol vessel", "fast attack craft",
        "interceptor boat", "coastal patrol", "naval patrol",
        "offshore patrol", "maritime patrol",
    ],
    "surveillance_systems": [
        "surveillance", "radar", "sensor", "electro-optical",
        "night vision", "thermal imaging", "uav", "drone",
        "intelligence surveillance reconnaissance", "isr",
        "cctv", "monitoring system",
    ],
    "training_services": [
        "military training", "defence training", "defense training",
        "combat training", "simulation", "training services",
        "capacity building", "peacekeeping training",
    ],
    "border_security": [
        "border security", "border control", "border surveillance",
        "perimeter security", "checkpoint", "border management",
    ],
    "communication_systems": [
        "communication system", "radio", "tactical communication",
        "secure communication", "c4isr", "command and control",
        "intercom", "satcom",
    ],
    "personal_protective_equipment": [
        "body armour", "body armor", "ballistic", "helmet",
        "protective vest", "ceramic plate", "kevlar",
    ],
    "military_logistics": [
        "military logistics", "defence logistics", "defense logistics",
        "supply chain", "sustainment", "maintenance repair overhaul",
        "mro", "spare parts",
    ],
    "weapons_systems": [
        "weapon system", "weapons system", "firearms", "rifle",
        "machine gun", "grenade launcher", "mortar", "howitzer",
        "anti-tank", "anti-aircraft",
    ],
}

# Defence-related CPV code prefixes (EU Common Procurement Vocabulary)
DEFENCE_CPV_PREFIXES = [
    "35",       # Security, defence, offence materials
    "50600",    # Repair and maintenance of defence/security material
    "60400",    # Military transport
    "71330",    # Non-structural engineering — often used for defence consulting
]

# Target markets — countries where Arkmurus is active or pursuing business
TARGET_MARKETS = {
    "AO": "Angola", "MZ": "Mozambique", "NG": "Nigeria", "GH": "Ghana",
    "KE": "Kenya", "TZ": "Tanzania", "UG": "Uganda", "RW": "Rwanda",
    "ET": "Ethiopia", "ZA": "South Africa", "SN": "Senegal", "CI": "Cote d'Ivoire",
    "CM": "Cameroon", "GA": "Gabon", "CG": "Congo", "CD": "DR Congo",
    "PL": "Poland", "RO": "Romania", "BG": "Bulgaria", "CZ": "Czechia",
    "HU": "Hungary", "SK": "Slovakia", "HR": "Croatia", "SI": "Slovenia",
    "LT": "Lithuania", "LV": "Latvia", "EE": "Estonia",
    "TR": "Turkey", "GR": "Greece", "PT": "Portugal", "ES": "Spain",
    "IT": "Italy", "FR": "France", "DE": "Germany", "GB": "United Kingdom",
    "SE": "Sweden", "FI": "Finland", "NO": "Norway", "DK": "Denmark",
    "BR": "Brazil", "CO": "Colombia", "CL": "Chile", "PE": "Peru",
    "ID": "Indonesia", "MY": "Malaysia", "TH": "Thailand", "PH": "Philippines",
    "IN": "India", "SA": "Saudi Arabia", "AE": "UAE", "QA": "Qatar",
    "OM": "Oman", "BH": "Bahrain", "KW": "Kuwait", "JO": "Jordan",
    "AU": "Australia", "NZ": "New Zealand", "JP": "Japan", "KR": "South Korea",
    "US": "United States", "CA": "Canada",
}

# Country name → ISO2 lookup (reverse of TARGET_MARKETS + common variants)
_COUNTRY_TO_ISO2: dict[str, str] = {}
for iso2, name in TARGET_MARKETS.items():
    _COUNTRY_TO_ISO2[name.lower()] = iso2
# Add common alternate names
_COUNTRY_TO_ISO2.update({
    "uk": "GB", "united kingdom": "GB", "britain": "GB",
    "usa": "US", "united states of america": "US",
    "türkiye": "TR", "turkiye": "TR",
    "ivory coast": "CI", "côte d'ivoire": "CI",
    "south korea": "KR", "republic of korea": "KR",
    "uae": "AE", "united arab emirates": "AE",
    "czech republic": "CZ", "czechia": "CZ",
    "democratic republic of congo": "CD", "drc": "CD",
    "republic of congo": "CG",
})

# SAM.gov NAICS codes for defence
SAM_NAICS_CODES = [
    "332993",  # Ammunition (except small arms)
    "332994",  # Small arms ammunition
    "336992",  # Military armored vehicle manufacturing
    "336411",  # Aircraft manufacturing
    "336414",  # Guided missile/space vehicle manufacturing
    "334511",  # Search, detection, navigation instruments
    "334220",  # Radio/TV broadcast and wireless communications
    "611699",  # Other miscellaneous schools (military training)
    "541990",  # Other professional/technical services
    "561210",  # Facilities support services
]


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class TenderAlert:
    id: str                          # tender_XXXX
    portal: str                      # TED | SAM_GOV | UNGM | CONTRACTS_FINDER | AFDB
    title: str
    description: str                 # first 500 chars
    buyer: str                       # procuring entity
    country: str
    country_iso2: str
    value_estimate: str              # "EUR 5M" or "undisclosed"
    cpv_codes: list[str]             # Common Procurement Vocabulary codes
    deadline: str                    # submission deadline
    publication_date: str
    url: str                         # link to full tender
    relevance_score: float = 0.0     # 0-1, how relevant to Arkmurus
    relevance_reasons: list[str] = field(default_factory=list)
    matched_products: list[str] = field(default_factory=list)
    detected_at: str = ""            # ISO timestamp

    def __post_init__(self):
        if not self.detected_at:
            self.detected_at = datetime.now(timezone.utc).isoformat()
        if not self.id:
            # Generate deterministic ID from portal + title
            h = hashlib.md5(f"{self.portal}:{self.title}".encode()).hexdigest()[:8]
            self.id = f"tender_{h}"

    def to_dict(self) -> dict:
        return asdict(self)


# ── Relevance scoring ─────────────────────────────────────────────────────────

def _score_relevance(tender: TenderAlert) -> float:
    """Score 0-1 based on CPV match, country, keywords, value, deadline."""
    score = 0.0
    reasons: list[str] = []
    matched: list[str] = []
    text = f"{tender.title} {tender.description}".lower()

    # ── CPV code match (+0.3) ─────────────────────────────────────────────
    cpv_match = False
    for cpv in tender.cpv_codes:
        for prefix in DEFENCE_CPV_PREFIXES:
            if cpv.startswith(prefix):
                cpv_match = True
                break
    if cpv_match:
        score += 0.3
        reasons.append("CPV code matches defence category")

    # ── Country in target markets (+0.2) ──────────────────────────────────
    if tender.country_iso2 and tender.country_iso2 in TARGET_MARKETS:
        score += 0.2
        reasons.append(f"Country {tender.country} is a target market")

    # ── Keyword matching (+0.3) ──────────────────────────────────────────
    keyword_score = 0.0
    for category, keywords in RELEVANCE_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text:
                if category not in matched:
                    matched.append(category)
                    keyword_score += 0.1  # Each new category match
                break
    keyword_score = min(keyword_score, 0.3)
    if keyword_score > 0:
        score += keyword_score
        reasons.append(f"Keywords match: {', '.join(matched[:5])}")

    # ── Value in realistic range (+0.1) ──────────────────────────────────
    value_score = _parse_value_range(tender.value_estimate)
    if value_score:
        score += 0.1
        reasons.append(f"Value in range: {tender.value_estimate}")

    # ── Deadline still open (+0.1) ────────────────────────────────────────
    if _deadline_still_open(tender.deadline):
        score += 0.1
        reasons.append("Deadline still open")

    tender.relevance_score = min(score, 1.0)
    tender.relevance_reasons = reasons
    tender.matched_products = matched
    # 2026-04-12: debug logging for tender filtering — helps diagnose 0-result issues
    if score < 0.2:
        logger.debug(
            "[Tender Scoring] REJECTED (score %.2f): [%s] %s | country=%s cpv=%s",
            score, tender.portal, tender.title[:80], tender.country_iso2,
            ",".join(tender.cpv_codes[:3]) or "none",
        )
    return tender.relevance_score


def _parse_value_range(value_str: str) -> bool:
    """Return True if value is in $1M-$500M range."""
    if not value_str or value_str.lower() == "undisclosed":
        return False
    # Extract numeric value
    nums = re.findall(r"[\d,]+\.?\d*", value_str.replace(",", ""))
    if not nums:
        return False
    try:
        val = float(nums[0])
        # Heuristic: if "M" or "million" in string, value is in millions
        if re.search(r"\d\s*m\b|million", value_str, re.IGNORECASE):
            val *= 1_000_000
        elif re.search(r"\bb\b|billion", value_str, re.IGNORECASE):
            val *= 1_000_000_000
        elif re.search(r"\bk\b|thousand", value_str, re.IGNORECASE):
            val *= 1_000
        # If raw number is > 100k, it's likely in base currency
        return 1_000_000 <= val <= 500_000_000
    except (ValueError, IndexError):
        return False


def _deadline_still_open(deadline: str) -> bool:
    """Return True if deadline hasn't passed yet."""
    if not deadline:
        return False
    try:
        # Try common date formats
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                    "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(deadline, fmt).replace(tzinfo=timezone.utc)
                return dt > datetime.now(timezone.utc)
            except ValueError:
                continue
        # If we parsed an ISO timestamp with timezone
        if "T" in deadline:
            dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            return dt > datetime.now(timezone.utc)
    except Exception:
        pass
    return False


def _resolve_country_iso2(country_name: str) -> str:
    """Resolve country name to ISO2 code."""
    if not country_name:
        return ""
    # If already an ISO2 code
    if len(country_name) == 2 and country_name.upper() in TARGET_MARKETS:
        return country_name.upper()
    return _COUNTRY_TO_ISO2.get(country_name.lower().strip(), "")


# ── Portal crawlers ──────────────────────────────────────────────────────────


async def _crawl_ted(client: httpx.AsyncClient, max_results: int = 20) -> list[TenderAlert]:
    """Crawl TED (Tenders Electronic Daily) — EU defence procurement.

    Uses the TED free REST API v3. Filters by CPV codes in the defence range
    (35000000-35999999) and target market country codes.
    """
    tenders: list[TenderAlert] = []
    try:
        # TED API v3.0 requires POST with JSON body, not GET with query params.
        url = "https://ted.europa.eu/api/v3.0/notices/search"
        # Build the POST body — q uses Lucene-style query syntax;
        # cpv:35* is too narrow, broaden to include maintenance/transport CPVs.
        pub_from = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y%m%d")
        pub_to = datetime.now(timezone.utc).strftime("%Y%m%d")
        body = {
            "query": "(cpv:35* OR cpv:506* OR cpv:604*)",
            "fields": [
                "title", "description", "buyerName", "organisationName",
                "country", "countryCode", "cpvCodes", "cpv",
                "estimatedValue", "valueEur", "deadline",
                "submissionDeadline", "publicationDate", "PD",
                "noticeId", "docId",
            ],
            "pageSize": min(max_results, 50),
            "page": 1,
            "sortField": "PD",
            "sortOrder": "desc",
        }
        logger.debug("[TED] POST body: %s", json.dumps(body)[:300])
        resp = await client.post(
            url, json=body, timeout=_HTTP_TIMEOUT,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            logger.warning("[TED] API returned %d: %s", resp.status_code, resp.text[:300])
            return tenders

        data = resp.json()
        if not isinstance(data, dict):
            if isinstance(data, list):
                notices = data
            else:
                logger.warning("[TED] API returned non-dict: %s", type(data))
                return tenders
        else:
            notices = data.get("results") or data.get("notices") or data.get("hits") or []
        if not isinstance(notices, list):
            logger.warning("[TED] API notices not a list: %s", type(notices))
            return tenders
        logger.info("[TED] API returned %d raw notices", len(notices))

        for notice in notices[:max_results]:
            try:
                title = (
                    notice.get("title", {}).get("text", "")
                    if isinstance(notice.get("title"), dict)
                    else str(notice.get("title", ""))
                )
                description = str(notice.get("description", {}).get("text", "")
                                  if isinstance(notice.get("description"), dict)
                                  else notice.get("description", ""))[:_MAX_DESCRIPTION_LEN]
                buyer = str(notice.get("buyerName", notice.get("organisationName", "")))
                country = str(notice.get("country", notice.get("countryCode", "")))
                country_iso2 = _resolve_country_iso2(country) or country[:2].upper()

                # Extract CPV codes
                cpv_codes = []
                cpv_raw = notice.get("cpvCodes") or notice.get("cpv") or []
                if isinstance(cpv_raw, list):
                    cpv_codes = [str(c.get("code", c) if isinstance(c, dict) else c) for c in cpv_raw]
                elif isinstance(cpv_raw, str):
                    cpv_codes = [cpv_raw]

                # Value
                value = notice.get("estimatedValue") or notice.get("valueEur") or ""
                if value and isinstance(value, (int, float)):
                    value = f"EUR {value:,.0f}"
                elif not value:
                    value = "undisclosed"
                else:
                    value = str(value)

                deadline = str(notice.get("deadline", notice.get("submissionDeadline", "")))
                pub_date = str(notice.get("publicationDate", notice.get("PD", "")))
                notice_id = str(notice.get("noticeId", notice.get("docId", "")))
                tender_url = f"https://ted.europa.eu/en/notice/-/detail/{notice_id}" if notice_id else ""

                t = TenderAlert(
                    id="",
                    portal="TED",
                    title=title,
                    description=description,
                    buyer=buyer,
                    country=country,
                    country_iso2=country_iso2,
                    value_estimate=value,
                    cpv_codes=cpv_codes,
                    deadline=deadline,
                    publication_date=pub_date,
                    url=tender_url,
                )
                tenders.append(t)
            except Exception as e:
                logger.debug("[TED] Failed to parse notice: %s", e)
                continue

    except httpx.TimeoutException:
        logger.warning("[TED] Request timed out")
    except Exception as e:
        logger.warning("[TED] Crawl failed: %s", e)

    logger.info("[TED] Crawled %d tenders", len(tenders))
    return tenders


async def _crawl_sam_gov(client: httpx.AsyncClient, max_results: int = 20) -> list[TenderAlert]:
    """Crawl SAM.gov — US government procurement.

    Requires SAM_GOV_API_KEY env var. Skipped if not set.
    """
    tenders: list[TenderAlert] = []
    api_key = os.getenv("SAM_GOV_API_KEY", "").strip()
    if not api_key:
        logger.info("[SAM.gov] Skipped — SAM_GOV_API_KEY not set")
        return tenders

    try:
        url = "https://api.sam.gov/opportunities/v2/search"
        # Search for active defence opportunities
        params = {
            "api_key": api_key,
            "limit": min(max_results, 25),
            "postedFrom": (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%m/%d/%Y"),
            "postedTo": datetime.now(timezone.utc).strftime("%m/%d/%Y"),
            "ncode": ",".join(SAM_NAICS_CODES[:5]),  # Top 5 NAICS codes
            "ptype": "o",  # Opportunities
        }
        resp = await client.get(url, params=params, timeout=_HTTP_TIMEOUT)
        if resp.status_code != 200:
            logger.warning("[SAM.gov] API returned %d: %s", resp.status_code, resp.text[:200])
            return tenders

        data = resp.json()
        opportunities = data.get("opportunitiesData") or data.get("opportunities") or []

        for opp in opportunities[:max_results]:
            try:
                title = str(opp.get("title", ""))
                description = str(opp.get("description", ""))[:_MAX_DESCRIPTION_LEN]
                # Clean HTML from description
                description = re.sub(r"<[^>]+>", " ", description).strip()
                buyer = str(opp.get("fullParentPathName", opp.get("department", "")))

                # Value
                award = opp.get("award") or {}
                value = award.get("amount") or opp.get("estimatedValue") or ""
                if value and isinstance(value, (int, float)):
                    value = f"USD {value:,.0f}"
                elif not value:
                    value = "undisclosed"

                deadline = str(opp.get("responseDeadLine", opp.get("archiveDate", "")))
                pub_date = str(opp.get("postedDate", ""))
                notice_id = str(opp.get("noticeId", opp.get("solicitationNumber", "")))
                opp_url = f"https://sam.gov/opp/{notice_id}/view" if notice_id else ""

                # NAICS
                naics = opp.get("naicsCode") or ""
                cpv_codes = [str(naics)] if naics else []

                t = TenderAlert(
                    id="",
                    portal="SAM_GOV",
                    title=title,
                    description=description,
                    buyer=buyer,
                    country="United States",
                    country_iso2="US",
                    value_estimate=str(value),
                    cpv_codes=cpv_codes,
                    deadline=deadline,
                    publication_date=pub_date,
                    url=opp_url,
                )
                tenders.append(t)
            except Exception as e:
                logger.debug("[SAM.gov] Failed to parse opportunity: %s", e)
                continue

    except httpx.TimeoutException:
        logger.warning("[SAM.gov] Request timed out")
    except Exception as e:
        logger.warning("[SAM.gov] Crawl failed: %s", e)

    logger.info("[SAM.gov] Crawled %d tenders", len(tenders))
    return tenders


async def _crawl_contracts_finder(client: httpx.AsyncClient, max_results: int = 20) -> list[TenderAlert]:
    """Crawl Contracts Finder — UK government procurement.

    Free REST API, no auth needed. Uses OCDS (Open Contracting Data Standard) format.
    """
    tenders: list[TenderAlert] = []
    try:
        # Contracts Finder OCDS API — publishedFrom must be dd/MM/yyyy,
        # stages must be "tender", and keyword helps find defence results.
        url = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
        pub_from = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%d/%m/%Y")
        params = {
            "stages": "tender",
            "size": min(max_results, 50),
            "publishedFrom": pub_from,
            "keyword": "defence OR military OR security OR ammunition OR armoured",
        }
        logger.debug("[Contracts Finder] params: %s", params)
        resp = await client.get(url, params=params, timeout=_HTTP_TIMEOUT)
        if resp.status_code != 200:
            logger.warning("[Contracts Finder] API returned %d: %s", resp.status_code, resp.text[:300])
            return tenders

        data = resp.json()
        releases = data.get("releases") or data.get("results") or []
        logger.info("[Contracts Finder] API returned %d raw releases", len(releases))

        for release in releases[:max_results]:
            try:
                tender_data = release.get("tender") or {}
                title = str(tender_data.get("title", release.get("title", "")))
                description = str(tender_data.get("description", ""))[:_MAX_DESCRIPTION_LEN]

                # Defence keyword filter — relaxed because the API-level
                # keyword param already pre-filters.  We still keep a
                # lightweight check so non-defence stragglers are skipped.
                text_lower = f"{title} {description}".lower()
                defence_kws = [
                    "defence", "defense", "military", "security", "armed forces",
                    "mod ", "ministry of defence", "police", "border", "surveillance",
                    "ammunition", "armoured", "armored", "weapon", "navy", "army",
                    "vehicle", "patrol", "radar", "communication",
                ]
                if not any(kw in text_lower for kw in defence_kws):
                    continue

                buyer_data = release.get("buyer") or {}
                buyer = str(buyer_data.get("name", ""))
                deadline = str(tender_data.get("tenderPeriod", {}).get("endDate", ""))
                pub_date = str(release.get("date", ""))

                # Value
                value_data = tender_data.get("value") or tender_data.get("minValue") or {}
                if isinstance(value_data, dict):
                    amount = value_data.get("amount")
                    currency = value_data.get("currency", "GBP")
                    value = f"{currency} {amount:,.0f}" if amount else "undisclosed"
                else:
                    value = "undisclosed"

                # CPV / classification
                cpv_codes = []
                items = tender_data.get("items") or []
                for item in items:
                    classification = item.get("classification") or {}
                    code = classification.get("id", "")
                    if code:
                        cpv_codes.append(str(code))

                ocid = release.get("ocid", "")
                tender_url = f"https://www.contractsfinder.service.gov.uk/Notice/{ocid}" if ocid else ""

                t = TenderAlert(
                    id="",
                    portal="CONTRACTS_FINDER",
                    title=title,
                    description=description,
                    buyer=buyer,
                    country="United Kingdom",
                    country_iso2="GB",
                    value_estimate=value,
                    cpv_codes=cpv_codes,
                    deadline=deadline,
                    publication_date=pub_date,
                    url=tender_url,
                )
                tenders.append(t)
            except Exception as e:
                logger.debug("[Contracts Finder] Failed to parse release: %s", e)
                continue

    except httpx.TimeoutException:
        logger.warning("[Contracts Finder] Request timed out")
    except Exception as e:
        logger.warning("[Contracts Finder] Crawl failed: %s", e)

    logger.info("[Contracts Finder] Crawled %d tenders", len(tenders))
    return tenders


async def _crawl_ungm(client: httpx.AsyncClient, max_results: int = 20) -> list[TenderAlert]:
    """Crawl UNGM (UN Global Marketplace) — UN procurement.

    No API — web scrape with httpx + regex extraction.
    Filters for defence/security/peacekeeping keywords.
    """
    tenders: list[TenderAlert] = []
    try:
        url = "https://www.ungm.org/Public/Notice"
        resp = await client.get(url, timeout=_HTTP_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            logger.warning("[UNGM] Returned %d", resp.status_code)
            return tenders

        html = resp.text

        # Extract notice blocks — UNGM renders notice cards with title/deadline/org
        # Pattern: look for notice titles and associated metadata
        # The page structure varies, so we use multiple regex strategies

        # Strategy 1: Look for notice links with IDs
        notice_pattern = re.compile(
            r'href=["\'](?:/Public/Notice/(\d+))["\'][^>]*>([^<]+)</a>',
            re.IGNORECASE,
        )
        deadline_pattern = re.compile(
            r'(?:deadline|closing\s*date)[:\s]*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})',
            re.IGNORECASE,
        )
        org_pattern = re.compile(
            r'(?:organization|agency|entity)[:\s]*([^<\n]{3,80})',
            re.IGNORECASE,
        )

        # Defence/security/peacekeeping keywords for filtering
        ungm_keywords = [
            "defence", "defense", "military", "security", "peacekeeping",
            "police", "armoured", "armored", "ammunition", "weapon",
            "surveillance", "border", "mine action", "demining",
            "patrol", "protection force", "armed", "explosive",
        ]

        matches = notice_pattern.findall(html)
        deadlines_found = deadline_pattern.findall(html)
        orgs_found = org_pattern.findall(html)
        logger.info("[UNGM] Page fetched (%d chars), regex found %d notice links", len(html), len(matches))

        for i, (notice_id, title) in enumerate(matches[:max_results * 2]):
            title = title.strip()
            title_lower = title.lower()
            if not any(kw in title_lower for kw in ungm_keywords):
                continue

            deadline = deadlines_found[i] if i < len(deadlines_found) else ""
            org = orgs_found[i].strip() if i < len(orgs_found) else "United Nations"

            t = TenderAlert(
                id="",
                portal="UNGM",
                title=title,
                description=title[:_MAX_DESCRIPTION_LEN],
                buyer=org,
                country="International",
                country_iso2="",
                value_estimate="undisclosed",
                cpv_codes=[],
                deadline=deadline,
                publication_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                url=f"https://www.ungm.org/Public/Notice/{notice_id}",
            )
            tenders.append(t)
            if len(tenders) >= max_results:
                break

    except httpx.TimeoutException:
        logger.warning("[UNGM] Request timed out")
    except Exception as e:
        logger.warning("[UNGM] Crawl failed: %s", e)

    logger.info("[UNGM] Crawled %d tenders", len(tenders))
    return tenders


async def _crawl_afdb(client: httpx.AsyncClient, max_results: int = 20) -> list[TenderAlert]:
    """Crawl AfDB (African Development Bank) procurement.

    No API — web scrape with httpx + regex.
    Filters for security/military/border/police keywords.
    """
    tenders: list[TenderAlert] = []
    try:
        url = "https://www.afdb.org/en/projects-and-operations/procurement"
        resp = await client.get(url, timeout=_HTTP_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            logger.warning("[AfDB] Returned %d", resp.status_code)
            return tenders

        html = resp.text

        # AfDB procurement page lists tenders with links, titles, countries
        notice_pattern = re.compile(
            r'href=["\']([^"\']*procurement[^"\']*)["\'][^>]*>([^<]+)</a>',
            re.IGNORECASE,
        )
        # Also try a broader pattern for project/tender links
        project_pattern = re.compile(
            r'href=["\']([^"\']+/documents/[^"\']+)["\'][^>]*>\s*([^<]+?)\s*</a>',
            re.IGNORECASE,
        )
        country_pattern = re.compile(
            r'(?:country|location)[:\s]*([A-Za-z\s]{2,40})',
            re.IGNORECASE,
        )

        afdb_keywords = [
            "security", "military", "border", "police", "defence", "defense",
            "surveillance", "patrol", "armed forces", "peacekeeping",
            "law enforcement", "mine clearance", "explosive",
        ]

        all_matches = notice_pattern.findall(html) + project_pattern.findall(html)
        countries_found = country_pattern.findall(html)
        logger.info("[AfDB] Page fetched (%d chars), regex found %d links", len(html), len(all_matches))

        seen_titles: set[str] = set()
        for i, (link, title) in enumerate(all_matches):
            title = title.strip()
            if not title or title in seen_titles:
                continue
            title_lower = title.lower()
            if not any(kw in title_lower for kw in afdb_keywords):
                continue
            seen_titles.add(title)

            country_name = countries_found[i].strip() if i < len(countries_found) else ""
            country_iso2 = _resolve_country_iso2(country_name)

            tender_url = link
            if not tender_url.startswith("http"):
                tender_url = f"https://www.afdb.org{link}"

            t = TenderAlert(
                id="",
                portal="AFDB",
                title=title,
                description=title[:_MAX_DESCRIPTION_LEN],
                buyer="African Development Bank",
                country=country_name or "Africa (Regional)",
                country_iso2=country_iso2,
                value_estimate="undisclosed",
                cpv_codes=[],
                deadline="",
                publication_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                url=tender_url,
            )
            tenders.append(t)
            if len(tenders) >= max_results:
                break

    except httpx.TimeoutException:
        logger.warning("[AfDB] Request timed out")
    except Exception as e:
        logger.warning("[AfDB] Crawl failed: %s", e)

    logger.info("[AfDB] Crawled %d tenders", len(tenders))
    return tenders


# ── Portal 6: SEACE Peru ───────────────────────────────────────────────────
# 2026-04-12: Peru's public procurement portal. Web scrape — no public API.

async def _crawl_seace_peru(client: httpx.AsyncClient, max_results: int = 20) -> list[TenderAlert]:
    """Crawl SEACE (Sistema Electrónico de Contrataciones del Estado) — Peru."""
    tenders: list[TenderAlert] = []
    try:
        # SEACE search page — filter for defence/security keywords
        url = "https://prodapp2.seace.gob.pe/seacebus-uiwd-pub/buscadorPublico/buscadorPublico.xhtml"
        resp = await client.get(url, timeout=_HTTP_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            logger.warning("[SEACE Peru] Returned %d", resp.status_code)
            return tenders

        html = resp.text
        # Parse procurement notices from HTML
        notice_re = re.compile(
            r'(?:titulo|descripcion|objeto)["\s:>]+([^<"]{20,200})',
            re.IGNORECASE,
        )
        link_re = re.compile(
            r'href=["\']([^"\']*(?:ficha|detalle|proceso)[^"\']*)["\']',
            re.IGNORECASE,
        )

        defence_kw = [
            "seguridad", "defensa", "militar", "policia", "armamento",
            "municion", "patrullaje", "blindado", "surveillance", "defence",
            "security", "military", "police", "border",
        ]

        titles = notice_re.findall(html)
        links = link_re.findall(html)
        seen: set[str] = set()

        for i, title in enumerate(titles):
            title = title.strip()
            if not title or title in seen:
                continue
            if not any(kw in title.lower() for kw in defence_kw):
                continue
            seen.add(title)

            link = links[i] if i < len(links) else ""
            if link and not link.startswith("http"):
                link = f"https://prodapp2.seace.gob.pe{link}"

            tenders.append(TenderAlert(
                id="", portal="SEACE_PERU", title=title,
                description=title[:_MAX_DESCRIPTION_LEN],
                buyer="Government of Peru",
                country="Peru", country_iso2="PE",
                value_estimate="undisclosed", cpv_codes=[],
                deadline="",
                publication_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                url=link or "https://www.seace.gob.pe",
            ))
            if len(tenders) >= max_results:
                break

    except httpx.TimeoutException:
        logger.warning("[SEACE Peru] Request timed out")
    except Exception as e:
        logger.warning("[SEACE Peru] Crawl failed: %s", e)

    logger.info("[SEACE Peru] Crawled %d tenders", len(tenders))
    return tenders


# ── Portal 7: e-licitatie Romania ──────────────────────────────────────────
# 2026-04-12: Romania's public procurement portal. Web scrape.

async def _crawl_elicitatie_romania(client: httpx.AsyncClient, max_results: int = 20) -> list[TenderAlert]:
    """Crawl e-licitatie.ro — Romania's central procurement platform."""
    tenders: list[TenderAlert] = []
    try:
        url = "https://www.e-licitatie.ro/pub/notices/ca-notices/search-ca"
        params = {"page": "0", "pageSize": str(min(max_results, 50))}
        resp = await client.get(url, params=params, timeout=_HTTP_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            logger.warning("[e-licitatie RO] Returned %d", resp.status_code)
            return tenders

        # Try JSON first (API may return JSON), fall back to HTML scrape
        try:
            data = resp.json()
            items = data.get("items") or data.get("results") or []
            for item in items[:max_results]:
                title = str(item.get("noticeTitle") or item.get("title") or "")
                if not title:
                    continue
                cpv = item.get("cpvCode") or ""
                tenders.append(TenderAlert(
                    id="", portal="ELICITATIE_RO",
                    title=title,
                    description=str(item.get("description") or title)[:_MAX_DESCRIPTION_LEN],
                    buyer=str(item.get("contractingAuthorityName") or ""),
                    country="Romania", country_iso2="RO",
                    value_estimate=str(item.get("estimatedValue") or "undisclosed"),
                    cpv_codes=[cpv] if cpv else [],
                    deadline=str(item.get("tenderReceiptDeadline") or ""),
                    publication_date=str(item.get("publicationDate") or ""),
                    url=f"https://www.e-licitatie.ro/pub/notices/ca-notices/{item.get('caNoticeId', '')}"
                        if item.get("caNoticeId") else "https://www.e-licitatie.ro",
                ))
        except (ValueError, KeyError):
            # HTML fallback — parse notice titles from page
            html = resp.text
            notice_re = re.compile(r'<td[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</td>', re.I)
            for title in notice_re.findall(html)[:max_results]:
                title = title.strip()
                if title and len(title) > 10:
                    tenders.append(TenderAlert(
                        id="", portal="ELICITATIE_RO", title=title,
                        description=title[:_MAX_DESCRIPTION_LEN],
                        buyer="", country="Romania", country_iso2="RO",
                        value_estimate="undisclosed", cpv_codes=[],
                        deadline="", publication_date="",
                        url="https://www.e-licitatie.ro",
                    ))

    except httpx.TimeoutException:
        logger.warning("[e-licitatie RO] Request timed out")
    except Exception as e:
        logger.warning("[e-licitatie RO] Crawl failed: %s", e)

    logger.info("[e-licitatie RO] Crawled %d tenders", len(tenders))
    return tenders


# ── Portal 8: MERX Canada ─────────────────────────────────────────────────
# 2026-04-12: Canada's central procurement portal (federal + provincial).

async def _crawl_merx_canada(client: httpx.AsyncClient, max_results: int = 20) -> list[TenderAlert]:
    """Crawl MERX / CanadaBuys — Canada's procurement portal."""
    tenders: list[TenderAlert] = []
    try:
        # CanadaBuys (the new MERX replacement) has a search API
        url = "https://canadabuys.canada.ca/en/tender-opportunities"
        resp = await client.get(url, timeout=_HTTP_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            logger.warning("[MERX Canada] Returned %d", resp.status_code)
            return tenders

        html = resp.text

        # Parse tender titles from HTML
        title_re = re.compile(
            r'<a[^>]*href=["\']([^"\']*tender[^"\']*)["\'][^>]*>\s*([^<]{10,200})\s*</a>',
            re.IGNORECASE,
        )
        defence_kw = [
            "defence", "defense", "military", "security", "naval",
            "arctic", "norad", "armoured", "ammunition", "surveillance",
            "patrol", "coast guard", "armed forces", "dnd",
        ]

        seen: set[str] = set()
        for link, title in title_re.findall(html):
            title = title.strip()
            if not title or title in seen:
                continue
            if not any(kw in title.lower() for kw in defence_kw):
                continue
            seen.add(title)

            tender_url = link
            if not tender_url.startswith("http"):
                tender_url = f"https://canadabuys.canada.ca{link}"

            tenders.append(TenderAlert(
                id="", portal="MERX_CANADA", title=title,
                description=title[:_MAX_DESCRIPTION_LEN],
                buyer="Government of Canada",
                country="Canada", country_iso2="CA",
                value_estimate="undisclosed", cpv_codes=[],
                deadline="",
                publication_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                url=tender_url,
            ))
            if len(tenders) >= max_results:
                break

    except httpx.TimeoutException:
        logger.warning("[MERX Canada] Request timed out")
    except Exception as e:
        logger.warning("[MERX Canada] Crawl failed: %s", e)

    logger.info("[MERX Canada] Crawled %d tenders", len(tenders))
    return tenders


# ── Deduplication ────────────────────────────────────────────────────────────

def _dedup_tenders(tenders: list[TenderAlert]) -> list[TenderAlert]:
    """Deduplicate tenders by title similarity (exact + normalised match)."""
    seen: dict[str, TenderAlert] = {}
    for t in tenders:
        # Normalise title for dedup
        norm = re.sub(r"[^a-z0-9]", "", t.title.lower())
        if norm in seen:
            # Keep the one with higher relevance
            if t.relevance_score > seen[norm].relevance_score:
                seen[norm] = t
        else:
            seen[norm] = t
    return list(seen.values())


# ── Core functions ───────────────────────────────────────────────────────────

async def crawl_all_portals(max_results_per_portal: int = 20) -> list[TenderAlert]:
    """Crawl all enabled portals in parallel, score relevance, deduplicate.

    Returns tenders sorted by relevance (highest first).
    """
    portal_health: dict[str, dict] = {}

    from .ua_rotation import random_headers
    async with httpx.AsyncClient(
        headers=random_headers(),
        follow_redirects=True,
    ) as client:
        # Launch all crawlers in parallel (8 portals)
        tasks = {
            "TED": asyncio.create_task(_crawl_ted(client, max_results_per_portal)),
            "SAM_GOV": asyncio.create_task(_crawl_sam_gov(client, max_results_per_portal)),
            "CONTRACTS_FINDER": asyncio.create_task(_crawl_contracts_finder(client, max_results_per_portal)),
            "UNGM": asyncio.create_task(_crawl_ungm(client, max_results_per_portal)),
            "AFDB": asyncio.create_task(_crawl_afdb(client, max_results_per_portal)),
            "SEACE_PERU": asyncio.create_task(_crawl_seace_peru(client, max_results_per_portal)),
            "ELICITATIE_RO": asyncio.create_task(_crawl_elicitatie_romania(client, max_results_per_portal)),
            "MERX_CANADA": asyncio.create_task(_crawl_merx_canada(client, max_results_per_portal)),
        }

        all_tenders: list[TenderAlert] = []
        for portal_name, task in tasks.items():
            try:
                result = await task
                all_tenders.extend(result)
                portal_health[portal_name] = {
                    "status": "ok",
                    "tenders_found": len(result),
                    "last_crawl": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as e:
                logger.warning("[Tender Monitor] Portal %s failed: %s", portal_name, e)
                portal_health[portal_name] = {
                    "status": "error",
                    "error": str(e),
                    "tenders_found": 0,
                    "last_crawl": datetime.now(timezone.utc).isoformat(),
                }

    # Score relevance
    for tender in all_tenders:
        _score_relevance(tender)

    # Deduplicate
    all_tenders = _dedup_tenders(all_tenders)

    # Sort by relevance (highest first)
    all_tenders.sort(key=lambda t: t.relevance_score, reverse=True)

    # Persist portal health
    try:
        await rs.set_json(_KEY_PORTAL_HEALTH, portal_health, ex=86400)
    except Exception as e:
        logger.debug("Portal health persist failed: %s", e)

    logger.info(
        "[Tender Monitor] Crawl complete: %d tenders across %d portals",
        len(all_tenders), len(portal_health),
    )
    return all_tenders


async def get_new_tenders(since_hours: int = 24) -> list[TenderAlert]:
    """Retrieve tenders from Redis detected in the last N hours."""
    try:
        raw_list = await rs.lrange(_KEY_ALERTS, 0, _MAX_ALERTS - 1)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
        tenders: list[TenderAlert] = []
        for raw in raw_list:
            try:
                d = json.loads(raw) if isinstance(raw, str) else raw
                detected_at = d.get("detected_at", "")
                if detected_at:
                    try:
                        dt = datetime.fromisoformat(detected_at.replace("Z", "+00:00"))
                        if dt < cutoff:
                            continue
                    except (ValueError, TypeError):
                        pass
                tenders.append(TenderAlert(**d))
            except Exception:
                continue
        return tenders
    except Exception as e:
        logger.warning("[Tender Monitor] get_new_tenders failed: %s", e)
        return []


async def run_monitoring_cycle() -> dict:
    """Run a full monitoring cycle: crawl, compare, store new tenders.

    Returns summary dict with portals_crawled, tenders_found, new_tenders,
    and top_matches.
    """
    start = datetime.now(timezone.utc)
    all_tenders = await crawl_all_portals()

    # Load previously seen tender IDs
    seen_raw = await rs.get(_KEY_SEEN)
    seen_ids: set[str] = set()
    if seen_raw:
        try:
            seen_ids = set(json.loads(seen_raw))
        except (json.JSONDecodeError, TypeError):
            pass

    # Identify NEW tenders
    new_tenders: list[TenderAlert] = []
    for tender in all_tenders:
        if tender.id not in seen_ids:
            new_tenders.append(tender)
            seen_ids.add(tender.id)

    # Persist new tenders to alerts list
    for tender in new_tenders:
        try:
            await rs.lpush(_KEY_ALERTS, json.dumps(tender.to_dict(), default=str))
        except Exception as e:
            logger.debug("Failed to persist tender %s: %s", tender.id, e)

    # Trim alerts list to cap
    try:
        await rs.ltrim(_KEY_ALERTS, 0, _MAX_ALERTS - 1)
    except Exception:
        pass

    # Update seen set (cap at 5000 to prevent unbounded growth)
    if len(seen_ids) > 5000:
        seen_ids = set(list(seen_ids)[-5000:])
    try:
        await rs.set(_KEY_SEEN, json.dumps(list(seen_ids)), ex=30 * 86400)  # 30 day TTL
    except Exception as e:
        logger.debug("Failed to persist seen set: %s", e)

    # Build top matches (relevance >= 0.3)
    top_matches = [
        {
            "id": t.id,
            "title": t.title[:100],
            "portal": t.portal,
            "country": t.country,
            "relevance": t.relevance_score,
            "matched_products": t.matched_products[:3],
            "value": t.value_estimate,
        }
        for t in new_tenders
        if t.relevance_score >= 0.3
    ][:10]

    duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

    stats = {
        "portals_crawled": 5,
        "tenders_found": len(all_tenders),
        "new_tenders": len(new_tenders),
        "top_matches": top_matches,
        "duration_ms": duration_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Persist stats
    try:
        await rs.set_json(_KEY_STATS, stats, ex=86400)
    except Exception:
        pass

    # ── Brain hook: feed tender insights to learning ──
    try:
        from . import brain_hook
        _tender_detail = "; ".join(
            f"{m['title'][:80]} ({m['portal']}, {m['country']}, relevance={m['relevance']:.2f})"
            for m in top_matches[:5]
        ) if top_matches else "no high-relevance tenders"
        await brain_hook.absorb(
            module="tender_monitor",
            summary=f"Tender scan: {len(all_tenders)} found, {len(new_tenders)} new, {len(top_matches)} high-relevance",
            detail=_tender_detail,
            success=len(all_tenders) > 0,
            confidence="ASSESSED",
            gap_type="api_missing" if len(all_tenders) == 0 else None,
            gap_detail="Tender monitor returned 0 results across all portals" if len(all_tenders) == 0 else None,
        )
    except Exception as _bh:
        logger.debug("tender_monitor brain_hook failed: %s", _bh)

    return stats


async def get_stats() -> dict:
    """Return last-run stats and portal health."""
    stats = await rs.get_json(_KEY_STATS) or {}
    health = await rs.get_json(_KEY_PORTAL_HEALTH) or {}
    alert_count = len(await rs.lrange(_KEY_ALERTS, 0, _MAX_ALERTS - 1))
    return {
        "last_run": stats,
        "portal_health": health,
        "total_alerts_stored": alert_count,
    }
