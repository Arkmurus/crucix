"""R-F3485 — permanent, append-only archive of news source observations.

WHY THIS EXISTS
───────────────
The hot article store is a 1,000-item rolling list (news_monitor.py:67) written
with ``lpush`` + ``ltrim`` (:673-674). Article 1,001 physically DELETES the
oldest raw record. Downstream the intel ledger keeps 500 chars and the brain
absorbs 200 (intel_ledger.py:608), so once a raw record is trimmed ARIA retains a
durable statement that something happened, but not the source material needed to
re-extract claims under a better classifier, detect a correction or retraction,
or audit how a conclusion was reached.

That single constant was doing two unrelated jobs:

  * bounding a HOT OPERATIONAL LIST so the UI and stats stay fast — correct, kept
  * enforcing RETENTION by destroying evidence — wrong, removed here

This module is the retention half. The hot list stays exactly as it is.

CLAUDE.md §7 (infinite memory) applies in full: no TTL, no prune, no eviction, no
oldest-first drop. There is deliberately no delete/purge function on this module,
and a guard test asserts the API surface stays that way.

DESIGN DECISIONS
────────────────
DEDICATED SQLITE FILE (R-F1446 pattern, as agent_registry / dd_vault /
dd_evidence_store). A separate file is a separate write lock, so the archive
cannot contend with the shared state_store — the 3.5h outage in
memory/incident_state_store_wedge_2026_07_02.md was one aiosqlite writer wedging.

OFF THE EVENT LOOP, ALWAYS (R-F3468, fixed today). Every DB call goes through
``_adb`` → ``asyncio.to_thread`` under a lock. The connection is opened
``check_same_thread=False``, which PERMITS off-thread use but does NOT make a
sqlite3 connection safe for CONCURRENT use — the lock is not optional.

AGGREGATE STATISTICS ONLY. news_monitor.get_stats() reads and JSON-decodes all
1,000 hot records every 30 seconds (:2446). Repeating that shape against a
growing archive is how an archive becomes an outage, so stats here are COUNT/MIN/
MAX queries against indexed columns.

THREE-PART IDENTITY. URL-only dedup — ``sha256(url)[:16]`` at :642 — cannot tell
a syndicated copy from independent corroboration, and never notices a correction
published at the same URL. So:

  canonical_url_hash   same story, ignoring tracking params and fragments
  content_hash         same TEXT — the syndication signal, and the revision trigger
  (event fingerprint)  left to the correlation layer, which owns claim identity

Same URL + same content  → duplicate, nothing written
Same URL + new content   → REVISION, prior wording preserved in a version row
New URL  + same content  → distinct article, linkable as syndication

That last case is the one that matters for the USP: two rows with one
content_hash are one story told twice, not two independent confirmations.

COPYRIGHT / DATA GOVERNANCE. Full article bodies are NOT stored by default.
Permanent metadata, hashes, provenance, the publisher's own feed summary and a
bounded excerpt carry the analytical value at far lower copyright and
personal-data exposure. Body retention is opt-in per source via ``body_ref``,
which points at storage governed by that source's licence rather than inlining
text here.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.intel.news_archive")

_DATA_DIR = Path(os.getenv("ARIA_DATA_DIR",
                           str(Path(__file__).resolve().parent.parent.parent / "data")))
_DB_PATH = _DATA_DIR / "news_archive.db"

_conn: Optional[sqlite3.Connection] = None
_db_lock = threading.Lock()

# Query/tracking parameters that never change which article you are reading.
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "fbclid", "gclid", "mc_cid", "mc_eid",
    "ref", "ref_src", "source", "cmpid", "CMP", "at_medium", "at_campaign",
    "spm", "xtor", "__twitter_impression", "guccounter", "amp",
})

_EXCERPT_MAX = int(os.getenv("ARIA_NEWS_ARCHIVE_EXCERPT_MAX", "4000"))


# ── identity ────────────────────────────────────────────────────────────────

def canonicalise_url(url: str) -> str:
    """Strip tracking noise so one article is one identity.

    Without this, ``?utm_source=rss`` makes the same story look like several
    distinct articles — which inflates volume and, downstream, can look like
    corroboration.
    """
    try:
        parts = urlsplit((url or "").strip())
        if not parts.scheme and not parts.netloc:
            return (url or "").strip()
        host = (parts.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k.lower() not in {p.lower() for p in _TRACKING_PARAMS}]
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), host, path, urlencode(kept), ""))
    except Exception:
        return (url or "").strip()


def url_hash(url: str) -> str:
    return hashlib.sha256(canonicalise_url(url).encode("utf-8")).hexdigest()


def content_hash(title: str, summary: str) -> str:
    """Hash of the normalised TEXT — the syndication + revision signal.

    Whitespace-normalised and case-folded so trivial reformatting between
    syndication partners does not read as a different story.
    """
    norm = " ".join(f"{title or ''} {summary or ''}".split()).casefold()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _publisher_family(url: str) -> str:
    """Reuse the hardened DD primitive (dd_independent_verifier:73).

    R-F3388 established its contract: the false-positive rate on independence
    MUST be 0, a conservative undercount is acceptable. Do not reimplement it.
    """
    try:
        from .dd_independent_verifier import publisher_family
        return publisher_family(url) or "pub:__unclassified__"
    except Exception:
        return "pub:__unclassified__"


# ── connection (R-F1446 dedicated file, R-F3468 off-loop + locked) ──────────

def _get_db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS news_articles (
            article_id         TEXT PRIMARY KEY,
            canonical_url      TEXT NOT NULL,
            canonical_url_hash TEXT NOT NULL,
            content_hash       TEXT NOT NULL,
            publisher          TEXT DEFAULT '',
            publisher_family   TEXT DEFAULT '',
            source_tier        TEXT DEFAULT '',
            title              TEXT DEFAULT '',
            feed_summary       TEXT DEFAULT '',
            body_ref           TEXT DEFAULT '',
            extraction_status  TEXT DEFAULT 'feed_only',
            language           TEXT DEFAULT '',
            category           TEXT DEFAULT '',
            published_at       TEXT DEFAULT '',
            first_seen_at      REAL NOT NULL,
            last_seen_at       REAL NOT NULL,
            relevance_score    REAL,
            off_topic          INTEGER,
            relevance_terms    TEXT DEFAULT '',
            relevance_reason   TEXT DEFAULT '',
            classifier_version TEXT DEFAULT '',
            provenance         TEXT DEFAULT '{}'
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ix_articles_url
            ON news_articles(canonical_url_hash);
        CREATE INDEX IF NOT EXISTS ix_articles_content
            ON news_articles(content_hash);
        CREATE INDEX IF NOT EXISTS ix_articles_family
            ON news_articles(publisher_family);
        CREATE INDEX IF NOT EXISTS ix_articles_seen
            ON news_articles(first_seen_at);

        -- Revisions: a correction or retraction at the SAME url must not
        -- overwrite what the publisher originally said.
        CREATE TABLE IF NOT EXISTS news_article_versions (
            version_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id   TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            captured_at  REAL NOT NULL,
            title        TEXT DEFAULT '',
            summary      TEXT DEFAULT '',
            change_type  TEXT DEFAULT 'initial'
        );
        CREATE INDEX IF NOT EXISTS ix_versions_article
            ON news_article_versions(article_id, captured_at);

        -- Per-stage processing outcome, so ARIA can answer "did this article
        -- become usable knowledge?" rather than "did I see a new URL?" (§25).
        CREATE TABLE IF NOT EXISTS news_article_stages (
            article_id TEXT NOT NULL,
            stage      TEXT NOT NULL,
            ok         INTEGER NOT NULL,
            detail     TEXT DEFAULT '',
            updated_at REAL NOT NULL,
            PRIMARY KEY (article_id, stage)
        );
        CREATE INDEX IF NOT EXISTS ix_stages_pending
            ON news_article_stages(stage, ok);
        """
    )
    conn.commit()


async def _adb(fn, *args, **kwargs):
    """Run a blocking DB call off the event loop, serialised (R-F3468)."""
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


# ── write path ──────────────────────────────────────────────────────────────

def _db_archive(article: dict) -> dict:
    conn = _get_db()
    now = time.time()
    url = article.get("url") or ""
    canon = canonicalise_url(url)
    uhash = url_hash(url)
    chash = content_hash(article.get("title", ""), article.get("summary", ""))
    aid = uhash[:32]

    row = conn.execute(
        "SELECT article_id, content_hash FROM news_articles WHERE canonical_url_hash=?",
        (uhash,),
    ).fetchone()

    if row is None:
        conn.execute(
            """INSERT INTO news_articles
               (article_id, canonical_url, canonical_url_hash, content_hash,
                publisher, publisher_family, source_tier, title, feed_summary,
                extraction_status, category, published_at, first_seen_at,
                last_seen_at, provenance)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (aid, canon, uhash, chash,
             str(article.get("source") or ""), _publisher_family(url),
             str(article.get("tier") or ""), str(article.get("title") or ""),
             str(article.get("summary") or "")[:_EXCERPT_MAX],
             "feed_only", str(article.get("category") or ""),
             str(article.get("published_at") or ""), now, now,
             json.dumps({"feed": article.get("feed_url") or "",
                         "ingested_at": datetime.now(timezone.utc).isoformat()})),
        )
        conn.execute(
            """INSERT INTO news_article_versions
               (article_id, content_hash, captured_at, title, summary, change_type)
               VALUES (?,?,?,?,?,?)""",
            (aid, chash, now, str(article.get("title") or ""),
             str(article.get("summary") or "")[:_EXCERPT_MAX], "initial"),
        )
        conn.commit()
        return {"article_id": aid, "status": "new", "content_hash": chash}

    if row["content_hash"] == chash:
        conn.execute("UPDATE news_articles SET last_seen_at=? WHERE article_id=?",
                     (now, row["article_id"]))
        conn.commit()
        return {"article_id": row["article_id"], "status": "duplicate",
                "content_hash": chash}

    # Same URL, different text — a correction, retraction or live-page update.
    # The prior wording is KEPT; only the head record advances.
    conn.execute(
        "UPDATE news_articles SET content_hash=?, title=?, feed_summary=?, "
        "last_seen_at=? WHERE article_id=?",
        (chash, str(article.get("title") or ""),
         str(article.get("summary") or "")[:_EXCERPT_MAX], now, row["article_id"]),
    )
    conn.execute(
        """INSERT INTO news_article_versions
           (article_id, content_hash, captured_at, title, summary, change_type)
           VALUES (?,?,?,?,?,?)""",
        (row["article_id"], chash, now, str(article.get("title") or ""),
         str(article.get("summary") or "")[:_EXCERPT_MAX], "revision"),
    )
    conn.commit()
    return {"article_id": row["article_id"], "status": "revision",
            "content_hash": chash}


@fail_wire(module="news_archive", gap_type="engine_failure")
async def archive_article(article: dict) -> dict:
    """Archive one source observation. Returns {article_id, status, content_hash}.

    status is ``new`` | ``revision`` | ``duplicate``. This is the FIRST durable
    write in the ingest chain — nothing may be marked seen before it returns
    (R-F3486).
    """
    return await _adb(_db_archive, article)


def _db_record_relevance(article_id, score, on_topic, terms, classifier_version,
                         reason) -> None:
    conn = _get_db()
    conn.execute(
        "UPDATE news_articles SET relevance_score=?, off_topic=?, "
        "relevance_terms=?, relevance_reason=?, classifier_version=? "
        "WHERE article_id=?",
        (float(score) if score is not None else None,
         0 if on_topic else 1, ",".join(terms or [])[:500], (reason or "")[:500],
         classifier_version or "", article_id),
    )
    conn.commit()


@fail_wire(module="news_archive", gap_type="engine_failure")
async def record_relevance(article_id: str, *, score: float, on_topic: bool,
                           terms: Optional[list[str]] = None,
                           classifier_version: str = "",
                           reason: str = "") -> None:
    """Persist the promotion verdict ONTO the archived record.

    news_monitor mutates the in-memory article with relevance_score/off_topic/
    relevance_terms (:1476-1478) AFTER _store_article has already written it
    (:1959/:2111), so the stored copy never receives them — the code comment
    claims the decision is "auditable" while the record cannot reproduce it.
    """
    await _adb(_db_record_relevance, article_id, score, on_topic, terms,
               classifier_version, reason)


def _db_set_extraction(article_id, status, excerpt, detail) -> None:
    conn = _get_db()
    if excerpt:
        conn.execute(
            "UPDATE news_articles SET extraction_status=?, feed_summary=?, "
            "body_ref=? WHERE article_id=?",
            (status, excerpt[:_EXCERPT_MAX], f"inline:{len(excerpt)}", article_id),
        )
    else:
        conn.execute(
            "UPDATE news_articles SET extraction_status=? WHERE article_id=?",
            (status, article_id),
        )
    conn.commit()


@fail_wire(module="news_archive", gap_type="engine_failure")
async def set_extraction_status(article_id: str, status: str, *,
                                excerpt: str = "", detail: str = "") -> None:
    """R-F3499 — record HOW MUCH of this article was actually read.

    A 500-char feed description and a fully read article are different grades of
    evidence, so the distinction has to survive on the record. A failed deep
    fetch is written as its own status rather than left looking un-attempted:
    "not tried", "tried and could not read it" and "read" must stay
    distinguishable, and none of the first two may be mistaken for the third.
    """
    await _adb(_db_set_extraction, article_id, status, excerpt, detail)
    if detail:
        await _adb(_db_mark_stage, article_id, f"extraction:{status}", False, detail)


def _db_mark_stage(article_id, stage, ok, detail) -> None:
    conn = _get_db()
    conn.execute(
        """INSERT INTO news_article_stages (article_id, stage, ok, detail, updated_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(article_id, stage) DO UPDATE SET
             ok=excluded.ok, detail=excluded.detail, updated_at=excluded.updated_at""",
        (article_id, stage, 1 if ok else 0, (detail or "")[:500], time.time()),
    )
    conn.commit()


@fail_wire(module="news_archive", gap_type="engine_failure")
async def mark_stage(article_id: str, stage: str, *, ok: bool,
                     detail: str = "") -> None:
    """Record the outcome of one processing stage (§25 proprioception)."""
    await _adb(_db_mark_stage, article_id, stage, ok, detail)


# ── read path ───────────────────────────────────────────────────────────────

def _db_get(article_id: str) -> Optional[dict]:
    conn = _get_db()
    row = conn.execute("SELECT * FROM news_articles WHERE article_id=?",
                       (article_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["off_topic"] = None if out["off_topic"] is None else bool(out["off_topic"])
    out["stages"] = {
        r["stage"]: {"ok": bool(r["ok"]), "detail": r["detail"],
                     "updated_at": r["updated_at"]}
        for r in conn.execute(
            "SELECT stage, ok, detail, updated_at FROM news_article_stages "
            "WHERE article_id=?", (article_id,))
    }
    return out


@fail_wire(module="news_archive", gap_type="engine_failure")
async def get_article(article_id: str) -> Optional[dict]:
    return await _adb(_db_get, article_id)


def _db_versions(article_id: str) -> list[dict]:
    conn = _get_db()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM news_article_versions WHERE article_id=? ORDER BY captured_at",
        (article_id,))]


@fail_wire(module="news_archive", gap_type="engine_failure")
async def get_versions(article_id: str) -> list[dict]:
    """Every captured wording of this article, oldest first."""
    return await _adb(_db_versions, article_id)


def _db_by_content(chash: str) -> list[dict]:
    conn = _get_db()
    return [dict(r) for r in conn.execute(
        "SELECT article_id, canonical_url, publisher, publisher_family "
        "FROM news_articles WHERE content_hash=?", (chash,))]


@fail_wire(module="news_archive", gap_type="engine_failure")
async def find_by_content_hash(chash: str) -> list[dict]:
    """Articles sharing identical text — i.e. syndicated copies of one story.

    Load-bearing for the USP: N rows here are ONE story told N times, not N
    independent confirmations.
    """
    return await _adb(_db_by_content, chash)


def _db_archived_subset(hashes: list[str]) -> set[str]:
    conn = _get_db()
    found: set[str] = set()
    # Chunked to stay well inside SQLite's variable limit (999 by default) no
    # matter how many entries a feed returns.
    for i in range(0, len(hashes), 400):
        chunk = hashes[i:i + 400]
        marks = ",".join("?" * len(chunk))
        found.update(r[0] for r in conn.execute(
            f"SELECT canonical_url_hash FROM news_articles "
            f"WHERE canonical_url_hash IN ({marks})", chunk))
    return found


@fail_wire(module="news_archive", gap_type="engine_failure")
async def archived_subset(hashes: list[str]) -> set[str]:
    """R-F3673 — which of these ``url_hash`` values are ALREADY ARCHIVED.

    The poll needs to answer "have I durably kept this article?", and the seen
    map cannot answer it: ``_mark_seen`` records only ``hash -> timestamp``, so a
    URL marked seen by a path that never archived it (anything ingested before
    the archive existed, or any pre-R-F3486 write that marked seen first) is
    indistinguishable from one properly stored. Measured live 2026-08-04: 5,777
    seen URLs against 1,623 archived rows, with 383 of the difference still
    present in the live feeds and therefore recoverable.

    One indexed query per feed against the UNIQUE index on canonical_url_hash —
    not a per-article round trip, and not a full table load that would grow
    without bound.

    Raises rather than returning a partial set: the caller MUST be able to tell
    "not archived" from "could not check", because treating an unreadable
    archive as "nothing is archived" would re-ingest every article in every feed.
    """
    if not hashes:
        return set()
    return await _adb(_db_archived_subset, list(hashes))


def _db_pending(stage: str, limit: int) -> list[dict]:
    conn = _get_db()
    return [dict(r) for r in conn.execute(
        "SELECT article_id, detail, updated_at FROM news_article_stages "
        "WHERE stage=? AND ok=0 ORDER BY updated_at LIMIT ?", (stage, limit))]


@fail_wire(module="news_archive", gap_type="engine_failure")
async def pending_stage(stage: str, limit: int = 100) -> list[dict]:
    """Articles whose named stage failed — the retry queue."""
    return await _adb(_db_pending, stage, limit)


def _db_replay(cursor: float, limit: int) -> dict:
    conn = _get_db()
    rows = [dict(r) for r in conn.execute(
        # R-F3494 — the projection must carry everything the CLASSIFIER reads.
        # It first returned url/title/summary only, and replaying an impoverished
        # record changed the verdict: an article the live grader rejects was
        # promoted on replay because publisher/tier/category were missing. A
        # replay that cannot reproduce the original inputs is not a replay.
        "SELECT article_id, canonical_url, title, feed_summary, first_seen_at, "
        "relevance_score, off_topic, classifier_version, publisher, "
        "publisher_family, source_tier, category, published_at "
        "FROM news_articles "
        "WHERE first_seen_at > ? ORDER BY first_seen_at LIMIT ?", (cursor, limit))]
    nxt = rows[-1]["first_seen_at"] if rows else cursor
    return {"rows": rows, "next_cursor": nxt, "done": len(rows) < limit}


@fail_wire(module="news_archive", gap_type="engine_failure")
async def iter_for_replay(cursor: float = 0.0, limit: int = 500) -> dict:
    """Resumable, archive-WIDE walk for classifier replay.

    news_monitor._replay_recent_articles_for_classifier caps at 200 (:1424) over
    a 1,000-item store, so a better classifier could only ever repair a narrow
    recent window. Compounding means better reasoning applied to ALL retained
    evidence, so replay must page the whole archive and be resumable.
    """
    return await _adb(_db_replay, float(cursor or 0.0), int(limit))


def _db_stats() -> dict:
    conn = _get_db()
    r = conn.execute(
        "SELECT COUNT(*) c, MIN(first_seen_at) lo, MAX(first_seen_at) hi "
        "FROM news_articles").fetchone()
    dupes = conn.execute(
        "SELECT COUNT(*) c FROM (SELECT content_hash FROM news_articles "
        "GROUP BY content_hash HAVING COUNT(*) > 1)").fetchone()
    revs = conn.execute(
        "SELECT COUNT(*) c FROM news_article_versions WHERE change_type='revision'"
    ).fetchone()
    return {
        "total_articles": int(r["c"] or 0),
        "oldest_at": r["lo"],
        "newest_at": r["hi"],
        "syndicated_clusters": int(dupes["c"] or 0),
        "revisions_captured": int(revs["c"] or 0),
        "db_path": str(_DB_PATH),
    }


@fail_wire(module="news_archive", gap_type="engine_failure")
async def archive_stats() -> dict:
    """Aggregate counts only — never "read every row and decode"."""
    return await _adb(_db_stats)
