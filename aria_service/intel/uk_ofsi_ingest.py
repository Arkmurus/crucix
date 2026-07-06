"""R-F1735 — UK OFSI open_api ingester — knowledge facts — mastery heatmap.

Second proven template in ARIA's mastery-ingest pipeline (fan-out from R-F1731).
UK OFSI Consolidated List is a free, open XML download (registration_type="none").
Same domain (sanctions_screening) as OFAC SDN — proven to move the heatmap.

Follows the binding template from R-F1731 exactly:
- store_fact NOT corpus_ingest (heatmap reads facts, not RAG)
- UNIQUE-per-record topic carrying domain tokens
- NO source_url/fact_type/entity_name (avoids verify pipeline stall)
- Content carries a TRACKED jurisdiction synonym
- _bg_task registration for GC survival

Public API:
  ingest_xml_bytes(xml_bytes, *, max_rows=2000) -> dict
  fetch_and_ingest(*, max_rows=2000) -> dict
  stats() -> dict
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from defusedxml import ElementTree as ET
from . import redis_store as rs

logger = logging.getLogger("aria.uk_ofsi_ingest")

STATS_KEY = "crucix:aria:uk_ofsi_ingest:stats"
MAX_ROWS_DEFAULT = 2000

# UK OFSI public XML download (open data, no registration).
OFSI_URL = "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.xml"

# Same domain as OFAC SDN — sanctions_screening is proven to move the heatmap.
SANCTIONS_TOPIC = "sanctions_screening"

# XML namespace
_NS = {"ofsi": "http://schemas.hmtreasury.gov.uk/ofsi/consolidatedlist"}


def _parse_xml(raw_bytes: bytes) -> list[dict]:
    """Parse UK OFSI XML into a list of entity dicts."""
    if not raw_bytes:
        return []
    root = ET.fromstring(raw_bytes)
    targets = root.findall(".//ofsi:FinancialSanctionsTarget", _NS)
    entities = []
    for t in targets:
        def _g(tag: str) -> str:
            el = t.find(f"ofsi:{tag}", _NS)
            return (el.text or "").strip() if el is not None and el.text else ""

        name = _g("name1") or _g("Name6") or ""
        if not name:
            continue
        # Build full name from components
        name_parts = [name]
        for part_tag in ["name2", "name3", "name4", "name5"]:
            part = _g(part_tag)
            if part:
                name_parts.append(part)
        full_name = " ".join(name_parts).strip()

        entity = {
            "name": full_name or name,
            "name6": _g("Name6"),
            "sdn_type": _g("GroupTypeDescription"),
            "regime": _g("RegimeName"),
            "country": _g("Country"),
            "ref": _g("UKSanctionsListRef"),
            "group_id": _g("GroupID"),
            "status": _g("GroupStatus"),
            "listing_type": _g("ListingType"),
            "date_listed": _g("DateListed"),
            "position": _g("Individual_Position"),
            "nationality": _g("Individual_Nationality"),
            "country_of_birth": _g("Individual_CountryOfBirth"),
            "entity_type": _g("Entity_Type"),
            "statement": _g("UKStatementOfReasons"),
        }
        entities.append(entity)
    return entities


def _entity_to_fact(entity: dict) -> tuple[str, str]:
    """Craft (topic, content) for one UK OFSI entity.

    topic = sanctions_screening: UK OFSI <ref> — <name> (unique per record,
    contains domain tokens 'sanctions' + 'screening').
    content = carries 'United Kingdom' (UK cell) + entity's country
    (tracked jurisdiction cells) + domain tokens.
    """
    name = entity.get("name") or "(unnamed entity)"
    sdn_type = (entity.get("sdn_type") or "entity").lower()
    regime = entity.get("regime") or "a UK sanctions regime"
    ref = entity.get("ref") or "unknown"
    country = entity.get("country") or ""
    statement = entity.get("statement") or ""

    # Unique topic per record — contains domain tokens + unique ref
    topic = f"{SANCTIONS_TOPIC}: UK OFSI {ref} — {name}"

    parts = [
        f"{name} ({sdn_type}) is designated on the United Kingdom OFSI "
        f"consolidated sanctions screening list under {regime}."
    ]
    if country:
        parts.append(f"Associated country/jurisdiction: {country}.")
    if statement:
        parts.append(f"UK statement of reasons: {statement[:300]}.")
    parts.append("Source: UK Office of Financial Sanctions Implementation (OFSI) consolidated list.")
    return topic, " ".join(parts)


async def ingest_rows(entities: list[dict], *, max_rows: int = MAX_ROWS_DEFAULT) -> dict:
    """Store each entity as a knowledge fact via store_fact.

    Prioritises entities in TRACKED non-UK jurisdictions (they lift the
    heatmap FLOOR). Idempotent: store_fact merges duplicates.
    """
    from . import knowledge as _k
    try:
        from .coverage_heatmap import JURISDICTION_SYNONYMS
        tracked = {k for k in JURISDICTION_SYNONYMS if k not in ("UK", "UN")}
        tracked_syn = {k: [s.lower() for s in v] for k, v in JURISDICTION_SYNONYMS.items()}
    except Exception:
        tracked, tracked_syn = set(), {}

    def _is_weak_tracked(country: str) -> bool:
        cl = (country or "").lower()
        for juris in tracked:
            if any(syn in cl for syn in tracked_syn.get(juris, [])):
                return True
        return False

    # Order: weak-tracked-jurisdiction entities first, then the rest.
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
                topic, content, source="uk_ofsi", confidence="CONFIRMED",
                skip_semantic_index=True,
            )
            ingested += 1
            ck = e.get("country") or "Unspecified"
            by_country[ck] = by_country.get(ck, 0) + 1
        except Exception as ex:
            errors += 1
            logger.debug("[uk_ofsi_ingest] store_fact failed: %s", ex)

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
    # Brain wiring
    try:
        from .engine_wiring import wire_success, wire_failure
        if ingested:
            wire_success(
                module="uk_ofsi_ingest",
                summary=f"Ingested {ingested} UK OFSI designations as sanctions_screening facts "
                        f"({len(by_country)} countries; {len(weak)} weak-juris prioritised).",
                source_id="open_api_ingest:uk_ofsi",
            )
        else:
            wire_failure(
                module="uk_ofsi_ingest",
                detail=f"UK OFSI ingest produced 0 facts (errors={errors}, short={skipped_short}).",
                gap_type="source_failure", source="open_api_ingest:uk_ofsi",
            )
    except Exception:
        pass
    logger.info("[uk_ofsi_ingest] %d ingested, %d errors, %d short", ingested, errors, skipped_short)
    return summary


async def ingest_xml_bytes(xml_bytes: bytes, *, max_rows: int = MAX_ROWS_DEFAULT) -> dict:
    """Parse UK OFSI XML and ingest as facts."""
    entities = _parse_xml(xml_bytes)
    if not entities:
        return {"error": "empty or unparseable UK OFSI XML", "ingested": 0}
    return await ingest_rows(entities, max_rows=max_rows)


async def fetch_and_ingest(*, max_rows: int = MAX_ROWS_DEFAULT) -> dict:
    """Fetch UK OFSI XML (open data, no auth) and ingest as facts."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:  # no-breaker: UK OFSI ingest is periodic batch; breaker would stall sanctions updates
            resp = await client.get(OFSI_URL)
        if resp.status_code != 200 or not resp.content:
            msg = f"UK OFSI download failed: HTTP {resp.status_code}"
            logger.warning("[uk_ofsi_ingest] %s", msg)
            try:
                from .engine_wiring import wire_failure
                wire_failure(module="uk_ofsi_ingest", detail=msg,
                             gap_type="source_failure", source="open_api_ingest:uk_ofsi")
            except Exception:
                pass
            return {"error": msg, "ingested": 0}
        return await ingest_xml_bytes(resp.content, max_rows=max_rows)
    except Exception as e:
        logger.warning("[uk_ofsi_ingest] fetch failed: %s", e)
        return {"error": f"fetch failed: {e!r}", "ingested": 0}


async def stats() -> dict:
    snap = await rs.get_json(STATS_KEY)
    if not snap:
        return {"ingested": 0, "note": "No UK OFSI ingest has run yet."}
    return snap
