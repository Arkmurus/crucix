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
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.agent_signup_vault")

# SQLite database path — MUST resolve onto /data volume (persistent across deploys).
# R-F1692: was resolving via ARIA_DATA_DIR which is unset on Fly, so the DB lived
# in /app/data/ (ephemeral) and ALL registration state was wiped every deploy.
# Now scoped to /data/ directly, matching the Fly volume mount.
_VAULT_DIR = Path("/data")
_VAULT_DB = _VAULT_DIR / "agent_signup_vault.db"

# Valid statuses
# R-F1491: added 'open_api' for portals that are free/open and need no registration.
# Previously these were marked as 'registered' which was fabricated — no registration
# ever happened. 'open_api' is honest: the data source is accessible without credentials.
# R-F1502: added 'needs_operator' for portals ARIA cannot auto-register
# (CAPTCHA, paid, email_verify, attempt_failed, manual_signup).
# Replaces the perpetual 'pending' status that never resolved.
# R-F1684: added 'declined' (operator said no — paid/declined per §18) and
# 'deferred' (waiting on env vars or prerequisites). These are terminal states
# that suppress the portal from the recurring digest — never re-surfaced.
VALID_STATUSES = {"pending", "registered", "verified", "failed", "expired", "cancelled", "open_api", "needs_operator", "declined", "deferred"}

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
        self._migrate_v1()
        self._migrate_v2()

    def _migrate_v1(self):
        """R-F1704: Correct fabricated 'registered' entries from the old
        determine_and_drive code.

        The old determine_and_drive() checked Redis credentials as proof of
        registration. But _audit_preparation stores credentials BEFORE form
        submission, so every 'prepared' portal had Redis credentials too.
        This caused 23 portals to be falsely marked as 'registered'.

        This migration checks every 'registered' entry: if its notes say
        'Credentials found in vault' (the old code's message), it was
        fabricated — downgrade to 'needs_operator'. Real registrations
        have notes like 'Autonomously registered' or 'Confirmed registered'.
        """
        import logging as _log1704
        _log1704 = logging.getLogger("aria.agent_signup_vault.migration")
        try:
            cursor = self._conn.execute(
                "SELECT site_id, site_name, notes FROM signups WHERE status = 'registered'"
            )
            fabricated = []
            for row in cursor.fetchall():
                notes = (row["notes"] or "").lower()
                site_id = row["site_id"]
                site_name = row["site_name"]
                # Old code set notes to "Credentials found in vault"
                # Real registrations have notes like "Autonomously registered"
                if "credentials found" in notes or not notes.strip():
                    fabricated.append((site_id, site_name))

            if fabricated:
                for site_id, site_name in fabricated:
                    self._conn.execute(
                        "UPDATE signups SET status = 'needs_operator', "
                        "updated_at = ?, notes = ? WHERE site_id = ?",
                        (time.time(),
                         f"R-F1704: corrected from fabricated 'registered' to 'needs_operator' — "
                         f"no real registration completed",
                         site_id),
                    )
                self._conn.commit()
                _log1704.info(
                    "[R-F1704] Corrected %d fabricated 'registered' entries to 'needs_operator': %s",
                    len(fabricated), ", ".join(s for s, _ in fabricated),
                )
                try:
                    from .engine_wiring import wire_success as _ws1704
                    _ws1704(
                        module="agent_signup_vault",
                        summary=f"Migration v1: corrected {len(fabricated)} fabricated registrations",
                        source_id="agent_signup_vault:migration_v1",
                    )
                except Exception:
                    pass
            else:
                _log1704.debug("[R-F1704] No fabricated registrations found — vault is clean")
        except Exception as _e1704:
            _log1704.warning("[R-F1704] Migration v1 failed (non-fatal): %s", _e1704)

    def _migrate_v2(self):
        """R-F1753: Correct fabricated 'registered' entries missed by v1.

        The v1 migration only caught notes containing 'Credentials found in
        vault'. But the old register_for_portal() code path wrote notes like
        'Already registered for X' — a different pattern. The live vault had
        19 entries with this pattern, all fabricated (Redis credentials existed
        from _audit_preparation, not from real registration).

        This migration catches ALL 'registered' entries whose notes match
        the fabricated pattern: 'Already registered for', 'Credentials found',
        or empty notes. Real registrations have notes like 'Autonomously
        registered', 'Confirmed registered', or 'Auto-registered:'.

        Also corrects needs_operator entries whose notes say [DECLINED] or
        [DEFERRED] — these should have the proper status.
        """
        import logging as _log1753
        _log1753 = logging.getLogger("aria.agent_signup_vault.migration")
        try:
            # Part 1: Fix fabricated 'registered' entries
            cursor = self._conn.execute(
                "SELECT site_id, site_name, notes FROM signups WHERE status = 'registered'"
            )
            fabricated = []
            for row in cursor.fetchall():
                notes = (row["notes"] or "").lower()
                site_id = row["site_id"]
                site_name = row["site_name"]
                # Fabricated patterns from old code paths:
                #   "Already registered for X" (register_for_portal is_registered shortcut)
                #   "Credentials found in vault" (old determine_and_drive)
                #   empty notes
                # Real registrations have notes like:
                #   "Autonomously registered", "Confirmed registered",
                #   "Auto-registered:", "Logged into existing", "retrieved"
                if ("already registered" in notes
                        or "credentials found" in notes
                        or not notes.strip()):
                    fabricated.append((site_id, site_name))

            if fabricated:
                for site_id, site_name in fabricated:
                    self._conn.execute(
                        "UPDATE signups SET status = 'needs_operator', "
                        "updated_at = ?, notes = ? WHERE site_id = ?",
                        (time.time(),
                         f"R-F1753: corrected from fabricated 'registered' to 'needs_operator' — "
                         f"no real registration completed",
                         site_id),
                    )
                self._conn.commit()
                _log1753.info(
                    "[R-F1753] Corrected %d fabricated 'registered' entries to 'needs_operator': %s",
                    len(fabricated), ", ".join(s for s, _ in fabricated),
                )
                try:
                    from .engine_wiring import wire_success as _ws1753
                    _ws1753(
                        module="agent_signup_vault",
                        summary=f"Migration v2: corrected {len(fabricated)} fabricated registrations",
                        source_id="agent_signup_vault:migration_v2",
                    )
                except Exception:
                    pass
            else:
                _log1753.debug("[R-F1753] No fabricated registrations found — vault is clean")

            # Part 2: Fix needs_operator entries that should be declined/deferred
            cursor2 = self._conn.execute(
                "SELECT site_id, notes FROM signups WHERE status = 'needs_operator'"
            )
            for row in cursor2.fetchall():
                notes = (row["notes"] or "").lower()
                site_id = row["site_id"]
                if "[declined" in notes:
                    self._conn.execute(
                        "UPDATE signups SET status = 'declined', updated_at = ? WHERE site_id = ?",
                        (time.time(), site_id),
                    )
                    _log1753.info("[R-F1753] Corrected '%s' from needs_operator to declined", site_id)
                elif "[deferred" in notes:
                    self._conn.execute(
                        "UPDATE signups SET status = 'deferred', updated_at = ? WHERE site_id = ?",
                        (time.time(), site_id),
                    )
                    _log1753.info("[R-F1753] Corrected '%s' from needs_operator to deferred", site_id)
            self._conn.commit()

        except Exception as _e1753:
            _log1753.warning("[R-F1753] Migration v2 failed (non-fatal): %s", _e1753)

    @fail_wire(module="agent_signup_vault", gap_type="registry_lookup")
    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── CRUD operations ────────────────────────────────────────────────

    @fail_wire(module="agent_signup_vault", gap_type="registry_lookup")
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

    @fail_wire(module="agent_signup_vault", gap_type="registry_lookup")
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

    @fail_wire(module="agent_signup_vault", gap_type="registry_lookup",
               control_flow_exempt=("ValueError",))  # validation raises are control flow
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
        _wire_success("agent_signup_vault.recorded", {
            "site_id": site_id,
            "site_name": site_name,
            "agent_id": agent_id,
            "status": status,
        })

        # R-F1233: Notify all agents about the new signup
        _notify_agents("signup_recorded", site_id, agent_id)

        return self.get(site_id)  # type: ignore[return-value]

    @fail_wire(module="agent_signup_vault", gap_type="registry_lookup",
               control_flow_exempt=("ValueError",))  # validation raises are control flow
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
        _wire_success("agent_signup_vault.status_updated", {
            "site_id": site_id,
            "status": status,
        })

        # R-F1233: Notify all agents about the status change
        _notify_agents("signup_status_updated", site_id)

        return self.get(site_id)

    @fail_wire(module="agent_signup_vault", gap_type="registry_lookup")
    def delete(self, site_id: str) -> bool:
        """Delete a signup entry. Returns True if deleted, False if not found."""
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM signups WHERE site_id = ?", (site_id,))
        conn.commit()
        deleted = cursor.rowcount > 0

        if deleted:
            _wire_success("agent_signup_vault.deleted", {
                "site_id": site_id,
            })
            _notify_agents("signup_deleted", site_id)

        return deleted

    # ── Statistics ─────────────────────────────────────────────────────

    @fail_wire(module="agent_signup_vault", gap_type="registry_lookup")
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

    # ── R-F1684: Test-data cleanup ──────────────────────────────────────

    @fail_wire(module="agent_signup_vault", gap_type="registry_lookup")
    def cleanup_test_data(self, max_age_hours: int = 24) -> int:
        """Delete test entries older than `max_age_hours`.

        Test entries (agent_id='test_agent' or 'test') accumulate during
        test runs and pollute the vault with phantom 'pending' entries.
        The gap detector's PortalCoverageExtractor picks them up and creates
        phantom gaps that waste the coder's budget.

        Returns the number of deleted entries.
        """
        conn = self._get_conn()
        cutoff = time.time() - (max_age_hours * 3600)
        cursor = conn.execute(
            "DELETE FROM signups WHERE agent_id IN ('test_agent', 'test') AND created_at < ?",
            (cutoff,),
        )
        conn.commit()
        deleted = cursor.rowcount
        if deleted:
            logger.info(
                "[agent_signup_vault] R-F1684: cleaned %d test entries older than %dh",
                deleted, max_age_hours,
            )
            _wire_success("agent_signup_vault.test_data_cleaned", {
                "deleted": deleted,
                "max_age_hours": max_age_hours,
            })
        return deleted

    # ── Bulk import from portal_registry ───────────────────────────────

    @fail_wire(module="agent_signup_vault", gap_type="registry_lookup")
    def import_open_portals(self, portals: list, agent_id: str = "system") -> int:
        """Import portals from portal_registry into the vault.

        Handles ALL portal types — no gaps:
        1. registration_type='none' → marked as 'open_api' (free/open APIs)
        2. Portals with signup_fields → marked as 'pending' (registrable)
        3. CAPTCHA portals → marked as 'needs_operator' (ARIA cannot bypass)
        4. API-key portals without signup_fields → marked as 'needs_operator'
        5. Portals without signup_fields → marked as 'needs_operator'

        R-F1684: covers the 6 missing portals (sam_gov, opensanctions, fedbizops,
        fapiis, state_ddtc, core_ac_uk) that fell through because they had no
        signup_fields or were CAPTCHA-only.

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

            reg_type = _get(portal, "registration_type", "")
            signup_fields = _get(portal, "signup_fields")
            requires_captcha = _get(portal, "requires_captcha", False)

            if reg_type == "none":
                # Free/open API — no registration required.
                try:
                    self.record(
                        site_id=site_id,
                        site_name=_get(portal, "name", site_id),
                        site_url=_get(portal, "url", ""),
                        agent_id=agent_id,
                        site_type="portal",
                        status="open_api",
                        notes="Free/open API — no registration required.",
                        metadata={"portal_type": "open_api", "registration_type": "none"},
                    )
                    count += 1
                except ValueError:
                    pass
            elif requires_captcha:
                # CAPTCHA-protected — ARIA cannot bypass autonomously.
                # R-F1684: previously these fell through the cracks because
                # the code only checked signup_fields. Now they get an honest
                # 'needs_operator' status.
                try:
                    self.record(
                        site_id=site_id,
                        site_name=_get(portal, "name", site_id),
                        site_url=_get(portal, "url", ""),
                        agent_id=agent_id,
                        site_type="portal",
                        status="needs_operator",
                        notes=f"CAPTCHA-protected — operator must register manually.",
                        metadata={"portal_type": reg_type or "email_form", "requires_captcha": True},
                    )
                    count += 1
                except ValueError:
                    pass
            elif signup_fields:
                # Registrable portal with defined signup fields
                try:
                    self.record(
                        site_id=site_id,
                        site_name=_get(portal, "name", site_id),
                        site_url=_get(portal, "url", ""),
                        agent_id=agent_id,
                        site_type="portal",
                        status="pending",
                        notes=f"Auto-imported from portal_registry. Has {len(signup_fields)} signup fields defined.",
                        metadata={"portal_type": reg_type or "email_form"},
                    )
                    count += 1
                except ValueError:
                    pass
            else:
                # Portal without signup_fields and without CAPTCHA — needs
                # operator action (API-key portals, email_form without fields).
                # R-F1684: previously these fell through entirely.
                try:
                    self.record(
                        site_id=site_id,
                        site_name=_get(portal, "name", site_id),
                        site_url=_get(portal, "url", ""),
                        agent_id=agent_id,
                        site_type="portal",
                        status="needs_operator",
                        notes=f"Registration type '{reg_type}' — no signup fields defined. Operator action needed.",
                        metadata={"portal_type": reg_type or "unknown"},
                    )
                    count += 1
                except ValueError:
                    pass

        return count


# ── Brain wiring ──────────────────────────────────────────────────────

# R-F1692: replaced the broken _wire_to_brain (called observe_self_event with
# wrong kwargs -> TypeError swallowed by except:pass) with engine_wiring calls
# that match the real function signatures. Every vault operation now emits a
# verifiable brain signal on BOTH success and failure branches.


def _wire_success(event: str, details: dict[str, Any]):
    """Emit a success signal to the brain (lazy import, never crashes)."""
    try:
        from .engine_wiring import wire_success as _ws
        _ws(
            module="agent_signup_vault",
            summary=f"{event}: {details.get('site_id', 'unknown')}",
            source_id=f"agent_signup_vault:{event}",
        )
    except Exception:
        pass


def _wire_failure(event: str, details: dict[str, Any], error: str = ""):
    """Emit a failure signal to the brain (lazy import, never crashes)."""
    try:
        from .engine_wiring import wire_failure as _wf
        _wf(
            module="agent_signup_vault",
            detail=f"{event} failed for {details.get('site_id', 'unknown')}: {error}",
            gap_type="source_failure",
            source="agent_signup_vault",
        )
    except Exception:
        pass


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


@fail_wire(module="agent_signup_vault", gap_type="registry_lookup")
def get_vault() -> AgentSignupVault:
    """Get or create the singleton vault instance."""
    global _VAULT_INSTANCE
    if _VAULT_INSTANCE is None:
        _VAULT_INSTANCE = AgentSignupVault()
    return _VAULT_INSTANCE
