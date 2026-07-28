"""R-F3389 — purge_by_keywords was blind to the facts collection: a successful purge removed nothing from it.

FOUND WHILE USING IT. Cleaning a test document out of live RAG, the dry run
reported success and printed, in passing:

    rag_store.purge_by_keywords: facts.get failed: Error executing plan:
    Internal error: error returned from database: (code: 1) too many SQL variables
    ... "scanned_facts": 0, "removed_facts": 0

The purge reported `available: true`, removed the document, and returned a clean
result — while having scanned ZERO of ~32k facts. This is the tool that exists to
remove fabricated or unlicensed content from ARIA's memory, and on one of its two
collections it silently did nothing.

WHY IT FAILS. `_scan_collection` calls `coll.get(include=[...])` with no bound,
which materialises the WHOLE collection in one query. On the documents collection
(~21k) that survives; on facts it exceeds SQLite's variable limit and throws. The
`except` logs a warning and returns `(0, [])` — so the failure degrades to
"nothing matched", which is indistinguishable from "nothing to remove".

WHY IT MATTERS AT THE ROOT. This is the R-F225/R-F257 family again: an operation
that quietly covers only part of the store. Its own docstring cites the OpenClaw
incident — fabricated content absorbed into RAG that "could leak back into
adjacent semantic searches". If such content lands in FACTS, the remediation tool
reports success and leaves it in place.

THE FIX IS PAGINATION, NOT A BIGGER LIMIT. chromadb 1.5.9 `get()` takes
limit/offset (already used elsewhere in this module), so the scan pages through
in bounded chunks. Raising a cap would only move the cliff.

AND THE FAILURE IS NO LONGER SILENT. A collection that cannot be scanned is
reported in the result (`scan_errors`), because "removed 0" must never be
ambiguous between "nothing matched" and "I could not look".
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from aria_service.intel import rag_store as RS


def _run(coro):
    return asyncio.run(coro)


class _PagedColl:
    """chromadb-like collection that REFUSES an unbounded get, as the real one
    does on a large table, and serves pages when asked properly."""

    def __init__(self, docs, max_page=1000):
        self._docs = list(docs)
        self.max_page = max_page
        self.unbounded_calls = 0
        self.deleted: list[str] = []

    def get(self, ids=None, where=None, limit=None, offset=None, include=None):
        if limit is None:
            self.unbounded_calls += 1
            raise RuntimeError(
                "Error executing plan: Internal error: error returned from "
                "database: (code: 1) too many SQL variables"
            )
        if limit > self.max_page:
            raise RuntimeError("too many SQL variables")
        off = offset or 0
        page = self._docs[off:off + limit]
        return {"ids": [f"id{off + i}" for i in range(len(page))],
                "documents": page,
                "metadatas": [{} for _ in page]}

    def delete(self, ids=None):
        self.deleted.extend(ids or [])


def _patched(docs_coll, facts_coll):
    return patch.multiple(RS,
                          _documents_collection=docs_coll,
                          _facts_collection=facts_coll,
                          _ensure_async=lambda: _ok())


async def _ok():
    return True


# ── the defect: facts must actually be scanned ────────────────────────────

def test_facts_are_scanned_not_skipped():
    docs = _PagedColl(["unrelated doc"] * 10)
    facts = _PagedColl(["a fabricated OpenClaw gateway claim"] + ["clean fact"] * 2500)
    with _patched(docs, facts):
        out = _run(RS.purge_by_keywords(["openclaw"], dry_run=True))
    assert out["scanned_facts"] > 0, (
        "facts collection was not scanned — a purge that reports success while "
        "looking at nothing is the whole defect"
    )
    assert out["removed_facts"] == 1, out


def test_large_facts_collection_does_not_blow_the_sql_limit():
    """The real failure: one unbounded get() over ~32k rows."""
    facts = _PagedColl(["clean fact"] * 32000)
    docs = _PagedColl([])
    with _patched(docs, facts):
        out = _run(RS.purge_by_keywords(["nothing-matches-this"], dry_run=True))
    assert facts.unbounded_calls == 0, "still issuing an unbounded get()"
    assert out["scanned_facts"] == 32000, out


def test_documents_are_still_scanned():
    docs = _PagedColl(["contains openclaw here"] + ["clean"] * 50)
    facts = _PagedColl([])
    with _patched(docs, facts):
        out = _run(RS.purge_by_keywords(["openclaw"], dry_run=True))
    assert out["scanned_docs"] == 51
    assert out["removed_docs"] == 1


# ── a scan that cannot run must not look like a clean result ──────────────

def test_unscannable_collection_is_reported_not_swallowed():
    class _Broken:
        def get(self, **kw):
            raise RuntimeError("collection unavailable")

        def delete(self, ids=None):
            pass

    docs = _PagedColl(["clean"] * 5)
    with _patched(docs, _Broken()):
        out = _run(RS.purge_by_keywords(["anything"], dry_run=True))
    assert out.get("scan_errors"), (
        "'removed 0' is ambiguous between 'nothing matched' and 'I could not "
        "look' — the caller must be told which"
    )
    assert any("facts" in str(e).lower() for e in out["scan_errors"]), out["scan_errors"]


def test_clean_scan_reports_no_errors():
    docs, facts = _PagedColl(["clean"] * 3), _PagedColl(["clean"] * 3)
    with _patched(docs, facts):
        out = _run(RS.purge_by_keywords(["nomatch"], dry_run=True))
    assert not out.get("scan_errors")


# ── real deletion, and dry-run must not delete ────────────────────────────

def test_dry_run_deletes_nothing():
    facts = _PagedColl(["openclaw fabrication"] * 3)
    docs = _PagedColl([])
    with _patched(docs, facts):
        out = _run(RS.purge_by_keywords(["openclaw"], dry_run=True))
    assert out["removed_facts"] == 3
    assert facts.deleted == [], "dry run deleted rows"


def test_live_run_deletes_the_matching_facts():
    facts = _PagedColl(["openclaw fabrication", "clean fact"])
    docs = _PagedColl([])
    with _patched(docs, facts):
        out = _run(RS.purge_by_keywords(["openclaw"], dry_run=False))
    assert out["removed_facts"] == 1
    assert len(facts.deleted) == 1


def test_matching_is_case_insensitive_substring():
    facts = _PagedColl(["An OpenClaw Gateway reference"])
    docs = _PagedColl([])
    with _patched(docs, facts):
        out = _run(RS.purge_by_keywords(["openclaw"], dry_run=True))
    assert out["removed_facts"] == 1


def test_empty_keywords_removes_nothing():
    facts = _PagedColl(["anything at all"])
    docs = _PagedColl([])
    with _patched(docs, facts):
        out = _run(RS.purge_by_keywords([], dry_run=True))
    assert out["removed_docs"] == 0 and out["removed_facts"] == 0
    assert facts.deleted == []
