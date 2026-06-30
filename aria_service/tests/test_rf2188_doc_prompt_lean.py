"""R-F2188 — lean system prompt for document-grounded chat.

A document-analysis chat built a ~138K-char / ~37K-token system prompt (the full
ARIA_SYSTEM_PROMPT + addenda) for a 1.4KB document. That + a loop-stalling
context build made the chat take 7.6 min and never deliver (live 2026-06-30
Ronext legal-roadmap review — the WhatsApp poll timed out). Fix: for
document-grounded chats, build on the COMPACT base prompt, which already carries
the honesty constitution + clause-5 document-review discipline, dropping the
~100K of market/OEM/GTM/search doctrine irrelevant to reviewing a document.

These capability tests drive the REAL _build_calibrated_system_prompt.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service import aria_engine as ae


def _build(msg: str) -> str:
    return asyncio.run(ae._build_calibrated_system_prompt(msg, persona=""))


def test_rf2188_doc_prompt_is_lean():
    """A document-grounded message must produce a SMALL system prompt
    (compact base), not the ~100K+ full prompt."""
    doc_msg = (
        'what are your thoughts about this legal road map?\n\n'
        '[ATTACHED DOCUMENT: Ronext Legal Roadmap]\n'
        + ("Phase 1 corporate governance. Phase 2 FCA licensing. " * 40)
    )
    prompt = _build(doc_msg)
    # Lean target: ~20K (the R-F2188 doc cap) vs the ~138K live bloat. Allow a
    # little headroom for the cap's truncation note.
    assert len(prompt) <= 21_000, (
        f"doc-mode system prompt should be lean (~20K), got {len(prompt)} — "
        "the 138K bloat is back"
    )
    # And dramatically smaller than the full prompt (the actual win).
    assert len(prompt) < len(ae.ARIA_SYSTEM_PROMPT) * 0.5, (
        "doc prompt should be far smaller than the full ARIA_SYSTEM_PROMPT"
    )


def test_rf2188_doc_prompt_keeps_review_discipline():
    """The lean doc prompt must STILL carry honesty + document-review +
    compliance discipline (review quality preserved)."""
    prompt = _build('review this\n\n[ATTACHED DOCUMENT: X]\nsome clauses here').lower()
    assert "never fabricate" in prompt or "no invented" in prompt, "honesty rule missing"
    assert "document review" in prompt or "quote it verbatim" in prompt or "verbatim" in prompt, \
        "document-review discipline missing"
    assert "compliance" in prompt, "compliance-first rule missing"


def test_rf2188_non_doc_prompt_unchanged():
    """A normal (non-document) chat must STILL get the full prompt — the fix
    must not lean out regular intelligence chat."""
    full = _build("what is the OFAC SDN status of Acme Corp in Cyprus?")
    # The full ARIA_SYSTEM_PROMPT is ~100K+; the compact is ~2K. Non-doc must be
    # the large one.
    assert len(full) > 30_000, (
        f"non-doc chat must keep the full prompt, got {len(full)} — the fix "
        "wrongly leaned out regular chat"
    )


def test_rf2188_doc_far_smaller_than_nondoc():
    """Direct contrast: doc-mode prompt must be dramatically smaller."""
    doc = _build('thoughts?\n\n[ATTACHED DOCUMENT: D]\ntext')
    nondoc = _build("assess the defence-procurement risk for Baykar in Turkey")
    assert len(doc) * 5 < len(nondoc), (
        f"doc prompt ({len(doc)}) should be many× smaller than non-doc "
        f"({len(nondoc)})"
    )
