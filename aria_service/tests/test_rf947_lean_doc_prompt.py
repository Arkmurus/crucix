"""R-F947 — lean system prompt when a document is attached.

Live (Korvera UTS contract, 2026-05-27): the calibrated system prompt had grown
to ~236K chars (~59K tokens) from store-backed addenda (calibration,
contradictions, correction lessons) that grow unbounded as ARIA learns. On
DeepSeek's ~64K window that left almost no room for the document, so a 15K-token
contract was truncated to ~Clause 5.4. In document mode we now skip the
unbounded/irrelevant addenda (keeping the constitution + persona + contract-review
checklist + law context) and hard-cap the prompt so the document survives.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service import aria_engine as ae

CAL_MARKER = "ZZZ_CALIBRATION_ADDENDUM_MARKER"
CON_MARKER = "ZZZ_CONTRADICTIONS_ADDENDUM_MARKER"


@pytest.fixture
def patched_addenda(monkeypatch):
    async def _cal():
        return {"dummy": True}
    monkeypatch.setattr(ae, "_get_cached_calibration", _cal)
    monkeypatch.setattr(ae, "_calibration_to_prompt_addendum", lambda c: CAL_MARKER)

    async def _con(_m):
        return CON_MARKER
    monkeypatch.setattr(ae, "_get_relevant_contradictions", _con)


_DOC_MSG = "Please review this agreement. [ATTACHED DOCUMENT: c.docx]\nbody text\n[END ATTACHED DOCUMENT]"
_PLAIN_MSG = "Give me an export-control assessment of this deal"


def test_rf947_doc_mode_drops_store_backed_addenda(patched_addenda):
    sp = asyncio.run(ae._build_calibrated_system_prompt(_DOC_MSG))
    assert CAL_MARKER not in sp, "calibration must be skipped in document mode"
    assert CON_MARKER not in sp, "contradictions must be skipped in document mode"
    # R-F2188: doc-mode now builds on the COMPACT base prompt (not the 100K+
    # full prompt) — the honesty + document-review constitution is present, lean.
    assert ae.ARIA_SYSTEM_PROMPT_COMPACT[:120] in sp


def test_rf947_plain_mode_keeps_addenda(patched_addenda):
    sp = asyncio.run(ae._build_calibrated_system_prompt(_PLAIN_MSG))
    assert CAL_MARKER in sp, "calibration must still inject for normal chat"
    assert CON_MARKER in sp, "contradictions must still inject for normal chat"


def test_rf947_doc_prompt_hard_capped(monkeypatch):
    """Even if a KEPT addendum is huge, the doc-mode prompt is capped so it can't
    eat the window. Force a giant contract-review addendum and assert the cap."""
    async def _cal():
        return {}
    monkeypatch.setattr(ae, "_get_cached_calibration", _cal)

    import aria_service.intel.contract_review_principles as _cr
    monkeypatch.setattr(_cr, "detect_review_intent", lambda m: True)
    monkeypatch.setattr(_cr, "addendum", lambda: "Y" * 400_000)

    sp = asyncio.run(ae._build_calibrated_system_prompt(_DOC_MSG))
    # R-F2188: the doc-mode cap dropped 150K→20K (compact base + bounded doc
    # addenda), so even a giant forced addendum is trimmed to ~20K.
    assert len(sp) <= 20_000 + 200, "doc-mode system prompt must be hard-capped"
    # base (compact) constitution survives the tail-trim (it is first)
    assert ae.ARIA_SYSTEM_PROMPT_COMPACT[:120] in sp
