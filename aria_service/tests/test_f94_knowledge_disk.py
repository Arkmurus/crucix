"""F94 (2026-04-30): knowledge store moved off the per-write Redis
blob (4.36 MB warning every absorb, brain_hook circuit trips) onto
disk JSON with debounced writes.

These tests verify the on-disk shape and the legacy-Redis migration
hydrate path.
"""
import asyncio
import json
import os
import tempfile

import pytest


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture
def fresh_knowledge(monkeypatch, tmp_path):
    """Reset the module's in-memory state and point its disk path at a
    test-owned tempfile so each test runs against a clean slate."""
    from aria_service.intel import knowledge as kn

    monkeypatch.setattr(kn, "_DISK_PATH", str(tmp_path / "test_knowledge.json"))
    monkeypatch.setattr(kn, "_cache", None)
    monkeypatch.setattr(kn, "_dirty", False)
    monkeypatch.setattr(kn, "_dirty_since_snapshot", False)
    monkeypatch.setattr(kn, "_flusher_started", False)
    monkeypatch.setattr(kn, "_flush_task", None)
    monkeypatch.setattr(kn, "_flusher_stop", False)
    yield kn
    # Cancel any flusher task this test left running so its loop can be
    # torn down cleanly without the "Task was destroyed but it is
    # pending!" warning.
    task = getattr(kn, "_flush_task", None)
    if task is not None and not task.done():
        task.cancel()


def test_store_fact_persists_to_disk(fresh_knowledge):
    """The whole point of F94: a fact written through the public API
    must end up on disk after flush, NOT in a per-call Redis SET."""
    kn = fresh_knowledge

    async def go():
        await kn.store_fact(
            topic="F94 test topic",
            content="some fact content that is long enough to pass the R-F1526 fifty character minimum gate",
            source="test",
            confidence="ASSESSED",
        )
        # Force the flush — the test loop won't naturally tick the
        # debounce window.
        await kn.flush()

    _run(go())

    with open(kn._DISK_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    topics = [f["topic"] for f in on_disk["facts"]]
    assert "F94 test topic" in topics


def test_disk_round_trip_survives_cache_reset(fresh_knowledge, monkeypatch):
    """Cold start scenario: write fact, kill in-memory cache, reload —
    the fact must come back from disk."""
    kn = fresh_knowledge

    async def write():
        await kn.store_fact("persistent topic", "x" * 100, source="test")
        await kn.flush()

    _run(write())

    # Simulate a process restart: drop the in-memory cache.
    monkeypatch.setattr(kn, "_cache", None)

    async def read():
        loaded = await kn._load()
        return [f["topic"] for f in loaded.get("facts", [])]

    topics = _run(read())
    assert "persistent topic" in topics


def test_legacy_redis_blob_migrates_to_disk_on_first_load(
    fresh_knowledge, monkeypatch
):
    """Pre-F94 deploys have the canonical state in Redis. On the first
    boot post-migration, _load() must pull from Redis when disk is
    empty AND immediately write it to disk so subsequent boots skip the
    Redis path entirely."""
    kn = fresh_knowledge
    # Disk file does not exist yet — verify.
    assert not os.path.exists(kn._DISK_PATH)

    legacy_payload = {
        "facts": [{"id": "abc12345", "topic": "legacy fact",
                   "content": "from old redis blob",
                   "source": "legacy", "confidence": "ASSESSED",
                   "createdAt": "2026-01-01T00:00:00Z",
                   "updatedAt": "2026-01-01T00:00:00Z",
                   "accessCount": 0}],
        "queries": [],
        "learnings": [],
        "version": 1,
    }

    # Stub the redis_store.get call kn._load() makes on the
    # disk-empty fallback path (line 574: rs.get(KEY)).
    async def fake_get(key):
        assert key == kn.KEY
        return legacy_payload

    monkeypatch.setattr(kn.rs, "get", fake_get)

    async def go():
        loaded = await kn._load()
        return loaded

    loaded = _run(go())

    # Cache hydrated from Redis...
    assert loaded["facts"][0]["topic"] == "legacy fact"
    # ...AND the disk file was written so the next boot can skip Redis.
    assert os.path.exists(kn._DISK_PATH)
    with open(kn._DISK_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["facts"][0]["topic"] == "legacy fact"


def test_save_does_not_block_on_redis(fresh_knowledge, monkeypatch):
    """The bug F94 fixes: every store_fact() did a full ~4 MB Redis
    SET. Now _save() must NOT call rs.set / rs.set_json — it just marks
    dirty and lets the debounce loop handle the (eventual, infrequent)
    Redis snapshot."""
    kn = fresh_knowledge

    redis_writes = []

    async def fake_set_json(key, value, ex=None, keepttl=False):
        redis_writes.append((key, len(json.dumps(value, default=str))))

    monkeypatch.setattr(kn.rs, "set_json", fake_set_json)

    async def go():
        # Simulate a brain_hook burst -- many facts in quick succession.
        for i in range(20):
            # R-F1956: use content well above the R-F1526 50-char minimum
            # so validation gates don't reject test data.
            await kn.store_fact(f"burst topic {i}", f"meaningful content item number {i:03d} that passes the length gate",
                                source="burst-test")
        # Force the disk flush.
        await kn.flush()

    _run(go())

    # Disk has them all...
    with open(kn._DISK_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert len([t for t in on_disk["facts"]
                if t["topic"].startswith("burst topic")]) == 20

    # ...and Redis was NOT hit per-write for knowledge keys. The snapshot
    # path is on a 10-min cadence and would only fire from the background
    # loop. Other modules (learning_progress, brain_hook stats, student)
    # may write to their own keys via the shared redis_store — those are
    # not from knowledge._save() and are expected background noise.
    #
    # R-F2787 (2026-07-19): scope the "knowledge write" filter to the ACTUAL
    # knowledge namespace (kn.KEY == "crucix:aria:knowledge", incl. its :meta /
    # :shard:* keys). The old filter also matched `"fact" in k.lower()`, which
    # miscounted OTHER modules' `verified_intel:facts` set_json writes (they
    # share redis_store) as knowledge writes and false-failed this test. Every
    # key knowledge._save() writes is under kn.KEY; none contains "fact", so the
    # broad clause only added false positives — narrowing it does not weaken the
    # F94 contract (§23), it stops measuring the wrong lifecycle.
    knowledge_writes = [
        (k, v) for k, v in redis_writes
        if k.startswith(kn.KEY)
    ]
    assert knowledge_writes == [], (
        f"expected 0 knowledge Redis writes from store_fact bursts; "
        f"got {knowledge_writes} (total {len(redis_writes)} writes, "
        f"all non-knowledge keys: {[(k, v) for k, v in redis_writes if not k.startswith(kn.KEY)]})"
    )
