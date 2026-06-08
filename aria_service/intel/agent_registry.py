"""R-F1160 — Multi-Agent Registry & Awareness Protocol.

ARIA can have multiple agents running simultaneously (the autonomous coder,
the gap detector, the research engine, the student brain, the self-healing
system, and external Claude Code sessions). This module gives every agent:

  1. REGISTRATION — each agent announces itself with its ID, type, and role
  2. HEARTBEAT — each agent publishes what it's doing right now
  3. AWARENESS — each agent can see all other active agents and their tasks
  4. DECONFLICTION — agents don't pick up work another agent is already doing
  5. MESSAGING — agents can send structured messages to each other

The registry is Redis-backed so it survives restarts and works across
process boundaries (Python agents, Node agents, CLI agents, Claude Code).

Usage
─────
  from .agent_registry import AgentRegistry

  registry = AgentRegistry()

  # Register on startup
  await registry.register("aria_coder", "autonomous_coder", "Fixing gaps")

  # Update task mid-work
  await registry.update_task("aria_coder", "Fixing neural_memory.py")

  # See who else is active
  agents = await registry.list_active_agents()
  for agent in agents:
      print(f"{agent['agent_id']} is {agent['current_task']}")

  # Send a message to another agent
  await registry.send_message("aria_coder", "gap_detector",
      {"type": "incoming_gap", "gap_id": "abc123", "module": "researcher.py"})

  # Read messages addressed to this agent
  messages = await registry.read_messages("aria_coder")

  # Check if a gap is already being worked on
  busy = await registry.is_gap_claimed("abc123")
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.agent_registry")

# R-F1166 — wire to brain on registry operations
from .engine_wiring import wire_success, wire_failure

# R-F1212 — forward reference for AgentContract (lazy-imported in register)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .agent_contract import AgentContract

# ── Redis key prefixes ────────────────────────────────────────────────────────

_AGENT_REGISTRY_KEY = "crucix:agent:registry"          # hash: agent_id → json status
_AGENT_HEARTBEAT_KEY = "crucix:agent:heartbeat:"        # string: agent_id → epoch timestamp
_AGENT_TASK_KEY = "crucix:agent:task:"                  # string: agent_id → current task description
_AGENT_MESSAGE_PREFIX = "crucix:agent:msg:"             # list: agent_id → incoming messages
_AGENT_GAP_CLAIM_PREFIX = "crucix:agent:gap_claim:"     # string: gap_id → agent_id (who's fixing it)
# How long before an agent is considered stale (no heartbeat)
_AGENT_STALE_THRESHOLD_S = int(os.getenv("ARIA_AGENT_STALE_THRESHOLD", "300"))  # 5 min

# How long a gap claim lives before auto-expiring
_GAP_CLAIM_TTL_S = int(os.getenv("ARIA_GAP_CLAIM_TTL", "3600"))  # 1 hour

# Max messages to keep per agent
_MAX_MESSAGES_PER_AGENT = int(os.getenv("ARIA_AGENT_MAX_MESSAGES", "100"))

# R-F1446: dedicated SQLite database path for agent registry
# Separate file = separate write lock = no contention with state_store
_REGISTRY_DB_DIR = Path(os.getenv("ARIA_DATA_DIR", str(Path(__file__).resolve().parent.parent.parent / "data")))
_REGISTRY_DB = _REGISTRY_DB_DIR / "agent_registry.db"


def _get_registry_db_path() -> Path:
    """Get the path to the dedicated agent registry database."""
    _REGISTRY_DB.parent.mkdir(parents=True, exist_ok=True)
    return _REGISTRY_DB


class AgentRegistry:
    """Multi-agent registry with heartbeat, awareness, deconfliction, messaging.

    R-F1446: uses a dedicated SQLite database (agent_registry.db) for
    registration and heartbeat writes, bypassing the shared state_store
    lock that was causing 12/13 agents to silently fail registration
    under boot-time contention. The dedicated file has its own write
    lock, so agent heartbeats no longer contend with chat, coder, and
    self_healing writes on the main state_store lock.
    """

    def __init__(self) -> None:
        self._redis = None  # lazy-loaded
        self._db_conn = None  # dedicated SQLite connection (R-F1446)
        self._db_path = _get_registry_db_path()

    def _get_db(self) -> sqlite3.Connection:
        """Get or create the dedicated SQLite connection for agent registry.

        R-F1446: separate database file = separate write lock = no contention
        with chat/coder/self_healing on the main state_store lock.
        Follows the same pattern as agent_signup_vault.py.
        """
        if self._db_conn is None:
            self._db_conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._db_conn.row_factory = sqlite3.Row
            self._db_conn.execute("PRAGMA journal_mode=WAL")
            self._db_conn.execute("PRAGMA foreign_keys=ON")
            self._init_db()
        return self._db_conn

    def _init_db(self):
        """Initialize the registry database schema."""
        self._db_conn.executescript("""
            CREATE TABLE IF NOT EXISTS agents (
                agent_id    TEXT PRIMARY KEY,
                agent_type  TEXT NOT NULL,
                current_task TEXT NOT NULL DEFAULT '',
                status      TEXT NOT NULL DEFAULT 'active',
                registered_at REAL NOT NULL,
                last_heartbeat REAL NOT NULL,
                metadata    TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS agent_tasks (
                agent_id    TEXT PRIMARY KEY,
                task        TEXT NOT NULL DEFAULT ''
            );
        """)
        self._db_conn.commit()

    def _db_register(self, agent_id: str, agent_type: str, current_task: str, now: float) -> None:
        """Write registration to the dedicated DB (no lock contention)."""
        conn = self._get_db()
        conn.execute(
            """INSERT OR REPLACE INTO agents
               (agent_id, agent_type, current_task, status, registered_at, last_heartbeat, metadata)
               VALUES (?, ?, ?, 'active', ?, ?, '{}')""",
            (agent_id, agent_type, current_task, now, now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO agent_tasks (agent_id, task) VALUES (?, ?)",
            (agent_id, current_task),
        )
        conn.commit()

    def _db_tick_heartbeat(self, agent_id: str, now: float, current_task: str | None = None) -> None:
        """Write heartbeat to the dedicated DB (no lock contention)."""
        conn = self._get_db()
        if current_task is not None:
            conn.execute(
                "UPDATE agents SET last_heartbeat=?, current_task=? WHERE agent_id=?",
                (now, current_task, agent_id),
            )
            conn.execute(
                "INSERT OR REPLACE INTO agent_tasks (agent_id, task) VALUES (?, ?)",
                (agent_id, current_task),
            )
        else:
            conn.execute(
                "UPDATE agents SET last_heartbeat=? WHERE agent_id=?",
                (now, agent_id),
            )
        conn.commit()

    def _db_set_heartbeat(self, agent_id: str, timestamp: float) -> None:
        """Set an agent's heartbeat to a specific timestamp (for testing)."""
        conn = self._get_db()
        conn.execute("UPDATE agents SET last_heartbeat=? WHERE agent_id=?", (timestamp, agent_id))
        conn.commit()

    def _db_list_agents(self) -> list[dict]:
        """List all agents from the dedicated DB."""
        conn = self._get_db()
        rows = conn.execute(
            "SELECT agent_id, agent_type, current_task, status, registered_at, last_heartbeat FROM agents"
        ).fetchall()
        return [dict(r) for r in rows]

    def _db_get_agent(self, agent_id: str) -> dict | None:
        """Get a single agent from the dedicated DB."""
        conn = self._get_db()
        row = conn.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        try:
            result["metadata"] = json.loads(result.pop("metadata", "{}"))
        except (json.JSONDecodeError, KeyError):
            result["metadata"] = {}
        return result

    def _db_unregister(self, agent_id: str) -> None:
        """Remove an agent from the dedicated DB."""
        conn = self._get_db()
        conn.execute("DELETE FROM agents WHERE agent_id=?", (agent_id,))
        conn.execute("DELETE FROM agent_tasks WHERE agent_id=?", (agent_id,))
        conn.commit()

    def close(self) -> None:
        """Close the dedicated database connection."""
        if self._db_conn:
            self._db_conn.close()
            self._db_conn = None

    def _db_clear(self) -> None:
        """Clear all data from the dedicated DB (for testing)."""
        try:
            conn = self._get_db()
            conn.execute("DELETE FROM agents")
            conn.execute("DELETE FROM agent_tasks")
            conn.commit()
        except Exception:
            pass

    def __del__(self) -> None:
        self.close()

    def _get_redis(self):
        """Get the redis store module (lazy-loaded).

        If _redis has been set to a mock (for testing), return it directly.
        Otherwise import the real redis_store module.
        Not async — returns the module/mock directly.
        """
        if self._redis is not None:
            return self._redis
        from . import redis_store as _rs
        self._redis = _rs
        return self._redis

    # ── REGISTRATION ──────────────────────────────────────────────────────────

    async def register(
        self,
        agent_id: str,
        agent_type: str,
        current_task: str = "starting up",
        metadata: Optional[dict] = None,
        contract: Optional["AgentContract"] = None,
    ) -> bool:
        """Register an agent in the registry.

        Args:
            agent_id: Unique identifier (e.g. "aria_coder", "gap_detector",
                     "claude_code_session_1")
            agent_type: Type of agent (e.g. "autonomous_coder", "gap_detector",
                       "research_engine", "student_brain", "claude_code")
            current_task: What the agent is doing right now
            metadata: Optional dict with additional info (version, pid, etc.)
            contract: Optional AgentContract to register alongside the agent.
                     R-F1212: every agent should have a binding contract.

        Returns True if registration succeeded.
        """
        now = time.time()
        entry = {
            "agent_id": agent_id,
            "agent_type": agent_type,
            "current_task": current_task,
            "status": "active",
            "registered_at": now,
            "last_heartbeat": now,
            "metadata": metadata or {},
        }
        # R-F1446: write to dedicated DB first (fast, no lock contention)
        try:
            self._db_register(agent_id, agent_type, current_task, now)
        except Exception as _db_e:
            logger.debug("[R-F1446] dedicated DB register failed for %s: %s", agent_id, _db_e)
        # Also write to Redis/state_store for backward compatibility
        try:
            rs = self._get_redis()
            await rs.hset(_AGENT_REGISTRY_KEY, {agent_id: json.dumps(entry)})
            await rs.set(f"{_AGENT_HEARTBEAT_KEY}{agent_id}", str(now))
            await rs.set(f"{_AGENT_TASK_KEY}{agent_id}", current_task)
            logger.info(
                "[R-F1160] agent registered: %s (%s) — %s",
                agent_id, agent_type, current_task,
            )
            # R-F1212: register contract alongside agent if provided
            if contract is not None:
                try:
                    from .agent_contract import CONTRACT_REGISTRY as _CR
                    await _CR.register_contract(contract)
                except Exception:
                    logger.debug("[R-F1160] contract registration failed for %s", agent_id)
            # R-F1166 — wire success to brain
            wire_success(
                module="agent_registry",
                summary=f"Agent registered: {agent_id} ({agent_type})",
                entity_name=agent_id,
                source_id="agent_registry:register",
            )
            return True
        except Exception as e:
            logger.warning("[R-F1160] agent registration failed for %s: %s", agent_id, e)
            # R-F1166 — wire failure to brain
            wire_failure(
                module="agent_registry",
                detail=f"register failed for {agent_id}: {e}",
                gap_type="agent_registration_failure",
                source="agent_registry:register",
            )
            return False

    async def unregister(self, agent_id: str) -> bool:
        """Remove an agent from the registry (on shutdown).

        R-F1446: removes from the dedicated DB first, then Redis.
        """
        # R-F1446: remove from dedicated DB
        try:
            self._db_unregister(agent_id)
        except Exception as _db_e:
            logger.debug("[R-F1446] dedicated DB unregister failed for %s: %s", agent_id, _db_e)
        try:
            rs = self._get_redis()
            # Remove from hash by setting to empty and relying on cleanup
            await rs.hset(_AGENT_REGISTRY_KEY, {agent_id: ""})
            await rs.delete(f"{_AGENT_HEARTBEAT_KEY}{agent_id}")
            await rs.delete(f"{_AGENT_TASK_KEY}{agent_id}")
            logger.info("[R-F1160] agent unregistered: %s", agent_id)
            return True
        except Exception as e:
            logger.warning("[R-F1160] agent unregister failed for %s: %s", agent_id, e)
            return False

    # ── HEARTBEAT ─────────────────────────────────────────────────────────────

    async def tick_heartbeat(self, agent_id: str, current_task: Optional[str] = None) -> None:
        """Publish a heartbeat for this agent.

        Call this periodically (every 30-60s) from the agent's main loop.
        If current_task is provided, also updates the agent's current task.

        R-F1446: writes to the dedicated DB first (fast, no lock contention),
        then also updates Redis/state_store for backward compatibility.
        """
        now = time.time()
        # R-F1446: write to dedicated DB first (fast, no lock contention)
        try:
            self._db_tick_heartbeat(agent_id, now, current_task=current_task)
        except Exception as _db_e:
            logger.debug("[R-F1446] dedicated DB heartbeat failed for %s: %s", agent_id, _db_e)
        # Also write to Redis/state_store for backward compatibility
        try:
            rs = self._get_redis()
            await rs.set(f"{_AGENT_HEARTBEAT_KEY}{agent_id}", str(now))
            if current_task is not None:
                await rs.set(f"{_AGENT_TASK_KEY}{agent_id}", current_task)
                # Also update the registry entry
                all_entries = await rs.hgetall(_AGENT_REGISTRY_KEY)
                raw_entry = all_entries.get(agent_id) if all_entries else None
                if raw_entry:
                    try:
                        entry = json.loads(raw_entry) if isinstance(raw_entry, str) else raw_entry
                        entry["current_task"] = current_task
                        entry["last_heartbeat"] = now
                        await rs.hset(_AGENT_REGISTRY_KEY, {agent_id: json.dumps(entry)})
                    except (json.JSONDecodeError, TypeError):
                        pass
        except Exception as e:
            logger.debug("[R-F1160] heartbeat tick failed for %s: %s", agent_id, e)

    async def update_task(self, agent_id: str, current_task: str) -> None:
        """Update the agent's current task description.

        Call this whenever the agent starts or finishes a significant piece
        of work. Other agents can see this via list_active_agents().
        """
        await self.tick_heartbeat(agent_id, current_task=current_task)
        logger.info("[R-F1160] agent %s now: %s", agent_id, current_task)

    # ── AWARENESS ─────────────────────────────────────────────────────────────

    async def list_active_agents(self, include_stale: bool = False) -> list[dict]:
        """List all registered agents and their current state.

        Returns a list of dicts with keys:
          agent_id, agent_type, current_task, status, last_heartbeat,
          heartbeat_age_s, metadata

        Agents whose heartbeat is older than _AGENT_STALE_THRESHOLD_S
        are marked as status="stale" (or excluded if include_stale=False).

        R-F1446: reads from the dedicated DB first (always available, no
        lock contention), falls back to Redis/state_store if empty.
        """
        agents: list[dict] = []
        now = time.time()

        # R-F1446: read from dedicated DB first
        try:
            db_agents = self._db_list_agents()
            if db_agents:
                for entry in db_agents:
                    last_hb = entry.get("last_heartbeat", 0)
                    age = now - float(last_hb) if last_hb else float("inf")
                    entry["heartbeat_age_s"] = round(age, 1)
                    if age > _AGENT_STALE_THRESHOLD_S:
                        entry["status"] = "stale"
                        if not include_stale:
                            continue
                    agents.append(entry)
                agents.sort(key=lambda a: a.get("last_heartbeat", 0), reverse=True)
                return agents
        except Exception as _db_e:
            logger.debug("[R-F1446] dedicated DB list failed: %s", _db_e)

        # Fallback: read from Redis/state_store
        try:
            rs = self._get_redis()
            raw_entries = await rs.hgetall(_AGENT_REGISTRY_KEY)
            if not raw_entries:
                return agents

            for agent_id, raw in raw_entries.items():
                # Skip empty entries (unregistered agents that were set to "")
                if not raw:
                    continue
                try:
                    entry = json.loads(raw) if isinstance(raw, str) else raw
                except (json.JSONDecodeError, TypeError):
                    continue

                if not isinstance(entry, dict):
                    continue

                last_hb = entry.get("last_heartbeat", 0)
                age = now - float(last_hb) if last_hb else float("inf")
                entry["heartbeat_age_s"] = round(age, 1)

                if age > _AGENT_STALE_THRESHOLD_S:
                    entry["status"] = "stale"
                    if not include_stale:
                        continue

                agents.append(entry)

            agents.sort(key=lambda a: a.get("last_heartbeat", 0), reverse=True)
        except Exception as e:
            logger.warning("[R-F1160] list_active_agents failed: %s", e)

        return agents

    async def get_agent_status(self, agent_id: str) -> Optional[dict]:
        """Get the status of a specific agent.

        Returns None if the agent is not registered.

        R-F1446: reads from the dedicated DB first, falls back to Redis.
        """
        # R-F1446: read from dedicated DB first
        try:
            db_entry = self._db_get_agent(agent_id)
            if db_entry is not None:
                now = time.time()
                last_hb = db_entry.get("last_heartbeat", 0)
                db_entry["heartbeat_age_s"] = round(now - float(last_hb), 1) if last_hb else None
                if db_entry["heartbeat_age_s"] and db_entry["heartbeat_age_s"] > _AGENT_STALE_THRESHOLD_S:
                    db_entry["status"] = "stale"
                return db_entry
        except Exception as _db_e:
            logger.debug("[R-F1446] dedicated DB get failed for %s: %s", agent_id, _db_e)

        # Fallback: read from Redis/state_store
        try:
            rs = self._get_redis()
            all_entries = await rs.hgetall(_AGENT_REGISTRY_KEY)
            raw = all_entries.get(agent_id) if all_entries else None
            if not raw:
                return None
            entry = json.loads(raw) if isinstance(raw, str) else raw
            now = time.time()
            last_hb = entry.get("last_heartbeat", 0)
            entry["heartbeat_age_s"] = round(now - float(last_hb), 1) if last_hb else None
            if entry["heartbeat_age_s"] and entry["heartbeat_age_s"] > _AGENT_STALE_THRESHOLD_S:
                entry["status"] = "stale"
            return entry
        except Exception as e:
            logger.debug("[R-F1160] get_agent_status failed for %s: %s", agent_id, e)
            return None

    # ── GAP DECONFLICTION ─────────────────────────────────────────────────────

    async def claim_gap(self, gap_id: str, agent_id: str) -> bool:
        """Claim a gap for fixing.

        Uses a hash-based approach for atomicity:
        1. Read the current claim from the hash
        2. If already claimed by another agent, reject
        3. If unclaimed or claimed by us, write our claim with TTL

        Note: this is NOT fully atomic (check-then-set race exists), but
        the TTL-based expiry means stale claims auto-clear. In practice,
        the race window is tiny and the worst case is two agents fixing
        the same gap — wasteful but not dangerous.

        Returns True if the claim succeeded (gap was not already claimed).
        Returns False if another agent already claimed this gap.
        """
        try:
            rs = self._get_redis()
            key = f"{_AGENT_GAP_CLAIM_PREFIX}{gap_id}"
            # Check if already claimed
            existing = await rs.get(key)
            if existing:
                claiming_agent = existing.decode("utf-8") if isinstance(existing, bytes) else str(existing)
                if claiming_agent != agent_id:
                    logger.info(
                        "[R-F1160] gap %s already claimed by %s — %s cannot claim",
                        gap_id, claiming_agent, agent_id,
                    )
                    return False
                # Same agent re-claiming — refresh TTL
                await rs.expire(key, _GAP_CLAIM_TTL_S)
                return True

            # Not claimed — write our claim
            await rs.set(key, agent_id)
            await rs.expire(key, _GAP_CLAIM_TTL_S)
            logger.info("[R-F1160] gap %s claimed by %s", gap_id, agent_id)
            return True
        except Exception as e:
            logger.debug("[R-F1160] claim_gap failed: %s", e)
            return True  # fail open — don't block work on registry errors

    async def release_gap(self, gap_id: str, agent_id: str) -> None:
        """Release a gap claim (after fixing or abandoning)."""
        try:
            rs = self._get_redis()
            await rs.delete(f"{_AGENT_GAP_CLAIM_PREFIX}{gap_id}")
            logger.info("[R-F1160] gap %s released by %s", gap_id, agent_id)
        except Exception as e:
            logger.debug("[R-F1160] release_gap failed: %s", e)

    async def is_gap_claimed(self, gap_id: str) -> Optional[str]:
        """Check if a gap is already claimed.

        Returns the agent_id that claimed it, or None if unclaimed.
        """
        try:
            rs = self._get_redis()
            raw = await rs.get(f"{_AGENT_GAP_CLAIM_PREFIX}{gap_id}")
            if raw:
                return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            return None
        except Exception:
            return None

    # ── AGENT-TO-AGENT MESSAGING ──────────────────────────────────────────────

    async def send_message(
        self,
        from_agent: str,
        to_agent: str,
        payload: dict,
    ) -> bool:
        """Send a structured message from one agent to another.

        The target agent can read its messages via read_messages().

        Args:
            from_agent: The sender's agent_id
            to_agent: The recipient's agent_id (use "*" for broadcast to all)
            payload: Dict with message content. Should include at minimum:
                     {"type": "...", "data": {...}}

        Returns True if the message was queued.
        """
        try:
            rs = self._get_redis()
            message = {
                "from": from_agent,
                "to": to_agent,
                "payload": payload,
                "timestamp": time.time(),
                "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            }
            key = f"{_AGENT_MESSAGE_PREFIX}{to_agent}"
            await rs.lpush(key, json.dumps(message))
            await rs.ltrim(key, 0, _MAX_MESSAGES_PER_AGENT - 1)
            logger.info(
                "[R-F1160] message sent: %s → %s (type=%s)",
                from_agent, to_agent, payload.get("type", "unknown"),
            )
            return True
        except Exception as e:
            logger.debug("[R-F1160] send_message failed: %s", e)
            return False

    async def read_messages(self, agent_id: str, mark_read: bool = True) -> list[dict]:
        """Read all pending messages for this agent.

        Args:
            agent_id: The recipient's agent_id
            mark_read: If True, removes messages from the queue after reading

        Returns a list of message dicts, newest first.
        """
        try:
            rs = self._get_redis()
            key = f"{_AGENT_MESSAGE_PREFIX}{agent_id}"
            if mark_read:
                raw_messages = await rs.lrange(key, 0, _MAX_MESSAGES_PER_AGENT - 1)
                await rs.delete(key)
            else:
                raw_messages = await rs.lrange(key, 0, _MAX_MESSAGES_PER_AGENT - 1)

            messages = []
            for raw in raw_messages:
                try:
                    msg = json.loads(raw) if isinstance(raw, str) else raw
                    messages.append(msg)
                except (json.JSONDecodeError, TypeError):
                    continue
            return messages
        except Exception as e:
            logger.debug("[R-F1160] read_messages failed for %s: %s", agent_id, e)
            return []

    async def broadcast_message(self, from_agent: str, payload: dict) -> bool:
        """Broadcast a message to ALL agents.

        Each agent reads broadcast messages via read_messages("*").
        """
        return await self.send_message(from_agent, "*", payload)

    # ── ADMIN / DIAGNOSTIC ────────────────────────────────────────────────────

    async def get_registry_stats(self) -> dict:
        """Get statistics about the agent registry."""
        try:
            agents = await self.list_active_agents(include_stale=True)
            active = [a for a in agents if a.get("status") == "active"]
            stale = [a for a in agents if a.get("status") == "stale"]
            by_type: dict[str, int] = {}
            for a in agents:
                at = a.get("agent_type", "unknown")
                by_type[at] = by_type.get(at, 0) + 1

            result = {
                "total_agents": len(agents),
                "active_agents": len(active),
                "stale_agents": len(stale),
                "by_type": by_type,
                "agents": [
                    {
                        "agent_id": a.get("agent_id"),
                        "agent_type": a.get("agent_type"),
                        "current_task": a.get("current_task"),
                        "status": a.get("status"),
                        "heartbeat_age_s": a.get("heartbeat_age_s"),
                    }
                    for a in agents
                ],
            }
            # R-F1166 — wire success to brain
            wire_success(
                module="agent_registry",
                summary=f"Registry stats: {result['active_agents']} active, {result['stale_agents']} stale",
                source_id="agent_registry:get_registry_stats",
            )
            return result
        except Exception as e:
            logger.warning("get_registry_stats failed: %s", e)
            wire_failure(
                module="agent_registry",
                detail=f"get_registry_stats failed: {e}",
                gap_type="agent_registry_failure",
                source="agent_registry:get_registry_stats",
            )
            return {"total_agents": 0, "active_agents": 0, "stale_agents": 0, "by_type": {}, "agents": []}

    # ── R-F1233: Vault awareness ────────────────────────────────────────────────

    async def get_pending_signups(self, limit: int = 20) -> list[dict]:
        """Get pending signup tasks from the agent signup vault.

        Any agent can call this to see what signups need attention.
        Returns entries with status='pending', newest first.
        """
        try:
            from .agent_signup_vault import get_vault
            vault = get_vault()
            return vault.list(status="pending", sort_by="created_at", sort_dir="asc", limit=limit)
        except Exception as e:
            logger.debug("[R-F1160] get_pending_signups failed: %s", e)
            return []

    async def get_vault_summary(self) -> dict:
        """Get a summary of the agent signup vault for agent awareness.

        Agents can include this in their heartbeat to show what they know
        about the signup landscape.
        """
        try:
            from .agent_signup_vault import get_vault
            vault = get_vault()
            stats = vault.stats()
            return {
                "total_sites": stats.get("total", 0),
                "pending": stats.get("by_status", {}).get("pending", 0),
                "registered": stats.get("by_status", {}).get("registered", 0),
                "verified": stats.get("by_status", {}).get("verified", 0),
                "failed": stats.get("by_status", {}).get("failed", 0),
                "stale_unverified": stats.get("stale_unverified", 0),
            }
        except Exception as e:
            logger.debug("[R-F1160] get_vault_summary failed: %s", e)
            return {"total_sites": 0, "pending": 0, "registered": 0, "verified": 0, "failed": 0, "stale_unverified": 0}

    async def notify_agents_about_vault(self, event: str, site_id: str, agent_id: str = "system") -> None:
        """Broadcast a vault event to all agents so they stay aware.

        Called whenever a vault entry is created or updated.
        Agents receive this as a message and can react accordingly.
        """
        try:
            await self.broadcast_message("vault", {
                "type": "vault_event",
                "event": event,
                "site_id": site_id,
                "timestamp": time.time(),
                "source_agent": agent_id,
            })
        except Exception as e:
            logger.debug("[R-F1160] notify_agents_about_vault failed: %s", e)

    async def cleanup_stale_agents(self) -> int:
        """Remove agents that haven't sent a heartbeat in too long.

        Returns the number of agents cleaned up.
        """
        try:
            rs = self._get_redis()
            cleaned = 0
            raw_entries = await rs.hgetall(_AGENT_REGISTRY_KEY)
            if not raw_entries:
                return 0

            now = time.time()
            for agent_id, raw in raw_entries.items():
                try:
                    entry = json.loads(raw) if isinstance(raw, str) else raw
                except (json.JSONDecodeError, TypeError):
                    continue
                last_hb = entry.get("last_heartbeat", 0)
                age = now - float(last_hb) if last_hb else float("inf")
                if age > _AGENT_STALE_THRESHOLD_S * 2:  # 2x threshold = definitely dead
                    await self.unregister(agent_id)
                    cleaned += 1
        except Exception as e:
            logger.warning("[R-F1160] cleanup_stale_agents failed: %s", e)

        if cleaned:
            logger.info("[R-F1160] cleaned up %d stale agent(s)", cleaned)
        return cleaned
