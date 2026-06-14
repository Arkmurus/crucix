"""R-F1160 — Capability tests for the multi-agent registry.

Tests:
1. Agent registration and unregistration
2. Heartbeat tick and task updates
3. Listing active agents (including stale detection)
4. Gap claiming and deconfliction
5. Agent-to-agent messaging
6. Registry stats
7. Cleanup of stale agents
8. Error resilience (Redis unavailable)
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from aria_service.intel.agent_registry import AgentRegistry


class _MockRedis:
    """Async stand-in that mimics the redis_store interface.

    Supports: get, set, delete, expire, hset, hgetall, lpush, lrange, ltrim
    All data stored in-memory dicts/lists.
    """

    def __init__(self):
        self._data: dict[str, str | bytes] = {}
        self._hash: dict[str, dict[str, str]] = {}
        self._lists: dict[str, list[str]] = {}
        self._ttl: dict[str, float] = {}

    async def get(self, key):
        raw = self._data.get(key)
        if raw is None:
            return None
        # Check TTL
        expiry = self._ttl.get(key)
        if expiry and time.time() > expiry:
            self._data.pop(key, None)
            self._ttl.pop(key, None)
            return None
        return raw if isinstance(raw, bytes) else raw.encode("utf-8") if isinstance(raw, str) else raw

    async def set(self, key, value):
        self._data[key] = value
        return True

    async def delete(self, key):
        self._data.pop(key, None)
        self._ttl.pop(key, None)
        self._lists.pop(key, None)
        return True

    async def expire(self, key, seconds):
        self._ttl[key] = time.time() + seconds
        return True

    async def hset(self, key, mapping: dict):
        if key not in self._hash:
            self._hash[key] = {}
        self._hash[key].update(mapping)
        return True

    async def hgetall(self, key):
        return self._hash.get(key, {})

    async def lpush(self, key, value):
        if key not in self._lists:
            self._lists[key] = []
        self._lists[key].insert(0, value)
        return True

    async def lrange(self, key, start, stop):
        lst = self._lists.get(key, [])
        return lst[start:stop + 1] if stop >= 0 else lst[start:]

    async def ltrim(self, key, start, stop):
        lst = self._lists.get(key, [])
        if stop >= 0:
            self._lists[key] = lst[start:stop + 1]
        else:
            self._lists[key] = lst[start:]
        return True


def _make_registry() -> AgentRegistry:
    """Create an AgentRegistry with a mock Redis backend.

    Clears the dedicated DB so each test starts fresh.
    """
    reg = AgentRegistry()
    reg._db_clear()  # R-F1446: clear dedicated DB for test isolation
    mock = _MockRedis()
    reg._get_redis = lambda: mock  # not async — returns the mock directly
    reg._redis = mock  # also set _redis directly so _get_redis returns it
    return reg


async def test_register_and_unregister():
    """Agent can register and unregister."""
    reg = _make_registry()

    ok = await reg.register("test_agent", "test_type", "testing")
    assert ok, "Registration should succeed"

    agents = await reg.list_active_agents(include_stale=True)
    assert len(agents) == 1, f"Expected 1 agent, got {len(agents)}"
    assert agents[0]["agent_id"] == "test_agent"
    assert agents[0]["agent_type"] == "test_type"
    assert agents[0]["current_task"] == "testing"
    assert agents[0]["status"] == "active"

    ok = await reg.unregister("test_agent")
    assert ok, "Unregistration should succeed"

    agents = await reg.list_active_agents(include_stale=True)
    assert len(agents) == 0, f"Expected 0 agents after unregister, got {len(agents)}"


async def test_heartbeat_and_task_update():
    """Heartbeat updates last_heartbeat and current_task."""
    reg = _make_registry()

    await reg.register("worker", "worker_type", "idle")
    await reg.tick_heartbeat("worker", current_task="working hard")

    status = await reg.get_agent_status("worker")
    assert status is not None
    assert status["current_task"] == "working hard"
    assert status["heartbeat_age_s"] is not None
    assert status["heartbeat_age_s"] < 2.0  # just ticked

    await reg.unregister("worker")


async def test_list_active_filters_stale():
    """list_active_agents excludes stale agents by default."""
    reg = _make_registry()
    mock = reg._redis

    await reg.register("active_agent", "type_a", "working")
    await reg.register("stale_agent", "type_b", "sleeping")

    # Manually set stale agent's heartbeat to ancient time
    old_time = time.time() - 10000  # way beyond 300s threshold
    reg._db_set_heartbeat("stale_agent", old_time)  # R-F1446: also update dedicated DB
    await mock.set("crucix:agent:heartbeat:stale_agent", str(old_time))
    entry = {
        "agent_id": "stale_agent",
        "agent_type": "type_b",
        "current_task": "sleeping",
        "status": "active",
        "registered_at": old_time,
        "last_heartbeat": old_time,
        "metadata": {},
    }
    await mock.hset("crucix:agent:registry", {"stale_agent": json.dumps(entry)})

    # Without include_stale — only active agents
    agents = await reg.list_active_agents(include_stale=False)
    assert len(agents) == 1, f"Expected 1 active agent, got {len(agents)}"
    assert agents[0]["agent_id"] == "active_agent"

    # With include_stale — both agents, stale one marked
    agents = await reg.list_active_agents(include_stale=True)
    assert len(agents) == 2, f"Expected 2 agents, got {len(agents)}"
    stale = [a for a in agents if a["agent_id"] == "stale_agent"]
    assert len(stale) == 1
    assert stale[0]["status"] == "stale"

    await reg.unregister("active_agent")
    await reg.unregister("stale_agent")


async def test_gap_claim_and_deconfliction():
    """Gap claiming prevents duplicate work."""
    reg = _make_registry()

    # First agent claims the gap
    claimed = await reg.claim_gap("gap_123", "agent_a")
    assert claimed, "First claim should succeed"

    # Second agent tries to claim the same gap
    claimed = await reg.claim_gap("gap_123", "agent_b")
    assert not claimed, "Second claim should be rejected"

    # Check who claimed it
    who = await reg.is_gap_claimed("gap_123")
    assert who == "agent_a", f"Expected agent_a, got {who}"

    # Release and re-claim
    await reg.release_gap("gap_123", "agent_a")
    who = await reg.is_gap_claimed("gap_123")
    assert who is None, "Gap should be released"

    claimed = await reg.claim_gap("gap_123", "agent_b")
    assert claimed, "Claim after release should succeed"

    await reg.release_gap("gap_123", "agent_b")


async def test_gap_claim_same_agent_renews():
    """Same agent re-claiming refreshes the TTL."""
    reg = _make_registry()
    mock = reg._redis

    await reg.claim_gap("gap_renew", "agent_a")
    key = "crucix:agent:gap_claim:gap_renew"

    # Set a short TTL
    await mock.expire(key, 2)

    # Re-claim by same agent — should extend TTL
    await reg.claim_gap("gap_renew", "agent_a")

    # TTL should now be the full _GAP_CLAIM_TTL_S (3600)
    expiry = mock._ttl.get(key)
    assert expiry is not None
    remaining = expiry - time.time()
    assert remaining > 3000, f"TTL too short: {remaining:.0f}s (expected >3000s)"

    await reg.release_gap("gap_renew", "agent_a")


async def test_agent_messaging():
    """Agents can send and receive messages."""
    reg = _make_registry()

    # Send a message
    sent = await reg.send_message("sender", "receiver", {
        "type": "task_update",
        "data": {"task_id": "abc", "status": "done"},
    })
    assert sent, "Send should succeed"

    # Read messages
    messages = await reg.read_messages("receiver")
    assert len(messages) == 1, f"Expected 1 message, got {len(messages)}"
    assert messages[0]["from"] == "sender"
    assert messages[0]["to"] == "receiver"
    assert messages[0]["payload"]["type"] == "task_update"
    assert messages[0]["payload"]["data"]["task_id"] == "abc"

    # Messages should be cleared after reading
    messages = await reg.read_messages("receiver")
    assert len(messages) == 0, "Messages should be cleared after read"


async def test_broadcast_messaging():
    """Broadcast messages go to all agents."""
    reg = _make_registry()

    await reg.broadcast_message("announcer", {"type": "announcement", "text": "hello"})

    # All agents can read broadcast messages
    msgs_a = await reg.read_messages("*")
    assert len(msgs_a) == 1
    assert msgs_a[0]["from"] == "announcer"

    msgs_b = await reg.read_messages("*")
    assert len(msgs_b) == 0, "Broadcast messages should be cleared after read"


async def test_registry_stats():
    """get_registry_stats returns correct counts."""
    reg = _make_registry()

    await reg.register("agent_1", "type_a", "task_1")
    await reg.register("agent_2", "type_b", "task_2")

    stats = await reg.get_registry_stats()
    assert stats["total_agents"] == 2
    assert stats["active_agents"] == 2
    assert stats["by_type"]["type_a"] == 1
    assert stats["by_type"]["type_b"] == 1

    await reg.unregister("agent_1")
    await reg.unregister("agent_2")


async def test_cleanup_stale_agents():
    """Stale agents are cleaned up."""
    reg = _make_registry()
    mock = reg._redis

    await reg.register("fresh_agent", "type_a", "working")
    await reg.register("dead_agent", "type_b", "gone")

    # Manually set dead agent's heartbeat to ancient
    old_time = time.time() - 10000
    await mock.set("crucix:agent:heartbeat:dead_agent", str(old_time))
    entry = {
        "agent_id": "dead_agent",
        "agent_type": "type_b",
        "current_task": "gone",
        "status": "active",
        "registered_at": old_time,
        "last_heartbeat": old_time,
        "metadata": {},
    }
    await mock.hset("crucix:agent:registry", {"dead_agent": json.dumps(entry)})

    cleaned = await reg.cleanup_stale_agents()
    assert cleaned == 1, f"Expected 1 cleaned, got {cleaned}"

    agents = await reg.list_active_agents(include_stale=True)
    assert len(agents) == 1
    assert agents[0]["agent_id"] == "fresh_agent"

    await reg.unregister("fresh_agent")


async def test_error_resilience():
    """Registry operations fail open when Redis is unavailable."""
    reg = AgentRegistry()
    reg._db_clear()  # R-F1446: start with clean dedicated DB

    # Make _get_redis raise an exception (sync, since _get_redis is now sync)
    def _broken_redis():
        raise ConnectionError("Redis unavailable")

    reg._get_redis = _broken_redis

    ok = await reg.register("ghost", "ghost_type", "nowhere")
    # R-F1475: register() returns True when the dedicated DB write succeeds,
    # even if Redis is down. The DB is the source of truth.
    assert ok is True, "Registration should succeed when DB write works (Redis is optional)"

    # R-F1446: dedicated DB has the agent (register writes to it first)
    agents = await reg.list_active_agents()
    assert len(agents) == 1, "Dedicated DB should have the agent"
    assert agents[0]["agent_id"] == "ghost"

    claimed = await reg.claim_gap("ghost_gap", "ghost_agent")
    assert claimed, "Claim should succeed (DB-backed)"

    who = await reg.is_gap_claimed("ghost_gap")
    # R-F1555: is_gap_claimed reads from the dedicated DB first, so it
    # returns the claiming agent even when Redis is down
    assert who == "ghost_agent", f"is_gap_claimed should return 'ghost_agent' from DB, got {who!r}"

    sent = await reg.send_message("ghost", "*", {"type": "test"})
    # R-F1555: send_message writes to the dedicated DB first, so it succeeds
    # even when Redis is down
    assert sent is True, "Send should succeed (DB-backed)"

    msgs = await reg.read_messages("ghost")
    # R-F1555: read_messages reads from the dedicated DB first
    assert len(msgs) == 1, f"Read should return 1 message from DB, got {len(msgs)}"

    stats = await reg.get_registry_stats()
    # R-F1446: dedicated DB has the agent, so stats reflect it
    assert stats["total_agents"] == 1
    assert stats["active_agents"] == 1
