"""R-F1231 — Agent Signup Vault.

Unified registry of every site/portal that DD agents have signed up to.
Tracks:
  - Which site/portal (linked to portal_registry.PortalDef)
  - Which agent did the signup
  - When (created_at, last_verified_at)
  - Status (pending, registered, verified, failed, expired)
  - Credential reference (link to portal_registry credential store)
  - Notes / metadata

This is the single source of truth for "what have our agents signed up to?"
It is SQLite-backed (not Redis) so it survives restarts and is queryable
without a running Redis instance.

Public API
──────────
  AgentSignupVault()              — vault instance (lazy-init SQLite)
  .list(filter: dict) -> list     — list signups with optional filters
  .get(site_id: str) -> dict      — get a single signup entry
  .record(site_id, agent, ...)    — record a new signup
  .update_status(site_id, ...)    — update status / notes
  .delete(site_id)                — remove a signup entry
  .stats() -> dict                — aggregate statistics
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.agent_signup_vault")

# SQLite database path
_VAULT_DIR = Path(os.getenv("ARIA_DATA_DIR", str(Path(__file__).resolve().parent.parent.parent / "data")))
_VAULT_DB = _VAULT_DIR / "agent_signup_vault.db"

# Valid statuses
VALID_STATUSES = {"pending", "registered", "verified", "failed", "expired", "cancelled"}

# Schema version for migration
_SCHEMA_VERSION = 1

# ── Schema ────────────────────────────────────────────────────────────

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS signups (
    site_id         TEXT PRIMARY KEY,       -- portal_registry portal id or vendor id
    site_name       TEXT NOT NULL,           -- human-readable name
    site_url        TEXT NOT NULL,           -- the site URL
    site_type       TEXT NOT NULL DEFAULT 'portal',  -- 'portal', 'vendor', 'api', 'other'
    agent_id        TEXT NOT NULL,           -- which agent signed up (e.g. 'dd_orchestrator', 'researcher')
    agent_type      TEXT NOT NULL DEFAULT 'dd',  -- 'dd', 'research', 'autonomous', 'operator'
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|registered|verified|failed|expired|cancelled
    created_at      REAL NOT NULL,           -- unix timestamp
    updated_at      REAL NOT NULL,           -- unix timestamp
    last_verified_at REAL,                   -- when the signup was last confirmed working
    credential_ref  TEXT,                    -- reference to portal_registry credential (portal_id)
    notes           TEXT,                    -- free-text notes
    metadata_json   TEXT DEFAULT '{}',       -- extensible metadata (JSON blob)
    UNIQUE(site_id)
);

CREATE TABLE IF NOT EXISTS vault_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO vault_meta (key, value) VALUES ('schema_version', '1');
"""

# ── Vault class ───────────────────────────────────────────────────────


class AgentSignupVault:
    """Unified vault of every site/portal DD agents have signed up to.

    Thread-safe via SQLite's built-in locking. Lazy-initializes the
    database on first use.
    """

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = Path(db_path or _VAULT_DB)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    # ── Connection management ──────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create the SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._init_db()
        return self._conn

    def _init_db(self):
        """Initialize the database schema."""
        self._conn.executescript(_CREATE_SQL)
        self._conn.commit()

    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── CRUD operations ────────────────────────────────────────────────

    def list(self, **filters: Any) -> list[dict[str, Any]]:
        """List signups with optional filters.

        Supported filters:
          status: str          — filter by status
          agent_id: str        — filter by agent
          site_type: str       — filter by site type
          search: str          — text search on site_name, site_url, notes
          limit: int           — max results (default 100)
          offset: int          — pagination offset
          sort_by: str         — field to sort by (default 'updated_at')
          sort_dir: str        — 'asc' or 'desc' (default 'desc')
        """
        conn = self._get_conn()
        limit = filters.pop("limit", 100)
        offset = filters.pop("offset", 0)
        search = filters.pop("search", None)
        sort_by = filters.pop("sort_by", "updated_at")
        sort_dir = filters.pop("sort_dir", "desc").upper()

        # Validate sort
        allowed_sort = {"created_at", "updated_at", "site_name", "status", "agent_id", "last_verified_at"}
        if sort_by not in allowed_sort:
            sort_by = "updated_at"
        if sort_dir not in ("ASC", "DESC"):
            sort_dir = "DESC"

        where_clauses: list[str] = []
        params: list[Any] = []

        for key, value in filters.items():
            if value is not None and key in ("status", "agent_id", "site_type"):
                where_clauses.append(f"{key} = ?")
                params.append(value)

        if search:
            where_clauses.append("(site_name LIKE ? OR site_url LIKE ? OR notes LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like])

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        query = f"SELECT * FROM signups WHERE {where_sql} ORDER BY {sort_by} {sort_dir} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get(self, site_id: str) -> dict[str, Any] | None:
        """Get a single signup entry by site_id."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM signups WHERE site_id = ?", (site_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        # Parse metadata_json
        try:
            result["metadata"] = json.loads(result.pop("metadata_json", "{}"))
        except (json.JSONDecodeError, KeyError):
            result["metadata"] = {}
        return result

    def record(
        self,
        site_id: str,
        site_name: str,
        site_url: str,
        agent_id: str,
        *,
        site_type: str = "portal",
        agent_type: str = "dd",
        status: str = "pending",
        credential_ref: str | None = None,
        notes: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a new signup entry.

        Args:
            site_id: Unique identifier (portal_registry portal id or vendor id)
            site_name: Human-readable site name
            site_url: The site URL
            agent_id: Which agent did the signup
            site_type: 'portal', 'vendor', 'api', 'other'
            agent_type: 'dd', 'research', 'autonomous', 'operator'
            status: pending|registered|verified|failed|expired|cancelled
            credential_ref: Reference to portal_registry credential
            notes: Free-text notes
            metadata: Extensible metadata dict

        Returns:
            The created entry dict.
        """
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}")

        now = time.time()
        conn = self._get_conn()

        # Check if already exists
        existing = conn.execute("SELECT site_id FROM signups WHERE site_id = ?", (site_id,)).fetchone()
        if existing:
            raise ValueError(f"Signup for '{site_id}' already exists. Use update_status() to modify.")

        metadata_json = json.dumps(metadata or {})

        conn.execute(
            """INSERT INTO signups
               (site_id, site_name, site_url, site_type, agent_id, agent_type,
                status, created_at, updated_at, credential_ref, notes, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (site_id, site_name, site_url, site_type, agent_id, agent_type,
             status, now, now, credential_ref, notes, metadata_json),
        )
        conn.commit()

        # Wire success to brain
        _wire_to_brain("agent_signup_vault.recorded", {
            "site_id": site_id,
            "site_name": site_name,
            "agent_id": agent_id,
            "status": status,
            "success": True,
        })

        # R-F1233: Notify all agents about the new signup
        _notify_agents("signup_recorded", site_id, agent_id)

        return self.get(site_id)  # type: ignore[return-value]

    def update_status(
        self,
        site_id: str,
        status: str,
        *,
        notes: str | None = None,
        credential_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update the status and/or notes of a signup entry.

        Args:
            site_id: The site to update
            status: New status
            notes: Updated notes (None = leave unchanged)
            credential_ref: Updated credential reference (None = leave unchanged)
            metadata: Updated metadata (merged with existing)

        Returns:
            The updated entry, or None if not found.
        """
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}")

        conn = self._get_conn()
        existing = conn.execute("SELECT * FROM signups WHERE site_id = ?", (site_id,)).fetchone()
        if existing is None:
            return None

        now = time.time()
        updates: list[str] = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, now]

        if status == "verified":
            updates.append("last_verified_at = ?")
            params.append(now)

        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)

        if credential_ref is not None:
            updates.append("credential_ref = ?")
            params.append(credential_ref)

        if metadata is not None:
            existing_meta = json.loads(existing["metadata_json"] or "{}")
            existing_meta.update(metadata)
            updates.append("metadata_json = ?")
            params.append(json.dumps(existing_meta))

        params.append(site_id)
        conn.execute(f"UPDATE signups SET {', '.join(updates)} WHERE site_id = ?", params)
        conn.commit()

        # Wire to brain
        _wire_to_brain("agent_signup_vault.status_updated", {
            "site_id": site_id,
            "status": status,
            "success": True,
        })

        # R-F1233: Notify all agents about the status change
        _notify_agents("signup_status_updated", site_id)

        return self.get(site_id)

    def delete(self, site_id: str) -> bool:
        """Delete a signup entry. Returns True if deleted, False if not found."""
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM signups WHERE site_id = ?", (site_id,))
        conn.commit()
        deleted = cursor.rowcount > 0

        if deleted:
            _wire_to_brain("agent_signup_vault.deleted", {
                "site_id": site_id,
                "success": True,
            })
            _notify_agents("signup_deleted", site_id)

        return deleted

    # ── Statistics ─────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Get aggregate statistics about the vault."""
        conn = self._get_conn()

        total = conn.execute("SELECT COUNT(*) FROM signups").fetchone()[0]
        by_status = {
            row["status"]: row["count"]
            for row in conn.execute("SELECT status, COUNT(*) as count FROM signups GROUP BY status").fetchall()
        }
        by_agent = {
            row["agent_id"]: row["count"]
            for row in conn.execute("SELECT agent_id, COUNT(*) as count FROM signups GROUP BY agent_id").fetchall()
        }
        by_type = {
            row["site_type"]: row["count"]
            for row in conn.execute("SELECT site_type, COUNT(*) as count FROM signups GROUP BY site_type").fetchall()
        }
        recently_verified = conn.execute(
            "SELECT COUNT(*) FROM signups WHERE last_verified_at IS NOT NULL AND last_verified_at > ?",
            (time.time() - 86400 * 7,),  # last 7 days
        ).fetchone()[0]
        stale = conn.execute(
            "SELECT COUNT(*) FROM signups WHERE status = 'registered' AND (last_verified_at IS NULL OR last_verified_at < ?)",
            (time.time() - 86400 * 30,),  # not verified in 30 days
        ).fetchone()[0]

        return {
            "total": total,
            "by_status": by_status,
            "by_agent": by_agent,
            "by_type": by_type,
            "recently_verified_7d": recently_verified,
            "stale_unverified": stale,
            "db_path": str(self._db_path),
        }

    # ── Bulk import from portal_registry ───────────────────────────────

    def import_open_portals(self, portals: list, agent_id: str = "system") -> int:
        """Mark registration_type='none' portals as 'registered' in the vault.

        These are free/open APIs that require no signup. They should be
        recorded as 'registered' immediately so the vault accurately reflects
        which data sources are accessible.

        Accepts both list[PortalDef] (dataclass objects) and list[dict].

        Args:
            portals: List of portal definitions (PortalDef or dict)
            agent_id: Agent to attribute the entries to

        Returns:
            Number of new entries created.
        """
        def _get(portal, key: str, default=None):
            if isinstance(portal, dict):
                return portal.get(key, default)
            return getattr(portal, key, default)

        count = 0
        for portal in portals:
            site_id = _get(portal, "id", "")
            if not site_id:
                continue
            # Skip if already in vault
            if self.get(site_id):
                continue
            # Only record portals with registration_type="none"
            if _get(portal, "registration_type", "") != "none":
                continue

            try:
                self.record(
                    site_id=site_id,
                    site_name=_get(portal, "name", site_id),
                    site_url=_get(portal, "url", ""),
                    agent_id=agent_id,
                    site_type="portal",
                    status="registered",
                    notes="Free/open API — no registration required.",
                    metadata={"portal_type": "open_api", "registration_type": "none"},
                )
                count += 1
            except ValueError:
                pass

        return count
        """Import portals from portal_registry into the vault.

        Scans the portal definitions and records any that have signup_fields
        defined (meaning they're registrable). Skips already-recorded sites.

        Accepts both list[PortalDef] (dataclass objects) and list[dict].

        Args:
            portals: List of portal definitions (PortalDef or dict)
            agent_id: Agent to attribute the signups to

        Returns:
            Number of new entries created.
        """
        def _get(portal, key: str, default=None):
            """Get attribute from either a dataclass or dict."""
            if isinstance(portal, dict):
                return portal.get(key, default)
            return getattr(portal, key, default)

        count = 0
        for portal in portals:
            site_id = _get(portal, "id", "")
            if not site_id:
                continue
            # Skip if already in vault
            if self.get(site_id):
                continue
            # Only record portals that have signup_fields (registrable)
            if not _get(portal, "signup_fields"):
                continue

            try:
                self.record(
                    site_id=site_id,
                    site_name=_get(portal, "name", site_id),
                    site_url=_get(portal, "url", ""),
                    agent_id=agent_id,
                    site_type="portal",
                    status="pending",
                    notes=f"Auto-imported from portal_registry. Has {len(_get(portal, 'signup_fields', []))} signup fields defined.",
                    metadata={"portal_type": _get(portal, "registration_type", "email_form")},
                )
                count += 1
            except ValueError:
                # Already exists — skip
                pass

        return count


# ── Brain wiring ──────────────────────────────────────────────────────


def _wire_to_brain(event: str, details: dict[str, Any]):
    """Emit a brain signal (lazy import, never crashes)."""
    try:
        from . import brain_hook as _bh
        _bh.observe_self_event(source="agent_signup_vault", event=event, details=details)
    except Exception:
        pass  # brain unreachable


def _notify_agents(event: str, site_id: str, agent_id: str = "system"):
    """Broadcast a vault event to all registered agents (lazy import, never crashes).

    This is how agents become aware of vault changes in near-real-time.
    Fire-and-forget: if the event loop isn't running or the agent registry
    is unreachable, the notification is silently dropped.
    """
    try:
        from .agent_registry import AgentRegistry
        import asyncio
        loop = asyncio.get_running_loop()
        if loop.is_running():
            registry = AgentRegistry()
            loop.create_task(registry.notify_agents_about_vault(event, site_id, agent_id))
    except (RuntimeError, Exception):
        pass  # no running loop or agent registry unreachable — non-critical


# ── Module-level singleton ────────────────────────────────────────────

_VAULT_INSTANCE: AgentSignupVault | None = None


def get_vault() -> AgentSignupVault:
    """Get or create the singleton vault instance."""
    global _VAULT_INSTANCE
    if _VAULT_INSTANCE is None:
        _VAULT_INSTANCE = AgentSignupVault()
    return _VAULT_INSTANCE
