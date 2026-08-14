"""Regression test for F83 — index_facts_batch collapses N model.encode calls into one.

Live log 2026-04-29 07:12:04 showed 17 separate `Batches: 1/1`
sentence-transformer log lines per article ingest. 15 of those came
from researcher._process_analysis calling knowledge.store_fact() per
fact, and store_fact's tail invokes semantic_search.index_fact() which
runs model.encode() once per call.

After the fix:
  - knowledge.store_fact accepts skip_semantic_index=True
  - semantic_search exposes index_facts_batch(items) which calls
    model.encode(list_of_texts, ...) ONCE
  - researcher._process_analysis (and friends) collect items during
    the per-fact loop and flush in one batch call

This test asserts the wiring without loading the real model.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock


def test_index_facts_batch_makes_one_encode_call():
    """N facts must produce exactly ONE model.encode invocation."""
    from aria_service.intel import semantic_search

    # Reset the singleton so we can verify add_batch
    test_index = semantic_search.SemanticIndex()

    # Fake model: returns N stacked vectors when called with N texts
    fake_model = MagicMock()
    fake_model.encode.side_effect = lambda texts, **kwargs: [[float(i), 0.0, 0.0] for i in range(len(texts))]

    items = [
        ("fact_1", "Angola defence procurement signed 2026", {"confidence": "CONFIRMED"}),
        ("fact_2", "Mozambique FADM mortar tender", {"confidence": "ASSESSED"}),
        ("fact_3", "Turkish Aselsan radar export", {"confidence": "PROBABLE"}),
        ("fact_4", "OFAC SDN designation new", {"confidence": "CONFIRMED"}),
        ("fact_5", "EU export licence ECJU update", {"confidence": "ASSESSED"}),
    ]

    with patch.object(semantic_search, "_get_embedder", return_value=fake_model):
        test_index.add_batch(items)

    # The whole point of the fix: ONE encode call, with all 5 texts
    # passed as a list (not 5 separate calls).
    assert fake_model.encode.call_count == 1, (
        f"Expected 1 batched encode call, got {fake_model.encode.call_count}. "
        "F83 regression — semantic index is back to per-fact encoding."
    )
    args, kwargs = fake_model.encode.call_args
    assert isinstance(args[0], list)
    assert len(args[0]) == 5
    assert kwargs.get("normalize_embeddings") is True

    # All 5 docs should be present in the TF-IDF and embedding indices.
    assert len(test_index._embeddings) == 5
    for fid in ("fact_1", "fact_2", "fact_3", "fact_4", "fact_5"):
        assert fid in test_index._docs
        assert fid in test_index._embeddings


def test_index_facts_batch_skips_empty_items():
    """Items with empty text or no tokens must not contribute to the
    encode call — matches add()'s contract for backward-compat."""
    from aria_service.intel import semantic_search

    test_index = semantic_search.SemanticIndex()
    fake_model = MagicMock()
    fake_model.encode.return_value = [[0.0, 0.0, 0.0]]

    items = [
        ("ok", "Real fact about defence procurement", None),
        ("empty_text", "", None),                   # skipped — no text
        ("none_text", None, None),                  # skipped — no text
        ("only_stopwords", "the and or",             # skipped — no tokens after filter
         {"confidence": "ASSESSED"}),
    ]

    with patch.object(semantic_search, "_get_embedder", return_value=fake_model):
        test_index.add_batch(items)

    # Only one item survives the filter, so encode receives a list of 1.
    args, _ = fake_model.encode.call_args
    assert len(args[0]) == 1
    assert "ok" in test_index._embeddings
    assert "empty_text" not in test_index._embeddings
    assert "none_text" not in test_index._embeddings
    assert "only_stopwords" not in test_index._embeddings


def test_index_facts_batch_no_op_on_empty_input():
    """Empty input must NOT trigger a model load or encode call.
    Important because callers (researcher._process_analysis etc.)
    may pass an empty semantic_batch when the article had no facts."""
    from aria_service.intel import semantic_search

    test_index = semantic_search.SemanticIndex()
    fake_model = MagicMock()

    with patch.object(semantic_search, "_get_embedder", return_value=fake_model):
        test_index.add_batch([])

    fake_model.encode.assert_not_called()


def test_store_fact_skip_semantic_index_flag_skips_per_fact_index():
    """knowledge.store_fact(skip_semantic_index=True) must NOT call
    semantic_search.index_fact in its tail. This is the pair to the
    batch helper — callers that batch downstream rely on this skip."""
    import asyncio
    from aria_service.intel import knowledge

    # Keep the on-disk knowledge JSON pristine — patch _save and _load.
    fake_db = {"facts": [], "queries": []}

    async def fake_load():
        return fake_db

    # **kwargs so the double follows `_save`'s real signature instead of
    # pinning a copy of it (R-F4022 added record=/kind=/structural=).
    async def fake_save(**_kw):
        pass

    with patch.object(knowledge, "_load", fake_load), \
         patch.object(knowledge, "_save", fake_save), \
         patch("aria_service.intel.semantic_search.index_fact") as mock_index_fact, \
         patch("aria_service.intel.rag_store.ingest_fact") as mock_rag_ingest:
        asyncio.run(knowledge.store_fact(
            topic="Test fact",
            content="A test fact about defence procurement.",
            source="test",
            skip_rag_ingest=True,
            skip_semantic_index=True,
        ))

    mock_index_fact.assert_not_called()
    mock_rag_ingest.assert_not_called()
