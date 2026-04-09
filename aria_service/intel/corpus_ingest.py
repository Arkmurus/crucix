"""ARIA tiered corpus ingest.

Provides a clean interface for pushing proprietary or curated documents
into the RAG store with explicit provenance metadata (tier, source class,
region, etc). Used by the corpus-ingest CLI and the /api/aria/corpus/ingest
endpoint.

Why this exists separately from rag_store.ingest_document
══════════════════════════════════════════════════════════
The existing `read_document` flow ingests opportunistic documents (whatever
gets shared in WhatsApp / email) with minimal metadata. We need a parallel
path for *curated* corpus documents where the human ingesting them knows:

  - which tier the source belongs to (A primary / B secondary / C live / D proprietary)
  - what the source class is (SIPRI / RAND / DD report / template / etc)
  - what region or domain the document covers
  - whether it's CPLP-relevant

Storing tier metadata on every chunk is what lets retrieval prefer Tier A/D
sources over Tier B/C, and what lets the confidence footer cite the tier mix
of the supporting passages.

This module is INDEPENDENT of any deployed chat path. Adding new ingest
sources here cannot affect existing chat replies until the retrieval-side
tier-aware ranking is wired in (separate work).

Public API
══════════
    extract_text_from_bytes(raw_bytes, filename, mimetype) -> str
        Pure extraction. Handles PDF (PyMuPDF), DOCX (zipfile/xml fallback),
        XLSX (openpyxl), TXT/MD (decode). Raises ValueError if no extractor
        produces usable text.

    ingest_corpus_document(text, filename, tier, source_class, ...) -> dict
        Async wrapper around rag_store.ingest_document that injects the
        tier/provenance metadata into extra_metadata. Returns the same
        dict shape as ingest_document.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from typing import Any

logger = logging.getLogger("aria.corpus_ingest")

# Tier vocabulary — MUST stay in sync with corpus_registry.py:VALID_TIERS.
# Past incident 2026-04-09: this set was missing B+, C+, and E even though
# corpus_registry.py defines all 7 tiers and the bulk-ingest CLIs accept
# them via --tier. The result was that Tier C+ ingest (the Lusophone moat,
# 19 sources) failed wholesale on the server side with HTTP 400 even
# though the registry and CLI both supported the tier name. The Tier B+
# (64 sources) and Tier E (15 sources) ingests would have failed the
# same way. Always update both sets together when adding tiers.
#
# 2026-04-09 corpus expansion v3: added A+ (real-time physical world
# tracking — most are deferred to Phase 4 because they need API keys
# or signal-stream infrastructure, but a few static sources go here)
# and F (private military companies / non-state armed actors).
VALID_TIERS = {"A", "A+", "B", "B+", "C", "C+", "D", "E", "F", "unknown"}

# Sentinel for "we tried every extractor and got nothing useful"
class ExtractError(ValueError):
    """Raised when no extractor produces usable text from the input bytes."""


def extract_text_from_bytes(
    raw_bytes: bytes,
    filename: str,
    mimetype: str = "",
    *,
    max_chars: int = 200_000,
) -> str:
    """Extract plain text from a binary document.

    Pure function — no I/O beyond decoding the input bytes. Caller is
    responsible for reading the file and passing in the bytes.

    Falls through extractors in order: PDF → DOCX → XLSX → plain text decode.
    Raises ExtractError if none produce at least 30 chars.

    `max_chars` caps the output so a 500-page PDF doesn't blow up downstream
    chunking. The corpus ingest path uses 200k by default — much larger than
    the chat-path /api/aria/read-document cap of 15k, because corpus docs
    are *meant* to be long.
    """
    if not raw_bytes:
        raise ExtractError("empty input")
    fname = (filename or "").lower()
    mime = (mimetype or "").lower()

    extracted = ""

    # ── PDF ────────────────────────────────────────────────────────────
    if "pdf" in mime or fname.endswith(".pdf"):
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=raw_bytes, filetype="pdf")
            extracted = "\n".join(page.get_text() for page in doc)
            doc.close()
        except ImportError:
            logger.warning("PyMuPDF not available — cannot extract PDF %s", filename)
        except Exception as e:
            logger.warning("PDF extraction failed for %s: %s", filename, e)

    # ── DOCX ───────────────────────────────────────────────────────────
    elif (
        "word" in mime
        or "officedocument.wordprocessingml" in mime
        or fname.endswith(".docx")
    ):
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
            if "word/document.xml" in zf.namelist():
                xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
                extracted = re.sub(r"<[^>]+>", " ", xml)
                extracted = " ".join(extracted.split())
            zf.close()
        except Exception as e:
            logger.warning("DOCX extraction failed for %s: %s", filename, e)

    # ── XLSX ───────────────────────────────────────────────────────────
    elif (
        "spreadsheet" in mime
        or "officedocument.spreadsheetml" in mime
        or fname.endswith((".xlsx", ".xlsm"))
    ):
        try:
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
            rows: list[str] = []
            for ws in wb.worksheets[:8]:  # cap at 8 sheets per workbook
                rows.append(f"--- Sheet: {ws.title} ---")
                for row in ws.iter_rows(max_row=2000, values_only=True):
                    rows.append(", ".join(str(c or "") for c in row))
            wb.close()
            extracted = "\n".join(rows)
        except Exception as e:
            logger.warning("XLSX extraction failed for %s: %s", filename, e)

    # ── Plain text / markdown ──────────────────────────────────────────
    elif fname.endswith((".txt", ".md", ".markdown", ".text")):
        try:
            extracted = raw_bytes.decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning("Plain text decode failed for %s: %s", filename, e)

    # ── Last-resort: try utf-8 decode in case the caller mislabelled it ─
    if not extracted or len(extracted) < 30:
        try:
            maybe = raw_bytes.decode("utf-8", errors="ignore")
            if len(maybe.strip()) >= 30:
                extracted = maybe
        except Exception:
            pass

    if not extracted or len(extracted.strip()) < 30:
        raise ExtractError(f"no usable text extracted from {filename!r}")

    if len(extracted) > max_chars:
        logger.info(
            "corpus_ingest: truncating %s from %d to %d chars",
            filename, len(extracted), max_chars,
        )
        extracted = extracted[:max_chars]

    return extracted


async def ingest_corpus_document(
    text: str,
    *,
    filename: str,
    tier: str,
    source_class: str,
    region: str = "",
    cplp_relevant: bool = False,
    confidence: str = "",
    publication_date: str = "",
    notes: str = "",
    extra_metadata: dict | None = None,
) -> dict:
    """Push a corpus document into the RAG store with full provenance metadata.

    Parameters
    ----------
    text:
        The extracted plain text. Use extract_text_from_bytes() first.
    filename:
        Original filename — used as the chunk title and as part of the source id.
    tier:
        One of A/B/C/D/unknown. See module docstring for tier definitions.
    source_class:
        Free-form label for the publisher / origin (e.g. "SIPRI",
        "Arkmurus DD report", "Wassenaar control list").
    region:
        Region tag for filtering at retrieval time (e.g. "Africa",
        "Lusophone", "MENA").
    cplp_relevant:
        True if the document touches CPLP markets specifically. Used by the
        retrieval boost.
    confidence:
        Optional default confidence to attach if the source class warrants
        it (e.g. Tier A primary docs default to "CONFIRMED").
    publication_date:
        ISO date string if known.
    notes:
        Free-form note from the human ingesting the document.
    extra_metadata:
        Additional key/values merged into the chunk metadata last.

    Returns the dict shape from rag_store.ingest_document, with `tier`
    and `source_class` echoed for the caller's convenience.
    """
    if tier not in VALID_TIERS:
        raise ValueError(f"invalid tier {tier!r} — must be one of {sorted(VALID_TIERS)}")

    from . import rag_store

    meta: dict[str, Any] = {
        "tier": tier,
        "source_class": source_class[:100],
        "region": region[:100],
        "cplp_relevant": bool(cplp_relevant),
    }
    if confidence:
        meta["confidence"] = confidence[:30]
    if publication_date:
        meta["publication_date"] = publication_date[:30]
    if notes:
        meta["notes"] = notes[:300]
    if extra_metadata:
        for k, v in extra_metadata.items():
            if isinstance(v, (str, int, float, bool)):
                meta[str(k)[:50]] = v if not isinstance(v, str) else v[:300]

    result = await rag_store.ingest_document(
        text=text,
        source=f"corpus:{tier}:{source_class}:{filename}"[:300],
        source_type="corpus",
        title=filename,
        market=region,
        extra_metadata=meta,
    )
    result["tier"] = tier
    result["source_class"] = source_class
    return result
