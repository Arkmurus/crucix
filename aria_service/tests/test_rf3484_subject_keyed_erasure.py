"""R-F3484 — erasure you can PROVE, and a sweep that stops pretending it can.

THE GAP. The only erasure ARIA had was `purge_by_keywords`, a substring sweep. Under UK
GDPR Art. 17 a controller must be able to DEMONSTRATE that a request was fulfilled, and a
substring sweep cannot: the subject's data survives inside an alias, a transliteration, an
initial, a misspelling, or any phrasing where the needle does not literally appear. The
caller could not distinguish a complete erasure from a lucky one — and, believing it done,
would stop looking.

TWO HALVES, and the second matters as much as the first:

  * `erase_by_subject()` matches `data_subject_key` EXACTLY, across every collection a
    search can read from (the R-F3478 invariant), and returns a receipt that is evidence:
    per-collection counts, `coverage="keyed"`, and an explicit statement of what it does
    NOT reach (records written before subject keying).

  * `purge_by_keywords()` now declares `coverage="keyword_best_effort"` with a caveat
    telling the caller not to record it as a fulfilled erasure request. Erasure that
    overstates itself is worse than erasure that fails.

CONSISTENT WITH §7 (ARIA never deletes): §7 forbids TTL, oldest-first prune and eviction
as MEMORY MANAGEMENT. This deletes only on an explicit, attributed request from a data
subject. No automatic deletion of any kind is introduced.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import rag_store
from aria_service.intel.rag_store import DATA_SUBJECT_KEY


class _FakeCollection:
    def __init__(self, rows: dict[str, dict]):
        # id -> metadata
        self.rows = dict(rows)
        self.deleted: list[str] = []

    def get(self, include=None, limit=None, offset=0):
        ids = list(self.rows.keys())[offset: (offset + limit) if limit else None]
        return {"ids": ids, "metadatas": [self.rows[i] for i in ids]}

    def delete(self, ids):
        for i in ids:
            self.rows.pop(i, None)
            self.deleted.append(i)


@pytest.fixture
def wired(monkeypatch):
    """One subject's data spread across hot, facts AND cold — plus other subjects."""
    hot = _FakeCollection({
        "h1": {DATA_SUBJECT_KEY: "subj-42", "source": "x"},
        "h2": {DATA_SUBJECT_KEY: "subj-99", "source": "x"},
    })
    facts = _FakeCollection({"f1": {DATA_SUBJECT_KEY: "subj-42"}})
    cold = _FakeCollection({
        "c1": {DATA_SUBJECT_KEY: "subj-42"},
        "c2": {"source": "legacy-no-key"},
    })
    monkeypatch.setattr(rag_store, "_documents_collection", hot)
    monkeypatch.setattr(rag_store, "_facts_collection", facts)
    monkeypatch.setattr(rag_store, "_documents_cold_collection", cold)

    async def _ok():
        return True

    monkeypatch.setattr(rag_store, "_ensure_async", _ok)
    return hot, facts, cold


def test_capability_a_subject_is_erased_across_every_collection(wired):
    """THE REQUEST: erase this person. Including the cold store a search still reads."""
    hot, facts, cold = wired
    res = asyncio.run(rag_store.erase_by_subject("subj-42"))

    assert res["erased"] == 3, res
    assert "h1" in hot.deleted and "f1" in facts.deleted and "c1" in cold.deleted
    assert res["per_collection"]["documents_cold"]["erased"] == 1, (
        "the cold store was missed — the same asymmetry R-F3478 closed for keyword purge")


def test_other_subjects_are_untouched(wired):
    """An erasure request is not a wildcard delete. Erasing more than was asked is its
    own breach."""
    hot, _, cold = wired
    asyncio.run(rag_store.erase_by_subject("subj-42"))
    assert "h2" in hot.rows, "another data subject's record was erased"
    assert "c2" in cold.rows, "an unkeyed record was erased without being matched"


def test_the_receipt_is_evidence(wired):
    """A controller must be able to SHOW the request was fulfilled."""
    res = asyncio.run(rag_store.erase_by_subject("subj-42"))
    assert res["coverage"] == "keyed"
    assert res["subject_key"] == "subj-42"
    assert res["scanned"] >= 3
    for label in ("documents", "facts", "documents_cold"):
        assert label in res["per_collection"], f"{label} missing from the receipt"


def test_the_receipt_states_what_it_cannot_reach(wired):
    """Records written before subject keying are NOT reachable. Saying so is the
    difference between a receipt and a false assurance."""
    res = asyncio.run(rag_store.erase_by_subject("subj-42"))
    assert "not reachable" in res["note"].lower() or "NOT reachable" in res["note"]
    assert "cannot prove completeness" in res["note"]


def test_dry_run_reports_without_deleting(wired):
    """A controller sizes the request before committing to it."""
    hot, _, cold = wired
    res = asyncio.run(rag_store.erase_by_subject("subj-42", dry_run=True))
    assert res["erased"] == 3
    assert hot.deleted == [] and cold.deleted == []
    assert "h1" in hot.rows and "c1" in cold.rows


def test_an_empty_subject_key_is_refused(wired):
    """A blank key must never become 'erase everything'."""
    hot, _, _ = wired
    res = asyncio.run(rag_store.erase_by_subject("   "))
    assert res["erased"] == 0
    assert res["reason"] == "subject_key_required"
    assert hot.deleted == []


def test_a_read_failure_is_not_reported_as_nothing_found(monkeypatch):
    """On an erasure request, 'I could not look' and 'nothing matched' are legally
    different answers."""
    class _Broken:
        def get(self, **kw):
            raise RuntimeError("chromadb down")

    monkeypatch.setattr(rag_store, "_documents_collection", _Broken())
    monkeypatch.setattr(rag_store, "_facts_collection", _FakeCollection({}))
    monkeypatch.setattr(rag_store, "_documents_cold_collection", _FakeCollection({}))

    async def _ok():
        return True

    monkeypatch.setattr(rag_store, "_ensure_async", _ok)
    res = asyncio.run(rag_store.erase_by_subject("subj-42"))
    assert res["scan_errors"], "a collection that could not be read was reported silently"


# ── the other half: the sweep must stop implying completeness ───────────────

def test_keyword_purge_declares_itself_best_effort(monkeypatch):
    class _Docs(_FakeCollection):
        def get(self, include=None, limit=None, offset=0):
            ids = list(self.rows.keys())[offset: (offset + limit) if limit else None]
            return {"ids": ids, "documents": ["nothing" for _ in ids],
                    "metadatas": [self.rows[i] for i in ids]}

    empty = _Docs({})
    monkeypatch.setattr(rag_store, "_documents_collection", empty)
    monkeypatch.setattr(rag_store, "_facts_collection", empty)
    monkeypatch.setattr(rag_store, "_documents_cold_collection", empty)

    async def _ok():
        return True

    monkeypatch.setattr(rag_store, "_ensure_async", _ok)
    res = asyncio.run(rag_store.purge_by_keywords(["anything"]))
    assert res["coverage"] == "keyword_best_effort"
    assert "cannot prove completeness" in res["completeness_caveat"]
    assert "fulfilled erasure request" in res["completeness_caveat"], (
        "the caveat must tell the caller not to log this as a fulfilled Art. 17 request")


def test_erasure_reaches_every_collection_retrieval_can_read():
    """THE INVARIANT, shared with R-F3478: erasure surface == retrieval surface. If a
    fourth collection is added, subject erasure must reach it too."""
    labels = {label for label, _ in rag_store._searchable_collections()}
    assert labels == set(rag_store._SEARCHABLE_COLLECTION_LABELS)
    assert "documents_cold" in labels
