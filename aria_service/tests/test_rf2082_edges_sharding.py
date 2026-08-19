"""R-F2082 — sharded neural edges with per-shard dirty tracking.

The scale-proof guarantee: per-persist edge work is O(changed shards), not
O(whole graph). The whole-graph re-encode held the GIL 7-8s and wedged the single
event loop every few minutes (live wedge_677) — the brain-side root cause of slow
web/dashboard population. These tests drive the real persist/load and assert:
  - the first persist migrates the legacy blob to shards (all shards written once),
  - a subsequent single-edge mutation rewrites ONLY the 1-2 shards it touches,
  - the sharded set round-trips losslessly,
  - a graph-wide decay marks every shard dirty.
"""
import asyncio
import importlib
import sys

import pytest


@pytest.fixture(autouse=True)
def reset_module():
    if "aria_service.intel.neural_memory" in sys.modules:
        importlib.reload(sys.modules["aria_service.intel.neural_memory"])
    yield


def _fake_storage(initial=None):
    storage = dict(initial or {})
    write_log = []

    async def fake_get(key):
        return storage.get(key)

    async def fake_set(key, val, *a, **k):
        storage[key] = val
        write_log.append(key)

    async def fake_get_json(key):
        import json
        v = storage.get(key)
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return None
        return None

    async def fake_set_json(key, val, *a, **k):
        storage[key] = val
        write_log.append(key)

    return storage, write_log, {"get": fake_get, "set": fake_set,
                                "get_json": fake_get_json, "set_json": fake_set_json}


def _patch(nm, mocks):
    from unittest.mock import patch
    return [
        patch.object(nm.rs, "_client", None),
        patch.object(nm.rs, "get", side_effect=mocks["get"]),
        patch.object(nm.rs, "set", side_effect=mocks["set"]),
        # R-F4173 (C-185) — the loader now reads the graph STRICTLY, so a
        # store failure raises instead of returning None. These fakes never
        # simulate a store failure, so the strict readers mirror the plain
        # ones exactly and this test's intent is unchanged.
        patch.object(nm.rs, "get_strict", side_effect=mocks["get"]),
        patch.object(nm.rs, "get_json_strict", side_effect=mocks["get_json"]),
        patch.object(nm.rs, "get_json", side_effect=mocks["get_json"]),
        patch.object(nm.rs, "set_json", side_effect=mocks["set_json"]),
    ]


def test_rf2082_single_mutation_rewrites_only_its_shards():
    """The core scale guarantee: after the initial migration, one edge boost
    re-encodes only the 1-2 shards holding its endpoints — NOT all shards."""
    from aria_service.intel import neural_memory as nm

    # a graph spread across many shards
    nm._neurons = {f"n{i}": {"id": f"n{i}", "concept": f"c{i}", "activation": 0.5}
                   for i in range(400)}
    nm._edges.clear()
    for i in range(400):
        nm._edges[f"n{i}"] = {f"n{(i + 1) % 400}": 0.5}
    nm._meta = {"total_neurons": 400, "total_edges": 400, "total_activations": 0, "born": None}
    nm._edges_dirty = True
    nm._edges_sharded_initialized = False
    nm._dirty_edge_shards.clear()

    storage, write_log, mocks = _fake_storage()
    patches = _patch(nm, mocks)
    for p in patches:
        p.start()
    try:
        # 1) first persist = migration → ALL shards written once
        asyncio.run(nm._persist())
        full_shards = {k for k in write_log if k.startswith("crucix:aria:neural_edges:shard:")}
        assert len(full_shards) == nm.EDGES_SHARD_COUNT, "migration must write every shard once"

        # 2) one edge mutation, then persist again → only touched shards rewrite
        write_log.clear()
        nm._strengthen_edge("n5", "n6", boost=0.2)
        assert nm._edges_dirty is True
        expected = {nm._edge_shard_index("n5"), nm._edge_shard_index("n6")}
        asyncio.run(nm._persist())
        rewritten = {int(k.rsplit(":", 1)[1]) for k in write_log
                     if k.startswith("crucix:aria:neural_edges:shard:")}
        assert rewritten == expected, (
            f"only the mutated endpoints' shards should rewrite; "
            f"expected {expected}, got {rewritten} of {nm.EDGES_SHARD_COUNT} shards"
        )
        assert len(rewritten) <= 2, "a single edge must not re-encode the whole graph"

        # 3) round-trips losslessly through the sharded store
        loaded = asyncio.run(nm._load_edges_sharded())
    finally:
        for p in patches:
            p.stop()

    assert loaded is not None
    assert loaded["n5"]["n6"] > 0.5 and loaded["n6"]["n5"] > 0.0
    assert len(loaded) == 400, "all edge groups must survive the shard round-trip"


def test_rf2082_decay_marks_all_shards_dirty():
    """A graph-wide decay touches every group, so every shard must be re-written."""
    from aria_service.intel import neural_memory as nm

    nm._neurons = {f"n{i}": {"id": f"n{i}", "concept": f"c{i}", "activation": 0.5,
                             "last_decayed": 0} for i in range(50)}
    nm._edges.clear()
    for i in range(50):
        nm._edges[f"n{i}"] = {f"n{(i + 1) % 50}": 0.5}
    nm._edges_dirty = False
    nm._dirty_edge_shards.clear()
    nm._LAST_GLOBAL_DECAY = 0  # force decay to run

    nm._apply_decay()

    assert nm._edges_dirty is True
    assert nm._dirty_edge_shards == set(range(nm.EDGES_SHARD_COUNT)), \
        "decay touches every group → every shard dirty"


def test_rf2082_load_returns_none_without_meta():
    """Absent shard meta → loader returns None so the caller falls back to the
    legacy single blob (migration safety)."""
    from aria_service.intel import neural_memory as nm

    storage, _, mocks = _fake_storage({})
    patches = _patch(nm, mocks)
    for p in patches:
        p.start()
    try:
        loaded = asyncio.run(nm._load_edges_sharded())
    finally:
        for p in patches:
            p.stop()
    assert loaded is None
