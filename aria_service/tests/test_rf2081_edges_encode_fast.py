"""R-F2081 — neural edges encode must be fast enough to not wedge the loop.

The broken path: `_encode_edges` re-serialized the whole ~70 MB edges graph with
stdlib json + gzip level 6, holding the GIL ~7-8s in its worker thread and
wedging the single event loop every few minutes (live wedge_677: heartbeat stale
8.40s/6.72s/7.61s with `_encode_edges` at the top frame) — the brain-side root
cause of slow web/dashboard population. The fix: orjson + gzip level 1 (~6.6x
faster, measured 4243ms -> 645ms on a prod-matched payload).

These tests invoke the real `_encode_edges`/`_decode_edges` and assert (a) it
round-trips losslessly and (b) it encodes a large graph well under the 5s wedge
threshold.
"""
import time

from aria_service.intel.neural_memory import _encode_edges, _decode_edges, _GZ_PREFIX


def _make_edges(n_groups: int, fanout: int) -> dict:
    # deterministic {str: {str: float}} mirroring the real edges structure
    return {
        f"concept_{i}": {f"target_{j}": round((i * 31 + j) % 1000 / 1000.0, 5)
                         for j in range(fanout)}
        for i in range(n_groups)
    }


def test_rf2081_round_trips_losslessly():
    edges = _make_edges(500, 30)
    blob = _encode_edges(edges)
    assert isinstance(blob, str) and blob.startswith(_GZ_PREFIX), "must be gzipped GZ1: form"
    decoded = _decode_edges(blob)
    assert decoded == edges, "encode->decode must be lossless"


def test_rf2081_large_graph_encodes_under_wedge_threshold():
    # ~600k edges — large enough that the OLD json+gzip6 path was multi-second.
    # orjson+gzip1 must stay well under the 5s event-loop stall threshold.
    edges = _make_edges(20_000, 30)
    t0 = time.monotonic()
    blob = _encode_edges(edges)
    elapsed = time.monotonic() - t0
    assert elapsed < 3.0, f"encode took {elapsed:.2f}s — must stay well under the 5s wedge threshold"
    # and it must still round-trip
    assert _decode_edges(blob) == edges


def test_rf2081_unserializable_value_falls_back_not_crashes():
    # A non-native value leaking in must not crash persist (defensive default=str).
    class Weird:
        def __str__(self):
            return "weird"
    edges = {"concept_a": {"target_x": 0.5}, "concept_b": {"target_y": Weird()}}
    blob = _encode_edges(edges)               # must not raise
    decoded = _decode_edges(blob)
    assert decoded["concept_b"]["target_y"] == "weird", "fallback should str()-coerce the odd value"
    assert decoded["concept_a"] == {"target_x": 0.5}
