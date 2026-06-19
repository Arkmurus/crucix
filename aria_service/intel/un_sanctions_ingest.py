"""R-F1738 — UN Sanctions open_api ingester — knowledge facts — mastery heatmap.

Fourth proven template in ARIA's mastery-ingest pipeline (fan-out from R-F1731).
UN Security Council Sanctions is a free, open web portal (registration_type="none").
Same domain (sanctions_screening) as OFAC SDN, UK OFSI, and EU — proven to move the heatmap.

NOTE: The UN Sanctions consolidated list API endpoint may differ from the portal URL.
The portal URL is https://www.un.org/securitycouncil/sanctions (a web UI).
The actual data download URL may be at a different endpoint.

Follows the binding template from R-F1731 exactly.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Optional

from . import redis_store as rs

logger = logging.getLogger("aria.un_sanctions_ingest")

STATS_KEY = "crucix:aria:un_sanctions_ingest:stats"
MAX_ROWS_DEFAULT = 2000

# UN Sanctions data URLs (open data, no registration).
UN_SC_URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
UN_PORTAL_URL = "https://www.un.org/securitycouncil/sanctions"

# Same domain as OFAC SDN, UK OFSI, EU.
SANCTIONS_TOPIC = "sanctions_screening"

# UN sanctions XML has NO namespace. Root is CONSOLIDATED_LIST.
# Individuals are under INDIVIDUALS/INDIVIDUAL, entities under ENTITIES/ENTITY.


def _parse_xml(raw_bytes: bytes) -> list[dict]:
    """Parse UN sanctions XML into a list of entity dicts.
    
    Real XML structure (no namespace):
    <CONSOLIDATED_LIST>
      <INDIVIDUALS>
        <INDIVIDUAL>
          <DATAID>...</DATAID>
          <FIRST_NAME>...</FIRST_NAME>
          <SECOND_NAME>...</SECOND_NAME>
          <THIRD_NAME>...</THIRD_NAME>
          <UN_LIST_TYPE>DRC</UN_LIST_TYPE>
          <REFERENCE_NUMBER>CDi.001</REFERENCE_NUMBER>
          <LISTED_ON>2012-12-31</LISTED_ON>
          <COMMENTS1>...</COMMENTS1>
          <NATIONALITY>...</NATIONALITY>
          <COUNTRY>...</COUNTRY>
        </INDIVIDUAL>
      </INDIVIDUALS>
      <ENTITIES>
        <ENTITY>
          <DATAID>...</DATAID>
          <FIRST_NAME>ADF</FIRST_NAME>
          <UN_LIST_TYPE>DRC</UN_LIST_TYPE>
          <REFERENCE_NUMBER>CDe.001</REFERENCE_NUMBER>
        </ENTITY>
      </ENTITIES>
    </CONSOLIDATED_LIST>
    """
    if not raw_bytes:
        return []
    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError:
        return []
    
    entities = []
    
    # Parse INDIVIDUALS
    ind_parent = root.find("INDIVIDUALS")
    if ind_parent is not None:
        for ind in ind_parent.findall("INDIVIDUAL"):
            def _g(tag: str) -> str:
                el = ind.find(tag)
                return (el.text or "").strip() if el is not None and el.text else ""
            
            first = _g("FIRST_NAME")
            second = _g("SECOND_NAME")
            third = _g("THIRD_NAME")
            name = " ".join(p for p in [first, second, third] if p).strip()
            if not name:
                continue
            
            entity = {
                "name": name,
                "sdn_type": "individual",
                "regime": _g("UN_LIST_TYPE") or "UN sanctions",
                "country": _g("COUNTRY") or _g("NATIONALITY") or "",
                "ref": _g("REFERENCE_NUMBER") or "",
                "remark": _g("COMMENTS1") or "",
            }
            entities.append(entity)
    
    # Parse ENTITIES
    ent_parent = root.find("ENTITIES")
    if ent_parent is not None:
        for ent in ent_parent.findall("ENTITY"):
            def _g2(tag: str) -> str:
                el = ent.find(tag)
                return (el.text or "").strip() if el is not None and el.text else ""
            
            name = _g2("FIRST_NAME")
            if not name:
                continue
            
            entity = {
                "name": name,
                "sdn_type": "entity",
                "regime": _g2("UN_LIST_TYPE") or "UN sanctions",
                "country": _g2("COUNTRY") or "",
                "ref": _g2("REFERENCE_NUMBER") or "",
                "remark": _g2("COMMENTS1") or "",
            }
            entities.append(entity)
    
    return entities


def _entity_to_fact(entity: dict) -> tuple[str, str]:
    """Craft (topic, content) for one UN sanctions entity."""
    name = entity.get("name") or "(unnamed entity)"
    sdn_type = (entity.get("sdn_type") or "entity").lower()
    regime = entity.get("regime") or "a UN sanctions regime"
    ref = entity.get("ref") or "unknown"
    country = entity.get("country") or ""

    topic = f"{SANCTIONS_TOPIC}: UN Sanctions {ref} — {name}"

    parts = [
        f"{name} ({sdn_type}) is designated on the United Nations "
        f"consolidated sanctions screening list under {regime}."
    ]
    if country:
        parts.append(f"Associated country/jurisdiction: {country}.")
    if entity.get("remark"):
        parts.append(f"UN remark: {entity['remark'][:300]}.")
    parts.append("Source: United Nations Security Council sanctions list.")
    return topic, " ".join(parts)


async def ingest_rows(entities: list[dict], *, max_rows: int = MAX_ROWS_DEFAULT) -> dict:
    """Store each entity as a knowledge fact via store_fact."""
    from . import knowledge as _k
    try:
        from .coverage_heatmap import JURISDICTION_SYNONYMS
        tracked = {k for k in JURISDICTION_SYNONYMS if k not in ("UN", "EU")}
        tracked_syn = {k: [s.lower() for s in v] for k, v in JURISDICTION_SYNONYMS.items()}
    except Exception:
        tracked, tracked_syn = set(), {}

    def _is_weak_tracked(country: str) -> bool:
        cl = (country or "").lower()
        for juris in tracked:
            if any(syn in cl for syn in tracked_syn.get(juris, [])):
                return True
        return False

    weak, rest = [], []
    for e in entities:
        (weak if _is_weak_tracked(e.get("country", "")) else rest).append(e)
    ordered = (weak + rest)[:max_rows]

    ingested = errors = skipped_short = 0
    by_country: dict[str, int] = {}
    for e in ordered:
        topic, content = _entity_to_fact(e)
        if len(content) < 60:
            skipped_short += 1
            continue
        try:
            await _k.store_fact(
                topic, content, source="un_sanctions", confidence="CONFIRMED",
                skip_semantic_index=True,
            )
            ingested += 1
            ck = e.get("country") or "Unspecified"
            by_country[ck] = by_country.get(ck, 0) + 1
        except Exception as ex:
            errors += 1
            logger.debug("[un_sanctions_ingest] store_fact failed: %s", ex)

    summary = {
        "ingested": ingested,
        "errors": errors,
        "skipped_short": skipped_short,
        "weak_juris_prioritised": len(weak),
        "by_country_top10": dict(sorted(by_country.items(), key=lambda kv: -kv[1])[:10]),
        "max_rows": max_rows,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await rs.set_json(STATS_KEY, summary)
    except Exception:
        pass
    try:
        from .engine_wiring import wire_success, wire_failure
        if ingested:
            wire_success(
                module="un_sanctions_ingest",
                summary=f"Ingested {ingested} UN sanctions designations as sanctions_screening facts "
                        f"({len(by_country)} countries; {len(weak)} weak-juris prioritised).",
                source_id="open_api_ingest:un_sanctions",
            )
        else:
            wire_failure(
                module="un_sanctions_ingest",
                detail=f"UN sanctions ingest produced 0 facts (errors={errors}, short={skipped_short}).",
                gap_type="source_failure", source="open_api_ingest:un_sanctions",
            )
    except Exception:
        pass
    logger.info("[un_sanctions_ingest] %d ingested, %d errors, %d short", ingested, errors, skipped_short)
    return summary


async def ingest_xml_bytes(xml_bytes: bytes, *, max_rows: int = MAX_ROWS_DEFAULT) -> dict:
    """Parse UN sanctions XML and ingest as facts."""
    entities = _parse_xml(xml_bytes)
    if not entities:
        return {"error": "empty or unparseable UN sanctions XML", "ingested": 0}
    return await ingest_rows(entities, max_rows=max_rows)


async def fetch_and_ingest(*, max_rows: int = MAX_ROWS_DEFAULT) -> dict:
    """Fetch UN sanctions data and ingest as facts."""
    import httpx
    errors = []
    
    for url in [UN_SC_URL, UN_PORTAL_URL]:
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(url)
            if resp.status_code == 200 and resp.content:
                result = await ingest_xml_bytes(resp.content, max_rows=max_rows)
                if result.get("ingested", 0) > 0:
                    return result
                errors.append(f"{url}: parsed 0 entities")
            else:
                errors.append(f"{url}: HTTP {resp.status_code}")
        except Exception as e:
            errors.append(f"{url}: {e}")
    
    msg = "; ".join(errors) if errors else "all endpoints failed"
    logger.warning("[un_sanctions_ingest] %s", msg)
    try:
        from .engine_wiring import wire_failure
        wire_failure(module="un_sanctions_ingest", detail=msg,
                     gap_type="source_failure", source="open_api_ingest:un_sanctions")
    except Exception:
        pass
    return {"error": msg, "ingested": 0}


async def stats() -> dict:
    snap = await rs.get_json(STATS_KEY)
    if not snap:
        return {"ingested": 0, "note": "No UN sanctions ingest has run yet."}
    return snap
