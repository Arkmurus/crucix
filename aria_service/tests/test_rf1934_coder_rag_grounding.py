"""R-F1934 — the coder must ground its fixes in ARIA's code RAG.

fix_gap previously read raw files (codebase.get_context) but never consulted the
indexed code knowledge (coding_rag_indexer: codebase structure + past fixes), so
fixes were blind to documented structure and prior solutions. _ground_context_
with_rag appends that knowledge to the LLM fix context. Best-effort + the
blocking chromadb queries run off-loop (asyncio.to_thread).
"""
from __future__ import annotations

import asyncio

from aria_service.autonomous import self_coder as sc
from aria_service.autonomous.gap_detector import Gap, GapSeverity
from aria_service.intel import coding_rag_indexer as crag
from aria_service.intel import redis_store as rs


def _gap():
    return Gap(gap_id="g1", gap_type="module_bug", severity=GapSeverity.HIGH,
               title="fix clamp bug", description="returns wrong value",
               module="aria_service/intel/foo.py")


def _coder():
    return sc.ARIACoder(redis_client=rs, aria_service_url="http://localhost:8000")


def test_rag_knowledge_is_appended_to_context(monkeypatch):
    monkeypatch.setattr(crag, "query_codebase_context",
                        lambda m, k=3, **kw: [{"content": "STRUCT: foo handles clamping"}])
    monkeypatch.setattr(crag, "query_relevant_fixes",
                        lambda q, k=3, **kw: [{"content": "PASTFIX: similar clamp fixed in bar"}])
    out = asyncio.run(_coder()._ground_context_with_rag("BASE_CONTEXT", _gap(), fix_id=""))
    assert "BASE_CONTEXT" in out                       # original preserved
    assert "STRUCT: foo handles clamping" in out        # structure snippet grounded
    assert "PASTFIX: similar clamp fixed in bar" in out  # past-fix snippet grounded
    assert "code-RAG knowledge" in out


def test_rag_failure_is_failsafe(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("rag down")
    monkeypatch.setattr(crag, "query_codebase_context", boom)
    monkeypatch.setattr(crag, "query_relevant_fixes", boom)
    out = asyncio.run(_coder()._ground_context_with_rag("BASE_CONTEXT", _gap(), fix_id=""))
    assert out == "BASE_CONTEXT"  # a RAG outage never breaks the fix path


def test_no_snippets_leaves_context_unchanged(monkeypatch):
    """R-F2801 — `_ground_context_with_rag` reads THREE sources, not two.

    self_coder.py:1817/1819/1824 query codebase context, relevant fixes AND
    constitutional constraints. This test stubbed only the first two, so it
    depended on the constitutional collection happening to be empty: it passed
    alone and failed in-suite the moment anything seeded that collection in the
    shared temp RAG store. "No snippets" means no snippets from ANY source.
    """
    monkeypatch.setattr(crag, "query_codebase_context", lambda *a, **k: [])
    monkeypatch.setattr(crag, "query_relevant_fixes", lambda *a, **k: [])
    monkeypatch.setattr(crag, "query_constitutional_constraints", lambda *a, **k: [])
    out = asyncio.run(_coder()._ground_context_with_rag("BASE_CONTEXT", _gap(), fix_id=""))
    assert out == "BASE_CONTEXT"


def test_constitutional_rules_are_injected_when_present(monkeypatch):
    """R-F2801 — the third source was never covered; only observed as noise.

    Constitutional grounding is the whole point of R-F2133 (§20): the coder must
    see the rules that constrain the change it is about to make.
    """
    monkeypatch.setattr(crag, "query_codebase_context", lambda *a, **k: [])
    monkeypatch.setattr(crag, "query_relevant_fixes", lambda *a, **k: [])
    monkeypatch.setattr(
        crag, "query_constitutional_constraints",
        lambda *a, **k: [{"rule": "CONSTITUTIONAL RULE: never-modify-protected-files"}],
    )
    out = asyncio.run(_coder()._ground_context_with_rag("BASE_CONTEXT", _gap(), fix_id=""))
    assert out.startswith("BASE_CONTEXT"), "the original context must be preserved"
    assert "never-modify-protected-files" in out, (
        "a constitutional rule must reach the coder's context, or §20 grounding is a no-op"
    )
