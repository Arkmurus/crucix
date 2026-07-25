"""R-F3077 — status reads must not cold-load the semantic model."""

from aria_service.intel import semantic_search


def test_get_index_stats_is_observational(monkeypatch):
    """Drive the real status function and prove it never invokes model loading."""

    def _cold_load_forbidden():
        raise AssertionError("an observational stats read attempted a model cold-load")

    successes = []
    monkeypatch.setattr(semantic_search, "_embedder", None)
    monkeypatch.setattr(semantic_search, "_get_embedder", _cold_load_forbidden)
    monkeypatch.setattr(
        semantic_search,
        "wire_success",
        lambda **kwargs: successes.append(kwargs),
    )

    stats = semantic_search.get_index_stats()

    assert stats["embedding_model"] is None
    assert stats["indexed_documents"] == semantic_search._index.size
    assert successes == [{
        "module": "semantic_search",
        "summary": "Semantic index stats observed via tfidf",
        "source_id": "semantic_search:get_index_stats",
    }]
