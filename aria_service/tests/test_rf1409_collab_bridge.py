"""R-F1409 — server-mediated Claude<->ARIA collaboration bridge.

ONE ARIA, one mailbox on the server. These tests drive the REAL
collab_bridge.send / poll / cursor / drain_for_aria against an in-memory
fake of redis_store (isolated, deterministic), proving:
  - cursor-based delivery (a message is never consumed/lost by being read)
  - addressing (a reader only sees messages addressed to it)
  - drain_for_aria makes the one ARIA aware (brain absorb) and advances the
    cursor so each note is acted on exactly once (idempotent)
  - input validation
"""
from __future__ import annotations

import asyncio
import json

import pytest

from aria_service.intel import collab_bridge


class _FakeRedis:
    """Minimal in-memory stand-in for redis_store (lists + counters + kv)."""
    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.kv: dict[str, str] = {}
        self.counters: dict[str, int] = {}

    async def incr(self, key, amount=1, *, critical=False):
        self.counters[key] = self.counters.get(key, 0) + amount
        return self.counters[key]

    async def lpush(self, key, value, *, critical=False):
        self.lists.setdefault(key, []).insert(0, value)

    async def ltrim(self, key, start, stop):
        if key in self.lists:
            self.lists[key] = self.lists[key][start:stop + 1]

    async def lrange(self, key, start, stop):
        items = self.lists.get(key, [])
        # redis lrange is inclusive; -1 means end
        if stop == -1:
            stop = len(items)
        else:
            stop = stop + 1
        return items[start:stop]

    async def get_strict(self, key):
        # R-F4301 — mirror the REAL redis_store contract. `collab_bridge.get_cursor`
        # reads the drain cursor through get_strict precisely so an unreadable
        # store is distinguishable from an absent key; a fake that omits it makes
        # every read look like a store failure, which is the opposite of what this
        # fake is for. None here means genuinely absent, exactly as production.
        return self.kv.get(key)

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, ex=None, *, critical=False):
        self.kv[key] = value


@pytest.fixture
def fake_rs(monkeypatch):
    fr = _FakeRedis()
    monkeypatch.setattr(collab_bridge, "rs", fr)
    return fr


def _run(coro):
    return asyncio.run(coro)


# ── send + poll + cursor ───────────────────────────────────────────────────

def test_send_then_poll_addressed_reader(fake_rs):
    msg = _run(collab_bridge.send("claude", "aria", "do the thing"))
    assert msg["frm"] == "claude" and msg["to"] == "aria"
    assert msg["seq"] == 1 and msg["id"] == "cb_1"

    # aria sees it; claude does not
    to_aria = _run(collab_bridge.poll("aria"))
    assert len(to_aria) == 1 and to_aria[0]["text"] == "do the thing"
    assert _run(collab_bridge.poll("claude")) == []


def test_poll_is_cursor_based_not_consuming(fake_rs):
    _run(collab_bridge.send("claude", "aria", "first"))
    _run(collab_bridge.send("claude", "aria", "second"))

    # Reading does not consume — a second poll from seq 0 still returns both
    assert len(_run(collab_bridge.poll("aria", after_seq=0))) == 2
    assert len(_run(collab_bridge.poll("aria", after_seq=0))) == 2
    # after_seq advances the window
    after1 = _run(collab_bridge.poll("aria", after_seq=1))
    assert len(after1) == 1 and after1[0]["text"] == "second"


def test_bidirectional(fake_rs):
    _run(collab_bridge.send("claude", "aria", "to aria"))
    _run(collab_bridge.send("aria", "claude", "to claude"))
    assert [m["text"] for m in _run(collab_bridge.poll("aria"))] == ["to aria"]
    assert [m["text"] for m in _run(collab_bridge.poll("claude"))] == ["to claude"]


def test_send_validation(fake_rs):
    with pytest.raises(ValueError):
        _run(collab_bridge.send("claude", "bob", "x"))   # bad 'to'
    with pytest.raises(ValueError):
        _run(collab_bridge.send("claude", "aria", "   "))  # empty text


# ── drain_for_aria: awareness + idempotent cursor ──────────────────────────

def test_drain_absorbs_and_advances_cursor(fake_rs, monkeypatch):
    absorbed = []

    async def _fake_absorb(**kwargs):
        absorbed.append(kwargs)
        return {}

    async def _fake_signal(*a, **k):
        return None

    import aria_service.intel.brain_hook as bh
    monkeypatch.setattr(bh, "absorb", _fake_absorb)
    monkeypatch.setattr(bh, "_record_signal", _fake_signal)

    _run(collab_bridge.send("claude", "aria", "fix the sanctions routing"))
    _run(collab_bridge.send("claude", "aria", "and the WA timeout"))

    res = _run(collab_bridge.drain_for_aria())
    assert res["drained"] == 2
    assert len(absorbed) == 2
    assert "fix the sanctions routing" in absorbed[0]["detail"]
    assert absorbed[0]["module"] == "collab_bridge"

    # Idempotent: a second drain finds nothing new (cursor advanced)
    res2 = _run(collab_bridge.drain_for_aria())
    assert res2["drained"] == 0
    assert len(absorbed) == 2

    # A new note after the cursor IS drained
    _run(collab_bridge.send("claude", "aria", "third"))
    res3 = _run(collab_bridge.drain_for_aria())
    assert res3["drained"] == 1
    assert len(absorbed) == 3


def test_drain_ignores_aria_to_claude(fake_rs, monkeypatch):
    async def _fake_absorb(**kwargs):
        return {}
    async def _fake_signal(*a, **k):
        return None
    import aria_service.intel.brain_hook as bh
    monkeypatch.setattr(bh, "absorb", _fake_absorb)
    monkeypatch.setattr(bh, "_record_signal", _fake_signal)

    _run(collab_bridge.send("aria", "claude", "a reply to claude"))
    res = _run(collab_bridge.drain_for_aria())
    assert res["drained"] == 0  # aria->claude is not inbound for aria


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
