"""OFAC SDN (Specially Designated Nationals) — direct primary source.

Why not just rely on OpenSanctions?
────────────────────────────────────
We already screen via OpenSanctions, which aggregates OFAC (plus EU, UN,
UK OFSI, etc.). But for professional DD:

  1. Primary-source citation matters. When a DD report says "entity is
     on the OFAC SDN list", the reader expects a link directly to the
     Treasury record (not an aggregator). Auditors and correspondent
     banks verify against the primary source.
  2. Aggregation lag. OpenSanctions refreshes daily or thereabouts;
     OFAC publishes updates continuously. A same-day designation could
     be live at Treasury but not yet in OpenSanctions. For a live DD
     run that matters.
  3. Defence-in-depth. If OpenSanctions is down, a cached direct OFAC
     pull keeps DD running. Our pipeline is not allowed to silent-fail
     on sanctions.

Data source
───────────
Treasury publishes the SDN list as CSV, XML, and JSON at:
  https://sanctionslistservice.ofac.treas.gov/
  https://www.treasury.gov/ofac/downloads/sdn.csv

We use the JSON endpoint (sanctionslistservice) which returns structured
records with aliases — critical for fuzzy matching. Refreshed daily
into an in-process cache.

Shape of a hit
──────────────
    {
        "name": "BAYKAR TEKNOLOJI A.S.",
        "aliases": ["BAYKAR"],
        "list_type": "SDN",
        "program": "UKRAINE-EO13660",     # sanction programme code
        "designation_date": "2023-12-15",
        "sdn_type": "Entity",             # or "Individual"
        "address": "Istanbul, TR",
        "_match_score": 0.91,
        "citation_url": "https://sanctionssearch.ofac.treas.gov/Details.aspx?id=<id>",
    }
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from . import _common

logger = logging.getLogger("aria.sources.ofac_sdn")

_SOURCE = "ofac_sdn"
_AUTH = "anonymous"

# Treasury's JSON feed — updated continuously as designations ship.
_FEED_JSON = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ENHANCED.JSON"
# Fallback CSV endpoint if the JSON feed is unavailable
_FEED_CSV = "https://www.treasury.gov/ofac/downloads/sdn.csv"

_CACHE: dict[str, Any] = {"fetched_at": 0.0, "records": []}
_CACHE_TTL_S = 3 * 3600  # 3h — between the 6h OpenSanctions refresh and real-time

_CACHE_LOCK = asyncio.Lock()


async def _load_records() -> list[dict]:
    """Fetch and cache the SDN list. Returns a list of normalised records."""
    async with _CACHE_LOCK:
        now = time.time()
        if _CACHE["records"] and (now - _CACHE["fetched_at"] < _CACHE_TTL_S):
            return _CACHE["records"]

        data = await _common.http_get_json(_FEED_JSON, timeout=25.0)
        if not data:
            logger.warning("[ofac_sdn] JSON feed unavailable; keeping stale cache (%d recs)",
                           len(_CACHE["records"]))
            return _CACHE["records"]

        # The enhanced JSON is keyed by UID. Each record has identity,
        # addresses, akas, programs, designation_date, etc.
        records: list[dict] = []
        items = data if isinstance(data, list) else (data.get("sdnEntry") or data.get("publishInformation") or [])
        if isinstance(items, dict):
            items = list(items.values())

        for rec in items:
            if not isinstance(rec, dict):
                continue
            # Try the enhanced shape first
            uid = rec.get("uid") or rec.get("id") or ""
            primary_name = (
                rec.get("sdnName")
                or rec.get("primaryName")
                or rec.get("name")
                or ""
            )
            sdn_type = rec.get("sdnType") or rec.get("type") or ""
            programs = rec.get("programs") or rec.get("sanctionsPrograms") or []
            if isinstance(programs, dict):
                programs = programs.get("program") or []
            if isinstance(programs, str):
                programs = [programs]

            # Aliases / aka — multiple possible shapes
            aliases: list[str] = []
            aka = rec.get("aka") or rec.get("akaList") or rec.get("aliases") or []
            if isinstance(aka, dict):
                aka = aka.get("aka") or []
            if isinstance(aka, list):
                for a in aka:
                    if isinstance(a, str):
                        aliases.append(a)
                    elif isinstance(a, dict):
                        full = (
                            a.get("lastName") or a.get("wholeName") or a.get("name")
                            or ""
                        )
                        first = a.get("firstName") or ""
                        if first:
                            full = f"{first} {full}".strip()
                        if full:
                            aliases.append(full)

            # Address / jurisdiction — first address only for the hit
            addr_blocks = rec.get("addressList") or rec.get("addresses") or []
            addr_str = ""
            if isinstance(addr_blocks, dict):
                addr_blocks = addr_blocks.get("address") or []
            if isinstance(addr_blocks, list) and addr_blocks:
                a0 = addr_blocks[0] if isinstance(addr_blocks[0], dict) else {}
                parts = [a0.get("city"), a0.get("stateOrProvince"), a0.get("country")]
                addr_str = ", ".join(p for p in parts if p)

            designation = rec.get("designationDate") or rec.get("publishDate") or ""

            records.append({
                "uid": str(uid),
                "name": primary_name,
                "aliases": aliases,
                "list_type": "SDN",
                "programs": [str(p) for p in programs if p],
                "sdn_type": sdn_type,
                "address": addr_str,
                "designation_date": str(designation),
                "citation_url": (
                    f"https://sanctionssearch.ofac.treas.gov/Details.aspx?id={uid}"
                    if uid else "https://sanctionssearch.ofac.treas.gov/"
                ),
            })

        _CACHE["records"] = records
        _CACHE["fetched_at"] = now
        logger.info("[ofac_sdn] cache refreshed (%d records)", len(records))
        return records


async def lookup(
    name: str,
    *,
    threshold: float = 0.70,
    max_hits: int = 15,
) -> dict:
    """Entity-scoped OFAC SDN lookup. Returns canonical source-result dict.

    Severity interpretation for the orchestrator
    ────────────────────────────────────────────
    Any hit on SDN is a HARD_STOP — you cannot transact with an SDN-
    designated person/entity (50 Percent Rule extends to subsidiaries).
    Even a fuzzy match deserves human review before clearing.
    """
    started = time.time()
    query = {"name": name}
    result = _common.empty_result(
        _SOURCE, query, auth=_AUTH,
        citation_url="https://sanctionssearch.ofac.treas.gov/",
    )

    if not name or len(name.strip()) < 2:
        return _common.error_result(
            _SOURCE, query, "name too short for screen",
            auth=_AUTH, started_at=started,
        )

    try:
        records = await _load_records()
        if not records:
            return _common.error_result(
                _SOURCE, query, "SDN list unavailable",
                auth=_AUTH, started_at=started,
            )
        hits = _common.fuzzy_filter(
            records, name, name_key="name",
            threshold=threshold, max_hits=max_hits,
        )
        result["hits"] = hits
        return _common.finalise(result, started)
    except Exception as e:
        logger.warning("[ofac_sdn] lookup failed for %r: %s", name, e)
        return _common.error_result(
            _SOURCE, query, f"{type(e).__name__}: {e}",
            auth=_AUTH, started_at=started,
        )


async def is_available() -> bool:
    """Health-check ping. Considered available if cache has records OR
    the feed fetch succeeds."""
    if _CACHE["records"]:
        return True
    data = await _common.http_get_json(_FEED_JSON, timeout=8.0)
    return bool(data)
