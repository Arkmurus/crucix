"""R-F3097 — the stats read UNDER-reported search capability during a cold window.

FOUND (2026-07-26, cross-review). R-F3077 correctly moved `get_index_stats` off
`_index.has_embeddings`, which called `_get_embedder()` at semantic_search.py:682 —
the very cold-load that endpoint exists to avoid. Correct fix, real side effect: a
cold process then reported a flat `"tfidf"`, while a real search calls
`_get_embedder()` (:445/:490/:540) and lazily loads the transformer. The dashboard
said the box was on TF-IDF for a system one query away from semantic search.

"Not yet loaded" is not "loaded and unavailable" — COULD NOT MEASURE is not MEASURED
AND FAILED, the same distinction the phase gates learned the hard way.

THE FIX IS ADDITIVE. Three guards (R-F3077, R-F2942, R-F2943) assert
`search_backend in {"tfidf", "sentence-transformers"}`, and their real subject is
"this read must not cold-load" — widening a string is not a reason to weaken them
(and two of the three are untracked peer WIP). So `search_backend` is unchanged and
the qualification rides alongside it.
"""
import pytest

from aria_service.intel import semantic_search


@pytest.fixture(autouse=True)
def no_cold_load(monkeypatch):
    """The whole point of this path — a stats read must never load the model."""
    def _boom(*_a, **_kw):
        raise AssertionError("R-F3077 REGRESSION: the stats read cold-loaded the embedder")
    monkeypatch.setattr(semantic_search, "_get_embedder", _boom)


def _stats(monkeypatch, *, embedder, checked, embedding_count):
    monkeypatch.setattr(semantic_search, "_embedder", embedder)
    monkeypatch.setattr(semantic_search, "_embedder_checked", checked)
    monkeypatch.setattr(type(semantic_search._index), "embedding_count",
                        property(lambda _s: embedding_count))
    return semantic_search.get_index_stats()


# ── the four states ────────────────────────────────────────────────────────
def test_rf3097_cold_process_is_provisional_not_a_downgrade(monkeypatch):
    """THE DEFECT: no load attempted yet must not read as 'we are on TF-IDF'."""
    s = _stats(monkeypatch, embedder=None, checked=False, embedding_count=0)
    assert s["search_backend_state"] == "tfidf-cold"
    assert s["search_backend_provisional"] is True
    assert s["search_backend_is_final"] is False


def test_rf3097_attempted_and_failed_is_genuinely_tfidf(monkeypatch):
    s = _stats(monkeypatch, embedder=None, checked=True, embedding_count=0)
    assert s["search_backend_state"] == "tfidf"
    assert s["search_backend_provisional"] is False
    assert s["search_backend_is_final"] is True, "a failed load is a settled answer"


def test_rf3097_model_up_but_index_not_embedded_is_indexing(monkeypatch):
    s = _stats(monkeypatch, embedder=object(), checked=True, embedding_count=0)
    assert s["search_backend_state"] == "indexing"
    assert s["search_backend_is_final"] is False, "this still resolves on its own"


def test_rf3097_fully_live_is_final(monkeypatch):
    s = _stats(monkeypatch, embedder=object(), checked=True, embedding_count=42)
    assert s["search_backend_state"] == "sentence-transformers"
    assert s["search_backend_is_final"] is True
    assert s["search_backend_provisional"] is False


# ── the settled contract is untouched ──────────────────────────────────────
@pytest.mark.parametrize("embedder,checked,count", [
    (None, False, 0), (None, True, 0), (object(), True, 0), (object(), True, 7),
])
def test_rf3097_search_backend_vocabulary_is_unchanged(monkeypatch, embedder, checked, count):
    """R-F3077/R-F2942/R-F2943 assert this two-value set. Widening it would have
    broken three guards whose real subject is cold-loading, not vocabulary."""
    s = _stats(monkeypatch, embedder=embedder, checked=checked, embedding_count=count)
    assert s["search_backend"] in {"tfidf", "sentence-transformers"}


def test_rf3097_primary_field_still_tracks_real_availability(monkeypatch):
    live = _stats(monkeypatch, embedder=object(), checked=True, embedding_count=5)
    assert live["search_backend"] == "sentence-transformers"
    cold = _stats(monkeypatch, embedder=None, checked=False, embedding_count=0)
    assert cold["search_backend"] == "tfidf"


def test_rf3097_a_consumer_can_render_the_honest_line(monkeypatch):
    """What this exists to enable: say 'TF-IDF (embedder not yet loaded)' instead of
    asserting a downgrade that never happened."""
    s = _stats(monkeypatch, embedder=None, checked=False, embedding_count=0)
    line = s["search_backend"] + (" (not yet loaded)" if s["search_backend_provisional"] else "")
    assert line == "tfidf (not yet loaded)"
