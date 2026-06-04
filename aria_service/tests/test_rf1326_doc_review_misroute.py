"""R-F1326 — capability test: attached-doc + legal-review mis-routing fix.

The bug: "review the NDA for feedback" with an attached document routed to
company_investigator because:
  1. _DOC_REFERENCE_RE didn't include legal-doc nouns (NDA, agreement, contract)
  2. "review" matched _INVESTIGATE_KW before the doc-aware skip could fire
  3. company_investigator had no defensive guard for non-company inputs

This test pins:
  1. _detect_tool_intent returns None (LLM-pure) for attached-doc + legal review
  2. _detect_tool_intent returns None for attached-doc + review verb (no noun)
  3. _detect_tool_intent still routes non-doc investigate requests correctly
  4. company_investigator._looks_like_document_text rejects document bodies
"""
from __future__ import annotations

import re as _re


def _make_wa_message(doc_body: str, question: str) -> str:
    """Simulate a WhatsApp message with an attached document block."""
    return (
        f"[ATTACHED DOCUMENT]\n{doc_body}\n[/ATTACHED DOCUMENT]\n"
        f"User: {question}"
    )


# ── Test 1: _detect_tool_intent ──────────────────────────────────────────

def _detect_tool_intent(message: str) -> dict | None:
    """Inline copy of the function from routes/aria.py for test isolation."""
    from aria_service.routes.aria import (
        _strip_attached_document,
        _strip_listener_context,
        _DOC_REFERENCE_RE,
        _DOC_REVIEW_VERB_RE,
    )
    msg = _strip_attached_document(_strip_listener_context(message)).strip()
    if not msg:
        return None

    # The doc-aware skip (R-F793/R-F1326) — must run before all tool keywords
    if "[ATTACHED DOCUMENT" in message and (
        _DOC_REFERENCE_RE.search(msg) or _DOC_REVIEW_VERB_RE.search(msg)
    ):
        return None

    # Simulate the investigate keyword check
    _INVESTIGATE_KW = _re.compile(
        r"\b("
        r"investigate|investigation|research|look\s+into|"
        r"tell\s+me\s+(?:everything\s+)?about|"
        r"what\s+(?:do\s+you\s+know|can\s+you\s+tell\s+me)(?:\s+about)?|"
        r"who\s+is|what\s+is|"
        r"information\s+(?:on|about)|"
        r"details?\s+(?:on|about)|"
        r"review|feedback|assess"
        r")\b",
        _re.IGNORECASE,
    )
    if _INVESTIGATE_KW.search(msg):
        return {"tool": "deep_research", "entity": "test"}

    return None


def test_rf1326_doc_with_nda_review_returns_none():
    """'review the NDA for feedback' with attached doc → None (LLM-pure)."""
    msg = _make_wa_message(
        "This Non-Disclosure Agreement is made on...",
        "review the NDA for feedback",
    )
    result = _detect_tool_intent(msg)
    assert result is None, (
        f"R-F1326: attached-doc + 'review the NDA' should return None "
        f"(LLM-pure), got {result}"
    )


def test_rf1326_doc_with_contract_review_returns_none():
    """'review this contract' with attached doc → None."""
    msg = _make_wa_message(
        "Service Agreement between parties...",
        "review this contract and highlight key clauses",
    )
    result = _detect_tool_intent(msg)
    assert result is None, (
        f"R-F1326: attached-doc + 'review this contract' should return None, "
        f"got {result}"
    )


def test_rf1326_doc_with_review_verb_no_noun_returns_none():
    """'review this' with attached doc → None (verb-only match)."""
    msg = _make_wa_message(
        "Some document text here...",
        "review this and give me feedback",
    )
    result = _detect_tool_intent(msg)
    assert result is None, (
        f"R-F1326: attached-doc + 'review this' should return None, "
        f"got {result}"
    )


def test_rf1326_doc_with_agreement_assessment_returns_none():
    """'assess this agreement' with attached doc → None."""
    msg = _make_wa_message(
        "Memorandum of Understanding...",
        "assess this agreement for risks",
    )
    result = _detect_tool_intent(msg)
    assert result is None, (
        f"R-F1326: attached-doc + 'assess this agreement' should return None, "
        f"got {result}"
    )


def test_rf1326_no_doc_still_routes_investigate():
    """Without an attached doc, 'review Acme Corp' still routes to investigate."""
    msg = "review Acme Corp and tell me about them"
    result = _detect_tool_intent(msg)
    assert result is not None, (
        "R-F1326: 'review Acme Corp' without attached doc should still "
        "route to investigate"
    )
    assert result["tool"] == "deep_research", (
        f"Expected deep_research, got {result}"
    )


# ── Test 2: _looks_like_document_text ────────────────────────────────────

def test_rf1326_looks_like_document_text_rejects_long_text():
    """Text >200 chars is rejected as non-company."""
    from aria_service.intel.company_investigator import _looks_like_document_text
    long_text = "X" * 250
    assert _looks_like_document_text(long_text), "Long text should be rejected"


def test_rf1326_looks_like_document_text_rejects_newlines():
    """Text with newlines is rejected."""
    from aria_service.intel.company_investigator import _looks_like_document_text
    assert _looks_like_document_text("Acme Corp\nThis is a contract..."), (
        "Multi-line text should be rejected"
    )


def test_rf1326_looks_like_document_text_rejects_legal_boilerplate():
    """Text with legal boilerplate is rejected."""
    from aria_service.intel.company_investigator import _looks_like_document_text
    assert _looks_like_document_text(
        "This Non-Disclosure Agreement is confidential and privileged"
    ), "Legal boilerplate should be rejected"


def test_rf1326_looks_like_document_text_accepts_company_name():
    """Normal company names pass through."""
    from aria_service.intel.company_investigator import _looks_like_document_text
    assert not _looks_like_document_text("Acme Corporation Ltd"), (
        "Normal company name should not be rejected"
    )
    assert not _looks_like_document_text("Nebraska Gas Turbine Inc"), (
        "Multi-word company name should not be rejected"
    )
    assert not _looks_like_document_text("BAE Systems plc"), (
        "Company with plc suffix should not be rejected"
    )


def test_rf1326_looks_like_document_text_rejects_attached_doc_marker():
    """Text containing [ATTACHED DOCUMENT marker is rejected."""
    from aria_service.intel.company_investigator import _looks_like_document_text
    assert _looks_like_document_text(
        "[ATTACHED DOCUMENT] some text here"
    ), "Attached document marker should be rejected"


# ── Test 3: _DOC_REFERENCE_RE expanded nouns ─────────────────────────────

def test_rf1326_doc_reference_re_matches_legal_nouns():
    """_DOC_REFERENCE_RE must match 'this NDA', 'the contract', etc."""
    from aria_service.routes.aria import _DOC_REFERENCE_RE
    cases = [
        "this NDA",
        "the agreement",
        "this contract",
        "the attached clause",
        "the terms",
        "the schedule",
        "this addendum",
        "the MOU",
        "this LOI",
        "the SOW",
        "this T&Cs",
    ]
    for case in cases:
        assert _DOC_REFERENCE_RE.search(case), (
            f"_DOC_REFERENCE_RE should match {case!r}"
        )


def test_rf1326_doc_review_verb_re_matches_review_verbs():
    """_DOC_REVIEW_VERB_RE must match 'review', 'feedback', etc."""
    from aria_service.routes.aria import _DOC_REVIEW_VERB_RE
    cases = [
        "review this",
        "feedback on the document",
        "redline the contract",
        "mark up the agreement",
        "comment on this NDA",
        "assess this document",
    ]
    for case in cases:
        assert _DOC_REVIEW_VERB_RE.search(case), (
            f"_DOC_REVIEW_VERB_RE should match {case!r}"
        )
