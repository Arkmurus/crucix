"""R-F1736 — EU Sanctions Map open_api ingester — knowledge facts — mastery heatmap.

Third proven template in ARIA's mastery-ingest pipeline (fan-out from R-F1731).
EU Sanctions Map is a free, open web portal (registration_type="none").
Same domain (sanctions_screening) as OFAC SDN and UK OFSI — proven to move the heatmap.

NOTE: The EU Sanctions Map API endpoint may differ from the URL in portal_registry.
The portal URL is https://sanctionsmap.eu (a web UI). The actual data download URL
may be at a different endpoint. This ingester uses the EU FSF (Financial Sanctions
File) XML endpoint when available, falling back to the web portal URL.

Follows the binding template from R-F1731 exactly.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Optional

from . import redis_store as rs

logger = logging.getLogger("aria.eu_sanctions_ingest")

STATS_KEY = "crucix:aria:eu_sanctions_ingest:stats"
MAX_ROWS_DEFAULT = 2000

# EU Sanctions data URLs (open data, no registration).
# The EU FSF (Financial Sanctions File) XML endpoint with public token.
# Token 'dG9rZW4tMjAxNw' = base64('token-2017') — the EU's public token, not a secret.
EU_FSF_URL = "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw"
EU_PORTAL_URL = "https://sanctionsmap.eu"

# Same domain as OFAC SDN and UK OFSI.
SANCTIONS_TOPIC = "sanctions_screening"

# XML namespace for EU FSF format
_NS = {"eu": "http://eu.europa.ec/fsd/fsf"}


def _parse_xml(raw_bytes: bytes) -> list[dict]:
    """Parse EU FSF XML into a list of entity dicts."""
    if not raw_bytes:
        return []
    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError:
        return []
    
    entities = []
    # Try different possible XML structures
    targets = root.findall(".//eu:SanctionEntity", _NS) or root.findall(".//SanctionEntity")
    
    for t in targets:
        def _g(tag: str) -> str:
            el = t.find(f"eu:{tag}", _NS) or t.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""
        
        name = _g("FullName") or _g("Name") or _g("FirstName") or ""
        if not name:
            continue
        
        entity = {
            "name": name,
            "sdn_type": _g("EntityType") or _g("LegalType") or "entity",
            "regime": _g("SanctionsProgram") or _g("Regulation") or "EU sanctions",
            "country": _g("Country") or _g("Nationality") or "",
            "ref": _g("ReferenceNumber") or _g("Id") or "",
            "remark": _g("Remark") or _g("Reason") or "",
        }
        entities.append(entity)
    
    return entities


def _entity_to_fact(entity: dict) -> tuple[str, str]:
    """Craft (topic, content) for one EU sanctions entity."""
    name = entity.get("name") or "(unnamed entity)"
    sdn_type = (entity.get("sdn_type") or "entity").lower()
    regime = entity.get("regime") or "an EU sanctions regime"
    ref = entity.get("ref") or "unknown"
    country = entity.get("country") or ""

    topic = f"{SANCTIONS_TOPIC}: EU Sanctions {ref} — {name}"

    parts = [
        f"{name} ({sdn_type}) is designated on the European Union "
        f"consolidated sanctions screening list under {regime}."
    ]
    if country:
        parts.append(f"Associated country/jurisdiction: {country}.")
    if entity.get("remark"):
        parts.append(f"EU remark: {entity['remark'][:300]}.")
    parts.append("Source: European Union consolidated sanctions list.")
    return topic, " ".join(parts)


async def ingest_rows(entities: list[dict], *, max_rows: int = MAX_ROWS_DEFAULT) -> dict:
    """Store each entity as a knowledge fact via store_fact."""
    from . import knowledge as _k
    try:
        from .coverage_heatmap import JURISDICTION_SYNONYMS
        tracked = {k for k in JURISDICTION_SYNONYMS if k not in ("EU", "UN")}
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
                topic, content, source="eu_sanctions", confidence="CONFIRMED",
                skip_semantic_index=True,
            )
            ingested += 1
            ck = e.get("country") or "Unspecified"
            by_country[ck] = by_country.get(ck, 0) + 1
        except Exception as ex:
            errors += 1
            logger.debug("[eu_sanctions_ingest] store_fact failed: %s", ex)

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
                module="eu_sanctions_ingest",
                summary=f"Ingested {ingested} EU sanctions designations as sanctions_screening facts "
                        f"({len(by_country)} countries; {len(weak)} weak-juris prioritised).",
                source_id="open_api_ingest:eu_sanctions",
            )
        else:
            wire_failure(
                module="eu_sanctions_ingest",
                detail=f"EU sanctions ingest produced 0 facts (errors={errors}, short={skipped_short}).",
                gap_type="source_failure", source="open_api_ingest:eu_sanctions",
            )
    except Exception:
        pass
    logger.info("[eu_sanctions_ingest] %d ingested, %d errors, %d short", ingested, errors, skipped_short)
    return summary


async def ingest_xml_bytes(xml_bytes: bytes, *, max_rows: int = MAX_ROWS_DEFAULT) -> dict:
    """Parse EU sanctions XML and ingest as facts."""
    entities = _parse_xml(xml_bytes)
    if not entities:
        return {"error": "empty or unparseable EU sanctions XML", "ingested": 0}
    return await ingest_rows(entities, max_rows=max_rows)


async def fetch_and_ingest(*, max_rows: int = MAX_ROWS_DEFAULT) -> dict:
    """Fetch EU sanctions data and ingest as facts."""
    import httpx
    errors = []
    
    # Try primary EU FSF endpoint
    for url in [EU_FSF_URL, EU_PORTAL_URL]:
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
    logger.warning("[eu_sanctions_ingest] %s", msg)
    try:
        from .engine_wiring import wire_failure
        wire_failure(module="eu_sanctions_ingest", detail=msg,
                     gap_type="source_failure", source="open_api_ingest:eu_sanctions")
    except Exception:
        pass
    return {"error": msg, "ingested": 0}


async def stats() -> dict:
    snap = await rs.get_json(STATS_KEY)
    if not snap:
        return {"ingested": 0, "note": "No EU sanctions ingest has run yet."}
    return snap
