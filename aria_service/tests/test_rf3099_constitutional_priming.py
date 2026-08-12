"""R-F3099 — CLAUDE.md §20's binding coding-RAG priming step returned nothing.

THE DEFECT (measured, not inferred). On a developer machine:

    query_constitutional_constraints('modifying <module> <task>', top_k=5)  -> []
    get_stats()['total_constitutional_rules']                               -> 0

`coding_constitutional` existed but held zero rules, so §20 — which every session
is required to run BEFORE writing any code — was a silent no-op. It never raised:
`_ensure()` returns True when the collection EXISTS, and an empty collection
queries cleanly to an empty list.

THE ROOT. R-F2130 already found this collection unpopulated and wired
`sync_constitutional_rules()` into the FastAPI lifespan (`main.py:1423`). That
grounds the SERVER. But the other first-class consumer is §20's pre-code priming,
which runs from the CLI, where the lifespan never executes — so the collection the
CLI reads is never the collection the server filled. Being grounded was a property
of who booted rather than of the query.

WHY NOT "call sync first in the §20 snippet". That is the band-aid §1 forbids, and
it leaves the next consumer to rediscover the same trap. R-F2623 is the precedent
that makes this binding: the §20 snippet used to wrap this same function in
asyncio.run() and raised TypeError every time, so the step silently never ran.
Twice now the failure has been in the STEP, not the rules. Populate on demand.

These tests drive the real `query_constitutional_constraints`, faking only the
chromadb boundary, so they fail against the pre-fix function.
"""
from __future__ import annotations

import pytest

from aria_service.intel import coding_rag_indexer as cri


class _FakeCollection:
    """Minimal chromadb collection stand-in: counts, and answers one query."""

    def __init__(self, count: int = 0):
        self._count = count
        self.queries: list[str] = []

    def count(self) -> int:
        return self._count

    def query(self, query_texts, n_results):  # noqa: ANN001 - chromadb signature
        self.queries.append(query_texts[0])
        if self._count == 0:
            return {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        docs = [f"CONSTITUTIONAL RULE: rule-{i}" for i in range(min(n_results, self._count))]
        return {
            "documents": [docs],
            "metadatas": [[{"name": f"rule-{i}"} for i in range(len(docs))]],
            "distances": [[0.1] * len(docs)],
        }


@pytest.fixture
def primed(monkeypatch):
    """Fresh process state: collection empty, lazy sync not yet attempted."""
    collection = _FakeCollection(count=0)
    monkeypatch.setattr(cri, "_ensure", lambda: True)
    monkeypatch.setattr(cri, "_constitutional_collection", collection)
    # raising=False so these tests run against the PRE-FIX module too — they must
    # fail on the SYMPTOM (no rules returned), not on a missing attribute.
    monkeypatch.setattr(cri, "_CONST_LAZY_SYNC_TRIED", False, raising=False)
    return collection


def test_empty_collection_is_populated_by_the_query_itself(primed, monkeypatch):
    """THE CAPABILITY TEST — the §20 step must return rules on a cold collection.

    Pre-fix this asserts []; the whole defect is that the step looked healthy
    while returning nothing.
    """
    calls = {"n": 0}

    def _fake_sync():
        calls["n"] += 1
        primed._count = 31          # the sync populates the collection
        return {"ok": True, "indexed": 31}

    monkeypatch.setattr(cri, "sync_constitutional_rules", _fake_sync)

    out = cri.query_constitutional_constraints("modifying <module> <task>", top_k=5)

    assert calls["n"] == 1, "an empty constitutional collection must be populated on demand"
    assert out, "§20 priming returned no constraints — the binding step is a no-op"
    assert len(out) == 5
    assert all("rule" in item for item in out)


def test_populated_collection_does_no_needless_sync(monkeypatch):
    """A collection that already holds rules must not re-index on every query."""
    collection = _FakeCollection(count=31)
    monkeypatch.setattr(cri, "_ensure", lambda: True)
    monkeypatch.setattr(cri, "_constitutional_collection", collection)
    # raising=False so these tests run against the PRE-FIX module too — they must
    # fail on the SYMPTOM (no rules returned), not on a missing attribute.
    monkeypatch.setattr(cri, "_CONST_LAZY_SYNC_TRIED", False, raising=False)

    calls = {"n": 0}
    monkeypatch.setattr(
        cri, "sync_constitutional_rules",
        lambda: calls.__setitem__("n", calls["n"] + 1),
    )

    out = cri.query_constitutional_constraints("anything", top_k=3)

    assert calls["n"] == 0, "sync fired against an already-populated collection"
    assert len(out) == 3


def test_sync_failure_degrades_gracefully_not_an_exception(primed, monkeypatch):
    """A failing sync must never break the caller.

    R-F3911 UPDATE — this asserted `out == []`. That was the best R-F3099 could do
    at the time, but the empty list WAS the residual defect: §20 makes this priming
    step binding, and a caller cannot distinguish "the store is broken" from "no rule
    applies". A failing sync now degrades to a LEXICAL match over the in-code
    CONSTITUTIONAL_RULES, labelled `degraded: True`.

    The INTENT of this test is preserved exactly — do not raise into the caller — and
    that is what it now asserts. Do not restore `== []`: it would re-dark the one
    mechanism that exists to remind a session of the constitution.
    """
    def _boom():
        raise RuntimeError("chromadb unavailable")

    monkeypatch.setattr(cri, "sync_constitutional_rules", _boom)

    out = cri.query_constitutional_constraints("modifying <module>", top_k=5)

    assert isinstance(out, list), "a failed sync must degrade, not raise"
    assert out, "a failed sync must still surface the constitutional rules (R-F3911)"
    assert all(r.get("degraded") for r in out), "the degraded path must say so"


def test_lazy_sync_is_attempted_at_most_once_per_process(primed, monkeypatch):
    """A permanently-empty collection must not re-encode on every single query."""
    calls = {"n": 0}
    monkeypatch.setattr(
        cri, "sync_constitutional_rules",
        lambda: calls.__setitem__("n", calls["n"] + 1),   # never populates
    )

    for _ in range(4):
        cri.query_constitutional_constraints("modifying <module>", top_k=5)

    assert calls["n"] == 1, "lazy sync must be attempted once per process, not per query"


def test_concurrent_syncs_do_not_interleave_clear_and_reindex(monkeypatch):
    """R-F3099 — clear+re-index is only safe as an atomic pair.

    Boot (main.py:1423) and the on-demand populate are genuinely concurrent on the
    server. Without the lock both callers pass the version guard, and one thread's
    delete can land after the other's add — leaving the collection short or empty,
    the exact state R-F2130 existed to prevent.
    """
    import threading

    collection = _FakeCollection(count=0)
    monkeypatch.setattr(cri, "_ensure", lambda: True)
    monkeypatch.setattr(cri, "_constitutional_collection", collection)
    monkeypatch.setattr(cri, "_CONST_SYNCED_VERSION", None, raising=False)

    indexed = {"n": 0}
    inside = {"concurrent": False, "depth": 0}
    depth_lock = threading.Lock()

    def _slow_index(rules):
        with depth_lock:
            inside["depth"] += 1
            if inside["depth"] > 1:
                inside["concurrent"] = True
        # Widen the window a racing thread would exploit.
        threading.Event().wait(0.02)
        with depth_lock:
            inside["depth"] -= 1
        indexed["n"] += 1
        collection._count = 31
        return 31

    monkeypatch.setattr(cri, "index_constitutional_rules", _slow_index)

    threads = [threading.Thread(target=cri.sync_constitutional_rules) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not inside["concurrent"], "two syncs re-indexed at once — clear/add can interleave"
    assert indexed["n"] == 1, f"expected exactly one re-index across 16 threads, got {indexed['n']}"
    assert collection.count() == 31


def test_canonical_rules_are_the_sync_source_of_truth():
    """The rules module is the source; guards the count §20 depends on existing."""
    from aria_service.intel import constitutional_rules as cr

    assert len(cr.CONSTITUTIONAL_RULES) >= 31, "constitutional rule set shrank unexpectedly"
    assert cr.RULES_VERSION, "rules must carry a version so the sync can guard on it"
    for rule in cr.CONSTITUTIONAL_RULES:
        assert rule.get("name"), "every rule needs a name for retrieval"
        assert rule.get("constraint"), "every rule needs an actionable constraint"
