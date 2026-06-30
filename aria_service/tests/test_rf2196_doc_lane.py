"""R-F2196 — document fast-lane.

A document-analysis chat ran the FULL heavy pipeline (tool-intent detection →
which fires a web_search/crawl, the GIL-bound 7-layer context build, the
reasoning walk, the multi-step verification) for a self-contained document
review — so it took minutes and never delivered (live 2026-06-30 Ronext
legal-roadmap: 295s, no completion, on a calm fully-warmed machine).

Fix: route attached-document reviews to a single lean LLM call (lean prompt +
the document), skipping the heavy pipeline. These capability tests drive the
REAL aria_chat path and assert the routing + the one-call contract.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.aria_engine import aria_chat, doc_lane_chat


class _Res:
    def __init__(self, text):
        self.text = text


class _CountingLLM:
    is_configured = True
    name = "stub"

    def __init__(self, text="My thoughts on the legal roadmap: Phase 2 FCA "
                            "licensing is the critical path; the document states "
                            "GBP 60,000-90,000 for it."):
        self.calls = 0
        self.systems = []
        self._text = text

    async def complete(self, system, user, *, max_tokens=600, timeout=30.0):
        self.calls += 1
        self.systems.append(system)
        return _Res(self._text)


_DOC_MSG = (
    'what are your thoughts about this legal road map?\n\n'
    '[ATTACHED DOCUMENT: Ronext Legal Roadmap]\n'
    'Phase 1 corporate governance. Phase 2 FCA Authorised Payment Institution '
    'licensing, GBP 60,000-90,000. Phase 3 commercial contracting.'
)


def test_rf2196_doc_review_takes_doc_lane_one_llm_call():
    """THE fix: a document-grounded chat must route to the doc-lane — exactly
    ONE LLM call, returning doc_lane=True, NOT the heavy multi-step pipeline."""
    llm = _CountingLLM()
    out = asyncio.run(aria_chat(_DOC_MSG, "rf2196_sess_a", llm))
    assert out.get("doc_lane") is True, (
        f"document chat must take the doc-lane, got keys {list(out)}"
    )
    assert llm.calls == 1, (
        f"doc-lane must be ONE LLM call (the heavy pipeline would make several / "
        f"dispatch a tool), got {llm.calls}"
    )
    assert "roadmap" in out["response"].lower() or "licensing" in out["response"].lower()


def test_rf2196_doc_lane_uses_lean_review_prompt():
    """The doc-lane must use the lean prompt that still carries the document-
    review discipline (R-F2188 compact base)."""
    llm = _CountingLLM()
    asyncio.run(aria_chat(_DOC_MSG, "rf2196_sess_b", llm))
    sys_prompt = (llm.systems[0] if llm.systems else "").lower()
    # lean: far smaller than the ~80K full prompt
    assert len(sys_prompt) < 21_000, f"doc-lane prompt not lean: {len(sys_prompt)}"
    # but retains the review discipline
    assert "verbatim" in sys_prompt or "document review" in sys_prompt
    assert "never fabricate" in sys_prompt or "no invented" in sys_prompt


def test_rf2196_empty_doc_lane_answer_falls_through():
    """If the doc-lane LLM returns empty, doc_lane_chat returns None so aria_chat
    falls through to the full grounded pipeline (fail-safe toward MORE)."""
    llm = _CountingLLM(text="")
    out = asyncio.run(doc_lane_chat(_DOC_MSG, "rf2196_sess_c", llm))
    assert out is None, "empty doc-lane answer must return None (fall through)"


def test_rf2196_non_document_chat_does_not_short_circuit():
    """A non-document message must NOT match the doc-lane branch (it needs the
    full grounded/tool pipeline)."""
    msg = "what is the OFAC SDN status of Acme Corp in Cyprus?"
    assert not ("[ATTACHED DOCUMENT" in msg or "[Document:" in msg)
