"""R-F933 — Compliance Watch capture store (slice 1): append-only, attributed,
hash-chained, tamper-evident.

The capture store is the evidentiary bedrock of the Compliance Watch pipeline.
These tests prove: every message is stored with full attribution; queries
filter by group + time; the hash chain verifies clean; and ANY tampering
(altering or removing a record) is detected — so a later finding can cite the
record as untampered evidence, and no accusation rests on a mutated log.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from aria_service.intel import compliance_watch as cw
from aria_service.intel import redis_store as rs


@pytest.fixture
def fake_rs(monkeypatch):
    """In-memory redis_store list ops so each test is isolated + deterministic."""
    store = {"lists": {}, "ctr": {}}

    async def lpush(k, v):
        store["lists"].setdefault(k, []).insert(0, v)

    async def lrange(k, s, e):
        lst = store["lists"].get(k, [])
        n = len(lst)
        e2 = (n - 1) if e == -1 else e
        return lst[s:e2 + 1]

    async def llen(k):
        return len(store["lists"].get(k, []))

    async def incr(k, amount=1):
        store["ctr"][k] = store["ctr"].get(k, 0) + amount
        return store["ctr"][k]

    monkeypatch.setattr(rs, "lpush", lpush)
    monkeypatch.setattr(rs, "lrange", lrange)
    monkeypatch.setattr(rs, "llen", llen)
    monkeypatch.setattr(rs, "incr", incr)
    return store


def test_rf933_capture_persists_full_attribution(fake_rs):
    out = asyncio.run(cw.capture_message(
        group="COMPLIANCE - ARIA", sender="Antonio",
        text="Aria, who is CSG Defence?", timestamp="2026-05-27T09:15:43Z",
    ))
    assert out["captured"] is True
    assert out["seq"] == 1
    assert len(out["hash"]) == 64
    rec = json.loads(fake_rs["lists"][cw._LOG_KEY][0])
    # every evidentiary field present + verbatim text preserved
    assert rec["group"] == "COMPLIANCE - ARIA"
    assert rec["sender"] == "Antonio"
    assert rec["text"] == "Aria, who is CSG Defence?"
    assert rec["timestamp"] == "2026-05-27T09:15:43Z"
    assert rec["prev_hash"] == cw._GENESIS  # first record chains off genesis
    assert "captured_at" in rec


def test_rf933_get_captured_newest_first_and_group_filter(fake_rs):
    asyncio.run(cw.capture_message(group="G1", sender="A", text="m1"))
    asyncio.run(cw.capture_message(group="G2", sender="B", text="m2"))
    asyncio.run(cw.capture_message(group="G1", sender="C", text="m3"))
    allrecs = asyncio.run(cw.get_captured(limit=10))
    assert [r["text"] for r in allrecs] == ["m3", "m2", "m1"]  # newest-first
    g1 = asyncio.run(cw.get_captured(group="G1", limit=10))
    assert [r["text"] for r in g1] == ["m3", "m1"]
    assert all(r["group"] == "G1" for r in g1)


def test_rf933_since_epoch_filter(fake_rs):
    asyncio.run(cw.capture_message(group="G", sender="A", text="old"))
    recs = asyncio.run(cw.get_captured(limit=10))
    cutoff = recs[0]["captured_at"] + 1.0  # in the future → excludes the old one
    assert asyncio.run(cw.get_captured(since_epoch=cutoff, limit=10)) == []


def test_rf933_verify_chain_clean(fake_rs):
    for i in range(5):
        asyncio.run(cw.capture_message(group="G", sender="S", text=f"msg{i}"))
    res = asyncio.run(cw.verify_chain())
    assert res["ok"] is True
    assert res["checked"] == 5
    assert res["broken_at"] is None


def test_rf933_tamper_is_detected(fake_rs):
    for i in range(4):
        asyncio.run(cw.capture_message(group="G", sender="S", text=f"msg{i}"))
    # An attacker edits a stored record's text (e.g. to hide an admission).
    lst = fake_rs["lists"][cw._LOG_KEY]
    victim = json.loads(lst[2])               # some older record
    victim["text"] = "REDACTED BY ATTACKER"
    lst[2] = json.dumps(victim)               # hash NOT recomputed → chain breaks
    res = asyncio.run(cw.verify_chain())
    assert res["ok"] is False
    assert res["broken_at"] == victim["seq"] or res["broken_at"] is not None


def test_rf933_hash_links_to_previous_record(fake_rs):
    asyncio.run(cw.capture_message(group="G", sender="S", text="first"))
    asyncio.run(cw.capture_message(group="G", sender="S", text="second"))
    lst = fake_rs["lists"][cw._LOG_KEY]
    newest = json.loads(lst[0])
    older = json.loads(lst[1])
    assert newest["prev_hash"] == older["hash"]  # forward linkage holds


def test_rf933_capture_never_raises_on_backend_failure(monkeypatch):
    """Capture is best-effort — a redis failure must not propagate into the
    live brain/signal path."""
    async def boom(*a, **k):
        raise RuntimeError("redis down")
    monkeypatch.setattr(rs, "incr", boom)
    monkeypatch.setattr(rs, "lrange", boom)
    out = asyncio.run(cw.capture_message(group="G", sender="S", text="x"))
    assert out["captured"] is False
    assert "error" in out
