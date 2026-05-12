"""R-F371 — neural memory regression guard tests.

Live evidence 2026-05-12 13:07:20 R-F251 boot diagnostic:
    neural_neurons: 11391 → 1413 (-87.6%)
    neural_edges: 261079 → 59692 (-77.1%)

Race trace:
    1. Boot init() reads NEURONS_KEY from SQLite (pre-migration partial
       state) → loads 1413 neurons into _neurons.
    2. Operator triggers Upstash→SQLite migration mid-session. Migration
       writes the 11391-neuron blob from Upstash to SQLite.
    3. Periodic _persist() fires, writes in-memory 1413 to SQLite,
       OVERWRITES the 11391 from migration → 9978 neurons silently lost.

Direct violation of aria_infinite_memory.md. Same pattern as R-F267/F268
mastery/regional/stats scaffold-write protection. Fix: _persist() reads
the on-disk value first; if disk has materially more data, reload from
disk instead of overwriting.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def reset_module():
    """Reset neural_memory module state between tests."""
    if "aria_service.intel.neural_memory" in sys.modules:
        importlib.reload(sys.modules["aria_service.intel.neural_memory"])
    yield


def _fake_disk(neurons_dict, edges_dict):
    """Fake the redis_store get_json/set_json so we can inject 'disk' state."""
    storage = {
        "crucix:aria:neurons": neurons_dict,
        "crucix:aria:neural_edges": edges_dict,
        "crucix:aria:neural_meta": {"total_neurons": len(neurons_dict),
                                     "total_edges": sum(len(v) for v in edges_dict.values()),
                                     "total_activations": 0, "born": None},
    }
    async def fake_get_json(key):
        return storage.get(key)
    async def fake_set_json(key, val):
        storage[key] = val
    return storage, fake_get_json, fake_set_json


def test_persist_reloads_when_disk_has_significantly_more_neurons():
    """The exact live failure mode: in-memory has 1413, disk has 11391
    (just migrated). _persist() must NOT overwrite — it must reload."""
    from aria_service.intel import neural_memory as nm

    # Disk has the full migrated dataset
    big_neurons = {f"n{i}": {"id": f"n{i}", "concept": f"c{i}", "activation": 0.5}
                   for i in range(11391)}
    big_edges = {f"n{i}": {f"n{(i+1) % 11391}": 0.5} for i in range(11391)}
    storage, fake_get, fake_set = _fake_disk(big_neurons, big_edges)

    # In-memory has only the pre-migration 1413 — simulating the race
    nm._neurons = {f"n{i}": {"id": f"n{i}", "concept": f"c{i}", "activation": 0.5}
                   for i in range(1413)}
    nm._edges.clear()
    for i in range(1413):
        nm._edges[f"n{i}"] = {f"n{(i+1) % 1413}": 0.5}
    nm._meta = {"total_neurons": 1413, "total_edges": 1413, "total_activations": 0, "born": None}

    # No raw redis client — force the get_json path
    with patch.object(nm.rs, "_client", None), \
         patch.object(nm.rs, "get_json", side_effect=fake_get), \
         patch.object(nm.rs, "set_json", side_effect=fake_set):
        asyncio.run(nm._persist())

    # In-memory should now reflect the disk state (regression-guard reload)
    assert len(nm._neurons) == 11391, (
        f"R-F371: expected reload to 11391 neurons, got {len(nm._neurons)}"
    )
    # And the disk should still hold the 11391 — NOT overwritten with 1413
    assert len(storage["crucix:aria:neurons"]) == 11391, (
        f"R-F371: disk overwritten — had 11391, now has {len(storage['crucix:aria:neurons'])}"
    )


def test_persist_writes_normally_when_in_memory_is_larger():
    """Healthy growth path: in-memory has more neurons than disk. _persist
    must write the new larger snapshot to disk (NOT skip)."""
    from aria_service.intel import neural_memory as nm

    # Disk has the older snapshot
    storage, fake_get, fake_set = _fake_disk(
        {f"n{i}": {"id": f"n{i}", "concept": f"c{i}", "activation": 0.5}
         for i in range(100)},
        {},
    )

    # In-memory has grown to 200
    nm._neurons = {f"n{i}": {"id": f"n{i}", "concept": f"c{i}", "activation": 0.5}
                   for i in range(200)}
    nm._edges.clear()
    nm._meta = {"total_neurons": 200, "total_edges": 0, "total_activations": 0, "born": None}

    with patch.object(nm.rs, "_client", None), \
         patch.object(nm.rs, "get_json", side_effect=fake_get), \
         patch.object(nm.rs, "set_json", side_effect=fake_set):
        asyncio.run(nm._persist())

    # Disk should now hold the new 200-neuron state
    assert len(storage["crucix:aria:neurons"]) == 200, (
        f"healthy growth — expected disk to update to 200, got {len(storage['crucix:aria:neurons'])}"
    )


def test_persist_writes_when_in_memory_and_disk_match():
    """Equal state: no reload needed, normal write proceeds."""
    from aria_service.intel import neural_memory as nm

    same_neurons = {f"n{i}": {"id": f"n{i}", "concept": f"c{i}", "activation": 0.5}
                    for i in range(50)}
    storage, fake_get, fake_set = _fake_disk(dict(same_neurons), {})

    nm._neurons = dict(same_neurons)
    nm._edges.clear()
    nm._meta = {"total_neurons": 50, "total_edges": 0, "total_activations": 0, "born": None}

    with patch.object(nm.rs, "_client", None), \
         patch.object(nm.rs, "get_json", side_effect=fake_get), \
         patch.object(nm.rs, "set_json", side_effect=fake_set):
        asyncio.run(nm._persist())

    assert len(storage["crucix:aria:neurons"]) == 50


def test_persist_writes_normally_when_disk_is_empty():
    """First-ever persist: disk is empty/None, in-memory has fresh data.
    Must write (not skip) — disk-empty is NOT a regression to guard against."""
    from aria_service.intel import neural_memory as nm

    storage, fake_get, fake_set = _fake_disk({}, {})
    storage["crucix:aria:neurons"] = None  # explicitly missing

    nm._neurons = {f"n{i}": {"id": f"n{i}", "concept": f"c{i}", "activation": 0.5}
                   for i in range(10)}
    nm._edges.clear()
    nm._meta = {"total_neurons": 10, "total_edges": 0, "total_activations": 0, "born": None}

    with patch.object(nm.rs, "_client", None), \
         patch.object(nm.rs, "get_json", side_effect=fake_get), \
         patch.object(nm.rs, "set_json", side_effect=fake_set):
        asyncio.run(nm._persist())

    assert len(storage["crucix:aria:neurons"]) == 10, (
        "first-write path — disk should now hold the 10-neuron in-memory state"
    )


def test_persist_within_10pct_tolerance_proceeds_normally():
    """Disk has only slightly more than memory (within 10% tolerance).
    Treated as normal growth fluctuation, not a migration race — write
    proceeds. Prevents over-conservative protection from blocking healthy
    writes during the natural decay/access cycle."""
    from aria_service.intel import neural_memory as nm

    # Disk has 105, memory has 100 — within 10% tolerance
    disk_neurons = {f"n{i}": {"id": f"n{i}", "concept": f"c{i}", "activation": 0.5}
                    for i in range(105)}
    storage, fake_get, fake_set = _fake_disk(disk_neurons, {})

    nm._neurons = {f"n{i}": {"id": f"n{i}", "concept": f"c{i}", "activation": 0.5}
                   for i in range(100)}
    nm._edges.clear()
    nm._meta = {"total_neurons": 100, "total_edges": 0, "total_activations": 0, "born": None}

    with patch.object(nm.rs, "_client", None), \
         patch.object(nm.rs, "get_json", side_effect=fake_get), \
         patch.object(nm.rs, "set_json", side_effect=fake_set):
        asyncio.run(nm._persist())

    # 105 / 100 = 1.05, NOT > 1.1 (the trigger threshold) — write proceeds
    assert len(storage["crucix:aria:neurons"]) == 100, (
        "10% tolerance — disk had 105 (within 10%), should accept the 100 write"
    )
