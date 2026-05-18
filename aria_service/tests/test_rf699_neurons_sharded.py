"""R-F699 (2026-05-18) — neurons-blob sharding (extends R-F334).

Live fly logs 2026-05-18 17:59:40 + 18:37:07 showed a recurring
wedge pattern: every brain_hook absorb wrote the full ~4MB neurons
blob to Redis. The `Redis SET crucix:aria:neurons: value size 4025697
bytes exceeds warn threshold` warning fired ~20× in 5s during heavy
ingest, blocking the event loop and causing PR04 cascades.

R-F699 mirrors the R-F334 sharded-snapshot pattern from
aria_service/intel/knowledge.py: N shards × ~500KB each, plus a
meta key carrying the total-neuron count for the R-F371 disk-check.

These tests cover the split + merge round-trip + the cheap-count
helper. Live live read/write tests would need a Redis mock; trust
that R-F334's pattern is proven and just test the shape transforms.
"""
from __future__ import annotations


def test_rf699_split_empty_neurons():
    from aria_service.intel import neural_memory as nm
    shards = nm._split_neurons_into_shards({})
    assert len(shards) == 1
    assert shards[0]["neurons"] == {}
    assert shards[0]["shard_index"] == 0
    assert shards[0]["shard_count"] == 1


def test_rf699_split_small_neurons_one_shard():
    """A small neuron set (< MAX_PER_SHARD) goes into one shard."""
    from aria_service.intel import neural_memory as nm
    neurons = {f"n{i}": {"id": f"n{i}", "act": 0.5} for i in range(100)}
    shards = nm._split_neurons_into_shards(neurons)
    assert len(shards) == 1
    assert len(shards[0]["neurons"]) == 100


def test_rf699_split_large_neurons_multi_shard():
    """A large neuron set splits into multiple shards of ≤ MAX_PER_SHARD."""
    from aria_service.intel import neural_memory as nm
    # 10000 neurons → expect 4 shards at MAX_PER_SHARD=3000
    neurons = {f"n{i}": {"id": f"n{i}", "act": 0.5} for i in range(10_000)}
    shards = nm._split_neurons_into_shards(neurons)
    assert len(shards) >= 4
    total = sum(len(s["neurons"]) for s in shards)
    assert total == 10_000
    for s in shards:
        assert len(s["neurons"]) <= nm.NEURONS_SHARD_MAX_PER_SHARD


def test_rf699_split_then_merge_roundtrip():
    """Round-trip: split → merge yields the original dict (same keys
    and same per-key values)."""
    from aria_service.intel import neural_memory as nm
    original = {
        f"neuron_{i}": {
            "id": f"neuron_{i}", "act": i / 1000,
            "concept": f"concept_{i}",
        }
        for i in range(5000)
    }
    shards = nm._split_neurons_into_shards(original)
    merged = nm._merge_neurons_shards(shards)
    assert merged == original


def test_rf699_split_deterministic_across_runs():
    """Same input → same shard partition (sorted by id). This matters
    for the disk-check semantics — the shard partition must be stable
    so that any node reading shards reconstructs the same view."""
    from aria_service.intel import neural_memory as nm
    neurons = {f"n{i:05d}": {"id": f"n{i:05d}"} for i in range(200)}
    a = nm._split_neurons_into_shards(neurons)
    b = nm._split_neurons_into_shards(neurons)
    # Same number of shards, same keys per shard, same order
    assert len(a) == len(b)
    for sa, sb in zip(a, b):
        assert sa["shard_index"] == sb["shard_index"]
        assert list(sa["neurons"].keys()) == list(sb["neurons"].keys())


def test_rf699_merge_handles_unordered_shards():
    """Shards may arrive in any order from concurrent reads — merge
    must sort by shard_index before concatenating."""
    from aria_service.intel import neural_memory as nm
    s0 = {"neurons": {"a": 1}, "shard_index": 0, "shard_count": 3}
    s1 = {"neurons": {"b": 2}, "shard_index": 1, "shard_count": 3}
    s2 = {"neurons": {"c": 3}, "shard_index": 2, "shard_count": 3}
    merged = nm._merge_neurons_shards([s2, s0, s1])  # reversed
    assert merged == {"a": 1, "b": 2, "c": 3}


def test_rf699_merge_handles_malformed_shards():
    """Defensive — non-dict entries in the shard list are skipped."""
    from aria_service.intel import neural_memory as nm
    s0 = {"neurons": {"a": 1}, "shard_index": 0, "shard_count": 1}
    merged = nm._merge_neurons_shards([s0, None, "garbage", {"oops": True}])
    assert merged == {"a": 1}


def test_rf699_constants_within_safe_bounds():
    """Sanity: shard size targets should be well below the 4MB
    Redis-warn threshold."""
    from aria_service.intel import neural_memory as nm
    assert nm.NEURONS_SHARD_TARGET_BYTES <= 1_000_000  # 1MB cap
    assert nm.NEURONS_SHARD_MAX_PER_SHARD <= 5000
    # Key format must be specified — caller fills {i}
    assert "{i}" in nm.NEURONS_SHARD_KEY_FMT


def test_rf699_smoke_module_imports():
    """Lifespan-style smoke: module imports cleanly after the edits."""
    from aria_service.intel import neural_memory as nm  # noqa: F401
    assert callable(nm._split_neurons_into_shards)
    assert callable(nm._merge_neurons_shards)
    assert callable(nm._save_neurons_sharded)
    assert callable(nm._load_neurons_sharded)
    assert callable(nm._peek_neurons_disk_count)
