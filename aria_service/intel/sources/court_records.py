"""Court / litigation lookup tier (R-F579, 2026-05-16).

Why this matters for DD
───────────────────────
The 188-source curated catalogue and the OpenSanctions feed cover
sanctions, registries, sweep press, and OEM IR — but the prior pipeline
had ZERO court-record lookup. A counterparty's litigation history is
one of the strongest BD-relevance signals:

  - Fraud or breach-of-contract suits → deal risk
  - Sanctions-evasion prosecutions → counter-intelligence flag
  - Officeholder removal / bankruptcy → continuity risk
  - Adverse-witness disclosures → reputation risk
  - Defamation / IP suits → strategic disclosure-risk

This module adds two free public sources:

  1. CourtListener (US federal + state, courtlistener.com).
     Free JSON API at /api/rest/v3/search/. No API key required for
     low-volume use; rate-limited per IP.
  2. Bailii (UK / Ireland / EU court records, bailii.org). No formal
     API; we use the existing site: search fallback through Google
     News (the same pattern apis/sources/procurement_portals.mjs
     uses for portal-RSS fallback).

Both are TIER 1a authoritative sources per the existing tier hierarchy
(court judgments are primary records). A single hit from either feed
satisfies Clause 17's verification rule.

Return shape
────────────
Each hit:
    {
        "title":        "Smith v. ABC Corp",
        "court":        "S.D.N.Y.",
        "date":         "2024-03-15",
        "citation_url": "https://www.courtlistener.com/opinion/...",
        "snippet":      "Defendant alleged to have violated...",
        "jurisdiction": "US",         # or "UK"
        "source":       "courtlistener",   # or "bailii"
        "tier":         "1a",
    }
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger("aria.intel.sources.court_records")

_COURTLISTENER_BASE = "https://www.courtlistener.com/api/rest/v3"
_BAILII_GNEWS = (
    "https://news.google.com/rss/search?q=site%3Abailii.org+{q}"
    "&hl=en-GB&gl=GB&ceid=GB:en"
)

_DEFAULT_TIMEOUT = 12.0
_USER_AGENT = (
    "Mozilla/5.0 (compatible; ARIA-DD/1.0; +https://intel.arkmurus.com) "
    "Court-records lookup R-F579"
)


def _norm_entity(entity: str) -> str:
    """Strip corporate suffixes that hurt court-search recall."""
    if not entity:
        return ""
    e = entity.strip()
    # Conservative — keep the head of the name. Drop common suffixes
    # so "ACME Widgets Limited" matches "ACME Widgets" in case caption.
    for suffix in (
        " S.A.", " S.A", " SA", " S.A.S.",
        " LLC", " LLP", " LTD", " Ltd", " Ltd.",
        " GmbH", " AG", " A.G.",
        " Limited", " Corp", " Corp.", " Corporation",
        " Inc", " Inc.", " Incorporated",
        " B.V.", " BV", " N.V.", " NV",
        " plc", " PLC", " Plc",
        " A.S.", " AS", " AŞ", " A.Ş.",
    ):
        if e.endswith(suffix):
            e = e[: -len(suffix)].strip()
            break
    return e


async def search_us_courts(
    entity: str,
    *,
    limit: int = 10,
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """Free CourtListener REST search. Returns at most `limit` hits."""
    name = _norm_entity(entity)
    if not name or len(name) < 3:
        return []
    url = (
        f"{_COURTLISTENER_BASE}/search/"
        f"?q={quote_plus(name)}&type=o&order_by=score+desc"
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, headers={"User-Agent": _USER_AGENT})
            if r.status_code != 200:
                logger.debug(
                    "[R-F579] courtlistener returned HTTP %s for %s",
                    r.status_code, name,
                )
                return []
            data = r.json() or {}
    except Exception as e:
        logger.debug("[R-F579] courtlistener fetch failed: %s", e)
        return []

    hits: list[dict[str, Any]] = []
    for row in (data.get("results") or [])[:limit]:
        # CourtListener opinion record. Fields vary by document type;
        # we only keep what the DD layer can cite.
        case_name = (row.get("caseName") or row.get("case_name") or "").strip()
        court = (row.get("court") or row.get("court_id") or "").strip()
        date_filed = (
            row.get("dateFiled") or row.get("date_filed")
            or row.get("date") or ""
        )
        snippet = (row.get("snippet") or "").strip()[:300]
        absolute_url = row.get("absolute_url") or ""
        citation_url = (
            f"https://www.courtlistener.com{absolute_url}"
            if absolute_url.startswith("/") else absolute_url
        )
        if not case_name:
            continue
        hits.append({
            "title":        case_name,
            "court":        court or "US (court_id unspecified)",
            "date":         (date_filed or "")[:10],
            "citation_url": citation_url or "https://www.courtlistener.com/",
            "snippet":      snippet,
            "jurisdiction": "US",
            "source":       "courtlistener",
            "tier":         "1a",
        })
    return hits


def _parse_bailii_rss(xml: str, limit: int) -> list[dict[str, Any]]:
    """Strip-and-extract title/link/date from a Google-News RSS feed
    that searched site:bailii.org. Conservative regex — fails to []
    if the shape isn't recognised rather than throwing."""
    items = re.findall(r"<item>([\s\S]*?)</item>", xml or "", re.I)
    out: list[dict[str, Any]] = []
    for body in items[:limit]:
        title = (re.search(r"<title>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?</title>", body, re.I) or [None, ""])[1]
        link = (re.search(r"<link>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?</link>", body, re.I) or [None, ""])[1]
        date = (re.search(r"<pubDate>([\s\S]*?)</pubDate>", body, re.I) or [None, ""])[1]
        # Title is "Case caption — bailii.org" via Google News;
        # the publisher tag is appended after an em-dash + space.
        # Strip the trailing publisher to keep the case name clean.
        title = re.sub(r"\s*[-—–]\s*bailii\.org\s*$", "", title or "", flags=re.I).strip()
        if not title:
            continue
        out.append({
            "title":        title[:200],
            "court":        "UK / Ireland / EU (Bailii)",
            "date":         (date or "")[:25],
            "citation_url": (link or "").strip() or "https://www.bailii.org/",
            "snippet":      "",  # Google-News RSS doesn't include opinion text
            "jurisdiction": "UK",
            "source":       "bailii",
            "tier":         "1a",
        })
    return out


async def search_uk_courts(
    entity: str,
    *,
    limit: int = 10,
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """Bailii lookup via the site: Google-News-RSS fallback pattern.

    No formal API exists for Bailii so we use the same indirection the
    procurement_portals adapter uses — Google News indexes Bailii
    case pages and we read the RSS feed to extract titles + links.
    Returns clean case captions; opinion text snippet not available
    via this path (callers may follow citation_url to fetch full text).
    """
    name = _norm_entity(entity)
    if not name or len(name) < 3:
        return []
    url = _BAILII_GNEWS.format(q=quote_plus(name))
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, headers={"User-Agent": _USER_AGENT})
            if r.status_code != 200:
                logger.debug(
                    "[R-F579] bailii (via gnews RSS) returned HTTP %s for %s",
                    r.status_code, name,
                )
                return []
            return _parse_bailii_rss(r.text, limit=limit)
    except Exception as e:
        logger.debug("[R-F579] bailii fetch failed: %s", e)
        return []


async def search_all(
    entity: str,
    *,
    limit_per_source: int = 10,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Concurrent lookup across both sources. Used by dd_orchestrator
    + counter_intelligence layers.

    Returns:
        {
            "entity":   "<normalised entity name>",
            "hits":     [merged + deduped list],
            "us_count": int,
            "uk_count": int,
            "sources":  ["courtlistener", "bailii"],
        }

    Never raises. Per-source failures degrade gracefully — the other
    source's hits are still returned. If both fail, hits is [] and
    the caller's DD layer sees an honest zero-result.
    """
    if not entity or not entity.strip():
        return {"entity": "", "hits": [], "us_count": 0, "uk_count": 0, "sources": []}

    name = _norm_entity(entity)
    us_task = asyncio.create_task(search_us_courts(name, limit=limit_per_source, timeout=timeout))
    uk_task = asyncio.create_task(search_uk_courts(name, limit=limit_per_source, timeout=timeout))
    us_hits, uk_hits = await asyncio.gather(us_task, uk_task, return_exceptions=True)
    if isinstance(us_hits, Exception):
        us_hits = []
    if isinstance(uk_hits, Exception):
        uk_hits = []

    # Dedupe by (jurisdiction, title) so an entity matching the same
    # case under two slightly-different captions doesn't double-count.
    seen: set[tuple[str, str]] = set()
    merged: list[dict[str, Any]] = []
    for h in (us_hits or []) + (uk_hits or []):
        k = (h["jurisdiction"], h["title"][:80].lower())
        if k in seen:
            continue
        seen.add(k)
        merged.append(h)

    return {
        "entity":   name,
        "hits":     merged,
        "us_count": len(us_hits or []),
        "uk_count": len(uk_hits or []),
        "sources":  ["courtlistener", "bailii"],
    }
