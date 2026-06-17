"""R-F1639 — neural_memory._encode_edges must run its json.dumps+gzip with
cyclic GC disabled (the last instance of the GIL-bound-serialization wedge class;
wedge_675 named neural_memory:164). Same root + fix as R-F1621 for knowledge.py.
Drives the REAL _encode_edges / _decode_edges.
"""
from __future__ import annotations

import gc
import json

from aria_service.intel import neural_memory as NM


def test_encode_edges_disables_gc_during_dump_and_restores(monkeypatch):
    gc.enable()
    seen = {}
    real = json.dumps

    def _spy(obj, *a, **k):
        seen["gc_enabled_during_dump"] = gc.isenabled()
        return real(obj, *a, **k)

    monkeypatch.setattr(NM.json, "dumps", _spy)
    out = NM._encode_edges({"a": {"b": 0.5}})

    assert seen.get("gc_enabled_during_dump") is False, "GC must be OFF during the edges dump"
    assert gc.isenabled() is True, "GC must be restored after"
    assert isinstance(out, str) and out  # produced a blob


def test_encode_decode_round_trips():
    edges = {"x": {"y": 1.0, "z": 0.25}, "p": {"q": 0.9}}
    blob = NM._encode_edges(edges)
    back = NM._decode_edges(blob)
    assert back == edges, "encode→decode must be lossless"


def test_gc_restored_even_on_dump_error(monkeypatch):
    gc.enable()

    def _boom(*a, **k):
        raise ValueError("dump boom")

    monkeypatch.setattr(NM.json, "dumps", _boom)
    raised = False
    try:
        NM._encode_edges({"a": {"b": 1.0}})
    except ValueError:
        raised = True
    assert raised
    assert gc.isenabled() is True, "GC must be restored even when the dump raises"


def test_does_not_enable_gc_if_caller_disabled_it():
    gc.disable()
    try:
        NM._encode_edges({"a": {"b": 1.0}})
        assert gc.isenabled() is False, "must not turn GC back on if a caller had it off"
    finally:
        gc.enable()
