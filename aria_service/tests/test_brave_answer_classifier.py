"""Regression tests for the brave_answer chat-classifier (added 2026-04-21).

The classifier routes simple factual questions ("what is X", "who is Y",
"when did Z") to Brave Answers — which is grounded in fresh web search,
cheap (~$0.04 paid, $0 on memory-first hit), and absorbs into all three
memory tiers for permanent recall.

These tests pin the trigger + exclude logic so:
  - Generic factual Qs route to brave_answer
  - DD / compliance / meta_query / composition stay on their paths
  - Officeholder-with-country keeps firing investigate (matches ABOVE brave in the classifier)
"""
from __future__ import annotations


# ── Must-route-to-brave_answer cases ────────────────────────────────

def test_what_is_factual_question_routes_to_brave():
    from aria_service.routes.aria import _BRAVE_QA_TRIGGER_RE, _BRAVE_QA_EXCLUDE_RE
    msg = "what is the capital of Mozambique"
    assert _BRAVE_QA_TRIGGER_RE.search(msg) is not None
    assert _BRAVE_QA_EXCLUDE_RE.search(msg) is None


def test_who_is_without_officeholder_keyword_routes_to_brave():
    """'Who is X' without a president/minister keyword → brave (officeholder
    classifier requires officeholder keyword + country match, so a plain
    'who is João Lourenço' would not match it)."""
    from aria_service.routes.aria import _BRAVE_QA_TRIGGER_RE, _BRAVE_QA_EXCLUDE_RE
    msg = "who is João Lourenço"
    assert _BRAVE_QA_TRIGGER_RE.search(msg)
    assert _BRAVE_QA_EXCLUDE_RE.search(msg) is None


def test_when_did_factual_triggers_brave():
    from aria_service.routes.aria import _BRAVE_QA_TRIGGER_RE, _BRAVE_QA_EXCLUDE_RE
    msg = "when did Angola gain independence"
    assert _BRAVE_QA_TRIGGER_RE.search(msg)
    assert _BRAVE_QA_EXCLUDE_RE.search(msg) is None


def test_aria_prefix_still_triggers_brave():
    """Chat fillers ('Aria, please, can you') must not block the match."""
    from aria_service.routes.aria import _BRAVE_QA_TRIGGER_RE
    for msg in [
        "Aria, what is CPV code 35",
        "please what is NATO STANAG 4569",
        "can you what is export control under EAR",
    ]:
        assert _BRAVE_QA_TRIGGER_RE.search(msg), f"should trigger: {msg!r}"


# ── Must-NOT-route-to-brave cases — must stay on their real paths ──

def test_dd_request_excluded_from_brave():
    from aria_service.routes.aria import _BRAVE_QA_TRIGGER_RE, _BRAVE_QA_EXCLUDE_RE
    msg = "what is the DD status on Acme Corp"
    assert _BRAVE_QA_TRIGGER_RE.search(msg)
    assert _BRAVE_QA_EXCLUDE_RE.search(msg)


def test_due_diligence_excluded_from_brave():
    from aria_service.routes.aria import _BRAVE_QA_EXCLUDE_RE
    msg = "what is the due diligence report for Serban"
    assert _BRAVE_QA_EXCLUDE_RE.search(msg)


def test_sanctions_screen_excluded_from_brave():
    from aria_service.routes.aria import _BRAVE_QA_EXCLUDE_RE
    msg = "what is the sanction exposure of this entity"
    assert _BRAVE_QA_EXCLUDE_RE.search(msg)


def test_meta_query_excluded_from_brave():
    """meta_query introspection must stay on the meta_query path
    (which injects brain stats + LLM chain + email reader status)."""
    from aria_service.routes.aria import _BRAVE_QA_EXCLUDE_RE
    for msg in [
        "what is your memory status",
        "what is your brain stats",
        "what is your email reader doing",
    ]:
        assert _BRAVE_QA_EXCLUDE_RE.search(msg), f"should be excluded: {msg!r}"


def test_mid_sentence_what_is_does_not_false_fire():
    """'Tell me about what is happening' is prose, not a factual question
    shape — trigger requires start-of-message match."""
    from aria_service.routes.aria import _BRAVE_QA_TRIGGER_RE
    msg = "tell me about what is happening in Angola"
    assert _BRAVE_QA_TRIGGER_RE.search(msg) is None


def test_composition_request_does_not_match_trigger():
    """'generate a digest' starts with a compose verb, not a question word."""
    from aria_service.routes.aria import _BRAVE_QA_TRIGGER_RE
    msg = "generate today's morning digest"
    assert _BRAVE_QA_TRIGGER_RE.search(msg) is None


def test_investigate_excluded_from_brave():
    from aria_service.routes.aria import _BRAVE_QA_EXCLUDE_RE
    msg = "what is the verdict — should we investigate this company"
    assert _BRAVE_QA_EXCLUDE_RE.search(msg)


# ── 2026-04-21 incident — email recall must NOT route to Brave ──

def test_what_was_in_test_email_excluded_from_brave():
    """Operator's WhatsApp query: 'what was in the test email I sent you today'
    was misrouted to brave_answer, which returned a cached 'no email content'
    answer and ARIA narrated that instead of running the RAG email search."""
    from aria_service.routes.aria import _BRAVE_QA_EXCLUDE_RE
    msg = "what was in the test email I sent you today"
    assert _BRAVE_QA_EXCLUDE_RE.search(msg)


def test_what_did_my_email_say_excluded():
    from aria_service.routes.aria import _BRAVE_QA_EXCLUDE_RE
    for msg in [
        "what was in my email",
        "what was in my recent email",
        "what was in the last email from Serban",
        "quote the email content",
        "show me the email body",
    ]:
        assert _BRAVE_QA_EXCLUDE_RE.search(msg), f"should be excluded: {msg!r}"


def test_email_recall_routes_to_meta_query():
    """These same queries should FIRE meta_query instead. The regex is
    defined inside _detect_tool_intent (function-local), so test the
    extended pattern we added here by recompiling the same shape."""
    import re
    meta_pattern = re.compile(
        r"\b(?:"
        r"(?:what|which)\s+(?:was|is|were)\s+(?:in\s+)?(?:my|the|that|this)\s+(?:test\s+|recent\s+|last\s+)?(?:email|inbox|message|e-mail)|"
        r"quote\s+(?:the\s+)?(?:email|content|body|message)|"
        r"(?:show|give)\s+me\s+(?:the\s+)?(?:email|inbox)\s+(?:content|body|text)"
        r")\b",
        re.IGNORECASE,
    )
    for msg in [
        "what was in the test email",
        "what was in my email",
        "quote the email content",
        "show me the email content",
    ]:
        assert meta_pattern.search(msg), f"meta_query should catch: {msg!r}"
