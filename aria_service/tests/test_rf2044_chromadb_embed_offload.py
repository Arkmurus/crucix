"""R-F2044 — the chromadb embedding path must use the process-offloaded encoder.

ROOT CAUSE (captured live in /data/wedge_stacks): a RAG query →
`_SharedSentenceTransformerEmbeddingFn.__call__` → the OLD code called
`semantic_search._get_embedder()` unconditionally (COLD-LOADING the in-process
SentenceTransformer on the request path, 10s+ under the event loop) and then ran
an in-process GIL-bound `model.encode()` — because chromadb passes
convert_to_numpy=True, which is NOT in _safe_encode's {normalize_embeddings}
offload allow-set, so RAG-query encodes NEVER offloaded and stalled the loop
6-11s at a time. With offload ON (default), main.py deliberately does NOT prewarm
the in-process model, so this path was *guaranteed* to cold-load.

These tests pin the contract: offload-first (no in-process cold load, normalize
matches), transparent in-process fallback when offload is unavailable, and the
chromadb 1.5+ `embed_query` entry point gets the same treatment.
"""
from __future__ import annotations

import numpy as np

import aria_service.intel.encode_offload as eo
import aria_service.intel.semantic_search as ss
from aria_service.intel.encode_offload import OffloadUnavailable
from aria_service.intel.rag_store import _SharedSentenceTransformerEmbeddingFn


def _fn():
    return _SharedSentenceTransformerEmbeddingFn("all-MiniLM-L6-v2")


def test_call_routes_to_offload_and_never_cold_loads_inprocess(monkeypatch):
    """The whole point: when offload is enabled the chromadb embed must NOT
    touch the in-process _get_embedder (the cold-load wedge) nor model.encode."""
    captured = {}

    def _fake_offload_encode(texts, normalize=True):
        captured["texts"] = texts
        captured["normalize"] = normalize
        return np.asarray([[0.1, 0.2, 0.3]] * len(texts), dtype="float32")

    monkeypatch.setattr(eo, "is_enabled", lambda: True)
    monkeypatch.setattr(eo, "encode", _fake_offload_encode)

    def _boom_embedder():
        raise AssertionError(
            "_get_embedder() must NOT be called when offload is enabled — that "
            "is the request-path cold-load wedge R-F2044 fixes"
        )
    monkeypatch.setattr(ss, "_get_embedder", _boom_embedder)
    monkeypatch.setattr(ss, "_safe_encode",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("in-process encode must not run")))

    out = _fn()(["hello world", "second text"])

    # list[list[float]] contract preserved (float32 → approx)
    assert isinstance(out, list) and isinstance(out[0], list)
    assert np.allclose(out, [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]], rtol=1e-5)
    assert captured["texts"] == ["hello world", "second text"]
    # R-F2044: normalize MUST be False to match chromadb's default + the existing
    # persisted (unnormalized) collection vectors — offload defaults to True.
    assert captured["normalize"] is False


def test_call_falls_back_inprocess_when_offload_disabled(monkeypatch):
    """Offload unavailable → transparent in-process fallback (no behaviour change
    vs the pre-R-F2044 path: _get_embedder + _safe_encode, convert_to_numpy)."""
    monkeypatch.setattr(eo, "is_enabled", lambda: False)

    sentinel_model = object()
    monkeypatch.setattr(ss, "_get_embedder", lambda: sentinel_model)

    seen = {}

    def _fake_safe_encode(model, texts, **kwargs):
        seen["model"] = model
        seen["texts"] = texts
        seen["kwargs"] = kwargs
        return np.asarray([[1.0, 2.0]] * len(texts), dtype="float32")

    monkeypatch.setattr(ss, "_safe_encode", _fake_safe_encode)

    out = _fn()(["a", "b"])

    assert out == [[1.0, 2.0], [1.0, 2.0]]
    assert seen["model"] is sentinel_model
    assert seen["texts"] == ["a", "b"]
    assert seen["kwargs"].get("convert_to_numpy") is True


def test_call_falls_back_inprocess_when_offload_errors(monkeypatch):
    """An offload exception (pool broken / OffloadUnavailable) must fall through
    to in-process, never propagate — the offload can never make embedding worse."""
    monkeypatch.setattr(eo, "is_enabled", lambda: True)

    def _boom(texts, normalize=True):
        raise OffloadUnavailable("pool down")
    monkeypatch.setattr(eo, "encode", _boom)

    monkeypatch.setattr(ss, "_get_embedder", lambda: object())
    monkeypatch.setattr(ss, "_safe_encode",
                        lambda model, texts, **k: np.asarray([[9.0]] * len(texts), dtype="float32"))

    out = _fn()(["x"])
    assert out == [[9.0]]


def test_embed_query_uses_same_offload_path(monkeypatch):
    """chromadb 1.5+ query protocol (embed_query) delegates to __call__, so the
    actual query-time embed (the wedge trigger) gets the offload treatment too."""
    calls = {}

    def _fake_offload_encode(texts, normalize=True):
        calls["texts"] = texts
        return np.asarray([[0.5, 0.5]] * len(texts), dtype="float32")

    monkeypatch.setattr(eo, "is_enabled", lambda: True)
    monkeypatch.setattr(eo, "encode", _fake_offload_encode)
    monkeypatch.setattr(ss, "_get_embedder",
                        lambda: (_ for _ in ()).throw(AssertionError("no cold load on query path")))

    out = _fn().embed_query("a single query string")
    assert out == [[0.5, 0.5]]
    assert calls["texts"] == ["a single query string"]   # str → [str] then offloaded
