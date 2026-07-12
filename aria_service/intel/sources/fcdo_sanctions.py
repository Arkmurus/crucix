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


# R-F2565 — a name-part element in the live 2022format is name1..name5 (lowercase
# forenames) + Name6 (capital surname/main). Match case-insensitively so a future
# case flip (or the legacy Name1..Name6) still parses.
_NAME_PART_RE = re.compile(r"^name([1-6])$", re.I)


def _parse_xml(xml_text: str) -> list[dict]:
    """Parse the OFSI/FCDO XML consolidated list ("2022format") into normalised records.

    R-F2565 — CORRECTED for the live schema. Each <FinancialSanctionsTarget> is a FLAT
    row = ONE name variant with fields directly beneath it (there is NO <Name>/<Address>
    wrapper subtree): name1..name5 (forenames) + Name6 (surname/main), NameNonLatinScript
    (Arabic/Cyrillic/Chinese), GroupID, AliasType ("Primary name" | "Primary name
    variation" | "AKA" | "FKA" | ...), RegimeName, DateDesignated,
    GroupTypeDescription, Address1..Address6/PostCode/Country. Rows that share a GroupID
    are the SAME entity's name variants — we group by GroupID and emit ONE record per
    entity (primary name + the rest as aliases).

    The previous parser looked for a nested <Name> element that this format does not
    contain, so name extraction returned nothing and EVERY record was dropped (`if not
    names: continue`) → 0 records from a 19.7k-target feed, silently. Verified against the
    live feed 2026-07-12.
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

    # Group rows by GroupID (fallback to a synthetic per-row id when absent).
    groups: dict[str, dict] = {}
    _synthetic = 0
    for target in root.iter():
        if _local(target.tag) not in ("FinancialSanctionsTarget", "Target"):
            continue

        group_id = ""
        alias_type = ""
        regime = ""
        designation_date = ""
        entity_type = ""
        uk_ref = ""
        name_parts: dict[int, str] = {}
        non_latin = ""            # R-F2565: NameNonLatinScript (Arabic/Cyrillic/Chinese)
        addr_parts: list[str] = []

        for child in target.iter():
            if child is target:
                continue
            tag = _local(child.tag)
            text = (child.text or "").strip()
            if not text:
                continue
            m = _NAME_PART_RE.match(tag)
            if m:
                name_parts[int(m.group(1))] = text
                continue
            if tag == "NameNonLatinScript":
                non_latin = text
            elif tag == "GroupID":
                group_id = text
            elif tag == "AliasType":
                alias_type = text
            elif tag in ("RegimeName", "Regime"):
                regime = text
            elif tag in ("DateDesignated", "DateListed") and not designation_date:
                designation_date = text
            elif tag == "GroupTypeDescription":
                entity_type = text
            elif tag == "UKSanctionsListRef":
                uk_ref = text
            elif tag in ("Address1", "Address2", "Address3", "Address4",
                         "Address5", "Address6", "PostCode", "Country"):
                addr_parts.append(text)

        # Full name = forenames (name1..name5) then surname (Name6), in index order.
        full_name = " ".join(name_parts[i] for i in sorted(name_parts) if name_parts[i]).strip()
        full_name = re.sub(r"\s+", " ", full_name)
        # R-F2565: a row whose ONLY name is non-Latin must NOT be dropped (that would be a
        # false clean if it were the sole row of its GroupID). Fall back to the non-Latin
        # name so the designation always surfaces.
        if not full_name and non_latin:
            full_name = non_latin
        if not full_name:
            continue

        # R-F2565: the non-Latin name is a distinct searchable string for the SAME entity —
        # carry it as an alias so native-script screen queries match.
        row_aliases = [full_name]
        if non_latin and non_latin != full_name:
            row_aliases.append(non_latin)

        gid = group_id or f"_row{_synthetic}"
        if not group_id:
            _synthetic += 1
        g = groups.get(gid)
        is_primary = alias_type.lower() == "primary name"
        addr = ", ".join(addr_parts) if addr_parts else ""
        if g is None:
            groups[gid] = {
                "group_id": group_id,
                "primary": full_name if is_primary else "",
                "first_seen": full_name,      # fallback primary if no explicit primary row
                # aliases seed: the non-Latin name always, plus this row's Latin name
                # unless it's the primary (which is tracked separately).
                "aliases": [a for a in row_aliases if not (is_primary and a == full_name)],
                "regime": regime,
                "designation_date": designation_date,
                "entity_type": entity_type,
                "uk_ref": uk_ref,
                "addresses": [addr] if addr else [],
            }
        else:
            if is_primary and not g["primary"]:
                g["primary"] = full_name
            elif full_name != g["primary"]:
                g["aliases"].append(full_name)
            if non_latin and non_latin != g["primary"]:
                g["aliases"].append(non_latin)
            # Backfill shared metadata from whichever row carries it.
            for k, v in (("regime", regime), ("designation_date", designation_date),
                         ("entity_type", entity_type), ("uk_ref", uk_ref)):
                if v and not g[k]:
                    g[k] = v
            if addr and addr not in g["addresses"]:
                g["addresses"].append(addr)

    records: list[dict] = []
    for gid, g in groups.items():
        primary = g["primary"] or g["first_seen"]
        # R-F2565: dedupe aliases (multi-primary/variation rows repeat) and drop the primary.
        aliases = list(dict.fromkeys(a for a in g["aliases"] if a and a != primary))
        group_id = g["group_id"]
        records.append({
            "name": primary,
            "aliases": aliases,
            "list_type": "UK_OFSI",
            "regime": g["regime"],
            "designation_date": g["designation_date"],
            "group_id": group_id,
            "entity_type": g["entity_type"] or "",
            "uk_ref": g["uk_ref"],
            "address": g["addresses"][0] if g["addresses"] else "",
            "all_addresses": g["addresses"],
            "citation_url": (
                f"https://www.gov.uk/government/publications/financial-sanctions-"
                f"consolidated-list-of-targets#group-{group_id}" if group_id
                else _CONSOLIDATED_URL
            ),
        })
    # R-F2565 schema-drift canary: the original bug (0 records) is caught downstream
    # (0 → "list unavailable", never clean), but a PARTIAL drift that silently drops a
    # SUBSET of entities' name fields would leave len(records)>0 and look healthy while
    # some designations become false cleans. A large feed body that yields implausibly
    # few entities is the tell — warn loudly so the drift is caught, not screened as thin.
    if len(xml_text) > 1_000_000 and len(records) < 100:
        logger.warning(
            "[fcdo_sanctions] SCHEMA-DRIFT CANARY: only %d entities parsed from a %.1fMB "
            "feed — implausibly few; the OFSI schema may have changed and the parser is "
            "stale (screening coverage is degraded, not clean).",
            len(records), len(xml_text) / 1e6)
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
