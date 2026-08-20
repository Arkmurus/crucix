"""R-F1890 — encode process-offload.

Root cause of the recurring web-chat/brain wedge: model.encode() holds the GIL
in-process even via asyncio.to_thread (threads share one GIL). Fix: run encode
in a separate process (1-worker ProcessPoolExecutor, spawn). _safe_encode routes
to it when available and falls back to the in-process path on ANY problem.

Routing tests are fast (no model). The integration test does a real
separate-process encode and is skipped if sentence-transformers isn't installed.
"""
from __future__ import annotations

import numpy as np
import pytest

import aria_service.intel.encode_offload as eo
import aria_service.intel.semantic_search as ss
from ._env_probe import requires_module


# ── routing / fallback (fast, no model) ──────────────────────────────────────
def test_encode_raises_offload_unavailable_when_pool_not_started():
    # pool not started in this process → unavailable → caller must fall back
    assert eo.is_enabled() is False
    with pytest.raises(eo.OffloadUnavailable):
        eo.encode("hello")


def test_safe_encode_routes_to_offload_when_enabled(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(eo, "is_enabled", lambda: True)
    monkeypatch.setattr(eo, "encode", lambda text, normalize=True: sentinel)

    class _Model:
        def encode(self, *a, **k):
            raise AssertionError("in-process encode should NOT run when offload is enabled")

    out = ss._safe_encode(_Model(), "hi", normalize_embeddings=True)
    assert out is sentinel


def test_safe_encode_falls_back_when_offload_unavailable(monkeypatch):
    monkeypatch.setattr(eo, "is_enabled", lambda: True)
    def _boom(text, normalize=True):
        raise eo.OffloadUnavailable("pool down")
    monkeypatch.setattr(eo, "encode", _boom)

    class _Model:
        def encode(self, text, **k):
            return f"INPROCESS:{text}"

    out = ss._safe_encode(_Model(), "hi", normalize_embeddings=True)
    assert out == "INPROCESS:hi"   # transparent fallback


def test_safe_encode_skips_offload_for_unknown_kwargs(monkeypatch):
    monkeypatch.setattr(eo, "is_enabled", lambda: True)
    def _must_not_call(*a, **k):
        raise AssertionError("offload must NOT handle unknown kwargs")
    monkeypatch.setattr(eo, "encode", _must_not_call)

    class _Model:
        def encode(self, text, **k):
            return "INPROCESS"

    # batch_size is not in the offload-safe kwarg set → in-process path
    out = ss._safe_encode(_Model(), "hi", normalize_embeddings=True, batch_size=8)
    assert out == "INPROCESS"


# ── real separate-process integration (slow; skipped without the model) ───────
@requires_module("sentence_transformers")
def test_real_offload_encodes_in_separate_process_and_matches_inprocess():
    eo.start(warmup=True)
    try:
        assert eo.is_enabled(), (
            "sentence-transformers is installed but the encode offload pool did "
            "not start; this is a product failure, not an environment skip"
        )
        vec = eo.encode("a defence procurement contract", normalize=True)
        assert hasattr(vec, "shape") and vec.shape[-1] == 384  # MiniLM dim

        # correctness: matches an in-process encode of the same text (cosine ~1)
        ref = ss._get_embedder().encode("a defence procurement contract", normalize_embeddings=True)
        cos = float(np.dot(np.asarray(vec).ravel(), np.asarray(ref).ravel()))
        assert cos > 0.99, f"offloaded embedding differs from in-process (cos={cos:.4f})"
    finally:
        eo.stop()
