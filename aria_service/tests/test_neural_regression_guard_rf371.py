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


# R-F3334 — these tests patched get_json/set_json ONLY, and asserted on the
# legacy "crucix:aria:neurons" blob. Two consequences, both invisible until the
# assertions were read against the code:
#
#   * R-F699 replaced that single-blob write with a SHARDED write through
#     rs.set(), and R-F2082 did the same for edges. Nothing writes the legacy key
#     any more, so the three "writes normally" tests could never pass, and
#     test_persist_writes_when_in_memory_and_disk_match passed VACUOUSLY — it
#     asserted a key nobody touches still held its seeded value, which is true
#     whether _persist works, does nothing, or does not exist.
#   * rs.set() was never patched, so every run wrote fake neuron shards into the
#     REAL dev store. A unit test was mutating durable state.
#
# The fix is not to swap in the new key names — that would break again at the
# next storage change, which is the whole defect. This fake covers EVERY store
# function _persist touches, disk state is SEEDED through the real writer, and
# assertions read back through the real loader. No key name appears in a test
# below, so the layout can change again without making these wrong.


def _fake_store():
    """One in-memory store standing in for the whole redis_store surface."""
    storage: dict = {}

    async def fake_get(key):
        return storage.get(key)

    async def fake_set(key, value, ex=None, keepttl=False):
        storage[key] = value

    async def fake_get_json(key):
        return storage.get(key)

    async def fake_set_json(key, obj, ex=None, keepttl=False):
        storage[key] = obj

    return storage, fake_get, fake_set, fake_get_json, fake_set_json


def _patched(nm, storage_fns):
    """Patch every store entry point _persist reaches."""
    _, fake_get, fake_set, fake_get_json, fake_set_json = storage_fns
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch.object(nm.rs, "_client", None))
    stack.enter_context(patch.object(nm.rs, "get", side_effect=fake_get))
    stack.enter_context(patch.object(nm.rs, "set", side_effect=fake_set))
    # R-F4173 (C-185) — the loader now reads the graph STRICTLY, so a store
    # failure raises instead of returning None. This fake never simulates a
    # store failure, so the strict readers mirror the plain ones exactly.
    stack.enter_context(patch.object(nm.rs, "get_strict", side_effect=fake_get))
    stack.enter_context(patch.object(nm.rs, "get_json_strict", side_effect=fake_get_json))
    stack.enter_context(patch.object(nm.rs, "get_json", side_effect=fake_get_json))
    stack.enter_context(patch.object(nm.rs, "set_json", side_effect=fake_set_json))
    return stack


def _seed_disk(nm, neurons):
    """Put `neurons` on 'disk' through the REAL writer, so the on-disk layout is
    whatever production actually writes."""
    asyncio.run(nm._save_neurons_sharded(dict(neurons)))


def _disk_neuron_count(nm):
    """Read the neuron count back through the REAL loader, with the same legacy
    fallback _peek_neurons_disk_count uses."""
    return asyncio.run(nm._peek_neurons_disk_count())


def _mem(n, start=0):
    return {f"n{i}": {"id": f"n{i}", "concept": f"c{i}", "activation": 0.5}
            for i in range(start, start + n)}


def test_persist_reloads_when_disk_has_significantly_more_neurons():
    """The exact live failure mode: in-memory has 1413, disk has 11391
    (just migrated). _persist() must NOT overwrite — it must reload."""
    from aria_service.intel import neural_memory as nm

    fns = _fake_store()
    with _patched(nm, fns):
        _seed_disk(nm, _mem(11391))          # the migrated dataset lands on disk

        nm._neurons = _mem(1413)             # in-memory is the pre-migration state
        nm._edges.clear()
        for i in range(1413):
            nm._edges[f"n{i}"] = {f"n{(i + 1) % 1413}": 0.5}
        nm._meta = {"total_neurons": 1413, "total_edges": 1413,
                    "total_activations": 0, "born": None}

        asyncio.run(nm._persist())

        assert len(nm._neurons) == 11391, (
            f"R-F371: expected reload to 11391 neurons, got {len(nm._neurons)}"
        )
        assert _disk_neuron_count(nm) == 11391, (
            f"R-F371: disk overwritten — had 11391, now has {_disk_neuron_count(nm)}"
        )


def test_persist_writes_normally_when_in_memory_is_larger():
    """Healthy growth path: in-memory has more neurons than disk. _persist
    must write the new larger snapshot to disk (NOT skip)."""
    from aria_service.intel import neural_memory as nm

    fns = _fake_store()
    with _patched(nm, fns):
        _seed_disk(nm, _mem(100))

        nm._neurons = _mem(200)
        nm._edges.clear()
        nm._meta = {"total_neurons": 200, "total_edges": 0,
                    "total_activations": 0, "born": None}

        asyncio.run(nm._persist())

        assert _disk_neuron_count(nm) == 200, (
            f"healthy growth — expected disk to update to 200, got {_disk_neuron_count(nm)}"
        )


def test_persist_writes_when_in_memory_and_disk_match():
    """Equal state: no reload needed, normal write proceeds.

    R-F3334: this asserted a seeded legacy key still held its seeded value,
    which was true no matter what _persist did. It now writes a DISTINGUISHABLE
    set — same count, different neuron ids — so 'the write happened' and 'the
    seed was never touched' can actually be told apart.
    """
    from aria_service.intel import neural_memory as nm

    fns = _fake_store()
    with _patched(nm, fns):
        _seed_disk(nm, _mem(50))                    # ids n0..n49 on disk

        nm._neurons = _mem(50, start=1000)          # ids n1000..n1049 in memory
        nm._edges.clear()
        nm._meta = {"total_neurons": 50, "total_edges": 0,
                    "total_activations": 0, "born": None}

        asyncio.run(nm._persist())

        on_disk = asyncio.run(nm._load_neurons_sharded())
        assert len(on_disk) == 50
        assert "n1000" in on_disk, (
            "equal counts must still WRITE — the disk should now hold the "
            "in-memory set, not the seed it happened to match in size"
        )


def test_persist_writes_normally_when_disk_is_empty():
    """First-ever persist: disk is empty, in-memory has fresh data.
    Must write (not skip) — disk-empty is NOT a regression to guard against."""
    from aria_service.intel import neural_memory as nm

    fns = _fake_store()
    with _patched(nm, fns):
        # nothing seeded: the store is genuinely empty
        nm._neurons = _mem(10)
        nm._edges.clear()
        nm._meta = {"total_neurons": 10, "total_edges": 0,
                    "total_activations": 0, "born": None}

        asyncio.run(nm._persist())

        assert _disk_neuron_count(nm) == 10, (
            "first-write path — disk should now hold the 10-neuron in-memory state"
        )


def test_persist_within_10pct_tolerance_proceeds_normally():
    """Disk has only slightly more than memory (within 10% tolerance).
    Treated as normal growth fluctuation, not a migration race — write
    proceeds. Prevents over-conservative protection from blocking healthy
    writes during the natural decay/access cycle."""
    from aria_service.intel import neural_memory as nm

    fns = _fake_store()
    with _patched(nm, fns):
        _seed_disk(nm, _mem(105))

        nm._neurons = _mem(100)
        nm._edges.clear()
        nm._meta = {"total_neurons": 100, "total_edges": 0,
                    "total_activations": 0, "born": None}

        asyncio.run(nm._persist())

        # 105 / 100 = 1.05, NOT > 1.1 (the trigger threshold) — write proceeds
        assert _disk_neuron_count(nm) == 100, (
            "10% tolerance — disk had 105 (within 10%), should accept the 100 write"
        )
