"""R-F3478 — a purge reported success while the content stayed searchable.

THE DEFECT. `rag_store.purge_by_keywords()` scanned `aria_documents` and `aria_facts`.
Retrieval ALSO queries `aria_documents_cold` — R-F2989 offloads the oldest ~10% of
documents there and `search()` consults both (the `documents_cold` query task). So any
material that had been offloaded to cold SURVIVED the purge and remained retrievable,
while the call returned removed-counts and reported success.

WHY THIS IS THE WORST SHAPE. A purge that FAILS is recoverable — the caller sees an error
and tries again. A purge that reports success while leaving the data searchable tells
someone acting on a right-to-erasure request that the content is gone when it is not. It
is the erasure analogue of a false clean, which is the failure class this product exists
to prevent everywhere else.

THE FIX IS THE INVARIANT, NOT THE THIRD COLLECTION. Adding one more hardcoded call would
leave the FOURTH collection free to escape the same way. Both paths now read
`_searchable_collections()`, and the guard below fails if a collection retrieval can
return content from is not covered by erasure.

NOTE ON SCOPE, because CLAUDE.md §7 says ARIA never deletes: §7 forbids TTL, oldest-first
prune and eviction as a MEMORY-MANAGEMENT strategy — "overflow → cold storage, never
delete". It does not forbid a deliberate, targeted erasure that a caller explicitly asks
for. This change makes an explicitly requested purge COMPLETE; it introduces no automatic
deletion of any kind.
"""
from __future__ import annotations

import asyncio
import pathlib
import re

import pytest

from aria_service.intel import rag_store


class _FakeCollection:
    """Minimal chromadb-collection stand-in: paged get() + delete()."""

    def __init__(self, rows: dict[str, str]):
        self.rows = dict(rows)
        self.deleted: list[str] = []

    def get(self, include=None, limit=None, offset=0):
        ids = list(self.rows.keys())[offset: (offset + limit) if limit else None]
        return {
            "ids": ids,
            "documents": [self.rows[i] for i in ids],
            "metadatas": [{"source": "t", "title": "t"} for _ in ids],
        }

    def delete(self, ids):
        for i in ids:
            self.rows.pop(i, None)
            self.deleted.append(i)


@pytest.fixture
def wired(monkeypatch):
    """Three live collections, each holding one matching chunk."""
    hot = _FakeCollection({"h1": "the OpenClaw gateway is real"})
    facts = _FakeCollection({"f1": "OpenClaw was verified"})
    cold = _FakeCollection({"c1": "archived note about OpenClaw"})
    monkeypatch.setattr(rag_store, "_documents_collection", hot)
    monkeypatch.setattr(rag_store, "_facts_collection", facts)
    monkeypatch.setattr(rag_store, "_documents_cold_collection", cold)

    async def _ok():
        return True

    monkeypatch.setattr(rag_store, "_ensure_async", _ok)
    return hot, facts, cold


def test_capability_offloaded_material_is_actually_erased(wired):
    """THE DEFECT: this chunk survived a purge that reported success."""
    hot, facts, cold = wired
    res = asyncio.run(rag_store.purge_by_keywords(["openclaw"]))

    assert "c1" in cold.deleted, (
        "material offloaded to the cold collection SURVIVED the purge while the call "
        f"reported success: {res}")
    assert cold.rows == {}, "cold content is still present and therefore still searchable"
    # The other two must keep working.
    assert "h1" in hot.deleted and "f1" in facts.deleted


def test_the_report_counts_the_cold_removals(wired):
    """A caller acting on an erasure request must be able to EVIDENCE what was removed;
    silent extra deletions are as unauditable as silent misses."""
    res = asyncio.run(rag_store.purge_by_keywords(["openclaw"]))
    assert res["removed_cold"] == 1, res
    assert res["scanned_cold"] == 1, res
    assert res["per_collection"]["documents_cold"]["removed"] == 1
    # Back-compatible keys must survive — existing callers read these.
    assert res["removed_docs"] == 1 and res["removed_facts"] == 1


def test_dry_run_still_reports_cold_without_deleting(wired):
    """Dry run is how an operator SIZES an erasure before committing to it. If it
    under-reports cold, the operator plans against a number that is too small."""
    _, _, cold = wired
    res = asyncio.run(rag_store.purge_by_keywords(["openclaw"], dry_run=True))
    assert res["removed_cold"] == 1, res
    assert cold.deleted == [], "dry_run deleted content"
    assert cold.rows, "dry_run destroyed cold content"


def test_an_absent_collection_is_not_reported_as_clean(monkeypatch):
    """`present: False` matters. A cold collection that does not exist yet must not be
    indistinguishable from one that was scanned and matched nothing — that ambiguity is
    exactly what let this defect hide."""
    monkeypatch.setattr(rag_store, "_documents_collection", _FakeCollection({}))
    monkeypatch.setattr(rag_store, "_facts_collection", _FakeCollection({}))
    monkeypatch.setattr(rag_store, "_documents_cold_collection", None)

    async def _ok():
        return True

    monkeypatch.setattr(rag_store, "_ensure_async", _ok)
    res = asyncio.run(rag_store.purge_by_keywords(["anything"]))
    assert res["per_collection"]["documents_cold"]["present"] is False
    assert res["per_collection"]["documents"]["present"] is True


def test_nonmatching_content_is_untouched(wired):
    """The other direction: a purge must not become a wildcard delete."""
    hot, facts, cold = wired
    cold.rows["c2"] = "an unrelated archived note"
    asyncio.run(rag_store.purge_by_keywords(["openclaw"]))
    assert "c2" in cold.rows, "a non-matching cold chunk was deleted"


# ── THE GUARD: erasure surface must equal retrieval surface ──────────────────

def test_every_searched_collection_is_covered_by_erasure():
    """THE CLASS FIX. A fourth collection added to retrieval and forgotten in the purge
    reproduces this defect exactly. This compares the two surfaces by reading the source,
    so the omission fails HERE instead of in someone's erasure request."""
    src = (pathlib.Path(rag_store.__file__)).read_text(encoding="utf-8", errors="replace")

    # Labels the retrieval path passes to _sync_query_collection(...)
    searched = set(re.findall(r"_sync_query_collection,\s*[^,]+,\s*[\"']([a-z_]+)[\"']", src))
    assert searched, "could not find the retrieval query labels — this guard is blind"

    covered = set(rag_store._SEARCHABLE_COLLECTION_LABELS)
    missing = sorted(searched - covered)
    assert not missing, (
        f"retrieval can return content from {missing}, but erasure does not reach it. "
        f"A purge would report success while that content stayed searchable. Add it to "
        f"_searchable_collections() so purge_by_keywords covers it.")


def test_the_guard_can_see_an_uncovered_collection():
    """VERIFY THE INSTRUMENT — a guard that cannot fail certifies everything."""
    probe = '''
        query_tasks.append(_aio.to_thread(_sync_query_collection, _x_collection, "documents"))
        query_tasks.append(_aio.to_thread(_sync_query_collection, _y_collection, "brand_new"))
    '''
    searched = set(re.findall(r"_sync_query_collection,\s*[^,]+,\s*[\"']([a-z_]+)[\"']", probe))
    assert "brand_new" in searched, "the label extraction cannot see a new collection"
    assert sorted(searched - set(rag_store._SEARCHABLE_COLLECTION_LABELS)) == ["brand_new"]
