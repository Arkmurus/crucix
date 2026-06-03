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
import logging
import time
from typing import Any

from . import _common
from ..engine_wiring import wired

logger = logging.getLogger("aria.sources.ofac_sdn")

_SOURCE = "ofac_sdn"
_AUTH = "anonymous"

# The enhanced JSON endpoint returns 200 with 0 bytes as of 2026-04-18
# (likely a CDN issue on Treasury's side). The XML endpoint at
# www.treasury.gov/ofac/downloads/sdn.xml is the documented stable feed
# and returns the full ~28MB list. We parse that instead.
#
# Schema: <sdnList><sdnEntry><uid/><firstName/><lastName/><sdnType/>
#   <programList><program/></programList>
#   <akaList><aka><type/><firstName/><lastName/></aka></akaList>
#   <addressList><address><city/><country/></address></addressList>
# </sdnEntry></sdnList>
_FEED_XML = "https://www.treasury.gov/ofac/downloads/sdn.xml"
# Fallback CSV feed if the XML endpoint is unavailable
_FEED_CSV = "https://www.treasury.gov/ofac/downloads/sdn.csv"

_CACHE: dict[str, Any] = {"fetched_at": 0.0, "records": []}
_CACHE_TTL_S = 3 * 3600  # 3h — between the 6h OpenSanctions refresh and real-time

_CACHE_LOCK = asyncio.Lock()


def _parse_xml(xml_text: str) -> list[dict]:
    """Parse the OFAC XML SDN list into normalised records."""
    try:
        try:
            from defusedxml import ElementTree as ET  # type: ignore
        except ImportError:
            from xml.etree import ElementTree as ET
        root = ET.fromstring(xml_text)
    except Exception as e:
        logger.warning("[ofac_sdn] XML parse failed: %s", e)
        return []

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    records: list[dict] = []
    for entry in root.iter():
        if _local(entry.tag) != "sdnEntry":
            continue

        uid = ""
        first = ""
        last = ""
        sdn_type = ""
        title = ""
        programs: list[str] = []
        aliases: list[str] = []
        addr_str = ""

        for child in entry:
            tag = _local(child.tag)
            text = (child.text or "").strip()
            if tag == "uid":
                uid = text
            elif tag == "firstName":
                first = text
            elif tag == "lastName":
                last = text
            elif tag == "sdnType":
                sdn_type = text
            elif tag == "title":
                title = text
            elif tag == "programList":
                for p in child:
                    if _local(p.tag) == "program" and (p.text or "").strip():
                        programs.append(p.text.strip())
            elif tag == "akaList":
                for a in child:
                    if _local(a.tag) != "aka":
                        continue
                    af = ""
                    al = ""
                    for sub in a:
                        st = _local(sub.tag)
                        sv = (sub.text or "").strip()
                        if st == "firstName":
                            af = sv
                        elif st == "lastName":
                            al = sv
                    full = f"{af} {al}".strip()
                    if full:
                        aliases.append(full)
            elif tag == "addressList" and not addr_str:
                for addr in child:
                    if _local(addr.tag) != "address":
                        continue
                    parts = []
                    for sub in addr:
                        st = _local(sub.tag)
                        sv = (sub.text or "").strip()
                        if st in ("city", "stateOrProvince", "country") and sv:
                            parts.append(sv)
                    if parts:
                        addr_str = ", ".join(parts)
                        break

        primary_name = f"{first} {last}".strip() if last else first
        if not primary_name:
            continue

        records.append({
            "uid": uid,
            "name": primary_name,
            "aliases": aliases,
            "list_type": "SDN",
            "programs": programs,
            "sdn_type": sdn_type,
            "title": title,
            "address": addr_str,
            "designation_date": "",  # XML doesn't carry a designation date per entry
            "citation_url": (
                f"https://sanctionssearch.ofac.treas.gov/Details.aspx?id={uid}"
                if uid else "https://sanctionssearch.ofac.treas.gov/"
            ),
        })
    return records


async def _load_records() -> list[dict]:
    """Fetch and cache the SDN list. Returns normalised records."""
    async with _CACHE_LOCK:
        now = time.time()
        if _CACHE["records"] and (now - _CACHE["fetched_at"] < _CACHE_TTL_S):
            return _CACHE["records"]

        xml_text = await _common.http_get_text(_FEED_XML, timeout=45.0)
        if not xml_text:
            logger.warning(
                "[ofac_sdn] XML feed unavailable; keeping stale cache (%d recs)",
                len(_CACHE["records"]),
            )
            return _CACHE["records"]

        # R-F716 (2026-05-19): _parse_xml walks ~28MB XML for ~19k SDN
        # records — synchronous ElementTree work on the event loop
        # stalled it for 5s+ at every refresh (live evidence: fly logs
        # 2026-05-19 08:47:35 wedged ~3s after this log line). Run in
        # a worker thread so concurrent chat-stream + brain-absorb stay
        # responsive during the refresh.
        records = await asyncio.to_thread(_parse_xml, xml_text)
        if records:
            _CACHE["records"] = records
            _CACHE["fetched_at"] = now
            logger.info("[ofac_sdn] cache refreshed (%d records)", len(records))
        return _CACHE["records"]


@wired(module="sources.ofac_sdn", summary="OFAC SDN lookup for {name}")
async def lookup(
    name: str,
    *,
    threshold: float = 0.85,
    max_hits: int = 15,
) -> dict:
    """Entity-scoped OFAC SDN lookup. Returns canonical source-result dict.

    Severity interpretation for the orchestrator
    ────────────────────────────────────────────
    Any hit on SDN is a HARD_STOP — you cannot transact with an SDN-
    designated person/entity (50 Percent Rule extends to subsidiaries).
    Even a fuzzy match deserves human review before clearing.

    R-F569 (2026-05-16) — threshold bumped 0.70 → 0.85 after MVP fire-
    test showed 0.70 produced 3+ false hits per query (Embraer →
    MARANER HOLDINGS, Aselsan → "Guillermo NIEBLAS NAVA",
    Acme → "TAMIN KALAYE SABZ ARAS"). The orchestrator's R-F569
    name-overlap gate downstream is the second line of defence —
    raising threshold here cuts the false candidates before they even
    leave the adapter.
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
    the XML feed fetch returns something XML-shaped."""
    if _CACHE["records"]:
        return True
    text = await _common.http_get_text(_FEED_XML, timeout=10.0)
    return bool(text and "<sdnList" in text[:200])
