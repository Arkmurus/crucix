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
    monkeypatch.setattr(crag, "query_codebase_context", lambda *a, **k: [])
    monkeypatch.setattr(crag, "query_relevant_fixes", lambda *a, **k: [])
    out = asyncio.run(_coder()._ground_context_with_rag("BASE_CONTEXT", _gap(), fix_id=""))
    assert out == "BASE_CONTEXT"
