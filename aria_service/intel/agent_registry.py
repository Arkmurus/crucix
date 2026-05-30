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
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("aria.agent_registry")

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


class AgentRegistry:
    """Multi-agent registry with heartbeat, awareness, deconfliction, messaging."""

    def __init__(self) -> None:
        self._redis = None  # lazy-loaded

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
    ) -> bool:
        """Register an agent in the registry.

        Args:
            agent_id: Unique identifier (e.g. "aria_coder", "gap_detector",
                     "claude_code_session_1")
            agent_type: Type of agent (e.g. "autonomous_coder", "gap_detector",
                       "research_engine", "student_brain", "claude_code")
            current_task: What the agent is doing right now
            metadata: Optional dict with additional info (version, pid, etc.)

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
        try:
            rs = self._get_redis()
            await rs.hset(_AGENT_REGISTRY_KEY, {agent_id: json.dumps(entry)})
            await rs.set(f"{_AGENT_HEARTBEAT_KEY}{agent_id}", str(now))
            await rs.set(f"{_AGENT_TASK_KEY}{agent_id}", current_task)
            logger.info(
                "[R-F1160] agent registered: %s (%s) — %s",
                agent_id, agent_type, current_task,
            )
            return True
        except Exception as e:
            logger.warning("[R-F1160] agent registration failed for %s: %s", agent_id, e)
            return False

    async def unregister(self, agent_id: str) -> bool:
        """Remove an agent from the registry (on shutdown)."""
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
        """
        try:
            rs = self._get_redis()
            now = time.time()
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
        """
        agents: list[dict] = []
        try:
            rs = self._get_redis()
            now = time.time()
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
        """
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
        agents = await self.list_active_agents(include_stale=True)
        active = [a for a in agents if a.get("status") == "active"]
        stale = [a for a in agents if a.get("status") == "stale"]
        by_type: dict[str, int] = {}
        for a in agents:
            at = a.get("agent_type", "unknown")
            by_type[at] = by_type.get(at, 0) + 1

        return {
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
