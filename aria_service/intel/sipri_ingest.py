"""ARIA SIPRI ingest — arms-transfers historical database into the RAG store.

Ingests the SIPRI Arms Transfers Database (public CSV export) as tier-A
market knowledge. Each transfer row becomes a natural-language sentence
that ARIA can retrieve for questions like:

  "What armoured vehicles has Nigeria bought in the last 5 years?"
  "Who are Angola's top three suppliers since 2018?"
  "Has Brazil imported Turkish UAVs?"

Why tier-A: SIPRI is the gold-standard primary source for international
arms transfers. Methodology is publicly documented, updates are annual,
and the data is used by every serious defence-research organisation
(CSIS, IISS, Jane's). ARIA treats it as primary evidence.

SIPRI distribution reality
──────────────────────────
SIPRI does not expose a stable programmatic download URL — the Trade
Registers interface requires a browser form + filters. Operational
pattern: the operator downloads the CSV once from
https://armstransfers.sipri.org/ArmsTransfer/TransferData and uploads
the bytes via `ingest_csv_bytes()`. A public-URL attempt is included as
a best-effort fallback for demo purposes but is expected to 404 on
real deployments.

Schema (columns we care about, SIPRI TIV CSV)
─────────────────────────────────────────────
  Year / Order year, Recipient, Supplier, Weapon designation,
  Weapon category, Number delivered, Number ordered, Status, Comments

Public API
──────────
  ingest_csv_bytes(raw: bytes, *, since_year=None, max_rows=5000) -> dict
  fetch_and_ingest_sipri() -> dict      # best-effort; degrades gracefully
  stats() -> dict                       # last ingest summary
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from . import redis_store as rs
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.sipri_ingest")

STATS_KEY = "crucix:aria:sipri_ingest:stats"
MAX_ROWS_DEFAULT = 5000

# Map SIPRI weapon categories to a shorter tag set for retrieval.
_CATEGORY_HINTS = {
    "aircraft": ["aircraft", "fighter", "trainer", "uav", "helicopter", "tanker"],
    "armoured": ["armoured", "tank", "afv", "ifv", "apc"],
    "artillery": ["artillery", "howitzer", "rocket launcher", "mortar"],
    "air_defence": ["air defence", "sam ", "surface-to-air", "radar"],
    "missiles": ["missile", "anti-tank", "anti-ship"],
    "naval": ["ship", "frigate", "corvette", "submarine", "patrol boat"],
    "small_arms": ["small arms", "rifle", "machine gun"],
    "sensors": ["sensor", "radar", "sonar", "eo/ir", "avionics"],
    "engines": ["engine", "turbine"],
}


def _classify_category(raw_category: str, designation: str) -> str:
    text = f"{raw_category} {designation}".lower()
    for tag, keywords in _CATEGORY_HINTS.items():
        if any(k in text for k in keywords):
            return tag
    return "other"


def _row_to_sentence(row: dict) -> str:
    """Turn a SIPRI row into a retrievable natural-language sentence."""
    year = (row.get("Year") or row.get("Order year") or row.get("Delivery year") or "").strip()
    recipient = (row.get("Recipient") or row.get("Buyer") or "").strip()
    supplier = (row.get("Supplier") or row.get("Seller") or "").strip()
    designation = (row.get("Weapon designation") or row.get("Designation") or "").strip()
    category = (row.get("Weapon category") or row.get("Category") or "").strip()
    n_delivered = (row.get("Number delivered") or row.get("Delivered") or "").strip()
    n_ordered = (row.get("Number ordered") or row.get("Ordered") or "").strip()
    status = (row.get("Status") or "").strip()
    comments = (row.get("Comments") or "").strip()
    parts = []
    if year and recipient and supplier:
        parts.append(f"In {year}, {recipient} sourced {designation or category or 'equipment'} from {supplier}.")
    if n_delivered:
        parts.append(f"Number delivered: {n_delivered}.")
    if n_ordered and n_ordered != n_delivered:
        parts.append(f"Number ordered: {n_ordered}.")
    if status:
        parts.append(f"Status: {status}.")
    if category:
        parts.append(f"Category: {category}.")
    if comments:
        parts.append(f"Notes: {comments[:300]}.")
    return " ".join(parts)


def _cplp_relevant(recipient: str) -> bool:
    rl = (recipient or "").lower()
    return any(c in rl for c in (
        "angola", "mozambique", "brazil", "portugal", "cabo verde", "cape verde",
        "são tomé", "sao tome", "guinea-bissau", "timor",
    ))


@fail_wire(module="sipri_ingest", gap_type="source_failure")
async def ingest_csv_bytes(raw: bytes, *, since_year: Optional[int] = None,
                           max_rows: int = MAX_ROWS_DEFAULT) -> dict:
    """Parse a SIPRI CSV and push each transfer as a RAG chunk.

    `since_year` filters rows before the given year — useful for an
    incremental refresh that only ingests the last 5 years.
    Returns a summary dict with per-category + per-recipient counts.
    """
    from . import corpus_ingest
    if not raw:
        return {"error": "empty CSV", "ingested": 0}
    text = raw.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    # SIPRI CSVs sometimes ship an initial header line with metadata —
    # if the first "row" has no Year field, skip it and re-fit the reader.
    rows = list(reader)
    if rows and not any(k for k in rows[0].keys() if "year" in (k or "").lower()):
        # Re-parse skipping a metadata preamble (first line)
        text2 = "\n".join(text.splitlines()[1:])
        reader = csv.DictReader(io.StringIO(text2))
        rows = list(reader)

    ingested = 0
    skipped_stale = 0
    skipped_empty = 0
    errors = 0
    by_category: dict[str, int] = {}
    by_recipient: dict[str, int] = {}

    for row in rows[:max_rows]:
        year_str = (row.get("Year") or row.get("Order year") or row.get("Delivery year") or "").strip()
        try:
            year_int = int(year_str) if year_str else None
        except ValueError:
            year_int = None
        if since_year and year_int and year_int < since_year:
            skipped_stale += 1
            continue
        sentence = _row_to_sentence(row)
        if len(sentence.strip()) < 30:
            skipped_empty += 1
            continue
        recipient = (row.get("Recipient") or row.get("Buyer") or "").strip()
        supplier = (row.get("Supplier") or row.get("Seller") or "").strip()
        designation = (row.get("Weapon designation") or row.get("Designation") or "").strip()
        category_tag = _classify_category(
            (row.get("Weapon category") or "").strip(),
            designation,
        )
        by_category[category_tag] = by_category.get(category_tag, 0) + 1
        if recipient:
            by_recipient[recipient] = by_recipient.get(recipient, 0) + 1
        filename = f"sipri_{year_int or 'unknown'}_{recipient or '?'}_{designation or '?'}"[:120]
        try:
            await corpus_ingest.ingest_corpus_document(
                text=sentence,
                filename=filename,
                tier="A",
                source_class="SIPRI Arms Transfers Database",
                region=recipient,
                cplp_relevant=_cplp_relevant(recipient),
                confidence="CONFIRMED",
                publication_date=str(year_int) if year_int else "",
                notes=f"supplier={supplier}; category={category_tag}",
                extra_metadata={
                    "sipri_year": year_int or 0,
                    "sipri_supplier": supplier[:80],
                    "sipri_recipient": recipient[:80],
                    "sipri_designation": designation[:120],
                    "sipri_category": category_tag,
                },
            )
            ingested += 1
        except Exception as e:
            errors += 1
            logger.debug("sipri ingest row failed: %s", e)

    summary = {
        "ingested": ingested,
        "skipped_stale": skipped_stale,
        "skipped_empty": skipped_empty,
        "errors": errors,
        "by_category": by_category,
        "by_recipient_top10": dict(sorted(by_recipient.items(), key=lambda kv: -kv[1])[:10]),
        "since_year": since_year,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await rs.set_json(STATS_KEY, summary)
    except Exception:
        pass
    try:
        from . import brain_hook
        await brain_hook.absorb_silent(
            module="sipri_ingest",
            summary=f"SIPRI ingest: {ingested} transfers, {len(by_recipient)} recipients, {len(by_category)} categories",
            success=True,
            extra_topics=["market_intel", "procurement"],
        )
    except Exception:
        pass
    logger.info(
        "[sipri_ingest] %d rows ingested, %d stale skipped, %d empty, %d errors",
        ingested, skipped_stale, skipped_empty, errors,
    )
    return summary


@fail_wire(module="sipri_ingest", gap_type="source_failure")
async def fetch_and_ingest_sipri() -> dict:
    """Best-effort fetch + ingest. Expected to 404 in most deployments —
    SIPRI requires a form download. Returns a stub result instructing the
    operator to upload the CSV via the /api/aria/corpus/ingest-sipri endpoint.
    """
    import httpx
    candidate_urls = [
        "https://armstransfers.sipri.org/ArmsTransfer/TransferData?raw=1",
    ]
    for url in candidate_urls:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and resp.content and len(resp.content) > 500:
                    return await ingest_csv_bytes(resp.content, since_year=datetime.now().year - 10)
        except Exception as e:
            logger.debug("SIPRI direct fetch failed (%s): %s", url, e)
    return {
        "error": "no_direct_download",
        "message": (
            "SIPRI does not expose a stable public CSV URL. Operator must "
            "download from https://armstransfers.sipri.org/ArmsTransfer/TransferData "
            "and upload via `ingest_csv_bytes()` or POST to "
            "/api/aria/corpus/ingest-sipri."
        ),
        "ingested": 0,
    }


@fail_wire(module="sipri_ingest", gap_type="source_failure")
async def stats() -> dict:
    snap = await rs.get_json(STATS_KEY)
    if not snap:
        return {"ingested": 0, "note": "No SIPRI ingest has run yet."}
    return snap
