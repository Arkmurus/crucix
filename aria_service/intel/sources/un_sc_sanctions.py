"""UN Security Council Consolidated Sanctions List — direct primary source.

What this list contains
───────────────────────
The UN Security Council maintains ONE consolidated list covering all
active sanctions regimes adopted by the SC — currently ~12 regimes
(Al-Qaida/ISIL 1267/1989, Taliban 1988, DPRK, Iran, Mali, Somalia, etc.).

Every UN member state is legally obligated to implement these sanctions.
If an entity appears here, all 193 UN members must freeze assets and
prohibit transactions — legally superior to any regional list.

Why direct, not just via OpenSanctions?
───────────────────────────────────────
  - Primary-source citation. For ARK-DD reports, "UN Security Council
    Res. 1267 (1999), listed 2014-03-06" is the canonical wording
    auditors want.
  - OpenSanctions has a ~24h update cadence; UN publishes updates
    immediately after Committee decisions (which can be same-day).
  - Defence-in-depth: if OpenSanctions is down, the DD pipeline should
    not lose UN screening capability.

Data source
───────────
The UN publishes the consolidated list at:
  https://scsanctions.un.org/resources/xml/en/consolidated.xml

XML schema includes DESIGNATED_INDIVIDUALS and DESIGNATED_ENTITIES
root elements; within each, INDIVIDUAL/ENTITY records with name parts,
aliases (INDIVIDUAL_ALIAS/ENTITY_ALIAS), addresses, designation date,
and reference to the specific SC resolution.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from . import _common
from ..engine_wiring import wired

logger = logging.getLogger("aria.sources.un_sc_sanctions")

_SOURCE = "un_sc"
_AUTH = "anonymous"

_FEED_XML = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
_HUMAN_URL = "https://www.un.org/securitycouncil/sanctions/information"

_CACHE: dict[str, Any] = {"fetched_at": 0.0, "records": []}
_CACHE_TTL_S = 12 * 3600  # 12h — UN updates ~monthly, but check more often
_CACHE_LOCK = asyncio.Lock()


def _parse_xml(xml_text: str) -> list[dict]:
    try:
        from defusedxml import ElementTree as ET
        root = ET.fromstring(xml_text)
    except Exception as e:
        logger.warning("[un_sc] XML parse failed: %s", e)
        return []

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    def _text(elem, tag: str) -> str:
        for c in elem:
            if _local(c.tag) == tag:
                return (c.text or "").strip()
        return ""

    records: list[dict] = []

    # Individuals
    for ind in root.iter():
        if _local(ind.tag) != "INDIVIDUAL":
            continue
        first = _text(ind, "FIRST_NAME")
        second = _text(ind, "SECOND_NAME")
        third = _text(ind, "THIRD_NAME")
        fourth = _text(ind, "FOURTH_NAME")
        dataid = _text(ind, "DATAID")
        reference = _text(ind, "REFERENCE_NUMBER")
        listed_on = _text(ind, "LISTED_ON")
        un_list_type = _text(ind, "UN_LIST_TYPE")
        name = " ".join(p for p in (first, second, third, fourth) if p).strip()
        if not name:
            continue

        aliases: list[str] = []
        for alias in ind.iter():
            if _local(alias.tag) == "INDIVIDUAL_ALIAS":
                alias_name = _text(alias, "ALIAS_NAME")
                if alias_name:
                    aliases.append(alias_name)

        records.append({
            "name": name,
            "aliases": aliases,
            "list_type": "UN_SC",
            "regime": un_list_type,
            "designation_date": listed_on,
            "group_id": dataid,
            "reference": reference,
            "entity_type": "Individual",
            "citation_url": f"https://www.un.org/securitycouncil/sanctions/information#{reference or dataid}",
        })

    # Entities
    for ent in root.iter():
        if _local(ent.tag) != "ENTITY":
            continue
        first = _text(ent, "FIRST_NAME")
        dataid = _text(ent, "DATAID")
        reference = _text(ent, "REFERENCE_NUMBER")
        listed_on = _text(ent, "LISTED_ON")
        un_list_type = _text(ent, "UN_LIST_TYPE")
        if not first:
            continue

        aliases: list[str] = []
        for alias in ent.iter():
            if _local(alias.tag) == "ENTITY_ALIAS":
                alias_name = _text(alias, "ALIAS_NAME")
                if alias_name:
                    aliases.append(alias_name)

        # First address only
        addr_str = ""
        for addr in ent.iter():
            if _local(addr.tag) == "ENTITY_ADDRESS":
                parts = []
                for field in ("CITY", "STATE_PROVINCE", "COUNTRY"):
                    v = _text(addr, field)
                    if v:
                        parts.append(v)
                if parts:
                    addr_str = ", ".join(parts)
                    break

        records.append({
            "name": first,
            "aliases": aliases,
            "list_type": "UN_SC",
            "regime": un_list_type,
            "designation_date": listed_on,
            "group_id": dataid,
            "reference": reference,
            "entity_type": "Entity",
            "address": addr_str,
            "citation_url": f"https://www.un.org/securitycouncil/sanctions/information#{reference or dataid}",
        })

    return records


async def _load_records() -> list[dict]:
    async with _CACHE_LOCK:
        now = time.time()
        if _CACHE["records"] and (now - _CACHE["fetched_at"] < _CACHE_TTL_S):
            return _CACHE["records"]

        xml_text = await _common.http_get_text(_FEED_XML, timeout=25.0)
        if not xml_text:
            logger.warning("[un_sc] feed unavailable; keeping stale cache (%d recs)",
                           len(_CACHE["records"]))
            return _CACHE["records"]

        # R-F716 (2026-05-19): sync ElementTree parse on the event
        # loop — see ofac_sdn.py rationale. Moved to worker thread.
        records = await asyncio.to_thread(_parse_xml, xml_text)
        # R-F2577 — partial-drift canary (see ofac_sdn): a large feed parsing implausibly few
        # records is a broken parse; keep the last-known-good cache + warn rather than screen
        # dropped UN-designated entities as clean. Floor = max(absolute 300, 50% of prior).
        _prior = len(_CACHE["records"])
        _floor = max(300, int(_prior * 0.5))
        if records and len(records) < _floor:
            logger.warning(
                "[un_sc] SCHEMA-DRIFT CANARY: parsed %d records (< floor %d; prior %d) from a "
                "%.1fMB feed — keeping last-known-good cache, NOT caching thin data",
                len(records), _floor, _prior, len(xml_text) / 1e6)
            return _CACHE["records"]
        if records:
            _CACHE["records"] = records
            _CACHE["fetched_at"] = now
            logger.info("[un_sc] cache refreshed (%d records)", len(records))
        return _CACHE["records"]


@wired(module="sources.un_sc_sanctions", summary="UN SC sanctions lookup for {name}")
async def lookup(
    name: str,
    *,
    threshold: float = 0.85,  # R-F569: bumped 0.72 → 0.85 after MVP fire-test (Aselsan→ALI SADDAM HUSSEIN false positive)
    max_hits: int = 15,
) -> dict:
    """Entity-scoped UN SC consolidated-list lookup.

    Severity for the orchestrator: any UN hit is HARD_STOP. There are
    no exceptions to UN sanctions under international law.
    """
    started = time.time()
    query = {"name": name}
    result = _common.empty_result(
        _SOURCE, query, auth=_AUTH, citation_url=_HUMAN_URL,
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
                _SOURCE, query, "UN SC list unavailable",
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
        logger.warning("[un_sc] lookup failed for %r: %s", name, e)
        return _common.error_result(
            _SOURCE, query, f"{type(e).__name__}: {e}",
            auth=_AUTH, started_at=started,
        )


async def is_available() -> bool:
    if _CACHE["records"]:
        return True
    text = await _common.http_get_text(_FEED_XML, timeout=8.0)
    return bool(text and "<" in text[:50])
