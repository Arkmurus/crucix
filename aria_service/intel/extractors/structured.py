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
        return empty

    try:
        tables = _extract_tables(soup)
    except Exception as e:
        logger.debug("[extractors.structured] tables failed: %s", e)
        tables = []

    try:
        json_ld = _extract_json_ld(soup)
    except Exception as e:
        logger.debug("[extractors.structured] json_ld failed: %s", e)
        json_ld = []

    try:
        microdata = _extract_microdata(soup)
    except Exception as e:
        logger.debug("[extractors.structured] microdata failed: %s", e)
        microdata = []

    try:
        og, tw, meta_generic = _extract_meta(soup)
    except Exception as e:
        logger.debug("[extractors.structured] meta failed: %s", e)
        og, tw, meta_generic = {}, {}, {}

    try:
        schema_types = _extract_schema_types(json_ld, microdata)
    except Exception:
        schema_types = []

    try:
        links_outbound = _extract_outbound_links(soup, base_url=base_url)
    except Exception:
        links_outbound = []

    return {
        "tables": tables,
        "json_ld": json_ld,
        "microdata": microdata,
        "opengraph": og,
        "twitter": tw,
        "meta": meta_generic,
        "schema_org": schema_types,
        "links_outbound": links_outbound,
    }
