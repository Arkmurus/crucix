"""UK FCDO / OFSI consolidated sanctions list — direct primary source.

Why direct FCDO/OFSI, not just via OpenSanctions?
──────────────────────────────────────────────────
Same reasoning as OFAC:
  - UK sanctions are a distinct legal regime post-Brexit (SAMLA 2018 /
    Sanctions Regulations). A UK DD client wants the citation to
    ofsi.gov.uk, not a third-party aggregator.
  - OFSI publishes a "consolidated list" covering all active financial
    sanctions from all UK regimes — the one-stop list.
  - Russia-specific sanctions post-Feb 2022 are updated weekly; the
    direct feed catches these same-day.

Data source
───────────
OFSI publishes the consolidated list at:
  https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.xml
  https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.csv
  https://www.gov.uk/government/publications/financial-sanctions-consolidated-list-of-targets

We use the XML feed (richer structure) with CSV fallback.

Shape of a hit
──────────────
    {
        "name": "BAYKAR TECHNOLOGIES",
        "aliases": ["BAYKAR"],
        "list_type": "UK_OFSI",
        "regime": "Russia",
        "designation_date": "2024-03-15",
        "group_id": "14212",
        "entity_type": "Entity",
        "address": "Istanbul, Turkey",
        "citation_url": "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.xml",
    }
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from . import _common
from ..engine_wiring import wired

logger = logging.getLogger("aria.sources.fcdo_sanctions")

_SOURCE = "uk_ofsi"
_AUTH = "anonymous"

_FEED_XML = "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.xml"
_CONSOLIDATED_URL = "https://www.gov.uk/government/publications/financial-sanctions-consolidated-list-of-targets"

_CACHE: dict[str, Any] = {"fetched_at": 0.0, "records": []}
_CACHE_TTL_S = 6 * 3600  # 6h — OFSI publishes weekly, but check twice daily
_CACHE_LOCK = asyncio.Lock()


def _parse_xml(xml_text: str) -> list[dict]:
    """Parse the OFSI XML consolidated list into normalised records.

    We use ElementTree with a defusedxml fallback if installed. The 2022
    format wraps entries in FinancialSanctionsTarget elements with
    nested Names, Addresses, etc.
    """
    try:
        from defusedxml import ElementTree as ET
        root = ET.fromstring(xml_text)
    except Exception as e:
        logger.warning("[fcdo_sanctions] XML parse failed: %s", e)
        return []

    # The XML namespace varies between OFSI refreshes — strip it.
    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    records: list[dict] = []
    for target in root.iter():
        if _local(target.tag) not in ("FinancialSanctionsTarget", "Target"):
            continue

        group_id = ""
        names: list[dict] = []
        regime = ""
        designation_date = ""
        entity_type = ""
        addresses: list[str] = []

        for child in target.iter():
            tag = _local(child.tag)
            text = (child.text or "").strip()
            if tag == "GroupID" and text:
                group_id = text
            elif tag == "Regime" and text:
                regime = text
            elif tag == "DateDesignated" and text:
                designation_date = text
            elif tag == "GroupTypeDescription" and text:
                entity_type = text
            elif tag == "Name":
                # Name subtree: Name1, Name2..6 + NameType (primary/aka)
                full: list[str] = []
                name_type = ""
                for n in child.iter():
                    nt = _local(n.tag)
                    nv = (n.text or "").strip()
                    if nt.startswith("Name") and nt != "NameType" and nv:
                        full.append(nv)
                    elif nt == "NameType" and nv:
                        name_type = nv
                if full:
                    names.append({
                        "name": " ".join(full).strip(),
                        "type": name_type or "Primary",
                    })
            elif tag == "Address":
                addr_parts: list[str] = []
                for a in child.iter():
                    at = _local(a.tag)
                    av = (a.text or "").strip()
                    if at in ("AddressLine1", "AddressLine2", "City", "Country", "PostCode") and av:
                        addr_parts.append(av)
                if addr_parts:
                    addresses.append(", ".join(addr_parts))

        if not names:
            continue

        # Primary name first, aliases afterwards
        primary = next((n["name"] for n in names if n["type"].lower().startswith("prim")), names[0]["name"])
        aliases = [n["name"] for n in names if n["name"] != primary]

        records.append({
            "name": primary,
            "aliases": aliases,
            "list_type": "UK_OFSI",
            "regime": regime,
            "designation_date": designation_date,
            "group_id": group_id,
            "entity_type": entity_type or "",
            "address": addresses[0] if addresses else "",
            "all_addresses": addresses,
            "citation_url": (
                f"https://www.gov.uk/government/publications/financial-sanctions-"
                f"consolidated-list-of-targets#group-{group_id}" if group_id
                else _CONSOLIDATED_URL
            ),
        })
    return records


async def _load_records() -> list[dict]:
    async with _CACHE_LOCK:
        now = time.time()
        if _CACHE["records"] and (now - _CACHE["fetched_at"] < _CACHE_TTL_S):
            return _CACHE["records"]

        xml_text = await _common.http_get_text(_FEED_XML, timeout=25.0)
        if not xml_text:
            logger.warning("[fcdo_sanctions] XML feed unavailable; keeping stale cache (%d recs)",
                           len(_CACHE["records"]))
            return _CACHE["records"]

        # R-F716 (2026-05-19): sync ElementTree parse on the event
        # loop — see ofac_sdn.py rationale. Moved to worker thread.
        records = await asyncio.to_thread(_parse_xml, xml_text)
        if records:
            _CACHE["records"] = records
            _CACHE["fetched_at"] = now
            logger.info("[fcdo_sanctions] cache refreshed (%d records)", len(records))
        return _CACHE["records"]


@wired(module="sources.fcdo_sanctions", summary="FCDO sanctions lookup for {name}")
async def lookup(
    name: str,
    *,
    threshold: float = 0.85,  # R-F569: bumped 0.70 → 0.85 after MVP fire-test false-positive sweep
    max_hits: int = 15,
) -> dict:
    """Entity-scoped UK OFSI consolidated-list lookup."""
    started = time.time()
    query = {"name": name}
    result = _common.empty_result(
        _SOURCE, query, auth=_AUTH, citation_url=_CONSOLIDATED_URL,
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
                _SOURCE, query, "OFSI list unavailable",
                auth=_AUTH, started_at=started,
            )
        hits = _common.fuzzy_filter(
            records, name, name_key="name",
            threshold=threshold, max_hits=max_hits,
        )
        result["hits"] = hits
        # R-F2167: flag UNVERIFIED if served a stale snapshot (refresh failed).
        return _common.mark_stale_if_expired(
            _common.finalise(result, started), _CACHE, _CACHE_TTL_S)
    except Exception as e:
        logger.warning("[fcdo_sanctions] lookup failed for %r: %s", name, e)
        return _common.error_result(
            _SOURCE, query, f"{type(e).__name__}: {e}",
            auth=_AUTH, started_at=started,
        )


async def is_available() -> bool:
    if _CACHE["records"]:
        return True
    # Cheap HEAD check via a short GET
    text = await _common.http_get_text(_FEED_XML, timeout=8.0)
    return bool(text and "<" in text[:50])
