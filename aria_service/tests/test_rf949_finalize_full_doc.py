"""R-F949 — the contract-review synthesis must see the FULL document.

Live (Korvera UTS contract, 2026-05-27): finalize_reviewed_contract — the step
that writes the USER-FACING review — sliced the document to [:16000]. So even
though the draft and the self-review windows covered the whole 60K agreement,
the synthesis saw only ~16K chars (≈ Clause 5.4) and falsely concluded "the
excerpt ends at Clause 5.4 / 3.1," overriding a complete review. The fix passes
the full document (up to 120K) and gives the synthesis room (4000 tokens) to
review every clause.
"""
from __future__ import annotations

import asyncio


def test_rf949_finalize_passes_full_document_and_bigger_budget():
    from aria_service.intel import contract_intelligence as ci

    captured = {}

    class _LLM:
        is_configured = True

        async def complete(self, system, user, **kw):
            captured["user"] = user
            captured["max_tokens"] = kw.get("max_tokens")

            class _R:
                text = "FINAL CLEAN REVIEW"
            return _R()

    # A document whose tail lies well past the old 16K slice.
    doc = "START_OF_CONTRACT " + ("clause body text " * 6000) + " END_MARKER_PAST_16K_CLAUSE_23"
    assert len(doc) > 16000

    out = asyncio.run(ci.finalize_reviewed_contract(
        user_question="review this agreement",
        draft_review="draft",
        findings="audit findings",
        document_excerpt=doc,
        llm=_LLM(),
    ))
    assert out == "FINAL CLEAN REVIEW"
    # THE FIX: content past the old 16K cap reaches the synthesis prompt.
    assert "END_MARKER_PAST_16K_CLAUSE_23" in captured["user"], "synthesis must see the whole document"
    # and it has room to write a full multi-clause review
    assert captured["max_tokens"] >= 4000


def test_rf949_finalize_returns_none_without_llm():
    from aria_service.intel import contract_intelligence as ci
    out = asyncio.run(ci.finalize_reviewed_contract(
        user_question="q", draft_review="d", findings="f", document_excerpt="x", llm=None))
    assert out is None
