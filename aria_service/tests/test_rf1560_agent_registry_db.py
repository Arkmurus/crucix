"""R-F1560 — Capability tests: gap-deconfliction + agent-messaging work
WITHOUT Redis, on the dedicated SQLite path alone.

Context
───────
The state backend is SQLite now (Upstash/Redis cancelled per CLAUDE.md §6/§16).
`register()`/heartbeat were given an independent dedicated-DB path under
R-F1446/R-F1475, and R-F1555 mirrored that for the four
gap-deconfliction + messaging methods:

    claim_gap / release_gap / send_message / read_messages

These tests prove those four methods STILL deconflict and deliver when Redis
is completely unavailable. The existing test_rf1160 suite always wires a
working in-memory mock Redis, so it never exercised the DB-only path. Here we
force every Redis access to raise (and, in a second variant, return None) and
assert the user-visible contracts hold purely on the dedicated DB:

  - claim_gap returns bool, and a second claim from another agent is DENIED
  - release_gap frees the gap so it can be re-claimed
  - send_message + read_messages round-trip a structured payload
  - return-type contracts are unchanged (bool / list)
"""
from __future__ import annotations

import time

import pytest

from aria_service.intel.agent_registry import AgentRegistry


class _BrokenRedis:
    """A redis_store stand-in whose every method raises — simulates Redis down.

    Returned by _get_redis() so the code under test exercises ONLY the
    dedicated-DB path; any reliance on Redis would surface as an exception.
    """

    def __getattr__(self, _name):
        async def _boom(*_a, **_kw):
            raise ConnectionError("simulated Redis outage (R-F1560)")
        return _boom


def _make_registry_no_redis(broken: bool = True) -> AgentRegistry:
    """AgentRegistry whose Redis is unavailable.

    broken=True  → _get_redis() returns a stand-in that raises on every call.
    broken=False → _get_redis() returns None (the other "unavailable" shape).

    The dedicated DB is cleared so each test starts fresh.
    """
    reg = AgentRegistry()
    reg._db_clear()  # isolate from any other test / prior run
    if broken:
        sentinel = _BrokenRedis()
        reg._redis = sentinel
        reg._get_redis = lambda: sentinel
    else:
        reg._redis = None
        # Force the lazy loader to keep returning None instead of importing
        # the real redis_store module.
        reg._get_redis = lambda: None
    return reg


@pytest.mark.asyncio
async def test_claim_gap_deconflicts_without_redis():
    """First claim wins, second agent is denied — on the DB path only."""
    reg = _make_registry_no_redis(broken=True)

    first = await reg.claim_gap("gap_rf1560_a", "agent_a")
    assert first is True, "First claim must succeed on the dedicated DB"
    assert isinstance(first, bool)

    second = await reg.claim_gap("gap_rf1560_a", "agent_b")
    assert second is False, "Second agent must be DENIED — deconfliction held by DB"
    assert isinstance(second, bool)

    who = await reg.is_gap_claimed("gap_rf1560_a")
    assert who == "agent_a", f"DB must report the original claimant, got {who!r}"

    reg.close()


@pytest.mark.asyncio
async def test_release_gap_frees_claim_without_redis():
    """release_gap frees the gap so another agent can claim it — DB path only."""
    reg = _make_registry_no_redis(broken=True)

    assert await reg.claim_gap("gap_rf1560_b", "agent_a") is True
    # Another agent is blocked while claimed.
    assert await reg.claim_gap("gap_rf1560_b", "agent_b") is False

    await reg.release_gap("gap_rf1560_b", "agent_a")
    assert await reg.is_gap_claimed("gap_rf1560_b") is None, "Gap must be free after release"

    # Now agent_b can take it.
    assert await reg.claim_gap("gap_rf1560_b", "agent_b") is True
    assert await reg.is_gap_claimed("gap_rf1560_b") == "agent_b"

    reg.close()


@pytest.mark.asyncio
async def test_message_round_trip_without_redis():
    """send_message + read_messages round-trip a payload on the DB path only."""
    reg = _make_registry_no_redis(broken=True)

    sent = await reg.send_message("sender", "receiver", {
        "type": "incoming_gap",
        "data": {"gap_id": "xyz", "module": "researcher.py"},
    })
    assert sent is True, "send_message must succeed on the dedicated DB"
    assert isinstance(sent, bool)

    msgs = await reg.read_messages("receiver")
    assert isinstance(msgs, list), "read_messages must return a list"
    assert len(msgs) == 1, f"Expected exactly 1 delivered message, got {len(msgs)}"
    assert msgs[0]["from"] == "sender"
    assert msgs[0]["to"] == "receiver"
    assert msgs[0]["payload"]["type"] == "incoming_gap"
    assert msgs[0]["payload"]["data"]["gap_id"] == "xyz"

    # Already-read messages are not redelivered.
    again = await reg.read_messages("receiver")
    assert again == [], "Read messages must not be redelivered"

    reg.close()


@pytest.mark.asyncio
async def test_broadcast_round_trip_without_redis():
    """Broadcast (to_agent='*') is readable via read_messages on the DB path."""
    reg = _make_registry_no_redis(broken=True)

    assert await reg.send_message("announcer", "*", {"type": "announce", "text": "hi"}) is True

    msgs = await reg.read_messages("*")
    assert isinstance(msgs, list)
    assert len(msgs) == 1
    assert msgs[0]["from"] == "announcer"
    assert msgs[0]["payload"]["type"] == "announce"

    reg.close()


@pytest.mark.asyncio
async def test_deconfliction_holds_when_redis_returns_none():
    """The 'Redis is None' shape of unavailability also deconflicts via the DB.

    A None redis would raise AttributeError on any await rs.<method>(); this
    asserts the DB path runs first and the contracts still hold.
    """
    reg = _make_registry_no_redis(broken=False)

    assert await reg.claim_gap("gap_rf1560_c", "agent_a") is True
    assert await reg.claim_gap("gap_rf1560_c", "agent_b") is False
    assert await reg.is_gap_claimed("gap_rf1560_c") == "agent_a"

    assert await reg.send_message("a", "b", {"type": "ping"}) is True
    msgs = await reg.read_messages("b")
    assert len(msgs) == 1 and msgs[0]["payload"]["type"] == "ping"

    reg.close()
