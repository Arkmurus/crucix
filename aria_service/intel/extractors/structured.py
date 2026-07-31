"""Structured-HTML extractor — tables, JSON-LD, microdata, OpenGraph.

What this module does differently from `_extract_structured_html` in
researcher.py
────────────────────────────────────────────────────────────────────
`_extract_structured_html` returns tables as JOINED TEXT — good for a
flat text blob but useless if the LLM needs to reason about a product
spec table or executive roster. This module returns tables as
STRUCTURED rows so downstream consumers (dd_orchestrator, OEM batch
research, the LLM synthesis step) get first-class typed data.

Public API
──────────
  extract(html: str) -> dict with keys:
    - tables: list[dict]    # [{"headers": [...], "rows": [{col: val}...]}]
    - json_ld: list[dict]   # parsed JSON-LD blocks (schema.org)
    - microdata: list[dict] # itemprop/itemscope entities
    - opengraph: dict       # og:title, og:type, og:description, og:url, og:image
    - twitter: dict         # twitter:card, twitter:title, etc.
    - meta: dict            # generic <meta name> tags flattened
    - schema_org: list[str] # schema.org types detected on the page
    - links_outbound: list[dict]  # <a href="..."> outside the origin domain

No LLM, no network. Pure BeautifulSoup + regex.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("aria.extractors.structured")


# ── R-F3564 — this extractor was DARK on every failure path ──────────────────
#
# THE DEFECT. `extract()` runs six sub-extractors, and every one of them caught
# Exception, wrote `logger.debug(...)`, and substituted an EMPTY result. §21a is
# explicit that "logged to console / except: pass" is DARK, not wired — but the
# real cost is not the missing gate tick, it is what the caller then believes.
#
# `researcher.extract_url_deep` feeds these results onto the DD evidence path.
# `tables = []` after a crash is BYTE-IDENTICAL to `tables = []` from a page that
# genuinely has no tables. So a broken extractor manufactures an ABSENCE OF
# EVIDENCE that reads as EVIDENCE OF ABSENCE — the false-clean class the DD
# product exists to prevent — and nothing anywhere would have said so.
#
# Wiring the FAILURE side is cheap and safe: wire_failure is fire-and-forget,
# deduped 1h (R-F66) and capped at 500 (R-F1669), so a page that breaks on every
# crawl records once an hour, not once a page. That dedup is why the
# "wiring this would flood the ledgers" objection — correct for grounding_reward
# (R-F2033) and cost_tracker (R-F2103) — does not apply here.
def _part_failed(part: str, exc: Exception) -> None:
    """One sub-extractor died. Log as before, and TELL THE BRAIN."""
    logger.debug("[extractors.structured] %s failed: %s", part, exc)
    try:
        from ..engine_wiring import wire_failure
        wire_failure(
            module="extractors_structured",
            detail=f"{part} extraction failed: {type(exc).__name__}: {exc}"[:400],
            gap_type="engine_failure",
            source="extractors/structured.py",
        )
    except Exception:                       # noqa: BLE001
        pass    # a wiring failure must never break extraction (R-F2149 class)


def _run_succeeded(counts: dict) -> None:
    """Report WHAT WAS ACTUALLY EXTRACTED, as counts.

    The count is the point, not the tick. `wire_success` with no payload cannot
    distinguish "ran and found 12 tables" from "ran and found nothing", which is
    the same blindness this R-number is closing — and is exactly the shape
    docs/wiring_backlog_2026_07_28.md prescribes for a module that deliberately
    swallows so an outage cannot crash a DD.
    """
    try:
        from ..engine_wiring import wire_success
        wire_success(
            module="extractors_structured",
            summary="structured extraction completed",
            detail=" ".join(f"{k}={v}" for k, v in sorted(counts.items())),
            confidence="CONFIRMED",
        )
    except Exception:                       # noqa: BLE001
        pass


def _parse_html(html: str):
    """Single-dispatch HTML parser with lxml preferred + html.parser fallback.

    Returns a BeautifulSoup object or None if parsing completely fails.
    """
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("[extractors] bs4 not installed — structured extraction disabled")
        return None
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        try:
            return BeautifulSoup(html, "html.parser")
        except Exception as e:
            logger.debug("[extractors] HTML parse failed: %s", e)
            return None


# ── Tables ─────────────────────────────────────────────────────────────────

def _extract_tables(soup) -> list[dict]:
    """Return a list of parsed tables. Each entry has:
      - caption (if <caption> present)
      - headers (list[str], from <th> elements or first row)
      - rows (list[dict] keyed by header; falls back to list[str] if no headers)
      - row_count, col_count
    """
    tables: list[dict] = []
    for table in soup.find_all("table"):
        # Skip layout tables (no substantive data)
        cells = table.find_all(["td", "th"])
        if len(cells) < 2:
            continue

        caption_elem = table.find("caption")
        caption = caption_elem.get_text(strip=True) if caption_elem else ""

        # Headers: either explicit <th> row or first <tr>
        headers: list[str] = []
        thead = table.find("thead")
        if thead:
            ths = thead.find_all("th")
            if ths:
                headers = [th.get_text(strip=True) for th in ths]
        if not headers:
            first_tr = table.find("tr")
            if first_tr:
                ths = first_tr.find_all("th")
                if ths:
                    headers = [th.get_text(strip=True) for th in ths]

        # Data rows
        rows_out: list[Any] = []
        all_trs = table.find_all("tr")
        # Skip the header row if we derived headers from it
        data_trs = all_trs[1:] if headers and all_trs and all_trs[0].find_all("th") else all_trs

        for tr in data_trs:
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            values = [c.get_text(" ", strip=True) for c in cells]
            if not any(v for v in values):
                continue
            if headers and len(values) == len(headers):
                rows_out.append({h: v for h, v in zip(headers, values)})
            else:
                rows_out.append(values)

        if not rows_out:
            continue

        tables.append({
            "caption": caption[:200],
            "headers": headers[:30],
            "rows": rows_out[:200],  # cap row count — don't dump 10k-row tables
            "row_count": len(rows_out),
            "col_count": len(headers) if headers else (len(rows_out[0]) if isinstance(rows_out[0], list) else len(rows_out[0] or {})),
        })

        if len(tables) >= 50:  # cap table count per page
            break

    return tables


# ── JSON-LD ────────────────────────────────────────────────────────────────

def _extract_json_ld(soup) -> list[dict]:
    """Parse every <script type="application/ld+json"> block.

    Schema.org markup is the single richest signal on well-configured
    corporate pages — it tells you the Organization type, address,
    founder, employee count, parent org, and more without any NLP work.
    """
    blocks: list[dict] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            # Some pages wrap the JSON in a trailing semicolon or HTML comments
            try:
                cleaned = re.sub(r"^\s*//[^\n]*\n|\s*/\*.*?\*/", "", raw, flags=re.DOTALL)
                data = json.loads(cleaned)
            except Exception:
                continue
        # Handle both single objects and arrays
        if isinstance(data, list):
            blocks.extend([d for d in data if isinstance(d, dict)])
        elif isinstance(data, dict):
            blocks.append(data)
        if len(blocks) >= 30:
            break
    return blocks


# ── Microdata (itemscope / itemprop) ───────────────────────────────────────

def _extract_microdata(soup) -> list[dict]:
    """Extract schema.org-style microdata. Less common than JSON-LD but
    still found on older corporate sites + some European portals.
    """
    items: list[dict] = []
    for elem in soup.find_all(attrs={"itemscope": True}):
        item_type = elem.get("itemtype", "") or ""
        # Collect direct-descendant itemprops (not nested scopes)
        props: dict[str, str] = {}
        for prop_elem in elem.find_all(attrs={"itemprop": True}):
            # Skip if this element is inside a nested itemscope
            parent = prop_elem.parent
            inside_nested = False
            while parent and parent != elem:
                if parent.get("itemscope") is not None:
                    inside_nested = True
                    break
                parent = parent.parent
            if inside_nested:
                continue
            key = prop_elem.get("itemprop", "")
            val = (
                prop_elem.get("content")
                or prop_elem.get("href")
                or prop_elem.get("src")
                or prop_elem.get_text(" ", strip=True)
            )
            if key and val:
                props[key] = str(val)[:400]
        if props:
            items.append({"type": item_type, "properties": props})
        if len(items) >= 30:
            break
    return items


# ── OpenGraph + Twitter + generic meta ─────────────────────────────────────

def _extract_meta(soup) -> tuple[dict, dict, dict]:
    """Return (opengraph, twitter, meta_generic) dicts.

    These are often the richest structured signal on otherwise-thin
    React/Vue SPAs where the visible <body> is JS-rendered but the
    <head> always has og:* tags.
    """
    og: dict[str, str] = {}
    tw: dict[str, str] = {}
    meta_generic: dict[str, str] = {}

    for m in soup.find_all("meta"):
        prop = (m.get("property") or "").lower()
        name = (m.get("name") or "").lower()
        content = m.get("content") or ""
        if not content:
            continue
        if prop.startswith("og:"):
            og[prop[3:]] = content[:500]
        elif prop.startswith("twitter:") or name.startswith("twitter:"):
            key = (prop or name)[8:]
            tw[key] = content[:500]
        elif name in ("description", "keywords", "author", "publisher", "robots", "generator"):
            meta_generic[name] = content[:500]
        elif name in ("article:published_time", "article:author", "dc.creator", "dc.publisher", "dc.date"):
            meta_generic[name] = content[:500]

    return og, tw, meta_generic


# ── Schema.org type detection ──────────────────────────────────────────────

def _extract_schema_types(json_ld: list[dict], microdata: list[dict]) -> list[str]:
    """Aggregate every schema.org @type mentioned on the page.

    Useful for quick filtering — if a page's schema types include
    "Organization" or "Corporation" we know it's a company page and
    can route DD queries against it. "Person" → bio page. "Product" →
    catalogue page.
    """
    types: set[str] = set()

    def _add(v):
        if isinstance(v, str):
            # Strip schema.org URL prefix
            t = v.split("/")[-1].split("#")[-1]
            if t and len(t) < 60:
                types.add(t)
        elif isinstance(v, list):
            for x in v:
                _add(x)

    for block in json_ld:
        if isinstance(block, dict):
            _add(block.get("@type"))
            # Check nested @graph
            for item in block.get("@graph") or []:
                if isinstance(item, dict):
                    _add(item.get("@type"))

    for item in microdata:
        _add(item.get("type", ""))

    return sorted(types)


# ── Outbound links (useful for knowledge graph expansion) ──────────────────

def _extract_outbound_links(soup, base_url: str = "") -> list[dict]:
    """Links pointing to domains OTHER than the current page's domain.

    Feeds the knowledge spider: "this page references linkedin.com,
    companieshouse.gov.uk, sec.gov — those are evidence of corporate
    footprint worth following."
    """
    base_host = ""
    if base_url:
        try:
            base_host = urlparse(base_url).netloc.lower().replace("www.", "")
        except Exception:
            pass

    out: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        try:
            parsed = urlparse(href)
        except Exception:
            continue
        host = (parsed.netloc or "").lower().replace("www.", "")
        if not host or host == base_host:
            continue
        if href in seen:
            continue
        seen.add(href)
        out.append({
            "url": href[:500],
            "host": host,
            "text": a.get_text(" ", strip=True)[:120],
        })
        if len(out) >= 80:
            break
    return out


# ── Public entry point ─────────────────────────────────────────────────────

def extract(html: str, *, base_url: str = "") -> dict:
    """Run the full structured-extraction pipeline.

    Args:
        html: Raw HTML fetched from the URL.
        base_url: Optional canonical URL — used to classify outbound links.

    Returns a dict with all structured data fields. Empty fields on
    parse failure; no exceptions propagated.
    """
    empty = {
        "tables": [], "json_ld": [], "microdata": [],
        "opengraph": {}, "twitter": {}, "meta": {},
        "schema_org": [], "links_outbound": [],
    }
    if not html:
        return empty

    soup = _parse_html(html)
    if soup is None:
        # TOTAL failure — bs4 absent or both parsers rejected the document. The
        # caller receives the same `empty` dict as a blank page, so this must be
        # said out loud or the DD records a clean absence it never verified.
        _part_failed("html_parse", RuntimeError("HTML could not be parsed"))
        return empty

    try:
        tables = _extract_tables(soup)
    except Exception as e:
        _part_failed("tables", e)
        tables = []

    try:
        json_ld = _extract_json_ld(soup)
    except Exception as e:
        _part_failed("json_ld", e)
        json_ld = []

    try:
        microdata = _extract_microdata(soup)
    except Exception as e:
        _part_failed("microdata", e)
        microdata = []

    try:
        og, tw, meta_generic = _extract_meta(soup)
    except Exception as e:
        _part_failed("meta", e)
        og, tw, meta_generic = {}, {}, {}

    try:
        schema_types = _extract_schema_types(json_ld, microdata)
    except Exception as e:
        _part_failed("schema_types", e)
        schema_types = []

    try:
        links_outbound = _extract_outbound_links(soup, base_url=base_url)
    except Exception as e:
        _part_failed("links_outbound", e)
        links_outbound = []

    out = {
        "tables": tables,
        "json_ld": json_ld,
        "microdata": microdata,
        "opengraph": og,
        "twitter": tw,
        "meta": meta_generic,
        "schema_org": schema_types,
        "links_outbound": links_outbound,
    }
    _run_succeeded({k: len(v) for k, v in out.items()})
    return out
