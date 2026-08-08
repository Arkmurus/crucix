"""R-F883 — contract self-review produces a CLEAN corrected answer, not a raw critique dump.

Live 2026-05-25: after the contract read + analysis chain finally worked, the
user's WhatsApp reply carried the raw self-review scaffolding — "── Self-review
window 1/4 ──" walls plus the auditor's own deliberation ("So no error found
here", "no removal needed"). self_review_contract returns a CRITIQUE of the
draft; nothing synthesised a clean answer. R-F883 adds finalize_reviewed_contract
which folds draft + findings + document into one decision-grade review; the chat
path shows only that (raw findings move to result metadata, not the chat text).
"""
from __future__ import annotations

import asyncio
import inspect

from aria_service.intel import contract_intelligence as ci
from aria_service.routes import aria as a

# R-F3786/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


class _StubLLM:
    is_configured = True

    class _R:
        def __init__(self, text):
            self.text = text
            self.model = "stub"

    def __init__(self, out):
        self._out = out
        self.calls = []

    async def complete(self, system, prompt, max_tokens=None, timeout=None):
        self.calls.append({"system": system, "prompt": prompt})
        return self._R(self._out)


def test_finalize_returns_clean_review_from_stub():
    llm = _StubLLM("FINAL REVIEW: Payment terms do NOT fully secure either party — "
                   "Export License No is blank (TBA); no governing law or dispute clause.")
    out = asyncio.run(ci.finalize_reviewed_contract(
        user_question="do the payment terms secure both parties?",
        draft_review="🟢 low risk. Low-risk destination.",
        findings="FLAG (MISSED BLANKS): Export License No: TBA. MEMORY CONTAMINATION: 'low-risk destination'.",
        document_excerpt="ARTICLE 1. Export License No: - TBA. Payment via DLC MT700.",
        llm=llm,
    ))
    assert out and "FINAL REVIEW" in out
    # the synthesis prompt carried the draft, findings AND document for grounding
    p = llm.calls[0]["prompt"]
    assert "AUDIT FINDINGS" in p and ("FULL DOCUMENT" in p or "DOCUMENT EXCERPT" in p) and "USER QUESTION" in p


def test_finalize_returns_none_without_llm():
    assert asyncio.run(ci.finalize_reviewed_contract(
        user_question="q", draft_review="d", findings="f",
        document_excerpt="doc", llm=None,
    )) is None


def test_synthesis_prompt_strips_contamination_and_adds_misses():
    p = ci.CORRECTION_SYNTHESIS_PROMPT.upper()
    assert "MEMORY CONTAMINATION" in p
    assert "DELETE" in p
    assert "INCORPORATE" in p
    assert "ONLY THE FINAL REVIEW" in p


def test_chat_caller_no_longer_dumps_raw_findings():
    src = module_source(a)
    # the old verbatim-dump header must be gone
    assert "Contract self-review audit" not in src
    # the caller synthesises and records findings as metadata, not chat text
    assert "finalize_reviewed_contract(" in src
    assert '"findings": findings[:4000]' in src
