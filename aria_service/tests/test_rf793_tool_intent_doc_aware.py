"""R-F793 (2026-05-22): tool intent routing — tighten _SCREEN_KW + doc-aware skip.

Live evidence 2026-05-22: operator's question "can you share with me
the EU sanction packet where this rule is implemented: …" matched the
bare noun 'sanction' in _SCREEN_KW, routed to the screen tool with
" packet where this rule is implemented…" as the entity, returned no
hits, LLM fell back to general-knowledge inference. Operator's other
question "Aria, please review and see whether LUKOIL is mentioned in
this document" needs to land on the LLM-pure path (with the attached
doc in context) — not on any external lookup tool.
"""
from __future__ import annotations

from aria_service.routes import aria as routes_aria


def test_rf793_strip_handles_web_ui_closing_tag():
    """Web UI (aria.html) emits `[/ATTACHED DOCUMENT]`. Pre-R-F793 this
    closing form was NOT recognised by _ATTACHED_DOC_RE — the doc text
    never got stripped from the message before keyword detection. Result:
    keyword routing ran over the entire document content, and any
    sanctions / procurement / conflict word inside the doc misrouted the
    tool. This test pins the regex to both formats so a future regex
    edit can't silently break the web-UI path again.
    """
    msg = (
        "[ATTACHED DOCUMENT: refinery.pdf · pdfplumber · 95%]\n"
        "Document text mentions sanctions and procurement keywords.\n"
        "[/ATTACHED DOCUMENT]\n\n"
        "review this and tell me what you see"
    )
    stripped = routes_aria._strip_attached_document(msg)
    assert "[ATTACHED DOCUMENT" not in stripped
    assert "[/ATTACHED DOCUMENT" not in stripped
    assert "sanctions" not in stripped, (
        "R-F793 regression: doc text leaked past _strip_attached_document. "
        "Web-UI closing tag [/ATTACHED DOCUMENT] must be recognised."
    )


def test_rf793_strip_handles_wa_listener_closing_tag():
    """WhatsApp listener (lib/whatsapp/waListener.mjs) emits the legacy
    `[END ATTACHED DOCUMENT]` form. Must continue to work."""
    msg = (
        "[ATTACHED DOCUMENT: brief.pdf]\n"
        "Document text mentions sanctions and procurement.\n"
        "[END ATTACHED DOCUMENT]\n\n"
        "screen this entity"
    )
    stripped = routes_aria._strip_attached_document(msg)
    assert "[ATTACHED DOCUMENT" not in stripped
    assert "[END ATTACHED DOCUMENT" not in stripped
    assert "sanctions" not in stripped


def test_rf793_bare_sanction_no_longer_matches_screen():
    """`_SCREEN_KW` must not match the standalone noun 'sanction' in
    "the EU sanction packet". The legitimate sanctions intent stays
    covered by the multi-word forms (`screen X`, `sanctions check X`,
    `compliance check X`, `fuzzy match`, `fuzzy screen`).
    """
    assert routes_aria._SCREEN_KW.search(
        "can you share with me the EU sanction packet where this rule is implemented"
    ) is None


def test_rf793_screen_verb_still_matches():
    assert routes_aria._SCREEN_KW.search("screen Acme Corp for sanctions") is not None
    assert routes_aria._SCREEN_KW.search("compliance check on Acme Corp") is not None
    assert routes_aria._SCREEN_KW.search("sanctions check Acme Holdings") is not None
    assert routes_aria._SCREEN_KW.search("run compliance on Acme Corp") is not None


def test_rf793_doc_reference_regex_matches_natural_phrasing():
    assert routes_aria._DOC_REFERENCE_RE.search("review this document") is not None
    assert routes_aria._DOC_REFERENCE_RE.search("see whether LUKOIL is mentioned in this document") is not None
    assert routes_aria._DOC_REFERENCE_RE.search("the attached file shows") is not None
    assert routes_aria._DOC_REFERENCE_RE.search("read the pdf carefully") is not None
    assert routes_aria._DOC_REFERENCE_RE.search("the uploaded report") is not None


def test_rf793_doc_reference_regex_does_not_match_random_text():
    assert routes_aria._DOC_REFERENCE_RE.search("brief me on Angola") is None
    assert routes_aria._DOC_REFERENCE_RE.search("screen LUKOIL") is None
    assert routes_aria._DOC_REFERENCE_RE.search("what tenders are open in Iraq") is None


def test_rf793_intent_returns_none_when_doc_attached_and_referenced():
    """The operator's exact question pattern: attached doc + question
    referring to it → no tool routing → LLM-pure path so the LLM reads
    the embedded [ATTACHED DOCUMENT …] block directly."""
    message = (
        "[ATTACHED DOCUMENT: refinery_due_diligence.pdf · pdfplumber · 95% · 12/12 pages]\n"
        "<full doc text would be here in production>\n"
        "[/ATTACHED DOCUMENT]\n\n"
        "Aria, please review and see whether LUKOIL is mentioned in this document."
    )
    assert routes_aria._detect_tool_intent(message) is None


def test_rf793_intent_routes_normally_when_doc_attached_but_question_unrelated():
    """If a doc is attached but the user's question is about an unrelated
    entity, routing should NOT be suppressed. Example: doc is briefing
    material, user asks about something separate. Here, no doc-reference
    keyword in the user text → routing proceeds normally."""
    message = (
        "[ATTACHED DOCUMENT: morning_briefing.pdf · pdfplumber · 99% · 3/3 pages]\n"
        "Briefing content\n"
        "[/ATTACHED DOCUMENT]\n\n"
        "/screen Acme Defence Holdings"
    )
    intent = routes_aria._detect_tool_intent(message)
    assert intent is not None
    assert intent.get("tool") == "screen"
    assert intent.get("entity") == "Acme Defence Holdings"


def test_rf793_slash_screen_still_fires_with_attached_doc_and_doc_reference():
    """Explicit slash command beats doc-aware suppression. If the user
    types `/screen X` even with a doc attached AND mentions the doc,
    the slash form wins — that's the user's explicit intent.

    Implementation order: /screen slash check runs (line ~4087) BEFORE
    the doc-aware skip (line ~4150 after the briefing block), so the
    slash returns before the skip evaluates.
    """
    message = (
        "[ATTACHED DOCUMENT: x.pdf · pdfplumber · 90% · 1/1 pages]\n"
        "text\n"
        "[/ATTACHED DOCUMENT]\n\n"
        "/screen Acme Defence Holdings  (regarding this document)"
    )
    intent = routes_aria._detect_tool_intent(message)
    assert intent is not None
    assert intent.get("tool") == "screen"


def test_rf793_no_doc_attached_but_doc_reference_routes_normally():
    """If user says 'this document' without actually attaching one,
    we don't suppress routing (no [ATTACHED DOCUMENT] block means
    there's nothing for the LLM to read anyway). Falls through to the
    normal keyword routing — which for this message returns None
    because no keyword matches."""
    message = "Aria, please review and see whether LUKOIL is mentioned in this document."
    # No attached doc. Should NOT trigger the R-F793 skip; instead
    # fall through to keyword routing. The bare "sanction" no longer
    # matches (R-F793 first fix), and no other keyword matches, so
    # intent ends up None — falls to LLM-pure path, which is correct
    # (the LLM will answer "I don't see a document attached").
    intent = routes_aria._detect_tool_intent(message)
    assert intent is None


def test_rf793_legal_citation_question_no_longer_misroutes_to_screen():
    """The operator's third (successful) attempt:
    "can you share with me the EU sanction packet where this rule is
    implemented" — pre-R-F793 matched bare 'sanction', routed to
    screen with garbage entity. Post-R-F793 the bare noun no longer
    matches → falls through → LLM-pure path → correct answer.
    """
    message = (
        "can you share with me the EU sanction packet where this rule is "
        "implemented: 'The real barrier is the 50% ownership rule…'"
    )
    intent = routes_aria._detect_tool_intent(message)
    # No screen route; either None (LLM-pure) or a different tool.
    assert (intent is None) or (intent.get("tool") != "screen"), (
        f"R-F793 regression: bare 'sanction' should no longer route to "
        f"screen on a legal-citation question. Got: {intent}"
    )
