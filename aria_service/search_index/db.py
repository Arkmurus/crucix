"""search_index.db — SQLite + FTS5 schema for ARIA's own search engine.

Why this module exists (R-F500, 2026-05-14)
───────────────────────────────────────────
Operator directive (mirrors [[aria_mirrors_claude]] and the
[[upstash_redis_provider]] cancel decision): ARIA should not depend on
any third-party search API. No Brave key, no Bing News API, no
SearXNG instance, no Kagi. Read the public web ourselves, index it
ourselves, query it ourselves.

This module is the foundation of that engine: a separate SQLite file
at `/data/aria_search.db` holding:
  - `domains`           — curated seed domains tagged by sector + tier
  - `documents`         — fetched pages (title, headings, body, lang, tier)
  - `documents_fts`     — FTS5 virtual table over (title, headings, body)
                          with BM25 ranking built in
  - `schema_version`    — single-row migration marker

The reason for a separate db file (not the state_store one) is twofold:
  1. Different access pattern. The state_store is JSON-blob RMW with
     small values (~KB). The search index is large bodies (10-100KB)
     and FTS5 read-heavy. Keeping them in one file would bloat
     state_store's WAL and slow every TTL sweep.
  2. Different backup cadence. The search index is rebuildable (just
     re-crawl the seed list). The state_store is irrecoverable — it
     holds the brain. We want to back them up at different intervals.

Concurrency model: aiosqlite, single worker thread (mirrors state_store).
WAL mode → readers don't block the crawler writer. Foreign keys ON
(documents.domain → domains.domain).

Pytest tests use `connect(":memory:")` which gets its own in-process
db; the `_reset()` helper drops the connection between tests.

Public API:
    connect(db_path: str | None = None) -> bool
    close() -> None
    get_connection()
    register_domain(domain, tier, sector=None, region=None,
                    language=None, rate_limit_per_sec=1.0,
                    enabled=True, notes=None) -> None
    get_domain(domain) -> dict | None
    list_domains(enabled_only=True, sector=None) -> list[dict]
    upsert_document(...) -> str (doc_id)
    get_document(url=None, doc_id=None) -> dict | None
    delete_document(url=None, doc_id=None) -> bool
    search_fts(query, top_k=20, lang=None, min_tier=None) -> list[dict]
    stats() -> dict

Everything is a coroutine.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.search_index.db")

# Module-level state — matches state_store.py pattern so behaviour
# under pytest's per-test asyncio.run() is identical.
_DB_PATH: Path | None = None
_conn = None  # aiosqlite.Connection — lazy init


SCHEMA_VERSION = 1


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _now() -> float:
    return time.time()


def _doc_id(canonical_url: str) -> str:
    """Stable doc_id = sha256(canonical_url). Idempotent on re-index."""
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()


def _canonicalize(url: str) -> str:
    """Lowercase scheme + host, strip fragment, strip trailing slash on
    bare path. NOT a full canonicalization — we don't drop query strings
    because some pages (search results, paginated listings) need them.
    Drop UTM-style tracking params as a small win."""
    if not url:
        return url
    try:
        from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
        p = urlparse(url)
        scheme = (p.scheme or "https").lower()
        netloc = p.netloc.lower()
        path = p.path or "/"
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        q_pairs = [(k, v) for (k, v) in parse_qsl(p.query, keep_blank_values=False)
                   if not k.lower().startswith(("utm_", "fbclid", "gclid", "mc_eid", "mc_cid"))]
        q = urlencode(sorted(q_pairs))
        return urlunparse((scheme, netloc, path, "", q, ""))
    except Exception:
        return url


# ─────────────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────────────

async def connect(db_path: str | None = None) -> bool:
    """Open the SQLite file and create the schema if missing.

    Returns True on success. The caller (lifespan in main.py) should
    log + degrade gracefully on False; the chat path will then fall
    back to the legacy web_search.search() only, no internal index.
    """
    global _conn, _DB_PATH
    try:
        import aiosqlite
    except ImportError:
        logger.error("search_index.db: aiosqlite not installed")
        return False

    if db_path is None:
        db_path = os.getenv("ARIA_SEARCH_DB_PATH", "/data/aria_search.db")

    in_memory = db_path == ":memory:" or db_path.startswith("file::memory")
    _DB_PATH = Path(db_path) if not in_memory else None

    if _DB_PATH is not None:
        try:
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("search_index.db: cannot create parent %s: %s",
                           _DB_PATH.parent, e)
            # Fall back to a tempdir-style path so callers don't crash.
            import tempfile
            _DB_PATH = Path(tempfile.gettempdir()) / "aria_search.db"

    try:
        _conn = await aiosqlite.connect(str(_DB_PATH) if _DB_PATH else db_path)
        # R-F2132: busy_timeout BEFORE journal_mode=WAL, and 120s not 5s. A
        # journal_mode PRAGMA can trigger a WAL recovery that needs a lock; at
        # the 5s default a multi-GB WAL recovery raises 'database is locked'
        # before the timeout applies (same boot-deadlock class as aria_state,
        # 2026-06-29 outage).
        await _conn.execute("PRAGMA busy_timeout=120000")
        await _conn.execute("PRAGMA journal_mode=WAL")
        await _conn.execute("PRAGMA synchronous=NORMAL")
        await _conn.execute("PRAGMA foreign_keys=ON")
        await _create_schema(_conn)
        await _conn.commit()
        if _DB_PATH:
            logger.info("search_index.db: SQLite ready at %s (WAL mode, FTS5)",
                        _DB_PATH)
        else:
            logger.info("search_index.db: in-memory db ready (FTS5)")
        return True
    except Exception as e:
        logger.error("search_index.db: connect failed: %s", e)
        _conn = None
        return False


async def close() -> None:
    """Close the connection. Called from lifespan shutdown."""
    global _conn
    if _conn is not None:
        try:
            await _conn.close()
        except Exception:
            pass
        _conn = None


async def _reset() -> None:
    """Drop the global connection; used by tests between runs."""
    await close()


def get_connection():
    """Return the live aiosqlite.Connection. Callers in this package
    (indexer, internal_search) use this to issue their own SQL. Returns
    None when connect() hasn't succeeded — callers must check."""
    return _conn


# ─────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────

async def _create_schema(conn) -> None:
    """Create tables + FTS5 virtual table + sync triggers if missing.
    Idempotent — safe to call on every boot."""
    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS domains (
            domain               TEXT PRIMARY KEY,
            tier                 INTEGER NOT NULL,
            sector               TEXT,
            region               TEXT,
            language             TEXT,
            rate_limit_per_sec   REAL NOT NULL DEFAULT 1.0,
            robots_txt           TEXT,
            robots_fetched_at    REAL,
            last_crawled_at      REAL,
            enabled              INTEGER NOT NULL DEFAULT 1,
            notes                TEXT
        );

        CREATE TABLE IF NOT EXISTS documents (
            doc_id            TEXT PRIMARY KEY,
            url               TEXT NOT NULL UNIQUE,
            canonical_url     TEXT NOT NULL,
            domain            TEXT NOT NULL,
            title             TEXT,
            headings          TEXT,
            body              TEXT,
            language          TEXT,
            source_tier       INTEGER,
            fetched_at        REAL NOT NULL,
            http_status       INTEGER,
            content_sha256    TEXT,
            word_count        INTEGER,
            FOREIGN KEY(domain) REFERENCES domains(domain) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_documents_domain      ON documents(domain);
        CREATE INDEX IF NOT EXISTS idx_documents_fetched_at  ON documents(fetched_at);
        CREATE INDEX IF NOT EXISTS idx_documents_lang        ON documents(language);
        CREATE INDEX IF NOT EXISTS idx_documents_tier        ON documents(source_tier);
        CREATE INDEX IF NOT EXISTS idx_documents_canonical   ON documents(canonical_url);
        """
    )

    # FTS5 virtual table — external content (no body duplication).
    # Tokenizer: porter stemming + unicode61 + diacritic folding.
    # This catches "contracted/contracts/contracting" together and
    # makes "Modirum" match "modirum" / "MODIRUM" / "Modírum".
    await conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            title, headings, body,
            content='documents',
            content_rowid='rowid',
            tokenize='porter unicode61 remove_diacritics 2'
        )
        """
    )

    # Sync triggers — keep FTS5 mirror in step with documents writes.
    await conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
            INSERT INTO documents_fts(rowid, title, headings, body)
            VALUES (new.rowid, COALESCE(new.title, ''),
                    COALESCE(new.headings, ''), COALESCE(new.body, ''));
        END;
        CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
            INSERT INTO documents_fts(documents_fts, rowid, title, headings, body)
            VALUES('delete', old.rowid, COALESCE(old.title, ''),
                   COALESCE(old.headings, ''), COALESCE(old.body, ''));
        END;
        CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
            INSERT INTO documents_fts(documents_fts, rowid, title, headings, body)
            VALUES('delete', old.rowid, COALESCE(old.title, ''),
                   COALESCE(old.headings, ''), COALESCE(old.body, ''));
            INSERT INTO documents_fts(rowid, title, headings, body)
            VALUES (new.rowid, COALESCE(new.title, ''),
                    COALESCE(new.headings, ''), COALESCE(new.body, ''));
        END;
        """
    )

    # Stamp schema version. INSERT OR IGNORE so we don't bump on reboot.
    await conn.execute(
        "INSERT OR IGNORE INTO schema_version(version) VALUES (?)",
        (SCHEMA_VERSION,),
    )


# ─────────────────────────────────────────────────────────────────
# Domains
# ─────────────────────────────────────────────────────────────────

async def register_domain(
    domain: str,
    tier: int,
    sector: str | None = None,
    region: str | None = None,
    language: str | None = None,
    rate_limit_per_sec: float = 1.0,
    enabled: bool = True,
    notes: str | None = None,
) -> None:
    """Insert or update a domain in the seed registry. Used by
    seed_list.py at boot to declare the curated corpus."""
    if _conn is None:
        return
    domain = domain.lower().lstrip(".")
    try:
        await _conn.execute(
            """
            INSERT INTO domains(domain, tier, sector, region, language,
                                rate_limit_per_sec, enabled, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                tier               = excluded.tier,
                sector             = excluded.sector,
                region             = excluded.region,
                language           = excluded.language,
                rate_limit_per_sec = excluded.rate_limit_per_sec,
                enabled            = excluded.enabled,
                notes              = excluded.notes
            """,
            (domain, int(tier), sector, region, language,
             float(rate_limit_per_sec), 1 if enabled else 0, notes),
        )
        await _conn.commit()
    except Exception as e:
        logger.warning("search_index.db: register_domain(%s) failed: %s",
                       domain, e)


async def get_domain(domain: str) -> dict | None:
    if _conn is None:
        return None
    domain = domain.lower().lstrip(".")
    try:
        cur = await _conn.execute(
            "SELECT domain, tier, sector, region, language, "
            "rate_limit_per_sec, robots_txt, robots_fetched_at, "
            "last_crawled_at, enabled, notes "
            "FROM domains WHERE domain = ?",
            (domain,),
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return None
        return {
            "domain": row[0], "tier": row[1], "sector": row[2],
            "region": row[3], "language": row[4],
            "rate_limit_per_sec": row[5], "robots_txt": row[6],
            "robots_fetched_at": row[7], "last_crawled_at": row[8],
            "enabled": bool(row[9]), "notes": row[10],
        }
    except Exception as e:
        logger.warning("search_index.db: get_domain(%s) failed: %s",
                       domain, e)
        return None


async def list_domains(
    enabled_only: bool = True,
    sector: str | None = None,
) -> list[dict]:
    if _conn is None:
        return []
    try:
        sql = ("SELECT domain, tier, sector, region, language, "
               "rate_limit_per_sec, last_crawled_at, enabled "
               "FROM domains WHERE 1=1")
        args: list[Any] = []
        if enabled_only:
            sql += " AND enabled = 1"
        if sector:
            sql += " AND sector = ?"
            args.append(sector)
        sql += " ORDER BY tier ASC, domain ASC"
        cur = await _conn.execute(sql, args)
        rows = await cur.fetchall()
        await cur.close()
        return [
            {"domain": r[0], "tier": r[1], "sector": r[2], "region": r[3],
             "language": r[4], "rate_limit_per_sec": r[5],
             "last_crawled_at": r[6], "enabled": bool(r[7])}
            for r in rows
        ]
    except Exception as e:
        logger.warning("search_index.db: list_domains failed: %s", e)
        return []


async def mark_domain_crawled(domain: str, ts: float | None = None) -> None:
    if _conn is None:
        return
    domain = domain.lower().lstrip(".")
    try:
        await _conn.execute(
            "UPDATE domains SET last_crawled_at = ? WHERE domain = ?",
            (ts if ts is not None else _now(), domain),
        )
        await _conn.commit()
    except Exception:
        pass


async def cache_robots(domain: str, robots_txt: str) -> None:
    if _conn is None:
        return
    domain = domain.lower().lstrip(".")
    try:
        await _conn.execute(
            "UPDATE domains SET robots_txt = ?, robots_fetched_at = ? "
            "WHERE domain = ?",
            (robots_txt, _now(), domain),
        )
        await _conn.commit()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────
# Documents
# ─────────────────────────────────────────────────────────────────

async def upsert_document(
    url: str,
    domain: str,
    title: str | None,
    headings: str | None,
    body: str | None,
    language: str | None,
    source_tier: int | None,
    http_status: int | None = 200,
    fetched_at: float | None = None,
    canonical_url: str | None = None,
) -> str:
    """Insert or replace a document. Returns the stable doc_id.

    Idempotent on canonical_url: re-indexing the same URL updates body,
    triggers the FTS5 sync, and returns the same doc_id.
    """
    if _conn is None:
        return ""
    if canonical_url is None:
        canonical_url = _canonicalize(url)
    doc_id = _doc_id(canonical_url)
    ts = fetched_at if fetched_at is not None else _now()
    body_text = body or ""
    sha = hashlib.sha256(body_text.encode("utf-8", errors="replace")).hexdigest()
    wc = len(body_text.split()) if body_text else 0
    domain = (domain or "").lower().lstrip(".")
    try:
        await _conn.execute(
            """
            INSERT INTO documents(
                doc_id, url, canonical_url, domain, title, headings, body,
                language, source_tier, fetched_at, http_status,
                content_sha256, word_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                url            = excluded.url,
                canonical_url  = excluded.canonical_url,
                domain         = excluded.domain,
                title          = excluded.title,
                headings       = excluded.headings,
                body           = excluded.body,
                language       = excluded.language,
                source_tier    = excluded.source_tier,
                fetched_at     = excluded.fetched_at,
                http_status    = excluded.http_status,
                content_sha256 = excluded.content_sha256,
                word_count     = excluded.word_count
            """,
            (doc_id, url, canonical_url, domain, title, headings, body_text,
             language, source_tier, ts, http_status, sha, wc),
        )
        await _conn.commit()
        return doc_id
    except Exception as e:
        logger.warning("search_index.db: upsert_document(%s) failed: %s",
                       url, e)
        return ""


async def get_document(url: str | None = None,
                       doc_id: str | None = None) -> dict | None:
    if _conn is None or (not url and not doc_id):
        return None
    try:
        if doc_id is None:
            doc_id = _doc_id(_canonicalize(url))
        cur = await _conn.execute(
            "SELECT doc_id, url, canonical_url, domain, title, headings, "
            "body, language, source_tier, fetched_at, http_status, "
            "content_sha256, word_count FROM documents WHERE doc_id = ?",
            (doc_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return None
        return _row_to_doc(row)
    except Exception as e:
        logger.warning("search_index.db: get_document failed: %s", e)
        return None


async def delete_document(url: str | None = None,
                          doc_id: str | None = None) -> bool:
    if _conn is None or (not url and not doc_id):
        return False
    try:
        if doc_id is None:
            doc_id = _doc_id(_canonicalize(url))
        cur = await _conn.execute(
            "DELETE FROM documents WHERE doc_id = ?", (doc_id,),
        )
        await _conn.commit()
        return (cur.rowcount or 0) > 0
    except Exception as e:
        logger.warning("search_index.db: delete_document failed: %s", e)
        return False


def _row_to_doc(row: tuple) -> dict:
    return {
        "doc_id": row[0], "url": row[1], "canonical_url": row[2],
        "domain": row[3], "title": row[4], "headings": row[5],
        "body": row[6], "language": row[7], "source_tier": row[8],
        "fetched_at": row[9], "http_status": row[10],
        "content_sha256": row[11], "word_count": row[12],
    }


# ─────────────────────────────────────────────────────────────────
# FTS5 search
# ─────────────────────────────────────────────────────────────────

# FTS5 reserves a handful of characters as query syntax (NEAR, AND, OR,
# parentheses, double-quote, colon, hyphen at token start). We don't want
# user input to crash the query — wrap each token in double quotes after
# stripping any embedded quotes. This is "phrase quoting" — each whitespace
# token becomes a phrase. AND-of-phrases is the FTS5 default.
_FTS_BAD_CHARS = str.maketrans({"\"": " ", "(": " ", ")": " ",
                                ":": " ", "*": " ", "^": " "})


def _sanitize_fts(query: str) -> str:
    if not query:
        return ""
    q = query.translate(_FTS_BAD_CHARS)
    tokens = [t for t in q.split() if t and not t.startswith("-")]
    if not tokens:
        return ""
    # Quote each token to make it a literal phrase; FTS5 then ANDs them.
    return " ".join(f"\"{t}\"" for t in tokens)


async def search_fts(
    query: str,
    top_k: int = 20,
    lang: str | None = None,
    min_tier: int | None = None,
) -> list[dict]:
    """BM25 ranked FTS5 query.

    Returns a list of dicts shaped like:
      {doc_id, url, title, snippet, score (bm25, lower=better), domain,
       source_tier, language, fetched_at}

    Note: SQLite FTS5 bm25() returns NEGATIVE numbers where MORE negative
    is more relevant. We invert here so callers can sort descending
    naturally (higher = more relevant).
    """
    if _conn is None:
        return []
    fts_q = _sanitize_fts(query)
    if not fts_q:
        return []
    sql = (
        "SELECT d.doc_id, d.url, d.title, d.headings, d.body, "
        "       d.domain, d.source_tier, d.language, d.fetched_at, "
        "       bm25(documents_fts) AS rank "
        "FROM documents_fts "
        "JOIN documents d ON d.rowid = documents_fts.rowid "
        "WHERE documents_fts MATCH ?"
    )
    args: list[Any] = [fts_q]
    if lang:
        sql += " AND d.language = ?"
        args.append(lang)
    if min_tier is not None:
        sql += " AND d.source_tier <= ?"  # tier 1 is best; lower number = better
        args.append(int(min_tier))
    sql += " ORDER BY rank ASC LIMIT ?"  # bm25 ASC = best first
    args.append(int(top_k))

    try:
        cur = await _conn.execute(sql, args)
        rows = await cur.fetchall()
        await cur.close()
    except Exception as e:
        logger.warning("search_index.db: search_fts(%r) failed: %s",
                       query, e)
        return []

    results: list[dict] = []
    for r in rows:
        doc_id, url, title, headings, body, domain, tier, language, ts, rank = r
        snippet = _build_snippet(body or headings or title or "", query)
        # Invert BM25 so callers see "bigger = more relevant".
        score = -float(rank) if rank is not None else 0.0
        results.append({
            "doc_id": doc_id, "url": url, "title": title or "",
            "snippet": snippet, "score": score, "domain": domain,
            "source_tier": tier, "language": language,
            "fetched_at": ts,
        })
    return results


def _build_snippet(text: str, query: str, max_chars: int = 280) -> str:
    """Best-effort snippet: find the first query token in the body, slice
    a window around it. Falls back to the head of the body."""
    if not text:
        return ""
    tokens = [t for t in query.lower().split()
              if t and len(t) >= 3 and not t.startswith("-")]
    lower = text.lower()
    pos = -1
    for tok in tokens:
        i = lower.find(tok)
        if i >= 0 and (pos < 0 or i < pos):
            pos = i
    if pos < 0:
        return (text[:max_chars] + "…") if len(text) > max_chars else text
    start = max(0, pos - 80)
    end = min(len(text), pos + max_chars - 80)
    s = text[start:end].strip()
    if start > 0:
        s = "…" + s
    if end < len(text):
        s = s + "…"
    return s


# ─────────────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────────────

async def stats() -> dict:
    """Coverage snapshot for /diagnostic + the coverage report."""
    if _conn is None:
        return {"ready": False}
    try:
        out: dict[str, Any] = {"ready": True}
        cur = await _conn.execute("SELECT COUNT(*) FROM documents")
        out["documents"] = (await cur.fetchone())[0]
        await cur.close()
        cur = await _conn.execute("SELECT COUNT(*) FROM domains WHERE enabled = 1")
        out["domains_enabled"] = (await cur.fetchone())[0]
        await cur.close()
        cur = await _conn.execute(
            "SELECT language, COUNT(*) FROM documents "
            "WHERE language IS NOT NULL GROUP BY language ORDER BY 2 DESC"
        )
        out["by_language"] = {row[0]: row[1] for row in await cur.fetchall()}
        await cur.close()
        cur = await _conn.execute(
            "SELECT source_tier, COUNT(*) FROM documents "
            "WHERE source_tier IS NOT NULL GROUP BY source_tier ORDER BY 1"
        )
        out["by_tier"] = {f"tier_{row[0]}": row[1] for row in await cur.fetchall()}
        await cur.close()
        cur = await _conn.execute(
            "SELECT MIN(fetched_at), MAX(fetched_at) FROM documents"
        )
        row = await cur.fetchone()
        await cur.close()
        out["oldest_fetch"] = row[0]
        out["newest_fetch"] = row[1]
        return out
    except Exception as e:
        logger.warning("search_index.db: stats failed: %s", e)
        return {"ready": False, "error": str(e)}
