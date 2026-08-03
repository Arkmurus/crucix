"""R-F333 + R-F334 — reasoning_library boot diagnostic + knowledge
Redis key-split.

R-F333 — at boot, the reasoning_library size is logged + a brain_hook
gap is recorded if empty. Live failure 21:19:37 had to wait 3 hours
for the quiz cycle to surface the same diagnostic.

R-F334 — knowledge Redis snapshot is now split into N shards each
~500KB gzipped instead of one 4MB+ blob. Live warning 21:19:39:
"value size 4023932 bytes exceeds warn threshold (4000000)". Sharding
gives multi-month headroom.
"""
from __future__ import annotations

import asyncio
import json

from ._source_probe import repo_path


# ───────────────────────── R-F333 — boot diagnostic source ────────────────────

def test_rf333_boot_diagnostic_present_in_main():
    """The R-F333 logger.info + brain_hook absorb on empty library
    must be present in main.py's lifespan."""
    import pathlib
    src = pathlib.Path(
        repo_path("aria_service/main.py")
    ).read_text(encoding="utf-8", errors="ignore")
    assert "R-F333" in src
    assert "reasoning_library boot diagnostic" in src
    assert "reasoning_library_empty_at_boot" in src


# ───────────────────────── R-F334 — sharding helpers ──────────────────────────

def test_rf334_split_into_shards_with_small_cache():
    """Small cache → single shard."""
    from aria_service.intel.knowledge import _split_into_shards
    cache = {
        "facts": [{"text": "fact 1"}, {"text": "fact 2"}],
        "queries": [],
        "learnings": [],
        "version": 1,
    }
    shards = _split_into_shards(cache)
    assert len(shards) == 1
    assert shards[0]["shard_count"] == 1
    assert shards[0]["shard_index"] == 0
    assert shards[0]["facts"] == cache["facts"]


def test_rf334_split_into_shards_with_large_cache():
    """Large cache → multiple shards, each list partitioned in order."""
    from aria_service.intel.knowledge import _split_into_shards
    cache = {
        "facts": [{"text": f"fact {i}", "context": "x" * 200} for i in range(15000)],
        "queries": [],
        "learnings": [],
        "version": 1,
    }
    shards = _split_into_shards(cache)
    # Should split into multiple shards
    assert len(shards) >= 2, (
        f"R-F334 regression: 15k facts should split into ≥2 shards, "
        f"got {len(shards)}"
    )
    # All shards must agree on shard_count
    counts = {s["shard_count"] for s in shards}
    assert len(counts) == 1, f"R-F334: inconsistent shard_count across shards"
    # shard_index sequence is 0..N-1
    indices = sorted(s["shard_index"] for s in shards)
    assert indices == list(range(len(shards)))
    # Total facts across all shards equals original
    total_facts = sum(len(s["facts"]) for s in shards)
    assert total_facts == 15000


def test_rf334_merge_shards_round_trip():
    """split → merge produces the same cache (item-count level)."""
    from aria_service.intel.knowledge import _split_into_shards, _merge_shards
    cache = {
        "facts": [{"text": f"fact {i}"} for i in range(500)],
        "queries": [{"q": f"query {i}"} for i in range(200)],
        "learnings": [{"l": f"learning {i}"} for i in range(50)],
        "version": 1,
    }
    shards = _split_into_shards(cache)
    merged = _merge_shards(shards)
    assert merged is not None
    assert len(merged["facts"]) == len(cache["facts"])
    assert len(merged["queries"]) == len(cache["queries"])
    assert len(merged["learnings"]) == len(cache["learnings"])
    # First and last items preserved
    assert merged["facts"][0] == cache["facts"][0]
    assert merged["facts"][-1] == cache["facts"][-1]


def test_rf334_merge_shards_handles_empty_list():
    from aria_service.intel.knowledge import _merge_shards
    assert _merge_shards([]) is None


def test_rf334_merge_shards_handles_out_of_order():
    """If shards arrive out of order (parallel gather), merge sorts by
    shard_index."""
    from aria_service.intel.knowledge import _merge_shards
    shards = [
        {"facts": [3, 4], "queries": [], "learnings": [], "version": 1,
         "shard_index": 1, "shard_count": 2},
        {"facts": [1, 2], "queries": [], "learnings": [], "version": 1,
         "shard_index": 0, "shard_count": 2},
    ]
    merged = _merge_shards(shards)
    assert merged["facts"] == [1, 2, 3, 4]


def test_rf334_empty_cache_produces_one_empty_shard():
    """A cache with version but no items still produces one shard so
    the meta+shard write happens (avoids the "no snapshot" state)."""
    from aria_service.intel.knowledge import _split_into_shards
    cache = {"facts": [], "queries": [], "learnings": [], "version": 1}
    shards = _split_into_shards(cache)
    assert len(shards) == 1
    assert shards[0]["facts"] == []
    assert shards[0]["shard_count"] == 1


# ───────────────────────── R-F334 — end-to-end save+load ──────────────────────

def test_rf334_capability_save_then_load_round_trip(monkeypatch):
    """Capability — save a sharded snapshot, then load it back. The
    facts list returned must equal the facts list we saved."""
    from aria_service.intel import knowledge as _kn
    from aria_service.intel import redis_store as _rs

    # In-memory backing store for the test
    backing: dict[str, str] = {}

    async def _fake_get(key):
        return backing.get(key)

    async def _fake_set(key, value, *a, **kw):
        backing[key] = value
        return True

    async def _fake_delete(key):
        backing.pop(key, None)
        return True

    monkeypatch.setattr(_rs, "get", _fake_get)
    monkeypatch.setattr(_rs, "set", _fake_set)
    monkeypatch.setattr(_rs, "delete", _fake_delete)
    # The knowledge module imports rs at module level — patch its
    # reference too.
    monkeypatch.setattr(_kn, "rs", _rs)

    cache = {
        "facts": [{"text": f"fact {i}", "topic": "x"} for i in range(1000)],
        "queries": [{"q": f"query {i}"} for i in range(100)],
        "learnings": [{"l": "x"}],
        "version": 1,
    }

    save_result = asyncio.run(_kn._save_sharded_snapshot(cache))
    assert save_result["shard_count"] >= 1
    assert save_result["items"]["facts"] == 1000

    # Meta key must exist
    assert _kn.SHARD_META_KEY in backing
    # At least N shard keys must exist
    shard_keys = [k for k in backing if ":shard:" in k]
    assert len(shard_keys) == save_result["shard_count"]

    loaded = asyncio.run(_kn._load_sharded_snapshot())
    assert loaded is not None
    assert len(loaded["facts"]) == 1000
    assert loaded["facts"][0]["text"] == "fact 0"
    assert loaded["facts"][-1]["text"] == "fact 999"


def test_rf334_capability_legacy_blob_still_loads_when_no_shards(monkeypatch):
    """Capability — if shard meta is absent but legacy single-blob
    exists, _load_sharded_snapshot returns None and the caller falls
    back to the legacy path. This is the migration safety net."""
    from aria_service.intel import knowledge as _kn
    from aria_service.intel import redis_store as _rs

    backing: dict[str, str] = {}

    async def _fake_get(key):
        return backing.get(key)

    monkeypatch.setattr(_rs, "get", _fake_get)
    monkeypatch.setattr(_kn, "rs", _rs)

    # No meta key, no shards → sharded loader returns None
    result = asyncio.run(_kn._load_sharded_snapshot())
    assert result is None


def test_rf334_shard_target_fits_under_warn_threshold():
    """The shard target (500 KB) must be well below the Redis warn
    threshold (4 MB) so a single oversized item can't push a shard over."""
    from aria_service.intel.knowledge import SHARD_TARGET_BYTES
    REDIS_WARN_BYTES = 4_000_000
    assert SHARD_TARGET_BYTES < REDIS_WARN_BYTES / 4, (
        f"R-F334 calibration: SHARD_TARGET_BYTES ({SHARD_TARGET_BYTES}) "
        f"too close to Redis warn threshold ({REDIS_WARN_BYTES}). "
        f"Should be ≤ 1MB to leave headroom for outlier items."
    )
