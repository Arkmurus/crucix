"""R-F2130 — the coder's constitutional-rules RAG must be POPULATED and QUERIED.

coding_constitutional was built but never populated (index_constitutional_rules
was only called in tests), so the autonomous coder was grounded in code structure
+ past fixes but NOT in the playbook rules. This wires the canonical rules in at
boot and makes the main coder grounding path query them. These tests drive the
real sync + query (via a fake chromadb collection) and lock the grounding wiring.
"""
import inspect

import aria_service.intel.coding_rag_indexer as cri
import aria_service.intel.constitutional_rules as cr

# R-F3789/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source


class _FakeCollection:
    def __init__(self):
        self.docs = {}
        self.deleted = []

    def count(self):
        return len(self.docs)

    def get(self):
        return {"ids": list(self.docs.keys())}

    def delete(self, ids):
        for i in ids:
            self.docs.pop(i, None)
        self.deleted.extend(ids)

    def upsert(self, documents, metadatas, ids):
        for d, m, i in zip(documents, metadatas, ids):
            self.docs[i] = (d, m)

    def query(self, query_texts, n_results):
        items = list(self.docs.values())[:n_results]
        return {
            "documents": [[d for d, _ in items]],
            "metadatas": [[m for _, m in items]],
            "distances": [[0.1] * len(items)],
        }


def _patch(monkeypatch):
    fake = _FakeCollection()
    monkeypatch.setattr(cri, "_ensure", lambda: True)
    monkeypatch.setattr(cri, "_constitutional_collection", fake)
    cri._CONST_SYNCED_VERSION = None  # reset the intra-process guard
    return fake


def test_rf2130_rules_are_well_formed():
    assert len(cr.CONSTITUTIONAL_RULES) >= 15
    for r in cr.CONSTITUTIONAL_RULES:
        assert r.get("name") and r.get("description") and r.get("constraint")
    assert cr.RULES_VERSION and cr.RULES_VERSION == cr.rules_version()


def test_rf2130_sync_populates_and_query_retrieves(monkeypatch):
    fake = _patch(monkeypatch)
    res = cri.sync_constitutional_rules()
    assert res["ok"] is True
    assert res["indexed"] == len(cr.CONSTITUTIONAL_RULES)
    assert fake.count() == len(cr.CONSTITUTIONAL_RULES)
    # the previously-empty collection now answers a query
    out = cri.query_constitutional_constraints("how do I fix a bug correctly", top_k=3)
    assert out and "rule" in out[0] and "CONSTITUTIONAL RULE" in out[0]["rule"]


def test_rf2130_sync_is_idempotent(monkeypatch):
    fake = _patch(monkeypatch)
    first = cri.sync_constitutional_rules()
    assert first["ok"] and not first.get("skipped")
    second = cri.sync_constitutional_rules()  # same version + non-empty -> skip
    assert second.get("skipped") is True
    assert fake.count() == len(cr.CONSTITUTIONAL_RULES)  # unchanged, no duplicates


def test_rf2130_clears_orphans_on_resync(monkeypatch):
    fake = _patch(monkeypatch)
    cri.sync_constitutional_rules()
    fake.docs["orphan_id"] = ("stale rule", {"type": "constitutional_rule"})
    cri._CONST_SYNCED_VERSION = None  # force a re-sync (simulates a rules edit)
    cri.sync_constitutional_rules()
    assert "orphan_id" not in fake.docs, "re-sync must clear stale/orphan docs"
    assert fake.count() == len(cr.CONSTITUTIONAL_RULES)


def test_rf2130_coder_grounding_queries_constitutional():
    """Lock the wiring: the main coder grounding path must query constitutional rules."""
    import aria_service.autonomous.self_coder as sc
    src = function_source(sc.ARIACoder, "_ground_context_with_rag")
    assert "query_constitutional_constraints" in src, \
        "the coder grounding must retrieve constitutional rules (R-F2130)"
