"""R-F442 — neural_edges dirty-flag + gzip capability tests.

Live evidence 2026-05-13 21:07 fly logs:
    Redis SET crucix:aria:neural_edges: value size 4140494 bytes
    exceeds warn threshold (4000000). Plan a key split before this
    hits the tier cap.
    (Same line fired 8× in ~30 seconds.)

Root cause: _persist() unconditionally rewrote the 4.14 MB edges blob
on every call, including from recall() — which only mutates neuron
fields (activation, last_activated), never _edges. Same root pattern
as R-F1 (knowledge gzip) and R-F36 (ledger gzip), with the extra waste
that the edges weren't even changing on the noisiest write path.

R-F442 fix shape:
  1. _edges_dirty flag — set in _strengthen_edge + _apply_decay (the
     only sites that mutate _edges). recall() leaves it False so
     _persist() skips the edges write entirely.
  2. Gzip the edges blob with the GZ1: prefix when it IS written.
     4.14 MB → ~700 KB at compresslevel=6.
  3. init() decodes both gzipped and legacy raw-JSON values so the
     existing snapshot migrates forward on the next write.

These tests prove the user-visible symptom (warn-flood per minute) is
fixed, not just internal logic.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
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


def _fake_storage(initial: dict | None = None):
    """Build a fake rs backend that captures raw set/get + set_json/get_json.
    Returns (storage_dict, write_log, mocks_dict)."""
    storage: dict = dict(initial or {})
    write_log: list[tuple[str, str]] = []  # (key, kind) — kind = 'raw' or 'json'

    async def fake_get(key):
        return storage.get(key)

    async def fake_set(key, val, *args, **kwargs):
        storage[key] = val
        write_log.append((key, "raw"))

    async def fake_get_json(key):
        v = storage.get(key)
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return None
        return None

    async def fake_set_json(key, val, *args, **kwargs):
        storage[key] = val
        write_log.append((key, "json"))

    return storage, write_log, {
        "get": fake_get,
        "set": fake_set,
        "get_json": fake_get_json,
        "set_json": fake_set_json,
    }


def _patched(nm, mocks):
    """Apply the rs mocks. _client=None forces the fallback path that
    uses rs.set / rs.set_json directly (instead of rs._client.pipeline)."""
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


# ── Test 1: the user-visible symptom (warn flood) is fixed ──────────────


def test_recall_pattern_does_not_write_edges():
    """The hot path: _persist() is called many times but _edges is never
    mutated (this is what recall() does). Edges blob must NOT be written.

    Pre-R-F442 this scenario wrote the 4.14 MB edges blob 8x/min in prod.
    """
    from aria_service.intel import neural_memory as nm

    nm._neurons = {f"n{i}": {"id": f"n{i}", "concept": f"c{i}", "activation": 0.5}
                   for i in range(20)}
    nm._edges.clear()
    nm._edges["n1"] = {"n2": 0.5}
    nm._meta = {"total_neurons": 20, "total_edges": 1, "total_activations": 0, "born": None}
    nm._edges_dirty = False  # recall() doesn't dirty edges

    storage, write_log, mocks = _fake_storage()

    patches = _patched(nm, mocks)
    for p in patches:
        p.start()
    try:
        # 5 recall-shaped persists in a row — only neurons should be written
        for _ in range(5):
            asyncio.run(nm._persist())
    finally:
        for p in patches:
            p.stop()

    # R-F2082: edges (single blob OR shards) must not be written when clean.
    edges_writes = [k for k, _ in write_log
                    if k == "crucix:aria:neural_edges" or k.startswith("crucix:aria:neural_edges:")]
    assert edges_writes == [], (
        f"R-F442: edges should not have been written when _edges_dirty=False. "
        f"Got {len(edges_writes)} edges writes."
    )
    # Neurons should still be written each time. R-F699 sharded them, so the
    # write target is crucix:aria:neurons:shard:N (+ :meta), not the legacy key.
    neuron_writes = [k for k, _ in write_log if k.startswith("crucix:aria:neurons:shard:")]
    assert len(neuron_writes) >= 5, (
        f"Neurons should still write on every persist; got {len(neuron_writes)} shard writes."
    )


# ── Test 2: when edges DO change, they get written + gzipped ────────────


def test_dirty_edges_are_written_to_shards():
    """R-F2082: when _edges_dirty=True, _persist writes the edges to SHARD keys
    (each gzipped GZ1:), NOT the single legacy blob, and the sharded set round-
    trips. (Superseded the single-blob GZ1: write the old R-F442 test asserted.)"""
    from aria_service.intel import neural_memory as nm

    nm._neurons = {"a": {"id": "a", "concept": "alpha", "activation": 0.5}}
    nm._edges.clear()
    nm._edges["a"] = {"b": 0.7}
    nm._meta = {"total_neurons": 1, "total_edges": 1, "total_activations": 0, "born": None}
    nm._edges_dirty = True

    storage, write_log, mocks = _fake_storage()

    patches = _patched(nm, mocks)
    for p in patches:
        p.start()
    try:
        asyncio.run(nm._persist())
        loaded = asyncio.run(nm._load_edges_sharded())
    finally:
        for p in patches:
            p.stop()

    # The legacy single key must NOT be written any more.
    assert storage.get("crucix:aria:neural_edges") is None, "legacy single blob must not be written"
    # Edges go to shard keys, each a gzipped GZ1: blob.
    shard_writes = [k for k, _ in write_log if k.startswith("crucix:aria:neural_edges:shard:")]
    assert shard_writes, "edges must be written to shard keys"
    nonempty = [storage[k] for k in shard_writes if isinstance(storage.get(k), str)]
    assert any(v.startswith("GZ1:") for v in nonempty), "shard blobs must be gzipped GZ1:"
    # And the sharded set round-trips back to the original edges.
    assert loaded == {"a": {"b": 0.7}}, f"Sharded round-trip mismatch: {loaded}"
    # Dirty flag clears after a successful write.
    assert nm._edges_dirty is False, "Dirty flag must clear after successful write"


# ── Test 3: _strengthen_edge marks edges dirty ──────────────────────────


def test_strengthen_edge_marks_dirty():
    """The mutator side: every edge boost flips _edges_dirty=True."""
    from aria_service.intel import neural_memory as nm

    nm._edges.clear()
    nm._edges_dirty = False
    nm._strengthen_edge("x", "y", boost=0.1)
    assert nm._edges_dirty is True
    # And the edge actually exists
    assert nm._edges["x"]["y"] > 0


# ── Test 4: _apply_decay marks edges dirty when it runs ─────────────────


def test_apply_decay_marks_dirty_when_it_runs():
    """When decay actually executes (past the 0.25-day throttle), it
    mutates every edge weight and must set _edges_dirty=True."""
    from aria_service.intel import neural_memory as nm

    nm._neurons = {"a": {"id": "a", "concept": "alpha", "activation": 0.5,
                          "last_decayed": 0}}
    nm._edges.clear()
    nm._edges["a"] = {"b": 0.5}
    nm._edges_dirty = False
    nm._LAST_GLOBAL_DECAY = 0  # force a decay pass to run

    nm._apply_decay()

    assert nm._edges_dirty is True, "Decay touched all edges; dirty flag must flip"


def test_apply_decay_throttled_does_not_mark_dirty():
    """When decay short-circuits on its 0.25-day throttle, it doesn't
    mutate anything, so the dirty flag stays False."""
    from aria_service.intel import neural_memory as nm
    import time as _time

    nm._neurons = {}
    nm._edges.clear()
    nm._edges_dirty = False
    nm._LAST_GLOBAL_DECAY = _time.time()  # just decayed → next call short-circuits

    nm._apply_decay()

    assert nm._edges_dirty is False, "Throttled decay should not mark dirty"


# ── Test 5: init() accepts legacy raw-JSON blob (forward migration) ─────


def test_init_accepts_legacy_raw_json_edges():
    """Existing prod data is raw JSON. init() must decode it AND set the
    dirty flag so the next _persist() migrates it to gzipped format."""
    from aria_service.intel import neural_memory as nm

    legacy_edges = {"n1": {"n2": 0.5, "n3": 0.2}, "n2": {"n1": 0.5}}
    storage, _, mocks = _fake_storage({
        "crucix:aria:neurons": {"n1": {"id": "n1", "concept": "c1", "activation": 0.5}},
        "crucix:aria:neural_edges": json.dumps(legacy_edges),  # raw JSON, no GZ1: prefix
        "crucix:aria:neural_meta": {"total_neurons": 1, "total_edges": 2,
                                     "total_activations": 0, "born": None},
    })

    patches = _patched(nm, mocks)
    for p in patches:
        p.start()
    try:
        asyncio.run(nm.init())
    finally:
        for p in patches:
            p.stop()

    assert dict(nm._edges) == legacy_edges, "Legacy edges must load"
    assert nm._edges_dirty is True, (
        "After loading raw-JSON edges, dirty must be True so the next "
        "persist migrates to GZ1: format"
    )


def test_init_accepts_gzipped_legacy_edges_and_migrates_to_shards():
    """R-F2082: a gzipped LEGACY single-blob still decodes correctly on init,
    but now dirty=True so the first persist migrates it to the sharded form.
    (Pre-R-F2082 a gzipped blob was 'in sync' → dirty=False; sharding supersedes
    that — any single-blob is legacy until written as shards.)"""
    from aria_service.intel import neural_memory as nm

    edges_obj = {"n1": {"n2": 0.5, "n3": 0.2}, "n2": {"n1": 0.5}}
    # Build a GZ1: blob the same way _encode_edges does
    raw = json.dumps(edges_obj).encode("utf-8")
    gz = gzip.compress(raw, compresslevel=6)
    gz_str = "GZ1:" + base64.b64encode(gz).decode("ascii")

    storage, _, mocks = _fake_storage({
        "crucix:aria:neurons": {"n1": {"id": "n1", "concept": "c1", "activation": 0.5}},
        "crucix:aria:neural_edges": gz_str,
        "crucix:aria:neural_meta": {"total_neurons": 1, "total_edges": 2,
                                     "total_activations": 0, "born": None},
    })

    patches = _patched(nm, mocks)
    for p in patches:
        p.start()
    try:
        asyncio.run(nm.init())
    finally:
        for p in patches:
            p.stop()

    assert dict(nm._edges) == edges_obj, "Gzipped legacy edges must decode correctly"
    assert nm._edges_dirty is True, (
        "R-F2082: a legacy single-blob must migrate to shards on first persist → dirty=True"
    )
    assert nm._edges_sharded_initialized is False, "not yet in sharded form"


# ── Test 6: encode/decode is lossless on real-shaped data ───────────────


def test_edges_encode_decode_roundtrip():
    """The compression wrapper must round-trip cleanly on edges of various
    sizes (including ints, floats, nested dicts)."""
    from aria_service.intel import neural_memory as nm

    cases = [
        {},
        {"a": {"b": 0.5}},
        {f"n{i}": {f"n{j}": round(0.1 * j, 3) for j in range(5)} for i in range(50)},
    ]
    for c in cases:
        encoded = nm._encode_edges(c)
        assert encoded.startswith("GZ1:"), f"Missing prefix for {c}"
        decoded = nm._decode_edges(encoded)
        assert decoded == c, f"Round-trip lost data: {decoded} != {c}"


def test_decode_edges_returns_none_on_garbage():
    """Defensive — bad input shouldn't crash, just return None."""
    from aria_service.intel import neural_memory as nm

    assert nm._decode_edges(None) is None
    assert nm._decode_edges("") is None
    assert nm._decode_edges("not-json-at-all") is None
    assert nm._decode_edges("GZ1:not-base64-either") is None
    # Valid gzip but JSON-undecodable — also None
    assert nm._decode_edges(123) is None  # type: ignore[arg-type]


# ── R-F443 chain tests — interactions with R-F371 disk-reload guard ─────


def test_r_f371_reload_path_restores_edges_from_gzipped_disk():
    """Chain test: when R-F371's disk-reload guard fires (in-memory had
    fewer neurons than disk — typical post-migration race), the disk
    edges must be decoded through the new R-F442 _decode_edges so the
    in-memory graph is restored correctly. Pre-R-F442 this used
    rs.get_json which would return None on a gzipped value and lose
    all edges silently."""
    from aria_service.intel import neural_memory as nm

    # Build the larger "disk" state — gzipped edges
    big_neurons = {f"n{i}": {"id": f"n{i}", "concept": f"c{i}", "activation": 0.5}
                   for i in range(5000)}
    big_edges_obj = {f"n{i}": {f"n{(i+1) % 5000}": 0.5} for i in range(5000)}
    big_edges_gz = nm._encode_edges(big_edges_obj)

    storage, _, mocks = _fake_storage({
        "crucix:aria:neurons": big_neurons,
        "crucix:aria:neural_edges": big_edges_gz,
        "crucix:aria:neural_meta": {"total_neurons": 5000, "total_edges": 5000,
                                     "total_activations": 0, "born": None},
    })

    # In-memory has only the pre-migration sliver
    nm._neurons = {f"n{i}": {"id": f"n{i}", "concept": f"c{i}", "activation": 0.5}
                   for i in range(500)}
    nm._edges.clear()
    nm._edges["n0"] = {"n1": 0.5}
    nm._meta = {"total_neurons": 500, "total_edges": 1, "total_activations": 0, "born": None}
    nm._edges_dirty = True  # pretend a mutation happened

    patches = _patched(nm, mocks)
    for p in patches:
        p.start()
    try:
        asyncio.run(nm._persist())
    finally:
        for p in patches:
            p.stop()

    # Neurons should be reloaded to 5000 (R-F371 behaviour)
    assert len(nm._neurons) == 5000, (
        f"R-F371 reload: expected 5000 neurons, got {len(nm._neurons)}"
    )
    # Edges should be reloaded to 5000 too (NOT lost during the gzipped decode)
    assert len(nm._edges) == 5000, (
        f"R-F442 chain bug: gzipped disk edges lost during R-F371 reload. "
        f"Expected 5000 edge groups, got {len(nm._edges)}"
    )
    # And dirty flag must be False — disk is the source of truth after reload
    assert nm._edges_dirty is False, (
        "After R-F371 disk-reload, in-memory == disk → dirty must be False"
    )


def test_r_f371_reload_handles_legacy_raw_json_edges():
    """Same as above but disk holds legacy raw-JSON edges. The R-F371
    reload path must still decode them correctly (forward-compat)."""
    from aria_service.intel import neural_memory as nm

    big_neurons = {f"n{i}": {"id": f"n{i}", "concept": f"c{i}", "activation": 0.5}
                   for i in range(2000)}
    legacy_edges = {f"n{i}": {f"n{(i+1) % 2000}": 0.5} for i in range(2000)}

    storage, _, mocks = _fake_storage({
        "crucix:aria:neurons": big_neurons,
        "crucix:aria:neural_edges": json.dumps(legacy_edges),  # raw JSON
        "crucix:aria:neural_meta": {"total_neurons": 2000, "total_edges": 2000,
                                     "total_activations": 0, "born": None},
    })

    nm._neurons = {"n0": {"id": "n0", "concept": "c0", "activation": 0.5}}
    nm._edges.clear()
    nm._meta = {"total_neurons": 1, "total_edges": 0, "total_activations": 0, "born": None}

    patches = _patched(nm, mocks)
    for p in patches:
        p.start()
    try:
        asyncio.run(nm._persist())
    finally:
        for p in patches:
            p.stop()

    assert len(nm._neurons) == 2000
    assert len(nm._edges) == 2000, (
        "Legacy raw-JSON edges must decode during R-F371 reload too"
    )


# ── R-F443: bytes-prefix detection (future-proof for non-decode backends) ──


def test_init_treats_bytes_gz_prefix_as_already_gzipped():
    """If a future Redis backend ever returns bytes instead of str,
    b'GZ1:...' must be detected as gzipped (not flagged as legacy that
    needs migration). Pre-R-F443 the legacy-detect code only checked
    str-form GZ1:, so a bytes payload would trigger a useless rewrite
    on every boot."""
    from aria_service.intel import neural_memory as nm

    edges_obj = {"n1": {"n2": 0.5}}
    raw = json.dumps(edges_obj).encode("utf-8")
    gz = gzip.compress(raw, compresslevel=6)
    gz_bytes = b"GZ1:" + base64.b64encode(gz)  # bytes form

    storage, _, mocks = _fake_storage({
        "crucix:aria:neurons": {"n1": {"id": "n1", "concept": "c1", "activation": 0.5}},
        "crucix:aria:neural_edges": gz_bytes,
        "crucix:aria:neural_meta": {"total_neurons": 1, "total_edges": 1,
                                     "total_activations": 0, "born": None},
    })

    patches = _patched(nm, mocks)
    for p in patches:
        p.start()
    try:
        asyncio.run(nm.init())
    finally:
        for p in patches:
            p.stop()

    assert dict(nm._edges) == edges_obj, "bytes GZ1: blob must decode like str form"
    assert nm._edges_dirty is True, (
        "R-F2082: a legacy single-blob (bytes GZ1: form) migrates to shards → dirty=True"
    )


def test_init_handles_empty_disk_gracefully():
    """If both NEURONS_KEY and EDGES_KEY are absent (fresh deploy with
    no prior state), init must not crash and must leave _edges_dirty
    False (nothing to migrate)."""
    from aria_service.intel import neural_memory as nm

    storage, _, mocks = _fake_storage({})  # everything returns None

    patches = _patched(nm, mocks)
    for p in patches:
        p.start()
    try:
        asyncio.run(nm.init())
    finally:
        for p in patches:
            p.stop()

    assert dict(nm._edges) == {}
    assert nm._edges_dirty is False, (
        "Empty disk → nothing to migrate → dirty should stay False"
    )
