"""R-F3620 — ARIA web-searched the public internet about herself.

THE LIVE INCIDENT (operator WhatsApp, 2026-08-01, mid-outage):

    Antonio: "Aria, what are the issue with your current command centre?
              You are not answering my question?"
    Aria:    "Based on the tool output provided, the search for 'command centre
              issues' returned ... Alienware Command Center (AWCC) – Error code
              0x803F8001 ..."
    footer:  Tools: web lookup

Her own LLM chain was failing every call at that moment (R-F3606). She answered
from consumer-hardware support forums.

THIS IS A ROUTING FAILURE, NOT A SEARCH FAILURE. `_detect_tool_intent` has an
R-F399 branch whose own comment says it "MUST run before any web-search path",
gated on `is_capability_introspection_query()`. That returned False, so the
question fell through to web search.

ROOT CAUSE — A FORKED MEASURE, NARROWEST FORK ON THE MOST CONSEQUENTIAL GATE.
Four predicates answered "is this about ARIA?" and disagreed on these exact
words. The one guarding TOOL ROUTING — whether she searches the public web
about herself — was the narrowest. R-F3612 (hours earlier, same day) widened
only the context-block fork and added a fourth. R-F3620 defines it ONCE in
self_infra_detector, at two documented strictness levels.
"""
import pytest

from aria_service.intel.self_infra_detector import (
    is_capability_introspection_query,
    is_self_fault_report,
)
from aria_service.intel.self_introspect_guard import detect_self_capability_question


# The operator's exact words, verbatim from the transcript.
_LIVE_Q1 = "Aria, what are the issue with your current command centre?"
_LIVE_Q2 = "what is the current issues you are experiencing with your system?"


# ── THE CAPABILITY TEST — drive the real router ──────────────────────────────


def test_capability_the_operators_question_routes_to_self_introspect():
    """FAILS BEFORE: _detect_tool_intent returned no self_introspect intent for
    these, so the message fell through to the web-search path and was answered
    from Alienware support forums."""
    from aria_service.routes.aria import _detect_tool_intent

    for q in (_LIVE_Q1, _LIVE_Q2):
        intent = _detect_tool_intent(q)
        assert intent is not None, f"no tool intent at all for {q!r}"
        assert intent.get("tool") == "self_introspect", (
            f"{q!r} routed to {intent.get('tool')!r} — a question about ARIA's "
            f"own health must never reach an external search tool"
        )


def test_capability_a_self_question_never_reaches_a_web_tool():
    """The user-visible property: whatever else happens, these must not
    dispatch an external research tool."""
    from aria_service.routes.aria import _detect_tool_intent

    external = {"web_search", "deep_research", "spawn_research_task",
                "brave_answer", "crawl", "extract_url", "read"}
    for q in (_LIVE_Q1, _LIVE_Q2, "what's wrong with you?",
              "are you experiencing any problems?",
              "is anything broken with your setup?"):
        intent = _detect_tool_intent(q) or {}
        assert intent.get("tool") not in external, (
            f"{q!r} dispatched {intent.get('tool')!r} — ARIA would search the "
            f"public web about herself"
        )


# ── The router-level predicate ───────────────────────────────────────────────


def test_the_routing_predicate_now_fires():
    for q in (_LIVE_Q1, _LIVE_Q2, "what's wrong with you?",
              "are you experiencing any problems?",
              "is anything broken with your setup?"):
        assert is_capability_introspection_query(q), q


def test_r_f918_availability_phrasings_still_covered():
    """Verify the instrument: the new predicate is a COMPLEMENT. Availability
    questions were already handled and must not silently depend on the new
    branch."""
    for q in ("why are you down?", "are you ok?", "why are you unavailable?"):
        assert is_capability_introspection_query(q), q
        assert not is_self_fault_report(q, strict=True), (
            f"{q!r} should still be covered by SELF_STATE_AVAILABILITY_RE, not "
            f"by the new fault-report branch — otherwise the complement claim "
            f"in the R-F3620 comment is wrong"
        )


# ── The expensive error: suppressing a legitimate search ─────────────────────


@pytest.mark.parametrize("q", [
    "what are the issues with the Korvera contract?",
    "what problems are there with your supplier?",
    "what's wrong with your analysis of the tender?",
    "summarise the problems found in the due diligence report",
    "is there a problem with the Bulgarian entity's filings?",
    "list the failures in the 2024 accounts",
    "screen Rosoboronexport for sanctions issues",
])
def test_third_party_fault_questions_still_reach_research(q):
    """STRICT exists for this. A false positive on the routing gate SUPPRESSES
    a real web search — 'your supplier' and 'your analysis' are the user's
    things, not ARIA's, and must keep routing to research.

    This is the check that decided the design: merging all four detectors into
    one broad predicate would have looked tidier and quietly broken real work.
    """
    assert not is_self_fault_report(q, strict=True), (
        f"{q!r} would be treated as a question about ARIA and would no longer "
        f"be researched"
    )


def test_strict_is_genuinely_stricter_than_broad():
    """Both levels must be real, or the two-strictness design is decoration."""
    q = "what problems are there with your supplier?"
    assert not is_self_fault_report(q, strict=True)
    assert is_self_fault_report(q, strict=False)


# ── The fork is gone ─────────────────────────────────────────────────────────


def test_the_context_block_delegates_instead_of_keeping_its_own_copy():
    """R-F3612 put an inline fault pattern in self_introspect_guard. That made
    FOUR forks. The guard must now consult the shared definition."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "intel"
           / "self_introspect_guard.py").read_text(encoding="utf-8")
    assert "is_self_fault_report" in src, (
        "the context block must delegate to the shared predicate"
    )
    # the duplicated fault alternation must be gone from the local regex
    assert "malfunction" not in src.split("def detect_self_capability_question")[0], (
        "the R-F3612 inline fault pattern is still duplicated in _CAPABILITY_KEYWORDS"
    )


def test_the_block_detector_still_answers_the_same_way():
    """Removing the fork must not narrow the context block — these all fired
    under R-F3612 and must still fire via delegation."""
    for q in (_LIVE_Q1, _LIVE_Q2, "what's wrong with you?",
              "are you experiencing any problems?",
              "is anything broken with your setup?"):
        assert detect_self_capability_question(q), q
    for q in ("what are the issues with the Korvera contract?",
              "summarise the problems found in the due diligence report",
              "is there a problem with the Bulgarian entity's filings?"):
        assert not detect_self_capability_question(q), q
