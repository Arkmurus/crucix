"""R-F2229 — §22a defense-in-depth: a doc-review request never reaches an
external tool inside _detect_tool_intent.

registry_lookup / dd_orchestrate / portal_register / pre_meeting_briefing all
sat BEFORE the R-F793 doc-review skip and were NOT doc-gated, so a doc-review
phrasing that matched one (e.g. "review this Estonian agreement" → registry_lookup
via "estonian") could route a document to an external tool. R-F2229 hoists the
doc-review skip ahead of those branches — EXCEPT an explicit multi-company
doc-investigate (R-F1416), which keeps precedence.
"""
from __future__ import annotations

from aria_service.routes.aria import _detect_tool_intent

_DOC = (
    "[ATTACHED DOCUMENT: contract.pdf]\n"
    "Acme Corporation Ltd\n"
    + ("This mutual non-disclosure agreement is entered into between the parties. " * 6)
    + "\n[END ATTACHED DOCUMENT]"
)


class TestR_F2229_DocReviewGuard:
    def test_doc_review_does_not_route_to_registry_lookup(self):
        """The leak: doc + a jurisdiction word + review verb. Must NOT fire
        registry_lookup (old code returned it via 'estonian')."""
        r = _detect_tool_intent(_DOC + "\nreview this Estonian agreement for feedback")
        assert r is None, f"doc-review must route LLM-pure (got {r})"

    def test_no_doc_registry_lookup_still_fires(self):
        """No document → registry_lookup must still work (no over-suppression)."""
        r = _detect_tool_intent("look up the Estonian company Foobar OU")
        assert r is not None and r.get("tool") == "registry_lookup", r

    def test_doc_review_briefing_word_does_not_route_to_tool(self):
        """Covers the class beyond registry_lookup (pre_meeting_briefing etc.)."""
        r = _detect_tool_intent(_DOC + "\nplease review and give feedback on this briefing")
        assert r is None, f"doc-review must route LLM-pure (got {r})"

    def test_doc_investigate_still_reaches_rf1416(self):
        """The deliberate exception: doc + investigate-verb + companies keeps
        external-tool precedence (must NOT be short-circuited to None)."""
        r = _detect_tool_intent(_DOC + "\ninvestigate the companies named in this agreement")
        assert r is not None, "R-F1416 doc-investigate must not be clobbered by the guard"
        assert r.get("tool") == "spawn_research_task", r
