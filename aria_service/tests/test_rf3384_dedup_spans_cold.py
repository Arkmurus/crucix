"""R-F3384 — RAG dedup only checked the HOT collection, so offloaded chunks re-duplicated.

CORRECTION FIRST: dedup was NOT missing. R-F225 already drops chunks whose
`content_hash` is present. The defect is narrower and nastier — it queries
`_documents_collection` only:

    existing = _documents_collection.get(where={"content_hash": {"$in": hashes}})

and this store has a hot/cold split. `_offload_oldest_to_cold()` MOVES the oldest
chunks: it adds them to `_documents_cold_collection` and then removes them from
hot. Once a chunk is offloaded, its hash is no longer in the collection the dedup
inspects, so re-ingesting the same document writes a fresh copy into hot.

THIS IS THE R-F257 CLASS, AGAIN. That fix found `search()` reading only hot, so
"every offloaded chunk became invisible to retrieval, violating the
infinite-memory guarantee". The identical oversight survived in the dedup path.

WHY IT MATTERS BEYOND TIDINESS. Two copies of one passage are two retrieval hits.
ARIA's whole verification posture — the C-3 independence gate, the news
corroboration rule that refuses to call one source two — depends on distinct hits
meaning distinct evidence. A duplicate makes one document look like
corroboration of itself. That is manufactured confidence, which is the failure
mode this product exists to avoid.

The fix is a single helper that asks BOTH collections, is tolerant of a cold
collection that is absent or unready (dedup must never break ingest), and is
used by the one dedup site.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from aria_service.intel import rag_store as RS

# R-F3772/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


def _run(coro):
    return asyncio.run(coro)


class _Coll:
    """Minimal chromadb collection stand-in that answers a content_hash query."""

    def __init__(self, hashes):
        self._hashes = list(hashes)
        self.queried_with = None

    def get(self, where=None, include=None):
        self.queried_with = where
        wanted = set(((where or {}).get("content_hash") or {}).get("$in") or [])
        return {"metadatas": [{"content_hash": h} for h in self._hashes if h in wanted]}


# ── the helper exists and asks BOTH collections ───────────────────────────

def test_helper_finds_hashes_in_the_hot_collection():
    hot, cold = _Coll(["aaa"]), _Coll([])
    with patch.object(RS, "_documents_collection", hot), \
         patch.object(RS, "_documents_cold_collection", cold):
        assert _run(RS._existing_content_hashes(["aaa", "bbb"])) == {"aaa"}


def test_helper_finds_hashes_that_were_offloaded_to_cold():
    """The whole defect: this hash lives ONLY in cold and was invisible."""
    hot, cold = _Coll([]), _Coll(["ccc"])
    with patch.object(RS, "_documents_collection", hot), \
         patch.object(RS, "_documents_cold_collection", cold):
        assert _run(RS._existing_content_hashes(["ccc"])) == {"ccc"}


def test_helper_unions_both_collections():
    hot, cold = _Coll(["aaa"]), _Coll(["ccc"])
    with patch.object(RS, "_documents_collection", hot), \
         patch.object(RS, "_documents_cold_collection", cold):
        assert _run(RS._existing_content_hashes(["aaa", "ccc", "zzz"])) == {"aaa", "ccc"}


def test_cold_is_actually_queried_not_just_present():
    """Guard against a cold branch that exists but is never reached."""
    hot, cold = _Coll([]), _Coll([])
    with patch.object(RS, "_documents_collection", hot), \
         patch.object(RS, "_documents_cold_collection", cold):
        _run(RS._existing_content_hashes(["aaa"]))
    assert cold.queried_with is not None, "cold collection was never asked"


# ── dedup must never break ingest ─────────────────────────────────────────

def test_missing_cold_collection_still_dedups_hot():
    hot = _Coll(["aaa"])
    with patch.object(RS, "_documents_collection", hot), \
         patch.object(RS, "_documents_cold_collection", None):
        assert _run(RS._existing_content_hashes(["aaa"])) == {"aaa"}


def test_cold_failure_does_not_lose_hot_results():
    """A cold query that throws must degrade to hot-only, not to nothing —
    failing open on BOTH would silently re-duplicate everything."""
    class _Boom:
        def get(self, *a, **k):
            raise RuntimeError("cold unavailable")

    hot = _Coll(["aaa"])
    with patch.object(RS, "_documents_collection", hot), \
         patch.object(RS, "_documents_cold_collection", _Boom()):
        assert _run(RS._existing_content_hashes(["aaa"])) == {"aaa"}


def test_hot_failure_does_not_lose_cold_results():
    class _Boom:
        def get(self, *a, **k):
            raise RuntimeError("hot unavailable")

    cold = _Coll(["ccc"])
    with patch.object(RS, "_documents_collection", _Boom()), \
         patch.object(RS, "_documents_cold_collection", cold):
        assert _run(RS._existing_content_hashes(["ccc"])) == {"ccc"}


def test_empty_input_short_circuits():
    with patch.object(RS, "_documents_collection", _Coll(["aaa"])), \
         patch.object(RS, "_documents_cold_collection", _Coll([])):
        assert _run(RS._existing_content_hashes([])) == set()


def test_helper_is_total_on_junk():
    with patch.object(RS, "_documents_collection", None), \
         patch.object(RS, "_documents_cold_collection", None):
        assert _run(RS._existing_content_hashes(["a"])) == set()


# ── the ingest path uses it (carrier: a helper with no caller is nothing) ──

def test_ingest_dedup_calls_the_two_collection_helper():
    import inspect
    src = function_source(RS, "ingest_document")
    assert "_existing_content_hashes" in src, (
        "ingest still queries one collection directly — offloaded chunks will "
        "re-duplicate, which is the R-F257 class all over again"
    )


def test_ingest_no_longer_queries_documents_collection_directly_for_dedup():
    import inspect
    src = function_source(RS, "ingest_document")
    assert 'where={"content_hash"' not in src, (
        "the hot-only dedup query is still present"
    )
