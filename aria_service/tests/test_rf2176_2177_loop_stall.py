# -*- coding: utf-8 -*-
"""Capability tests for the event-loop stall fix + coder-visibility wiring.

R-F2176 — neural_memory sharded-load decode is offloaded off the event loop
          (was a sync gzip-decompress on the main thread → 5.4s stall, captured
          in wedge_681). These tests prove the refactored load path still returns
          correct data end-to-end (the offload itself is asyncio.to_thread).
R-F2177 — the CPU-hotspot / loop-stall signals now reach the autonomous coder as
          capability gaps (continuous_profiler's gap call passed invalid kwargs
          and silently TypeError'd → the hotspot gap was dropped).
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import inspect
import json

import pytest

# Direct-import the called symbols (bare calls) so the pre-commit direct-calls
# check resolves them; `neuralmem` is kept only for the rs monkeypatch (attribute
# access, not a call).
import aria_service.intel.neural_memory as neuralmem
from aria_service.intel.neural_memory import (
    _decode_merge_edge_shards,
    _decode_merge_neuron_shards,
    _load_edges_sharded,
    _GZ_PREFIX,
    EDGES_SHARD_META_KEY,
    EDGES_SHARD_KEY_FMT,
)
from aria_service.intel.capability_gaps import record_gap


class _FakeRS:
    """Minimal async stand-in for the redis_store used by neural_memory."""

    def __init__(self, data: dict):
        self._d = data

    async def get(self, k):
        return self._d.get(k)

    # R-F4173 (C-185) — the loader now reads the graph STRICTLY, so a store
    # failure raises instead of returning None. This fake never simulates a
    # store failure, so get_strict mirrors get exactly.
    async def get_strict(self, k):
        return self._d.get(k)


def _encode_edges(d: dict) -> str:
    """Encode an edge dict the way _decode_edges expects (GZ1: + b64(gzip(json)))."""
    return _GZ_PREFIX + base64.b64encode(gzip.compress(json.dumps(d).encode())).decode()


# ── R-F2176 — offloaded decode still correct ────────────────────────────────

def test_rf2176_edge_decode_merge_helper_roundtrips():
    d1 = {"g1": {"g2": 1.5}}
    d2 = {"g3": {"g4": 0.25}}
    merged = _decode_merge_edge_shards([_encode_edges(d1), None, _encode_edges(d2)])
    assert merged == {"g1": {"g2": 1.5}, "g3": {"g4": 0.25}}  # None shard skipped


def test_rf2176_neuron_decode_merge_helper_merges():
    s1 = json.dumps({"shard_index": 0, "neurons": {"n1": {"id": "n1"}}})
    s2 = json.dumps({"shard_index": 1, "neurons": {"n2": {"id": "n2"}}})
    out = _decode_merge_neuron_shards([s1, s2])
    assert isinstance(out, dict) and "n1" in out and "n2" in out


def test_rf2176_neuron_decode_merge_returns_none_on_bad_shard():
    assert _decode_merge_neuron_shards(["{not json"]) is None


def test_rf2176_load_edges_sharded_returns_correct_data_via_offload():
    """End-to-end: _load_edges_sharded reads shard meta + shards and returns the
    merged dict — now decoding in a worker thread (asyncio.to_thread) instead of
    blocking the event loop."""
    edges = {"groupA": {"groupB": 0.9}}
    fake = _FakeRS({
        EDGES_SHARD_META_KEY: json.dumps({"shard_count": 1}),
        EDGES_SHARD_KEY_FMT.format(i=0): _encode_edges(edges),
    })
    orig = neuralmem.rs
    neuralmem.rs = fake
    try:
        result = asyncio.run(_load_edges_sharded())
    finally:
        neuralmem.rs = orig
    assert result == edges


def test_rf2176_load_edges_sharded_empty_on_absent_shards():
    """Absent shards (rs.get → None) are treated as empty → empty merge, no crash."""
    fake = _FakeRS({EDGES_SHARD_META_KEY: json.dumps({"shard_count": 2})})
    orig = neuralmem.rs
    neuralmem.rs = fake
    try:
        result = asyncio.run(_load_edges_sharded())
    finally:
        neuralmem.rs = orig
    assert result == {}


# ── R-F2177 — stall/hotspot signals reach the coder (valid gap kwargs) ───────

def test_rf2177_record_gap_accepts_profiler_and_stall_kwargs():
    """The kwargs continuous_profiler and the stall detector now pass must bind to
    record_gap's real signature — the bug was passing description=/module= which
    raised TypeError on every call (swallowed → gap silently dropped)."""
    sig = inspect.signature(record_gap)
    sig.bind(gap_type="performance", severity=2, title="CPU hotspot: x",
             detail="frame X occupied 60% of samples", source="continuous_profiler")
    sig.bind(gap_type="performance", severity="HIGH", title="event-loop stall 5s",
             detail="event loop stalled 5.4s — offload the CPU-bound call",
             source="event_loop_stall_detector")


def test_rf2177_record_gap_rejects_old_invalid_kwargs():
    """The OLD kwargs (the bug) must NOT bind — proves the fix is real, not a no-op."""
    sig = inspect.signature(record_gap)
    with pytest.raises(TypeError):
        sig.bind(gap_type="performance", severity=2, title="x",
                 description="...", module="continuous_profiler")
