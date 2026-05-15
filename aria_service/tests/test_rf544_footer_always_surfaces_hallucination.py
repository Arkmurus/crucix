"""R-F544 (2026-05-15, renumbered from R-F536 due to R-F534 collision) —
capability test: the R-F401 self-claim guard block MUST always appear
in the footer when an invariant violation is present, regardless of
how `build_footer()` is called.

Pre-R-F544, `confidence_footer.build_footer()` had a "nothing to say"
early-return that dropped the entire footer when:
  - verification=None (the common /chat/stream case), AND
  - the body had no inline [CONFIRMED] / [PROBABLE] / [ASSESSED] tag,
    AND
  - rag_sources_count=0

In that path, the R-F401 self_claim_guard scan never ran, so a
hallucination like "ARIA has 18-month TTL on facts" passed through
silently — directly violating [[aria_infinite_memory]] doctrine
("ARIA has INFINITE memory — never forgets, never loses data").

This capability test pins the user-visible doctrine: if the response
carries an R-F401 invariant violation, the footer must surface it.
"""
from __future__ import annotations


def test_rf544_hallucination_surfaces_when_verification_none_and_no_tag():
    """The bug fixture from 2026-05-13: verification=None, body has no
    inline tag, but body carries an 18-month TTL hallucination. The
    R-F401 guard block MUST appear in the footer."""
    from aria_service.intel.confidence_footer import build_footer
    body = (
        "I retain knowledge but the knowledge base has an 18-month TTL "
        "and entries are evicted after expiry. I will forget facts not "
        "reverified by then. " + "X" * 50
    )
    footer = build_footer(response_text=body, verification=None)
    assert footer, (
        "R-F544 regression: footer dropped on chat-path (verification=None, "
        "no inline tag). Hallucination would pass through silently."
    )
    assert "R-F401" in footer, (
        "R-F544 CRITICAL: 18-month TTL hallucination present but R-F401 "
        "guard block missing from footer."
    )


def test_rf544_hallucination_surfaces_via_chat_stream_args():
    """Same body but called with the streaming chat handler's typical
    args (tools_used + build_rev + trace_id, verification=None). The
    guard block must still surface."""
    from aria_service.intel.confidence_footer import build_footer
    body = (
        "ARIA knowledge persists with an 18-month TTL and gets evicted. " + "Y" * 50
    )
    footer = build_footer(
        response_text=body,
        verification=None,
        tools_used=["chat_lookup"],
        build_rev="R-F544",
        trace_id="abc12345",
    )
    assert footer
    assert "R-F401" in footer


def test_rf544_no_tools_disclosure_always_appears():
    """When no tool ran, the footer must explicitly disclose "(none —
    from memory / training)" rather than silently omitting the line.
    Doctrine: team can't infer from silence."""
    from aria_service.intel.confidence_footer import build_footer
    body = "X" * 100
    footer = build_footer(response_text=body, verification=None)
    assert "(none — from memory / training)" in footer


def test_rf544_short_reply_with_tag_still_renders():
    """A substantive 1-line DD verdict carrying a [PROBABLE] tag must
    still get a footer. Pre-R-F544 the 80-char floor dropped these."""
    from aria_service.intel.confidence_footer import build_footer
    body = "The director is Joe Bloggs [PROBABLE — single Companies House filing]."
    assert len(body) < 80  # pin the short-reply nature
    footer = build_footer(
        response_text=body,
        verification=None,
        tools_used=["companies_house_lookup"],
    )
    assert footer
    assert "PROBABLE" in footer
    assert "companies_house_lookup" in footer


def test_rf544_clean_short_reply_with_no_signals_still_skipped():
    """A short greeting with no tag, no tools, no verification, no
    violation — keep the original behaviour: don't decorate."""
    from aria_service.intel.confidence_footer import build_footer
    footer = build_footer(response_text="Hi there!", verification=None)
    assert footer == "", (
        "R-F544: short cosmetic greeting should not get a footer."
    )


def test_rf544_violation_scan_runs_only_once():
    """The pre-R-F544 code re-ran scan_response twice (once for early
    return, once for rendering). Pin that the scan happens once via
    monkey-patching the underlying module."""
    from aria_service.intel import confidence_footer as cf
    from aria_service.intel import self_claim_guard as scg

    call_count = {"n": 0}
    original = scg.scan_response

    def counting_scan(*args, **kwargs):
        call_count["n"] += 1
        return original(*args, **kwargs)

    scg.scan_response = counting_scan
    try:
        body = "I have an 18-month TTL on knowledge. " + "Z" * 60
        cf.build_footer(response_text=body, verification=None)
    finally:
        scg.scan_response = original
    assert call_count["n"] == 1, (
        f"R-F544: scan_response called {call_count['n']} times, expected 1. "
        "Pre-R-F544 it ran twice (early-return + render block)."
    )
