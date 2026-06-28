"""R-F1731 — OFAC SDN open_api ingester → knowledge facts → mastery heatmap.

The FIRST proven template for ARIA's mastery-ingest pipeline (ARIA's 2026-06-19
plan). The 13 `registration_type="none"` ("open_api") portals are free, need no
registration/CAPTCHA, but their bulk data is NOT in the knowledge base — so the
coverage heatmap (Phase A gate #2) scores their topics as thin/absent. Sanctions
is the worst core topic (~50%); OFAC SDN is the canonical US sanctions list and a
public download. This module bulk-ingests it as KNOWLEDGE FACTS so the
`sanctions_screening` heatmap cells rise.

WHY store_fact, not corpus_ingest (the trap that would waste the whole effort):
`coverage_heatmap.build_heatmap` counts only `knowledge.all_facts()` + intel_ledger
signals — it does NOT read the RAG/corpus store. So sipri_ingest (corpus_ingest →
RAG) does NOT move the heatmap. We MUST write via `knowledge.store_fact`.

WHY the topic must be the exact domain slug (the matcher constraint):
A fact counts for a (domain, jurisdiction) cell iff its text (entity+topic+content+
summary+detail+source, lowercased) contains ALL domain tokens (snake_case-split,
≥3 chars) AND ANY jurisdiction synonym. So topic='sanctions_screening' satisfies
BOTH domain tokens ('sanctions','screening') in one field; a topic of 'ofac' or
'sanctions' alone would miss 'screening' → ZERO heatmap movement. And the content
must carry a TRACKED jurisdiction synonym (coverage_heatmap.JURISDICTION_SYNONYMS:
US/UK/EU/UN + Angola/Mozambique/… only ~17). We always include "United States"
(OFAC is US authority) so every entry lifts the US cell, PLUS the entity's own
country so entries in weak cells (Angola/Mozambique/Gulf) lift the FLOOR (gate #2
is the MIN cell, not the average).

Public API
──────────
  ingest_csv_bytes(sdn_bytes, add_bytes=b"", *, max_rows=2000) -> dict
  fetch_and_ingest(*, max_rows=2000) -> dict   # GET sdn.csv + add.csv from OFAC
  stats() -> dict
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from . import redis_store as rs

logger = logging.getLogger("aria.ofac_sdn_ingest")

STATS_KEY = "crucix:aria:ofac_sdn_ingest:stats"
MAX_ROWS_DEFAULT = 2000

# Canonical OFAC public downloads (no registration — open data).
SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"
ADD_URL = "https://www.treasury.gov/ofac/downloads/add.csv"

# R-F1733: the topic must (a) carry BOTH heatmap domain tokens 'sanctions' +
# 'screening' so the fact lands in the sanctions cell, AND (b) be UNIQUE per entity.
# store_fact (knowledge.py:1194-1246) has one-fact-per-topic UPDATE semantics: a new
# fact whose exact topic already exists OVERWRITES it instead of creating a new fact.
# A shared literal topic='sanctions_screening' therefore collapsed 800 entities into
# one churned fact (0 heatmap movement). We prefix the domain slug and suffix the
# entity id+name → tokens preserved, topic unique → one distinct fact per designation.
SANCTIONS_DOMAIN_SLUG = "sanctions_screening"


def _entity_topic(ent_num: str, name: str) -> str:
    return f"{SANCTIONS_DOMAIN_SLUG}: OFAC SDN {ent_num} — {name}"[:160]

# OFAC SDN.CSV is headerless, fixed column order; empty fields are "-0-".
_SDN_COLS = ["ent_num", "name", "sdn_type", "program", "title", "call_sign",
             "vess_type", "tonnage", "grt", "vess_flag", "vess_owner", "remarks"]
# OFAC ADD.CSV is headerless: ent_num, add_num, address, city_state_postal, country, remarks
_ADD_COLS = ["ent_num", "add_num", "address", "city_state_postal", "country", "remarks"]


def _clean(v: Optional[str]) -> str:
    """OFAC uses '-0-' for empty; strip quotes/whitespace."""
    s = (v or "").strip().strip('"').strip()
    return "" if s == "-0-" else s


def _parse_fixed_csv(raw_bytes: bytes, cols: list[str]) -> list[dict]:
    if not raw_bytes:
        return []
    text = raw_bytes.decode("latin-1", errors="replace")  # OFAC files are latin-1
    out: list[dict] = []
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        rec = {cols[i]: _clean(row[i]) for i in range(min(len(cols), len(row)))}
        if rec.get("ent_num"):
            out.append(rec)
    return out


def _build_country_index(add_rows: list[dict]) -> dict[str, str]:
    """ent_num → first non-empty country from the address file."""
    idx: dict[str, str] = {}
    for a in add_rows:
        en = a.get("ent_num")
        c = a.get("country")
        if en and c and en not in idx:
            idx[en] = c
    return idx


def _entity_to_fact(entity: dict, country: str) -> tuple[str, str]:
    """Craft (topic, content) for one SDN entity.

    topic = the sanctions domain slug (carries both domain tokens).
    content = a >50-char sentence carrying 'United States'/'OFAC' (US cell) AND
    the entity's country (so tracked weak-jurisdiction cells rise). Designed so
    coverage_heatmap._matches_cell(content_text, sanctions tokens, <juris>) is True
    for US and for the entity's country when tracked.
    """
    name = entity.get("name") or "(unnamed entity)"
    sdn_type = (entity.get("sdn_type") or "entity").lower()
    program = entity.get("program") or "an OFAC sanctions program"
    title = entity.get("title")
    remarks = entity.get("remarks")
    parts = [
        f"{name} ({sdn_type}) is designated on the United States OFAC SDN "
        f"sanctions screening list under {program}."
    ]
    if country:
        parts.append(f"Associated country/jurisdiction: {country}.")
    if title:
        parts.append(f"Title/role: {title}.")
    if remarks:
        parts.append(f"OFAC remarks: {remarks[:280]}.")
    parts.append("Source: US Treasury OFAC Specially Designated Nationals (SDN) list.")
    # R-F1733: unique-per-entity topic (see _entity_topic) so each designation is a
    # distinct fact, not an update of a shared topic.
    return _entity_topic(entity.get("ent_num", ""), name), " ".join(parts)


async def ingest_rows(sdn_rows: list[dict], add_rows: list[dict],
                      *, max_rows: int = MAX_ROWS_DEFAULT) -> dict:
    """Join entities↔countries, craft a fact per entity, store via store_fact.

    Prioritises entities in TRACKED non-US jurisdictions (they lift the heatmap
    FLOOR) before filling the remainder up to max_rows. Idempotent: store_fact
    merges duplicates, so re-runs don't double-count.
    """
    from . import knowledge as _k
    try:
        from .coverage_heatmap import JURISDICTION_SYNONYMS
        tracked = {k for k in JURISDICTION_SYNONYMS if k not in ("US", "UN")}
        tracked_syn = {k: [s.lower() for s in v] for k, v in JURISDICTION_SYNONYMS.items()}
    except Exception:
        tracked, tracked_syn = set(), {}

    country_idx = _build_country_index(add_rows)

    def _is_weak_tracked(country: str) -> bool:
        cl = (country or "").lower()
        for juris in tracked:
            if any(syn in cl for syn in tracked_syn.get(juris, [])):
                return True
        return False

    # Order: weak-tracked-jurisdiction entities first, then the rest.
    weak, rest = [], []
    for e in sdn_rows:
        (weak if _is_weak_tracked(country_idx.get(e.get("ent_num", ""), "")) else rest).append(e)
    ordered = (weak + rest)[:max_rows]

    ingested = errors = skipped_short = 0
    by_country: dict[str, int] = {}
    for e in ordered:
        country = country_idx.get(e.get("ent_num", ""), "")
        topic, content = _entity_to_fact(e, country)
        if len(content) < 60:  # R-F1526 store_fact guard is >50
            skipped_short += 1
            continue
        try:
            # R-F1732: do NOT pass source_url+fact_type+entity_name together —
            # that triggers store_fact's per-fact verified_intel.averify_and_store
            # pipeline (knowledge.py:72), which made a 1500-row bulk ingest
            # pathologically slow. For authoritative open-data reference rows we
            # want FAST legacy writes; provenance lives in the content ("Source:
            # US Treasury OFAC SDN list"). Contradiction-detection is scoped to the
            # same topic only, so this stays fast. skip_semantic_index: the heatmap
            # reads facts, not the index.
            await _k.store_fact(
                topic, content, source="ofac_sdn", confidence="CONFIRMED",
                skip_semantic_index=True,
            )
            ingested += 1
            ck = country or "Unspecified"
            by_country[ck] = by_country.get(ck, 0) + 1
        except Exception as ex:
            errors += 1
            logger.debug("[ofac_sdn_ingest] store_fact failed for %s: %s", e.get("ent_num"), ex)

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
    # §21a brain-wiring — success AND failure reach the brain.
    try:
        from .engine_wiring import wire_success, wire_failure
        if ingested:
            wire_success(
                module="ofac_sdn_ingest",
                summary=f"Ingested {ingested} OFAC SDN designations as sanctions_screening facts "
                        f"({len(by_country)} countries; {len(weak)} weak-juris prioritised).",
                source_id="open_api_ingest:ofac_sdn",
            )
        else:
            wire_failure(
                module="ofac_sdn_ingest",
                detail=f"OFAC SDN ingest produced 0 facts (errors={errors}, short={skipped_short}).",
                gap_type="source_failure", source="open_api_ingest:ofac_sdn",
            )
    except Exception:
        pass
    logger.info("[ofac_sdn_ingest] %d ingested, %d errors, %d short", ingested, errors, skipped_short)
    return summary


async def ingest_csv_bytes(sdn_bytes: bytes, add_bytes: bytes = b"",
                           *, max_rows: int = MAX_ROWS_DEFAULT) -> dict:
    """Parse OFAC SDN.CSV (+ optional ADD.CSV for country) and ingest."""
    sdn_rows = _parse_fixed_csv(sdn_bytes, _SDN_COLS)
    add_rows = _parse_fixed_csv(add_bytes, _ADD_COLS)
    if not sdn_rows:
        return {"error": "empty or unparseable SDN.csv", "ingested": 0}
    return await ingest_rows(sdn_rows, add_rows, max_rows=max_rows)


async def fetch_and_ingest(*, max_rows: int = MAX_ROWS_DEFAULT) -> dict:
    """Fetch OFAC SDN.csv + ADD.csv (open data, no auth) and ingest as facts."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:  # no-breaker: OFAC SDN ingest is periodic batch; breaker would stall sanctions updates
            sdn = await client.get(SDN_URL)
            add = await client.get(ADD_URL)
        if sdn.status_code != 200 or not sdn.content:
            msg = f"OFAC SDN download failed: sdn HTTP {sdn.status_code}"
            logger.warning("[ofac_sdn_ingest] %s", msg)
            try:
                from .engine_wiring import wire_failure
                wire_failure(module="ofac_sdn_ingest", detail=msg,
                             gap_type="source_failure", source="open_api_ingest:ofac_sdn")
            except Exception:
                pass
            return {"error": msg, "ingested": 0}
        add_bytes = add.content if add.status_code == 200 else b""
        return await ingest_csv_bytes(sdn.content, add_bytes, max_rows=max_rows)
    except Exception as e:
        logger.warning("[ofac_sdn_ingest] fetch failed: %s", e)
        return {"error": f"fetch failed: {e!r}", "ingested": 0}


async def stats() -> dict:
    snap = await rs.get_json(STATS_KEY)
    if not snap:
        return {"ingested": 0, "note": "No OFAC SDN ingest has run yet."}
    return snap
