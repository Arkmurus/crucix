"""R-F2201 — lean web workers.

To run 1 engine + N web workers in the per-machine RAM (8GB), web-role processes
SKIP the heavy in-memory graph load (knowledge ~223k facts + neural ~1.2M edges)
and serve grounded chat via the process-shared RAG store (chromadb) + the LLM.
The ENGINE role keeps the full graphs for autonomous work.

Safety crux (these tests): with the graphs NOT loaded, the 7-layer context build
must DEGRADE GRACEFULLY (each layer is _safe_call-wrapped → empty on failure),
never crash; and nothing hard-blocks chat on knowledge_ready/neural_ready. So a
lean web worker serves doc-reviews + basic Qs at full quality (doc-lane R-F2196
+ fast-lane skip the build) and substantive chats via RAG + LLM.
"""
from __future__ import annotations

import pytest

import aria_service.main as m
from aria_service import aria_engine as ae
from aria_service.intel import knowledge, neural_memory


def test_rf2201_web_role_is_the_lean_gate(monkeypatch):
    """The gate condition: a 'web' role process is the one that goes lean
    (skips the heavy graph warmup). engine / all keep the graphs."""
    monkeypatch.setenv("ARIA_ROLE", "web")
    m._resolved_role = None
    assert m._aria_role() == "web"            # → lean (warmup skipped)

    monkeypatch.setenv("ARIA_ROLE", "engine")
    assert m._aria_role() == "engine"         # → full graphs
    monkeypatch.setenv("ARIA_ROLE", "all")
    assert m._aria_role() == "all"            # → full graphs (today's default)


def test_rf2201_context_build_degrades_without_graphs(monkeypatch):
    """THE safety guarantee: with knowledge + neural NOT loaded (a lean web
    worker), the 7-layer context build returns a string and NEVER crashes —
    each layer is wrapped so an unloaded store yields an empty layer, not an
    exception."""
    # Simulate a lean web worker: empty in-memory graphs.
    monkeypatch.setattr(knowledge, "_knowledge", {}, raising=False)
    monkeypatch.setattr(neural_memory, "_neurons", {}, raising=False)
    monkeypatch.setattr(neural_memory, "_edges", {}, raising=False)

    out = ae._build_7_layer_context("assess corruption risk in market X", None)
    assert isinstance(out, str), "context build must return a string even with no graphs"
    # It must not raise; an empty-ish context is the acceptable degraded result.


def test_rf2201_doc_lane_needs_no_graphs(monkeypatch):
    """A document review takes the doc-lane (one LLM call, no 7-layer build), so
    it works at full quality on a lean web worker with no graphs loaded."""
    import asyncio

    monkeypatch.setattr(knowledge, "_knowledge", {}, raising=False)
    monkeypatch.setattr(neural_memory, "_neurons", {}, raising=False)

    class _Res:
        text = "Review of the document: clause 2 is the critical path [from attached document]."

    class _LLM:
        is_configured = True
        name = "stub"
        calls = 0

        async def complete(self, system, user, *, max_tokens=600, timeout=30.0):
            type(self).calls += 1
            return _Res()

    msg = "thoughts?\n\n[ATTACHED DOCUMENT: D]\nclause 1. clause 2 critical path. clause 3."
    out = asyncio.run(ae.aria_chat(msg, "rf2201_sess", _LLM()))
    assert out.get("doc_lane") is True, "doc review must take the graph-free doc-lane"
    assert _LLM.calls == 1
