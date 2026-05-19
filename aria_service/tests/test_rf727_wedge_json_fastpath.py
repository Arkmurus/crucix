"""R-F727 (2026-05-19) — regression tests for the wedge-fix json fast path.

Root cause captured in wedge_673 stack: passing `default=str` to
`json.dump` / `json.dumps` forces CPython's pure-Python `JSONEncoder`,
which holds the GIL through the entire serialisation. With 5 concurrent
worker threads in pure-Python json frames (3× neural_memory edges +
1× intel_ledger + 1× knowledge), the main event loop starved for
213.97s.

These tests verify two contracts on each of the three encode paths:

1. **Fast path works on JSON-native data** — the function must produce
   valid output for `{str: {str: float}}` (neural_memory edges shape)
   and `{str: any-native}` (knowledge / ledger cache shape) without
   the `default=` fallback firing. We can't directly observe the C
   vs Python encoder choice, but we can prove the fast path produces
   correct output (so any future refactor that breaks the fast path
   would be caught by output-shape regression).

2. **Fallback path still works on non-native types** — if a stray
   datetime / set / Path / etc. leaks in, the function must NOT
   raise. The `try/except TypeError → default=str` fallback keeps
   persist alive.

The wedge itself can't be unit-tested directly (it needs N concurrent
worker threads + heartbeat watchdog). The fast-path / fallback split
*is* unit-testable and is the load-bearing behaviour of R-F727.
"""
from __future__ import annotations

import asyncio
import datetime
import gzip
import json
import os
import tempfile
from pathlib import Path


# ── neural_memory._encode_edges ────────────────────────────────────────


def test_encode_edges_fast_path_on_pure_float_dict():
    """R-F727: _edges is structurally {str: {str: float}}. The fast
    path must accept it without falling back."""
    from aria_service.intel.neural_memory import _encode_edges, _decode_edges

    edges = {
        "neuron_a": {"neuron_b": 0.42, "neuron_c": 0.85},
        "neuron_b": {"neuron_d": 0.13},
    }
    encoded = _encode_edges(edges)
    assert isinstance(encoded, str)
    assert encoded.startswith("GZ1:")

    decoded = _decode_edges(encoded)
    assert decoded == edges


def test_encode_edges_fallback_on_non_native_value():
    """R-F727: a stray datetime / set in the edge dict (defensively
    possible) must NOT crash — fallback to default=str runs."""
    from aria_service.intel.neural_memory import _encode_edges, _decode_edges

    odd = {
        "neuron_a": {
            "neuron_b": 0.42,
            "neuron_d": datetime.date(2026, 5, 19),
        },
    }
    encoded = _encode_edges(odd)
    assert isinstance(encoded, str)
    assert encoded.startswith("GZ1:")

    decoded = _decode_edges(encoded)
    assert decoded is not None
    assert "neuron_a" in decoded
    # date stringified by the fallback
    assert decoded["neuron_a"]["neuron_d"] == "2026-05-19"


# ── intel_ledger._write_to_disk_atomic ────────────────────────────────


def test_intel_ledger_write_fast_path_on_native_dict(tmp_path, monkeypatch):
    """R-F727: signals cache is JSON-native (floats, strings, ints,
    nested dicts/lists). Fast path must serialise without fallback."""
    from aria_service.intel import intel_ledger as il

    target = tmp_path / "aria_signals.json"
    monkeypatch.setattr(il, "_DISK_PATH", str(target))

    data = {
        "signals": [
            {"id": "s1", "ts": 1779200000.0, "weight": 0.5, "tags": ["a", "b"]},
            {"id": "s2", "ts": 1779200001.5, "weight": 0.9, "tags": []},
        ],
        "counters": {"propaganda": 12, "verified": 36716},
    }
    il._write_to_disk_atomic(data)

    assert target.exists()
    with open(target, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded == data


def test_intel_ledger_write_fallback_on_non_native(tmp_path, monkeypatch):
    """R-F727: a stray datetime in the cache must not crash persist."""
    from aria_service.intel import intel_ledger as il

    target = tmp_path / "aria_signals.json"
    monkeypatch.setattr(il, "_DISK_PATH", str(target))

    data = {
        "signals": [],
        "last_ingest": datetime.datetime(2026, 5, 19, 21, 17, 5),
    }
    # Should not raise — falls back to default=str.
    il._write_to_disk_atomic(data)

    assert target.exists()
    with open(target, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    # Datetime stringified by fallback
    assert "2026-05-19" in loaded["last_ingest"]


# ── knowledge._write_to_disk_atomic ───────────────────────────────────


def test_knowledge_write_fast_path_on_native_dict(tmp_path, monkeypatch):
    """R-F727: knowledge cache is JSON-native. Fast path must
    serialise without fallback."""
    from aria_service.intel import knowledge as kn

    target = tmp_path / "aria_knowledge.json"
    monkeypatch.setattr(kn, "_DISK_PATH", str(target))

    data = {
        "facts": [
            {"id": "f1", "topic": "compliance", "content": "OFAC SDN list",
             "confidence": "CONFIRMED", "stored_at": 1779200000.0},
        ],
        "queries": [],
        "learnings": [],
    }
    kn._write_to_disk_atomic(data)

    assert target.exists()
    with open(target, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded == data


def test_knowledge_write_fallback_on_non_native(tmp_path, monkeypatch):
    """R-F727: any non-native value (Path, set, datetime) in the
    knowledge cache must not crash persist. The fallback handles
    non-native VALUES (json.dump rejects non-native KEYS regardless
    of default=, so we test the realistic value-side leak)."""
    from aria_service.intel import knowledge as kn

    target = tmp_path / "aria_knowledge.json"
    monkeypatch.setattr(kn, "_DISK_PATH", str(target))

    data = {
        "facts": [],
        "primary_source": Path("/data/aria_knowledge.json"),
        "stored_at": datetime.datetime(2026, 5, 19, 21, 17, 5),
    }
    kn._write_to_disk_atomic(data)

    assert target.exists()
    with open(target, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    # Both stringified by the default=str fallback
    assert "aria_knowledge.json" in loaded["primary_source"]
    assert "2026-05-19" in loaded["stored_at"]


# ── _encode_snapshot fast paths (run in to_thread per R-F714) ─────────


def test_intel_ledger_encode_snapshot_fast_path_and_fallback():
    """R-F727: _encode_snapshot is the gzipped-base64 wrapper used by the
    flush loop. Same default=str → pure-Python encoder bug. Verify fast
    path on native data + fallback on non-native."""
    from aria_service.intel import intel_ledger as il

    native = {
        "signals": [
            {"id": "s1", "ts": 1779200000.0, "weight": 0.5},
        ],
    }
    encoded = il._encode_snapshot(native)
    assert isinstance(encoded, str)
    assert encoded.startswith(il._GZ_PREFIX)
    # Round-trip via base64+gzip to confirm payload integrity
    import base64
    raw = gzip.decompress(base64.b64decode(encoded[len(il._GZ_PREFIX):]))
    assert json.loads(raw) == native

    # Non-native via fallback
    odd = {"signals": [], "last": datetime.datetime(2026, 5, 19)}
    encoded2 = il._encode_snapshot(odd)
    assert isinstance(encoded2, str)
    assert encoded2.startswith(il._GZ_PREFIX)


def test_knowledge_encode_snapshot_fast_path_and_fallback():
    """R-F727: same contract for knowledge sharded-snapshot encoder."""
    from aria_service.intel import knowledge as kn

    native = {
        "facts": [{"topic": "compliance", "content": "x", "stored_at": 1779200000.0}],
    }
    encoded = kn._encode_snapshot(native)
    assert isinstance(encoded, str)
    assert encoded.startswith(kn._GZ_PREFIX)

    import base64
    raw = gzip.decompress(base64.b64decode(encoded[len(kn._GZ_PREFIX):]))
    assert json.loads(raw) == native

    odd = {"facts": [], "born": datetime.date(2026, 5, 19)}
    encoded2 = kn._encode_snapshot(odd)
    assert isinstance(encoded2, str)
    assert encoded2.startswith(kn._GZ_PREFIX)


# ── crawler/fetcher._strip_html_to_text wrapped in to_thread ──────────


def test_strip_html_callable_inside_to_thread():
    """R-F727: the BeautifulSoup parse used to run on the main event
    loop in `fetch_for_crawl`; now wrapped in `asyncio.to_thread`. The
    function itself remains sync — verify it produces the expected
    tuple shape so the new `await asyncio.to_thread(...)` call site
    keeps working."""
    from aria_service.crawler.fetcher import _strip_html_to_text

    html = (
        "<html><head><title>Test Page</title></head>"
        "<body><h1>Hello</h1><p>A body sentence with enough words to pass "
        "the 20-word minimum applied downstream. One two three four five "
        "six seven eight nine ten eleven twelve thirteen.</p></body></html>"
    )

    async def _run():
        return await asyncio.to_thread(_strip_html_to_text, html)

    title, headings, body = asyncio.run(_run())
    assert title == "Test Page"
    assert "Hello" in headings
    assert "body sentence" in body
