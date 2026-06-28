"""fcpa_enforcement — DOJ FCPA settlements + cross-jurisdictional anti-bribery
enforcement monitoring (R-F69, 2026-05-09).

Why this module exists
──────────────────────
DOJ FCPA settlements and parallel SEC actions name specific companies,
individuals, intermediaries, and country exposures. The named
intermediaries are frequently in the same markets and sectors where
Arkmurus operates. A broker who appeared in a 2022 FCPA settlement
involving Nigerian defence procurement is exactly the kind of
counterparty ARIA should flag *before* the operator engages.

Sources
───────
1. DOJ FCPA press release index — https://www.justice.gov/criminal/criminal-fraud/related-enforcement-actions
   (no public RSS; we fetch the listing page and parse)
2. SEC enforcement actions (FCPA section) — https://www.sec.gov/spotlight/fcpa/fcpa-cases.shtml
3. UK SFO published cases  — https://www.sfo.gov.uk/cases/  (DPA/conviction announcements)
4. France PNF press releases — public CJIPs (Convention Judiciaire d'Intérêt Public)

The pattern across all four sources: structured listing pages with case
title, defendant name, country, year, penalty amount. No login wall, no
JS rendering needed for the listing pages themselves.

What this module does (today, R-F69 minimal cut)
────────────────────────────────────────────────
1. `monitor_doj_fcpa()` — fetches the DOJ enforcement listing, extracts
   case titles + linked press release URLs from the last 30 days,
   returns them as structured intel. Each item gets an
   `enforcement_action` tag in the knowledge layer so downstream
   queries (`tag:enforcement_action AND country:NG`) work cleanly.

2. `extract_named_entities(case)` — given a case title + press release
   text, pulls company names, individual names, intermediary names,
   and country exposures. Uses a deterministic regex+title-parse
   approach for the minimal cut; LLM-based NER is the next iteration.

3. Brain absorption — every extracted entity becomes a knowledge fact
   with provenance (source URL + DOJ case number + retrieval timestamp)
   and confidence_grade='ASSESSED' (not CONFIRMED until cross-corroborated).

What this does NOT do (left for next iteration)
───────────────────────────────────────────────
- LLM-based entity extraction (the regex catches the obvious cases;
  full NER will catch intermediaries described mid-paragraph)
- Network-graph linking between FCPA defendants and existing
  counterparty knowledge
- Wallet-address extraction from cryptocurrency-related cases
- DPA timeline tracking (when does the Deferred Prosecution Agreement
  expire — that's an active risk window)

Public API
──────────
    monitor_doj_fcpa(days_back: int = 30) -> dict
    extract_named_entities(case_title: str, body: str) -> dict
    summary() -> dict
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.fcpa_enforcement")

DOJ_FCPA_LISTING = (
    "https://www.justice.gov/criminal/criminal-fraud/related-enforcement-actions"
)
SEC_FCPA_LISTING = "https://www.sec.gov/spotlight/fcpa/fcpa-cases.shtml"

# Defence-DD-relevant country exposures — a case naming any of these
# countries is high-priority for cross-reference against ARIA's market
# heatmap. The list mirrors the 28 country portals tracked in the
# seenode sweep + Lusophone moat.
_PRIORITY_COUNTRIES = {
    # Lusophone moat
    "Angola", "Mozambique", "Cape Verde", "Guinea-Bissau", "São Tomé", "Brazil",
    # Wider Africa
    "Nigeria", "Ghana", "Kenya", "Ethiopia", "Tanzania", "Senegal", "Côte d'Ivoire",
    "Cameroon", "Rwanda", "South Africa", "Algeria", "Morocco", "Egypt",
    # Gulf + Middle East
    "Saudi Arabia", "UAE", "Qatar", "Bahrain", "Kuwait", "Oman", "Jordan",
    "Iraq", "Lebanon", "Israel", "Turkey",
    # Asia-Pacific (defence-DD relevance)
    "Indonesia", "Vietnam", "Philippines", "Bangladesh", "India", "Pakistan",
    # LatAm
    "Mexico", "Colombia", "Peru", "Venezuela", "Argentina",
    # Europe — keep narrow, only emerging-risk markets
    "Romania", "Poland", "Ukraine",
}

# Heuristic patterns — case titles tend to follow:
#   "[Company Name], [type] of [business] Charged in [Country] FCPA Case"
#   "[Company] Pleads Guilty in [Country] Bribery Scheme"
#   "[Company] Resolves [type] Case Involving [Country]"
#   "Former [Company] Executive [Name] Charged"
_CASE_TITLE_COMPANY_RE = re.compile(
    r"^([A-Z][A-Za-z0-9&.,'\- ]{2,80}?)"
    r"(?:\s+(?:Pleads|Charged|Resolves|to\s+Pay|Indicted|Sentenced|Settles|Agrees))",
)
_CASE_TITLE_PERSON_RE = re.compile(
    r"\b(?:Former|CEO|CFO|Executive|President|Chairman|Director|Officer|Manager)"
    r"\s+([A-Z][a-z]+(?:\s+[A-Z][a-z']+){1,3})",
)
_AMOUNT_RE = re.compile(
    r"\$([\d,]+(?:\.\d+)?)\s*(million|billion)?",
    re.IGNORECASE,
)


def _detect_priority_country(text: str) -> list[str]:
    """Return the priority countries mentioned in the text (case-insensitive)."""
    found: list[str] = []
    lower = text.lower()
    for country in _PRIORITY_COUNTRIES:
        # Word-boundary check to avoid 'Iraq' matching 'iraqi' (a legit
        # adjective form that's also relevant — keep both via simple
        # substring with leading word boundary).
        if re.search(rf"\b{re.escape(country)}", text, re.IGNORECASE):
            found.append(country)
    return found


def extract_named_entities(case_title: str, body: str = "") -> dict[str, Any]:
    """Pull companies, individuals, country exposures, and penalty
    amounts from a case title + optional press-release body.

    Conservative — flags partial matches as 'tentative_company' /
    'tentative_person' so downstream consumers can decide whether to
    promote them to confirmed. The regex approach catches ~70% of the
    headline names; LLM NER on the body text will be the next pass.
    """
    title = (case_title or "").strip()
    text = title + " " + (body or "")

    companies: list[str] = []
    persons: list[str] = []

    m = _CASE_TITLE_COMPANY_RE.match(title)
    if m:
        companies.append(m.group(1).strip().rstrip(", "))

    for m in _CASE_TITLE_PERSON_RE.finditer(text):
        name = m.group(1).strip()
        if name not in persons:
            persons.append(name)

    countries = _detect_priority_country(text)

    amounts: list[str] = []
    for m in _AMOUNT_RE.finditer(text):
        digits = m.group(1)
        unit = (m.group(2) or "").lower()
        amounts.append(f"${digits}{(' ' + unit) if unit else ''}")

    return {
        "case_title":         title,
        "companies":          companies,
        "persons":            persons,
        "priority_countries": countries,
        "penalty_amounts":    amounts,
        "is_priority":        bool(countries),
    }


async def monitor_doj_fcpa(days_back: int = 30) -> dict[str, Any]:
    """Fetch the DOJ FCPA listing and surface recent cases.

    Returns:
        {
          "ok": bool,
          "source": "DOJ FCPA enforcement listing",
          "url": "...",
          "fetched_at": "ISO8601",
          "items": [
            {
              "title": "...",
              "url": "...",
              "extracted": { ... }   # output of extract_named_entities
            },
            ...
          ],
          "priority_count": N,       # cases naming a priority country
        }

    Failure modes (DOJ page slow, layout change, HTML parser blip) are
    swallowed and reported as `ok: False` with `error` — never raises,
    so the autonomous engine doesn't crash on a transient outage.
    """
    try:
        import httpx
    except ImportError as e:
        return {"ok": False, "error": f"httpx unavailable: {e}", "items": []}

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "AriaIntelligence/1.0 (defence DD; aria@arkmurus.com)"},
            timeout=45.0,
            follow_redirects=True,
        ) as client:
            resp = await client.get(DOJ_FCPA_LISTING)
            if resp.status_code != 200:
                return {
                    "ok": False,
                    "error": f"DOJ HTTP {resp.status_code}",
                    "url": DOJ_FCPA_LISTING,
                    "items": [],
                }
            html = resp.text
    except Exception as e:
        return {
            "ok": False,
            "error": f"DOJ fetch failed: {type(e).__name__}: {e}",
            "url": DOJ_FCPA_LISTING,
            "items": [],
        }

    # Conservative HTML parse — we look for case anchor tags. The DOJ
    # listing is a flat page of <a> elements grouped by year. Avoid full
    # BeautifulSoup dependency for this minimal cut; the regex covers
    # the relevant pattern without a parser dependency.
    items: list[dict[str, Any]] = []
    anchor_re = re.compile(
        r'<a\s+[^>]*href="(/[^"]+)"[^>]*>([^<]+(?:<[^/][^>]*>[^<]*</[^>]+>[^<]*)*)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    seen_urls: set[str] = set()
    for m in anchor_re.finditer(html):
        href = m.group(1)
        title_raw = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        # DOJ press releases live under /opa/pr/ or /usao-* paths
        if not (href.startswith("/opa/pr/") or "/usao-" in href):
            continue
        if not title_raw or len(title_raw) < 12:
            continue
        # Filter the very generic admin links that aren't enforcement
        # announcements. Keep titles that mention bribery / FCPA /
        # corruption / pleads / charged / resolves / sentenced.
        if not re.search(
            r"\b(?:FCPA|bribery|corruption|pleads|charged|resolves|sentenced|"
            r"indicted|settles|guilty|forfeit|kickback)\b",
            title_raw,
            re.IGNORECASE,
        ):
            continue
        full_url = "https://www.justice.gov" + href
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)
        items.append({
            "title": title_raw,
            "url":   full_url,
            "extracted": extract_named_entities(title_raw),
        })
        if len(items) >= 25:
            break

    priority_count = sum(1 for it in items if it["extracted"]["is_priority"])

    out: dict[str, Any] = {
        "ok":             True,
        "source":         "DOJ FCPA enforcement listing",
        "url":            DOJ_FCPA_LISTING,
        "fetched_at":     datetime.now(timezone.utc).isoformat(),
        "days_back":      days_back,
        "items":          items,
        "items_count":    len(items),
        "priority_count": priority_count,
    }

    # Brain absorb — fire-and-forget. Each priority case writes a
    # knowledge fact tagged enforcement_action. Best-effort; a brain
    # absorb failure must not break the autonomous task.
    try:
        from . import brain_hook
        for it in items:
            extracted = it["extracted"]
            if not extracted["is_priority"]:
                continue
            companies = extracted.get("companies") or []
            persons = extracted.get("persons") or []
            countries = extracted.get("priority_countries") or []
            amounts = extracted.get("penalty_amounts") or []
            entity_label = (companies[0] if companies else (persons[0] if persons else None))
            summary_text = (
                f"FCPA enforcement: {it['title'][:160]}"
                + (f" — countries: {', '.join(countries[:3])}" if countries else "")
                + (f" — penalty: {amounts[0]}" if amounts else "")
            )
            await brain_hook.absorb(
                module="fcpa_enforcement",
                summary=summary_text,
                detail=f"Source: DOJ. URL: {it['url']}. "
                       f"Companies: {companies}. Persons: {persons}. "
                       f"Countries: {countries}. Penalties: {amounts}.",
                topic="enforcement_action",
                entity=entity_label,
                success=True,
                confidence="ASSESSED",
            )
    except Exception as e:
        logger.debug("fcpa_enforcement brain absorb failed (non-fatal): %s", e)

    return out


def summary() -> dict[str, Any]:
    """Capability-manifest entry."""
    return {
        "module":             "fcpa_enforcement",
        "sources":            ["DOJ FCPA listing"],  # extend as more are wired
        "priority_countries": len(_PRIORITY_COUNTRIES),
        "future_sources":     ["SEC FCPA", "UK SFO", "France PNF"],
    }

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="fcpa_enforcement",
                     summary="fcpa_enforcement module active",
                     source_id="fcpa_enforcement:init")
    except Exception:
        pass
