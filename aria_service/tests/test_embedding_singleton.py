"""Regression test for F79 — single sentence-transformer model in process.

Before the fix, rag_store.py used chromadb's
SentenceTransformerEmbeddingFunction which internally instantiated its
own SentenceTransformer in addition to the singleton in
semantic_search._get_embedder(). Two model copies in process: ~3s extra
cold start, ~25 wasted HF HEAD requests at boot, and 5-10× slower
encode batches once both loaded.

After the fix, rag_store uses _SharedSentenceTransformerEmbeddingFn
which delegates to the semantic_search singleton.

This test asserts the wiring without actually loading the model
(sentence-transformers may not be installed in CI).
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock


def test_shared_embedding_fn_delegates_to_semantic_search_singleton():
    """The chromadb-side embedder must NOT instantiate its own model —
    it must fetch the singleton from semantic_search."""
    from aria_service.intel import rag_store

    fn = rag_store._SharedSentenceTransformerEmbeddingFn("all-MiniLM-L6-v2")

    # Build a fake model whose .encode returns a numpy-like with .tolist()
    fake_model = MagicMock()
    fake_array = MagicMock()
    fake_array.tolist.return_value = [[0.1, 0.2, 0.3]]
    fake_model.encode.return_value = fake_array

    # Patch the singleton accessor — if the wrapper bypassed it and
    # spun up its own SentenceTransformer, this patch would have no
    # effect and the test would fail or call the real model.
    with patch("aria_service.intel.semantic_search._get_embedder",
               return_value=fake_model) as mock_get:
        out = fn(["sample text"])

    mock_get.assert_called_once()
    fake_model.encode.assert_called_once()
    args, kwargs = fake_model.encode.call_args
    # chromadb passes a list — wrapper must preserve list shape, not
    # collapse to a single string (which would change embedding semantics).
    assert args[0] == ["sample text"]
    assert kwargs.get("convert_to_numpy") is True
    # normalize_embeddings must NOT be passed — chromadb's HNSW cosine
    # space expects unnormalised vectors. Passing True would invalidate
    # every existing collection vector in production.
    assert "normalize_embeddings" not in kwargs
    assert out == [[0.1, 0.2, 0.3]]


def test_shared_embedding_fn_raises_when_singleton_unavailable():
    """If semantic_search has no model (e.g. sentence-transformers not
    installed), the wrapper must raise loudly rather than silently
    returning empty embeddings — silent corruption would land empty
    vectors in chromadb that can never match anything."""
    from aria_service.intel import rag_store
    fn = rag_store._SharedSentenceTransformerEmbeddingFn("all-MiniLM-L6-v2")
    with patch("aria_service.intel.semantic_search._get_embedder",
               return_value=None):
        try:
            fn(["text"])
        except RuntimeError as e:
            assert "unavailable" in str(e).lower()
        else:
            raise AssertionError("RuntimeError not raised when embedder is None")


def test_shared_embedding_fn_name_matches_chromadb_default():
    """MUST equal chromadb's built-in
    SentenceTransformerEmbeddingFunction.name() so existing collections
    (which may persist this string in metadata) continue to load. If
    this assertion fails after a chromadb upgrade, verify the new
    upstream name and update both sides together — a one-sided change
    will refuse to load prod collections."""
    from aria_service.intel import rag_store
    fn = rag_store._SharedSentenceTransformerEmbeddingFn("all-MiniLM-L6-v2")
    assert fn.name() == "sentence_transformer"
