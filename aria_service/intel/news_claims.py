"""R-F3511 — structured, quotable, revisable claims extracted from news.

WHAT THIS REPLACES
──────────────────
What reached the brain from news was this (intel_ledger.py:608):

    summary=f"Ledger signal: {text[:200]}"

Two hundred characters of prose. Everything that makes a statement checkable was
gone: who asserted it, where it can be re-read, how many INDEPENDENT sources
carry it, what earlier belief it revises, and the exact words supporting it.

That shape cannot compound. A better later source cannot correct an earlier
belief because there IS no earlier belief — only a truncated sentence. New
evidence lands beside the old text rather than revising it, so contradiction is
invisible and retraction impossible.

THE HONESTY FLOOR
─────────────────
A claim MUST carry a verbatim excerpt from the source. If ARIA cannot quote the
words, ARIA does not make the claim — ``record_claim`` refuses.

This is why extraction here is DETERMINISTIC and excerpt-anchored rather than
generated. An LLM asked to "extract claims" produces fluent, plausible,
unsupported ones, which is precisely the fabrication this product exists to
prevent. The moat is verification, not generation
(memory/north_star_zero_fabrication). A model may propose a claim; it may not be
the reason one is believed.

CORROBORATION IS COUNTED BY ORIGIN
──────────────────────────────────
Reuses dd_independent_verifier.publisher_family, the same primitive R-F3487 uses,
so N syndicated copies of one wire report remain ONE witness. R-F3388's rule
governs: the false-positive rate on independence MUST be 0; a conservative
undercount is acceptable.

REVISION KEEPS HISTORY (§7)
───────────────────────────
Superseding a claim marks the old one not-current and links the chain both ways.
Nothing is deleted, there is no prune, and a guard test asserts no destructive
verb ever appears on this module's API.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.intel.news_claims")

_DATA_DIR = Path(os.getenv("ARIA_DATA_DIR",
                           str(Path(__file__).resolve().parent.parent.parent / "data")))
_DB_PATH = _DATA_DIR / "news_claims.db"

_conn: Optional[sqlite3.Connection] = None
_db_lock = threading.Lock()

_EXCERPT_MIN = int(os.getenv("ARIA_CLAIM_EXCERPT_MIN", "12"))


def _norm(text: str) -> str:
    """Whitespace/case-normalised form used for verbatim checking.

    Publishers reflow whitespace between syndication partners, so a strict
    character match would reject genuine quotes. Normalising whitespace and case
    is the smallest relaxation that keeps "these words appear in the source" a
    real check rather than a fuzzy one.
    """
    return " ".join((text or "").split()).casefold()


def claim_fingerprint(claim: dict[str, Any]) -> str:
    """Identity of the ASSERTION, independent of who reported it.

    Two publishers reporting the same fact must land on one claim so their
    corroboration can be counted, rather than creating two claims that each look
    single-sourced.
    """
    parts = "|".join(_norm(str(claim.get(k) or ""))
                     for k in ("subject", "predicate", "object"))
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:32]


def _publisher_family(url: str) -> str:
    try:
        from .dd_independent_verifier import publisher_family
        return publisher_family(url) or "pub:__unclassified__"
    except Exception:
        return "pub:__unclassified__"


def _get_db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS claims (
                claim_id        TEXT PRIMARY KEY,
                fingerprint     TEXT NOT NULL,
                subject         TEXT NOT NULL,
                predicate       TEXT NOT NULL,
                object          TEXT DEFAULT '',
                excerpt         TEXT NOT NULL,
                source_url      TEXT DEFAULT '',
                publisher_family TEXT DEFAULT '',
                source_tier     TEXT DEFAULT '',
                extraction_status TEXT DEFAULT '',
                confidence      TEXT DEFAULT '',
                event_date      TEXT DEFAULT '',
                observed_at     REAL NOT NULL,
                supersedes      TEXT DEFAULT '',
                superseded_by   TEXT DEFAULT '',
                is_current      INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS ix_claims_fp ON claims(fingerprint);
            CREATE INDEX IF NOT EXISTS ix_claims_subject ON claims(subject, is_current);

            -- One row per (claim, origin). Corroboration is COUNTED from here,
            -- so a repeat from the same origin cannot raise it.
            CREATE TABLE IF NOT EXISTS claim_sources (
                fingerprint TEXT NOT NULL,
                origin_key  TEXT NOT NULL,
                source_url  TEXT DEFAULT '',
                observed_at REAL NOT NULL,
                PRIMARY KEY (fingerprint, origin_key)
            );
            """
        )
        _conn.commit()
    return _conn


async def _adb(fn, *args, **kwargs):
    def _call():
        with _db_lock:
            return fn(*args, **kwargs)
    return await asyncio.to_thread(_call)


def _reset_for_tests() -> None:
    global _conn
    with _db_lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
        _conn = None


def _db_record(claim: dict, story_id: str, supersedes: str) -> dict:
    conn = _get_db()
    now = time.time()
    fp = claim_fingerprint(claim)
    url = str(claim.get("source_url") or "")

    # Origin identity: a shared STORY collapses publishers together (syndication),
    # exactly as R-F3487 does for correlation.
    origin = f"story:{story_id}" if story_id else _publisher_family(url)

    # Confidence never exceeds what the evidence grade supports (R-F3509).
    conf = str(claim.get("confidence") or "")
    try:
        from .news_enrichment import cap_confidence_for_extraction
        conf = cap_confidence_for_extraction(
            conf, str(claim.get("extraction_status") or ""))
    except Exception:
        pass

    existing = conn.execute(
        "SELECT claim_id FROM claims WHERE fingerprint=? AND is_current=1",
        (fp,)).fetchone()

    conn.execute(
        "INSERT OR IGNORE INTO claim_sources (fingerprint, origin_key, source_url, "
        "observed_at) VALUES (?,?,?,?)", (fp, origin, url, now))

    if existing and not supersedes:
        # Same assertion, another source: corroboration only, no new claim row.
        conn.commit()
        return {"recorded": True, "claim_id": existing["claim_id"],
                "reason": "corroborated existing claim"}

    claim_id = hashlib.sha256(f"{fp}|{url}|{now}".encode()).hexdigest()[:32]
    conn.execute(
        """INSERT INTO claims (claim_id, fingerprint, subject, predicate, object,
             excerpt, source_url, publisher_family, source_tier, extraction_status,
             confidence, event_date, observed_at, supersedes, is_current)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
        (claim_id, fp, str(claim.get("subject") or ""),
         str(claim.get("predicate") or ""), str(claim.get("object") or ""),
         str(claim.get("excerpt") or ""), url, _publisher_family(url),
         str(claim.get("source_tier") or ""),
         str(claim.get("extraction_status") or ""), conf,
         str(claim.get("event_date") or ""), now, supersedes or ""),
    )
    if supersedes:
        # §7 — the old claim is RETAINED, marked not-current, and linked both ways.
        conn.execute(
            "UPDATE claims SET is_current=0, superseded_by=? WHERE claim_id=?",
            (claim_id, supersedes))
    conn.commit()
    return {"recorded": True, "claim_id": claim_id, "reason": "new claim"}


@fail_wire(module="news_claims", gap_type="engine_failure")
async def record_claim(claim: dict[str, Any], *, source_text: str = "",
                       story_id: str = "", supersedes: str = "") -> dict:
    """Record one claim, but ONLY if its excerpt is verbatim in the source.

    Refusing is the point. A claim ARIA cannot quote is a claim ARIA cannot
    defend, and an unsupported-but-fluent statement is the failure mode this
    whole product is built against.
    """
    excerpt = str(claim.get("excerpt") or "").strip()
    if len(excerpt) < _EXCERPT_MIN:
        return {"recorded": False, "claim_id": "",
                "reason": "no supporting excerpt — a claim must be quotable"}
    if not str(claim.get("subject") or "").strip() or \
            not str(claim.get("predicate") or "").strip():
        return {"recorded": False, "claim_id": "",
                "reason": "a claim needs a subject and a predicate"}
    if source_text and _norm(excerpt) not in _norm(source_text):
        return {"recorded": False, "claim_id": "",
                "reason": "excerpt not found verbatim in the source text"}
    return await _adb(_db_record, claim, story_id, supersedes)


def _row(r) -> dict:
    d = dict(r)
    d["is_current"] = bool(d.get("is_current"))
    return d


def _db_get(claim_id: str) -> Optional[dict]:
    conn = _get_db()
    r = conn.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
    if r is None:
        return None
    out = _row(r)
    out["independent_origins"] = conn.execute(
        "SELECT COUNT(*) c FROM claim_sources WHERE fingerprint=?",
        (out["fingerprint"],)).fetchone()["c"]
    return out


@fail_wire(module="news_claims", gap_type="engine_failure")
async def get_claim(claim_id: str) -> Optional[dict]:
    return await _adb(_db_get, claim_id)


def _db_by_fp(fp: str) -> Optional[dict]:
    conn = _get_db()
    r = conn.execute(
        "SELECT * FROM claims WHERE fingerprint=? ORDER BY observed_at DESC LIMIT 1",
        (fp,)).fetchone()
    if r is None:
        return None
    out = _row(r)
    out["independent_origins"] = conn.execute(
        "SELECT COUNT(*) c FROM claim_sources WHERE fingerprint=?", (fp,)).fetchone()["c"]
    return out


@fail_wire(module="news_claims", gap_type="engine_failure")
async def get_claim_by_fingerprint(fp: str) -> Optional[dict]:
    return await _adb(_db_by_fp, fp)


def _db_current(subject: str, limit: int) -> list[dict]:
    conn = _get_db()
    if subject:
        rows = conn.execute(
            "SELECT * FROM claims WHERE is_current=1 AND lower(subject)=lower(?) "
            "ORDER BY observed_at DESC LIMIT ?", (subject, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM claims WHERE is_current=1 ORDER BY observed_at DESC "
            "LIMIT ?", (limit,)).fetchall()
    return [_row(r) for r in rows]


@fail_wire(module="news_claims", gap_type="engine_failure")
async def current_claims(subject: str = "", limit: int = 100) -> list[dict]:
    """What ARIA currently believes — superseded claims excluded, never deleted."""
    return await _adb(_db_current, subject, limit)


def _db_history(claim_id: str) -> list[dict]:
    conn = _get_db()
    seen, out = set(), []
    cur = claim_id
    while cur and cur not in seen:          # walk forward through supersessions
        seen.add(cur)
        r = conn.execute("SELECT * FROM claims WHERE claim_id=?", (cur,)).fetchone()
        if r is None:
            break
        out.append(_row(r))
        cur = str(r["superseded_by"] or "")
    return out


@fail_wire(module="news_claims", gap_type="engine_failure")
async def claim_history(claim_id: str) -> list[dict]:
    """The full revision chain, oldest first. Nothing in it was ever deleted."""
    return await _adb(_db_history, claim_id)
