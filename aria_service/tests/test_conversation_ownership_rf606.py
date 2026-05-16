"""R-F606 (2026-05-16) — Conversation ownership enforcement tests.

Pre-R-F606 the conversation_store + /conversations routes had three holes
that let any authenticated user act on any other user's chat:

  1. `get_conversation(session_id)` read meta and session blindly.
  2. `rename_conversation(session_id, title)` didn't accept user_id at all.
  3. `delete_conversation(user_id, session_id)` zrem'd from the caller's
     own user-keyed set (no-op when wrong user) BUT then unconditionally
     deleted the target's meta + session keys.

R-F606 closes the loop:
  - Store-layer ownership check baked into get/rename/delete.
  - Route layer requires `user_id` query param and surfaces 404 on
     missing OR ownership mismatch (deliberately conflated so we don't
     leak conversation existence).

The matched Node-proxy patch pins user_id to the JWT-resolved userId so
clients can't pass a forged one.
"""
from __future__ import annotations

import asyncio
import pytest


# ── Fake redis backing store ────────────────────────────────────────────


class _FakeRS:
    """Minimal in-memory stub of the rs functions conversation_store uses."""

    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.json_blobs: dict[str, dict] = {}

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update({k: str(v) for k, v in mapping.items()})

    async def zadd(self, key, score, member):
        self.zsets.setdefault(key, {})[member] = float(score)

    async def zrem(self, key, member):
        z = self.zsets.get(key, {})
        if member in z:
            del z[member]
            return 1
        return 0

    async def zrevrange(self, key, start, end):
        z = self.zsets.get(key, {})
        members = sorted(z.items(), key=lambda kv: kv[1], reverse=True)
        return [m for m, _ in members[start : end + 1]]

    async def get_json(self, key):
        return self.json_blobs.get(key)

    async def set_json(self, key, value, ex=None):
        self.json_blobs[key] = value

    async def delete(self, key):
        self.hashes.pop(key, None)
        self.json_blobs.pop(key, None)
        self.zsets.pop(key, None)


@pytest.fixture
def fake_rs(monkeypatch):
    from aria_service.intel import conversation_store
    fake = _FakeRS()
    monkeypatch.setattr(conversation_store, "rs", fake)
    return fake


def _seed_convo(rs: _FakeRS, *, user_id: str, session_id: str, title: str = "T"):
    """Seed a conversation owned by user_id directly into the fake store."""
    rs.zsets[f"crucix:aria:conversations:{user_id}"] = {session_id: 1.0}
    rs.hashes[f"crucix:aria:conv:meta:{session_id}"] = {
        "title": title,
        "userId": user_id,
        "createdAt": "1.0",
        "updatedAt": "1.0",
        "messageCount": "2",
    }
    rs.json_blobs[f"crucix:aria:session:{session_id}"] = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hi back"},
        ],
    }


# ── Store-layer unit tests ──────────────────────────────────────────────


def test_get_conversation_right_user_returns_convo(fake_rs):
    from aria_service.intel import conversation_store

    _seed_convo(fake_rs, user_id="alice", session_id="s1", title="alice-chat")
    out = asyncio.run(conversation_store.get_conversation("s1", user_id="alice"))
    assert out is not None
    assert out["title"] == "alice-chat"
    assert out["session_id"] == "s1"
    assert len(out["messages"]) == 2


def test_get_conversation_wrong_user_returns_none(fake_rs):
    from aria_service.intel import conversation_store

    _seed_convo(fake_rs, user_id="alice", session_id="s1")
    out = asyncio.run(conversation_store.get_conversation("s1", user_id="bob"))
    assert out is None, "cross-user read MUST be blocked at the store layer"


def test_get_conversation_no_user_id_still_works_for_internal_calls(fake_rs):
    """Internal admin / backfill callers can pass user_id=None and get
    the convo. Route layer never does this — but back-compat matters."""
    from aria_service.intel import conversation_store

    _seed_convo(fake_rs, user_id="alice", session_id="s1")
    out = asyncio.run(conversation_store.get_conversation("s1"))
    assert out is not None and out["session_id"] == "s1"


def test_get_conversation_missing_session_returns_none(fake_rs):
    from aria_service.intel import conversation_store

    out = asyncio.run(conversation_store.get_conversation("ghost", user_id="alice"))
    assert out is None


def test_delete_conversation_wrong_user_is_noop(fake_rs):
    """Pre-R-F606 wrong-user delete *destroyed* meta+session anyway.
    Now it returns False and leaves all 3 keys intact."""
    from aria_service.intel import conversation_store

    _seed_convo(fake_rs, user_id="alice", session_id="s1")
    ok = asyncio.run(conversation_store.delete_conversation("bob", "s1"))
    assert ok is False
    # Meta + session keys MUST still exist
    assert fake_rs.hashes.get("crucix:aria:conv:meta:s1"), "meta wiped by wrong-user delete"
    assert fake_rs.json_blobs.get("crucix:aria:session:s1"), "session wiped by wrong-user delete"
    assert "s1" in fake_rs.zsets.get("crucix:aria:conversations:alice", {})


def test_delete_conversation_right_user_clears_all_three_keys(fake_rs):
    from aria_service.intel import conversation_store

    _seed_convo(fake_rs, user_id="alice", session_id="s1")
    ok = asyncio.run(conversation_store.delete_conversation("alice", "s1"))
    assert ok is True
    assert not fake_rs.hashes.get("crucix:aria:conv:meta:s1")
    assert not fake_rs.json_blobs.get("crucix:aria:session:s1")
    assert "s1" not in fake_rs.zsets.get("crucix:aria:conversations:alice", {})


def test_rename_conversation_wrong_user_is_noop(fake_rs):
    from aria_service.intel import conversation_store

    _seed_convo(fake_rs, user_id="alice", session_id="s1", title="orig")
    ok = asyncio.run(conversation_store.rename_conversation("s1", "PWN", user_id="bob"))
    assert ok is False
    assert fake_rs.hashes["crucix:aria:conv:meta:s1"]["title"] == "orig"


def test_rename_conversation_right_user_updates_title(fake_rs):
    from aria_service.intel import conversation_store

    _seed_convo(fake_rs, user_id="alice", session_id="s1", title="orig")
    ok = asyncio.run(conversation_store.rename_conversation("s1", "renamed", user_id="alice"))
    assert ok is True
    assert fake_rs.hashes["crucix:aria:conv:meta:s1"]["title"] == "renamed"


def test_rename_conversation_no_user_id_back_compat(fake_rs):
    """user_id=None path retained so admin/backfill callers don't break."""
    from aria_service.intel import conversation_store

    _seed_convo(fake_rs, user_id="alice", session_id="s1", title="orig")
    ok = asyncio.run(conversation_store.rename_conversation("s1", "admin-set"))
    assert ok is True
    assert fake_rs.hashes["crucix:aria:conv:meta:s1"]["title"] == "admin-set"


# ── Capability test: the original leak scenario ─────────────────────────


def test_capability_cross_user_read_is_blocked(fake_rs):
    """Operator scenario: alice runs `wa_group_Antonio` and bob, on the same
    deployment, guesses that session_id. Pre-R-F606 he gets the full
    message history. Now he gets None."""
    from aria_service.intel import conversation_store

    _seed_convo(fake_rs, user_id="alice", session_id="wa_group_Antonio", title="alice-priv")
    # Bob enumerates a likely session_id from the public WA-group convention
    leaked = asyncio.run(
        conversation_store.get_conversation("wa_group_Antonio", user_id="bob")
    )
    assert leaked is None, (
        "R-F606 ownership check failed: bob successfully read alice's chat by "
        "guessing the session_id"
    )


def test_capability_cross_user_delete_is_blocked(fake_rs):
    """Pre-R-F606 the wrong-user delete still nuked meta+session. Verify
    alice's data survives bob's attempted delete."""
    from aria_service.intel import conversation_store

    _seed_convo(fake_rs, user_id="alice", session_id="wa_group_Antonio")
    asyncio.run(conversation_store.delete_conversation("bob", "wa_group_Antonio"))

    # Alice can still load her conversation
    survived = asyncio.run(
        conversation_store.get_conversation("wa_group_Antonio", user_id="alice")
    )
    assert survived is not None, "R-F606: bob's spoofed delete destroyed alice's chat"
