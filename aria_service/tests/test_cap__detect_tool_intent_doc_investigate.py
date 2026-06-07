"""R-F1416 — Multi-company doc investigation per-entity routing capability test.

T0-4 problem: "Investigate these companies from <doc>" went to path:chat with
the whole doc in context (~51k→68k tokens > 61,536 budget → truncated → timeout).

Fix: _detect_tool_intent now detects doc-investigate patterns BEFORE the doc-aware
skip, extracts company names from the document text, and dispatches per-entity
via spawn_research_task (research_each type).
"""
import pytest
import re

from aria_service.routes.aria import _detect_tool_intent


def _make_doc_message(doc_body: str, question: str) -> str:
    """Build a message with an ATTACHED DOCUMENT block and a question.
    The doc body must be >= 200 chars to pass _extract_attached_document's guard."""
    # Pad the doc body to >= 200 chars if needed
    if len(doc_body) < 200:
        padding = "\n" + "x" * (200 - len(doc_body))
        doc_body += padding
    return (
        f"[ATTACHED DOCUMENT]\n{doc_body}\n[/ATTACHED DOCUMENT]\n\n"
        f"{question}"
    )


class TestDocInvestigateIntent:
    """Tests for R-F1416 doc-investigate intent detection."""

    def test_doc_investigate_routes_to_spawn_research_task(self):
        """'investigate these companies from this document' with ATTACHED DOCUMENT
        returns spawn_research_task intent, not None."""
        doc_body = (
            "COMPANY REGISTER EXTRACT\n\n"
            "1. Acme Corporation Ltd\n"
            "   Registered address: 123 Main St, London\n"
            "   Registration number: 12345678\n\n"
            "2. Beta Industries GmbH\n"
            "   Registered address: Industriestrasse 1, Berlin\n"
            "   Registration number: HRB 98765\n\n"
            "3. Gamma Defense LLC\n"
            "   Registered address: 456 Oak Ave, Delaware\n"
        )
        message = _make_doc_message(
            doc_body,
            "Investigate these companies from this document"
        )
        result = _detect_tool_intent(message)
        assert result is not None, "Should detect doc-investigate intent"
        assert result.get("tool") == "spawn_research_task", (
            f"Expected spawn_research_task, got {result.get('tool')}"
        )
        assert result.get("task_type") == "research_each", (
            f"Expected research_each, got {result.get('task_type')}"
        )
        entities = result.get("entities", [])
        assert len(entities) >= 2, f"Expected at least 2 entities, got {entities}"
        # Should have extracted company names from the doc
        assert any("Acme" in e for e in entities), f"Acme not found in {entities}"
        assert any("Beta" in e for e in entities), f"Beta not found in {entities}"
        assert any("Gamma" in e for e in entities), f"Gamma not found in {entities}"
        assert result.get("_reason") == "doc_investigate_rf1416"

    def test_doc_investigate_with_research_verb(self):
        """'research the entities in this file' also triggers doc-investigate."""
        doc_body = (
            "LIST OF SUPPLIERS\n\n"
            "Alpha Trading Ltd\n"
            "Delta Manufacturing Inc\n"
        )
        message = _make_doc_message(
            doc_body,
            "Research the entities in this file"
        )
        result = _detect_tool_intent(message)
        assert result is not None, "Should detect doc-investigate intent"
        assert result.get("tool") == "spawn_research_task"
        entities = result.get("entities", [])
        assert len(entities) >= 1

    def test_doc_review_still_returns_none(self):
        """'review this document' still returns None (falls through to LLM-pure)."""
        doc_body = "Some contract text here for review purposes.\n" * 20
        message = _make_doc_message(
            doc_body,
            "Review this document for me"
        )
        result = _detect_tool_intent(message)
        # Should return None because 'review' matches _DOC_REVIEW_VERB_RE
        # and there's no investigate/research verb
        assert result is None, (
            f"Expected None for doc review, got {result}"
        )

    def test_doc_investigate_without_attached_doc_returns_none(self):
        """'investigate these companies' without ATTACHED DOCUMENT returns None
        (falls through to other intent detection)."""
        message = "Investigate these companies: Acme Corp, Beta Ltd"
        result = _detect_tool_intent(message)
        # Without ATTACHED DOCUMENT, the doc-investigate intent doesn't fire.
        # It may match other intents (batch detection) or return None.
        # We just assert it's not the doc_investigate reason.
        if result:
            assert result.get("_reason") != "doc_investigate_rf1416", (
                "Should not fire doc-investigate without ATTACHED DOCUMENT"
            )

    def test_doc_investigate_extracts_all_caps_fallback(self):
        """When no company-suffix names found, fallback extracts all-caps lines."""
        doc_body = (
            "EXTRACT FROM TRADE REGISTER\n\n"
            "ARMOR SOLUTIONS INC\n"
            "123 Business Park\n"
            "DEFENCE DYNAMICS LTD\n"
            "456 Industrial Zone\n"
        )
        message = _make_doc_message(
            doc_body,
            "Investigate all companies from this document"
        )
        result = _detect_tool_intent(message)
        assert result is not None, f"Expected intent, got None"
        entities = result.get("entities", [])
        assert len(entities) >= 1
        # Should have extracted at least one company name
        # ARMOR SOLUTIONS INC and DEFENCE DYNAMICS LTD should be found
        # EXTRACT FROM TRADE REGISTER is a header and should be filtered out
        assert any("ARMOR" in e or "DEFENCE" in e for e in entities), (
            f"No company names found in {entities}"
        )

    def test_doc_investigate_caps_at_10_entities(self):
        """Doc-investigate caps extraction at 10 entities."""
        doc_body = "\n".join(
            f"Company {i} Ltd" for i in range(20)
        )
        message = _make_doc_message(
            doc_body,
            "Investigate all companies from this document"
        )
        result = _detect_tool_intent(message)
        assert result is not None
        entities = result.get("entities", [])
        assert len(entities) <= 10, f"Capped at 10, got {len(entities)}"

    def test_doc_investigate_short_doc_returns_none(self):
        """A very short document (<200 chars) doesn't trigger doc-investigate."""
        doc_body = "Short text"
        message = (
            f"[ATTACHED DOCUMENT]\n{doc_body}\n[/ATTACHED DOCUMENT]\n\n"
            f"Investigate companies from this document"
        )
        result = _detect_tool_intent(message)
        # Short doc should not trigger the intent (len < 200 check in _extract_attached_document)
        if result:
            assert result.get("_reason") != "doc_investigate_rf1416"
