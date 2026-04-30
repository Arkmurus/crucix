"""F110 (2026-04-30): intel ledger moved off the per-write Redis blob
(2.5 MB SET on every channel/ingest + sweep tick — root cause of the
2026-04-29 Upstash truncation that lost 2587 signals) onto disk JSON
with debounced writes. Mirrors F94's pattern for the knowledge store.

These tests verify the on-disk shape, the legacy-Redis migration
hydrate path, and that hot-path writes never hit Redis.
"""
import asyncio
import json
import os

import pytest


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture
def fresh_ledger(monkeypatch, tmp_path):
    """Reset the module's in-memory state and point its disk path at a
    test-owned tempfile so each test runs against a clean slate."""
    from aria_service.intel import intel_ledger as il

    monkeypatch.setattr(il, "_DISK_PATH", str(tmp_path / "test_signals.json"))
    monkeypatch.setattr(il, "_cache", None)
    monkeypatch.setattr(il, "_dirty", False)
    monkeypatch.setattr(il, "_dirty_since_snapshot", False)
    monkeypatch.setattr(il, "_flusher_started", False)
    monkeypatch.setattr(il, "_flush_task", None)
    monkeypatch.setattr(il, "_flusher_stop", False)
    yield il
    task = getattr(il, "_flush_task", None)
    if task is not None and not task.done():
        task.cancel()


def test_add_signal_persists_to_disk(fresh_ledger):
    """The whole point of F110: a signal written through the public API
    must end up on disk after flush, NOT in a per-call Redis SET."""
    il = fresh_ledger

    async def go():
        await il.add_signal({
            "summary": "F110 test signal — Angola SAM tender",
            "source": "test",
            "type": "tender",
        })
        # Force the flush — the test loop won't naturally tick the
        # debounce window.
        await il.flush()

    _run(go())

    with open(il._DISK_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    texts = [s["text"] for s in on_disk["signals"]]
    assert any("Angola SAM tender" in t for t in texts)


def test_disk_round_trip_survives_cache_reset(fresh_ledger, monkeypatch):
    """Cold start scenario: write signal, kill in-memory cache, reload —
    the signal must come back from disk."""
    il = fresh_ledger

    async def write():
        await il.add_signal({
            "summary": "persistent signal x" * 5,
            "source": "test",
            "type": "osint",
        })
        await il.flush()

    _run(write())

    # Simulate a process restart: drop the in-memory cache.
    monkeypatch.setattr(il, "_cache", None)

    async def read():
        loaded = await il._load()
        return [s["text"] for s in loaded.get("signals", [])]

    texts = _run(read())
    assert any("persistent signal" in t for t in texts)


def test_legacy_redis_blob_migrates_to_disk_on_first_load(
    fresh_ledger, monkeypatch
):
    """Pre-F110 deploys have the canonical state in Redis (truncated or
    not). On the first boot post-migration, _load() must pull from Redis
    when disk is empty AND immediately write it to disk so subsequent boots
    skip the Redis path entirely."""
    il = fresh_ledger
    assert not os.path.exists(il._DISK_PATH)

    legacy_payload = {
        "signals": [{
            "text": "legacy ledger signal from old redis blob",
            "source": "legacy", "type": "osint", "url": "",
            "countries": ["Angola"], "products": [], "oems": [],
            "severity": "medium", "ts": "2026-01-01T00:00:00+00:00",
        }],
        "version": 1,
    }

    async def fake_get_json(key):
        assert key == il.KEY
        return legacy_payload

    monkeypatch.setattr(il.rs, "get_json", fake_get_json)

    async def go():
        loaded = await il._load()
        return loaded

    loaded = _run(go())

    # Cache hydrated from Redis...
    assert loaded["signals"][0]["text"].startswith("legacy ledger signal")
    # ...AND the disk file was written so the next boot can skip Redis.
    assert os.path.exists(il._DISK_PATH)
    with open(il._DISK_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["signals"][0]["text"].startswith("legacy ledger signal")


def test_hot_path_writes_do_not_block_on_redis(fresh_ledger, monkeypatch):
    """The bug F110 fixes: every add_signal / ingest_sweep_signals did a
    full ~2.5 MB Redis SET on the hot path. Now _save() must NOT call
    rs.set / rs.set_json — it just marks dirty and lets the debounce loop
    handle the (eventual, infrequent) Redis snapshot."""
    il = fresh_ledger

    redis_writes = []

    async def fake_set_json(key, value, ex=None, keepttl=False):
        redis_writes.append((key, len(json.dumps(value, default=str))))

    monkeypatch.setattr(il.rs, "set_json", fake_set_json)

    async def go():
        # Simulate a sweep-ingest burst — many signals in quick succession.
        for i in range(20):
            await il.add_signal({
                "summary": f"burst signal {i}",
                "source": "burst-test",
                "type": "osint",
            })
        await il.flush()

    _run(go())

    # Disk has them all...
    with open(il._DISK_PATH, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert len([t for t in on_disk["signals"]
                if t["text"].startswith("burst signal")]) == 20

    # ...and the LEDGER KEY was NOT hit per-write. The snapshot path is on
    # a 10-min cadence and only fires from the background loop.
    # NOTE: add_signal also calls brain_hook.absorb() which writes to
    # `crucix:aria:student:mastery` + `crucix:aria:brain_hook:stats` —
    # those are an unrelated brain-side concern, not what F110 fixes.
    ledger_writes = [w for w in redis_writes if w[0] == il.KEY]
    assert ledger_writes == [], (
        f"expected 0 Redis writes to {il.KEY} from add_signal bursts; "
        f"got {ledger_writes}"
    )
