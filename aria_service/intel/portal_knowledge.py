"""
R-F2064 — Portal Registration Knowledge Base.

Persistent SQLite storage for ARIA's portal registration learning.
Stores per-domain patterns, attempt history, and field selectors so
the agent gets smarter with every registration attempt.

Tables:
  - sites: per-domain stats (attempts, success rate, avg duration)
  - attempts: individual attempt records with full diagnostics
  - field_patterns: successful field selectors per domain
  - global_patterns: cross-domain patterns (submit button selectors, etc.)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.portal_knowledge")

# Default path — /data on Fly, local fallback
_DEFAULT_DB_DIR = "/data"
try:
    Path(_DEFAULT_DB_DIR).mkdir(parents=True, exist_ok=True)
except Exception:
    _DEFAULT_DB_DIR = Path(__file__).parent.parent.parent / "data"


class RegistrationKnowledge:
    """Persistent knowledge base for portal registration learning."""

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = Path(_DEFAULT_DB_DIR) / "portal_knowledge.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        logger.info("Portal knowledge base at %s", self.db_path)

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sites (
                    domain TEXT PRIMARY KEY,
                    last_success TEXT,
                    total_attempts INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    fail_count INTEGER DEFAULT 0,
                    average_duration REAL DEFAULT 0,
                    last_config TEXT,
                    last_error TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    portal_id TEXT,
                    timestamp TEXT NOT NULL,
                    success INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    duration REAL,
                    captcha_solved INTEGER DEFAULT 0,
                    email_used TEXT,
                    api_key_obtained INTEGER DEFAULT 0,
                    config_used TEXT,
                    diagnostics TEXT,
                    FOREIGN KEY(domain) REFERENCES sites(domain)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS field_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    selector TEXT NOT NULL,
                    confidence REAL DEFAULT 1.0,
                    last_used TEXT,
                    success_count INTEGER DEFAULT 1,
                    fail_count INTEGER DEFAULT 0,
                    FOREIGN KEY(domain) REFERENCES sites(domain)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS global_patterns (
                    pattern_type TEXT PRIMARY KEY,
                    pattern_value TEXT,
                    confidence REAL DEFAULT 1.0,
                    last_used TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_attempts_domain ON attempts(domain)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_attempts_timestamp ON attempts(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_field_patterns_domain ON field_patterns(domain)
            """)
            conn.commit()

    # ── Site-level operations ─────────────────────────────────────────────

    def get_site(self, domain: str) -> dict[str, Any]:
        """Get site stats."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM sites WHERE domain = ?", (domain,))
            row = cur.fetchone()
            return dict(row) if row else {}

    def update_site(self, domain: str, success: bool, duration: float,
                    config: dict | None = None, error: str | None = None) -> None:
        """Update site stats after an attempt."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT total_attempts, success_count, fail_count, average_duration FROM sites WHERE domain = ?",
                (domain,),
            )
            row = cur.fetchone()
            if row:
                total, succ, fail, avg_dur = row
                total += 1
                if success:
                    succ += 1
                else:
                    fail += 1
                avg_dur = (avg_dur * (total - 1) + duration) / total if total > 0 else duration
                conn.execute("""
                    UPDATE sites SET
                        total_attempts = ?, success_count = ?, fail_count = ?,
                        average_duration = ?, last_success = ?, last_error = ?,
                        last_config = ?, updated_at = ?
                    WHERE domain = ?
                """, (total, succ, fail, avg_dur,
                      now if success else None, error if not success else None,
                      json.dumps(config) if config else None, now, domain))
            else:
                conn.execute("""
                    INSERT INTO sites (domain, total_attempts, success_count, fail_count,
                        average_duration, last_success, last_error, last_config, updated_at)
                    VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)
                """, (domain, 1 if success else 0, 0 if success else 1,
                      duration, now if success else None, error if not success else None,
                      json.dumps(config) if config else None, now))
            conn.commit()

    # ── Attempt-level operations ──────────────────────────────────────────

    def record_attempt(self, domain: str, portal_id: str, success: bool,
                       duration: float, error: str | None = None,
                       captcha_solved: bool = False, email_used: str | None = None,
                       api_key_obtained: bool = False,
                       config: dict | None = None,
                       diagnostics: list | None = None) -> str:
        """Record a registration attempt."""
        attempt_id = str(uuid.uuid4())[:12]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO attempts (id, domain, portal_id, timestamp, success, error,
                    duration, captcha_solved, email_used, api_key_obtained, config_used, diagnostics)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                attempt_id, domain, portal_id,
                datetime.now(timezone.utc).isoformat(),
                1 if success else 0, error, duration,
                1 if captcha_solved else 0, email_used,
                1 if api_key_obtained else 0,
                json.dumps(config) if config else None,
                json.dumps(diagnostics) if diagnostics else None,
            ))
            conn.commit()
        return attempt_id

    def get_recent_attempts(self, domain: str, limit: int = 10) -> list[dict]:
        """Get recent attempts for a domain."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM attempts WHERE domain = ? ORDER BY timestamp DESC LIMIT ?",
                (domain, limit),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_all_attempts(self, limit: int = 50) -> list[dict]:
        """Get all recent attempts across all domains."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM attempts ORDER BY timestamp DESC LIMIT ?", (limit,)
            )
            return [dict(row) for row in cur.fetchall()]

    # ── Field pattern operations ──────────────────────────────────────────

    def get_field_patterns(self, domain: str) -> dict[str, str]:
        """Get known field selectors for a domain, highest confidence first."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT field_name, selector FROM field_patterns "
                "WHERE domain = ? ORDER BY confidence DESC, success_count DESC",
                (domain,),
            )
            result = {}
            for field_name, selector in cur.fetchall():
                if field_name not in result:
                    result[field_name] = selector
            return result

    def upsert_field_pattern(self, domain: str, field_name: str,
                              selector: str, confidence: float = 1.0) -> None:
        """Store or update a field selector pattern."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            # Check if exists
            cur = conn.execute(
                "SELECT id, confidence, success_count FROM field_patterns "
                "WHERE domain = ? AND field_name = ? AND selector = ?",
                (domain, field_name, selector),
            )
            row = cur.fetchone()
            if row:
                fid, old_conf, old_success = row
                new_conf = (old_conf + confidence) / 2
                conn.execute("""
                    UPDATE field_patterns SET confidence = ?, last_used = ?,
                        success_count = success_count + 1
                    WHERE id = ?
                """, (new_conf, now, fid))
            else:
                conn.execute("""
                    INSERT INTO field_patterns (domain, field_name, selector, confidence, last_used)
                    VALUES (?, ?, ?, ?, ?)
                """, (domain, field_name, selector, confidence, now))
            conn.commit()

    def record_field_failure(self, domain: str, field_name: str, selector: str) -> None:
        """Record that a field selector failed."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE field_patterns SET fail_count = fail_count + 1, confidence = confidence * 0.5
                WHERE domain = ? AND field_name = ? AND selector = ?
            """, (domain, field_name, selector))
            conn.commit()

    # ── Global pattern operations ─────────────────────────────────────────

    def get_global_pattern(self, pattern_type: str) -> str | None:
        """Get a cross-domain pattern."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT pattern_value FROM global_patterns "
                "WHERE pattern_type = ? ORDER BY confidence DESC LIMIT 1",
                (pattern_type,),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def upsert_global_pattern(self, pattern_type: str, pattern_value: str,
                               confidence: float = 1.0) -> None:
        """Store or update a cross-domain pattern."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO global_patterns (pattern_type, pattern_value, confidence, last_used)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(pattern_type) DO UPDATE SET
                    pattern_value = excluded.pattern_value,
                    confidence = (confidence + excluded.confidence) / 2,
                    last_used = excluded.last_used
            """, (pattern_type, pattern_value, confidence, now))
            conn.commit()

    # ── Stats ─────────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Get overall registration stats."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            sites = conn.execute("SELECT COUNT(*) as c FROM sites").fetchone()["c"]
            total_attempts = conn.execute("SELECT COUNT(*) as c FROM attempts").fetchone()["c"]
            successes = conn.execute("SELECT COUNT(*) as c FROM attempts WHERE success = 1").fetchone()["c"]
            failures = conn.execute("SELECT COUNT(*) as c FROM attempts WHERE success = 0").fetchone()["c"]
            captcha_ok = conn.execute(
                "SELECT COUNT(*) as c FROM attempts WHERE captcha_solved = 1"
            ).fetchone()["c"]
            api_keys = conn.execute(
                "SELECT COUNT(*) as c FROM attempts WHERE api_key_obtained = 1"
            ).fetchone()["c"]
            return {
                "sites_tracked": sites,
                "total_attempts": total_attempts,
                "successes": successes,
                "failures": failures,
                "captcha_solved": captcha_ok,
                "api_keys_obtained": api_keys,
                "success_rate": round(successes / total_attempts, 3) if total_attempts else 0,
            }
