"""World Bank Documents & Reports API — free, no auth, research-shaped.

What this is (and isn't)
────────────────────────
DIFFERENT from worldbank_debarred.py:
  - worldbank_debarred: compliance screen, "is this entity barred from
    WB-financed contracts?" The official WB Firm360 API has no public
    registration path (R-F155 / R-F279 verified the developer portal
    does NOT exist). Coverage comes via OpenSanctions's `wb_debarred`
    dataset aggregation — DD signal preserved, no operator action
    required.
  - worldbank_documents (this file): RESEARCH. "What does the WB
    publish about procurement / governance / fraud investigations
    in this country or about this entity?" Free, no auth. Covers
    200k+ documents.

Use cases for DD
────────────────
  - A target operates in a WB-funded market (Angola, Kenya, Nigeria,
    etc.) — the WB's Country Procurement Assessment Reports
    characterise the national procurement environment, red flags,
    fraud patterns.
  - The target may appear in a WB project completion report,
    mid-term review, implementation monitoring document, or
    Integrity Vice-Presidency (INT) investigation.
  - Defence-adjacent: WB does NOT fund military procurement, but it
    DOES fund security-sector-adjacent infrastructure (ports,
    border management, critical infrastructure) and some MSME
    programmes where defence primes act as subcontractors.

API endpoint
────────────
  https://search.worldbank.org/api/v3/wds?qterm=…
  Documented: https://documents.worldbank.org/en/publication/documents-reports/api
  Parameters we use:
    qterm       — free-text search
    count_exact — country filter (exact match)
    rows        — max results per page (default 10)
    fl          — comma-separated fields to return
    format=json — JSON output
    docty       — document type filter (Procurement Plan, Country
                   Assessment, etc.)
    docdt_to / docdt_from — date range filter

Shape of a hit
──────────────
    {
        "id": "P1234",
        "title": "Country Procurement Assessment (CPAR): …",
        "doc_type": "Country Procurement Assessment",
        "country": "Kenya",
        "publication_date": "2023-06-01",
        "abstract": "",        # not always present
        "url": "https://documents.worldbank.org/…",
        "pdf_url": "",         # when available
    }
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import quote

from . import _common

logger = logging.getLogger("aria.sources.worldbank_documents")

_SOURCE = "worldbank_documents"
_AUTH = "anonymous"

_API_BASE = "https://search.worldbank.org/api/v3/wds"
_HUMAN_URL = "https://documents.worldbank.org/"

# Fields we want back from each hit — minimal to keep payloads small
_FIELDS = ",".join([
    "id", "display_title", "title", "docty", "count", "country",
    "docdt", "abstract", "url_friendly_title", "url_thumb",
    "pdfurl", "theme",
])


# ── Core search ────────────────────────────────────────────────────────────

async def lookup(
    name: str,
    *,
    country: str = "",
    doc_type: str = "",
    year_from: int | None = None,
    year_to: int | None = None,
    max_hits: int = 15,
) -> dict:
    """Entity-scoped WB Documents API lookup.

    Args:
        name: Query string. Can be an entity name, a topic ("procurement
            fraud"), or a free-text phrase.
        country: Optional country filter — exact match, so pass the WB's
            canonical name ("Angola", "Kenya", "Côte d'Ivoire").
        doc_type: Optional document-type filter. Useful values:
            "Country Procurement Assessment", "Implementation
            Completion and Results Report", "Procurement Plan",
            "Project Appraisal Document", "INT Investigation".
        year_from / year_to: Date filter (inclusive).
        max_hits: Cap on results.

    Returns canonical source-result shape. `hits` is a list of document
    dicts.

    Severity interpretation for the orchestrator: document mentions are
    INFO-level research context, not sanctions signals. An INT
    Investigation hit involving the target is AMBER — it doesn't mean
    the entity is guilty, but it's worth manual review.
    """
    started = time.time()
    query = {
        "name": name, "country": country, "doc_type": doc_type,
        "year_from": year_from, "year_to": year_to,
    }
    result = _common.empty_result(
        _SOURCE, query, auth=_AUTH, citation_url=_HUMAN_URL,
    )

    if not name or len(name.strip()) < 2:
        return _common.error_result(
            _SOURCE, query, "name too short for search",
            auth=_AUTH, started_at=started,
        )

    # Build query params
    params: list[tuple[str, str]] = [
        ("qterm", name.strip()),
        ("rows", str(min(max_hits, 50))),
        ("format", "json"),
        ("fl", _FIELDS),
    ]
    if country:
        params.append(("count_exact", country.strip()))
    if doc_type:
        params.append(("docty_exact", doc_type.strip()))
    if year_from:
        params.append(("docdt_from", f"{year_from:04d}-01-01T00:00:00Z"))
    if year_to:
        params.append(("docdt_to", f"{year_to:04d}-12-31T23:59:59Z"))

    # Construct URL — httpx handles encoding but we want explicit
    query_str = "&".join(f"{k}={quote(v, safe='')}" for k, v in params)
    url = f"{_API_BASE}?{query_str}"

    try:
        data = await _common.http_get_json(url, timeout=20.0)
    except Exception as e:
        return _common.error_result(
            _SOURCE, query, f"{type(e).__name__}: {e}",
            auth=_AUTH, started_at=started,
        )

    if not data:
        return _common.error_result(
            _SOURCE, query, "WB Documents API returned empty response",
            auth=_AUTH, started_at=started,
        )

    total = data.get("total", 0)
    docs_raw = data.get("documents") or {}
    # The API returns documents as a dict keyed by id. Most keys are
    # doc ids, but a few are metadata ('facets', 'rows', 'os', 'page').
    hits: list[dict] = []
    for key, doc in docs_raw.items():
        if not isinstance(doc, dict):
            continue
        if key in ("facets", "rows", "os", "page", "total"):
            continue
        try:
            title = (
                doc.get("display_title")
                or doc.get("title")
                or "(no title)"
            )
            pub_date_raw = doc.get("docdt", "")
            pub_date = pub_date_raw[:10] if pub_date_raw else ""
            hit = {
                "id": doc.get("id") or key,
                "name": title[:300],
                "title": title,
                "doc_type": doc.get("docty", ""),
                "country": doc.get("count", "") or doc.get("country", ""),
                "theme": doc.get("theme", ""),
                "publication_date": pub_date,
                "abstract": (doc.get("abstract") or "")[:800],
                "url": doc.get("url_friendly_title") or doc.get("pdfurl", ""),
                "pdf_url": doc.get("pdfurl", ""),
                "citation_url": doc.get("url_friendly_title") or doc.get("pdfurl") or _HUMAN_URL,
            }
            hits.append(hit)
        except Exception as e:
            logger.debug("[worldbank_documents] hit parse failed: %s", e)
            continue
        if len(hits) >= max_hits:
            break

    # Severity hint based on doc types in results
    severity_hint = ""
    for h in hits[:5]:
        dt = (h.get("doc_type") or "").lower()
        if "int investigation" in dt or "integrity" in dt:
            severity_hint = "AMBER — INT Investigation document matches query"
            break
        if "audit" in dt or "fraud" in dt:
            severity_hint = "AMBER — audit/fraud document matches query"
            break
    if not severity_hint and hits:
        severity_hint = (
            f"INFO — {len(hits)} WB research documents (of {total} total) "
            f"match query"
        )

    result["hits"] = hits
    result["total_available"] = int(total) if isinstance(total, int) else 0
    result["severity_hint"] = severity_hint
    return _common.finalise(result, started)


async def is_available() -> bool:
    """Health-check ping for the cross-server health endpoint."""
    try:
        url = f"{_API_BASE}?qterm=test&rows=1&format=json&fl=id"
        data = await _common.http_get_json(url, timeout=8.0)
        return isinstance(data, dict) and "documents" in data
    except Exception:
        return False
