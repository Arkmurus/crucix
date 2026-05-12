"""R-F350 (2026-05-12) — tool-outcome classifier for streaming status line.

Live evidence (operator chat 2026-05-12 ADSM DD):
    ARIA — 💭 Thinking…
    ARIA — 🔄 Tool: extract_url completed. Generating response...
    [...body says...] Website extraction (attempted) | 0 — tool crashed 🚫

The streaming status header fired "completed" because the intent
detector matched and `tool_used` was set, regardless of whether
`_execute_tool` actually returned useful data. Honest streaming
requires the header to reflect tool OUTCOME, not tool INVOCATION.

These tests pin the classifier behaviour at the call-site that emits
the SSE status line in `/chat/stream`.
"""
from __future__ import annotations

import pytest

from aria_service.routes.aria import _classify_tool_outcome


class TestRF350ToolOutcomeClassifier:

    def test_empty_context_is_attempted(self):
        assert _classify_tool_outcome("") == "attempted"
        assert _classify_tool_outcome("   ") == "attempted"
        assert _classify_tool_outcome(None) == "attempted"  # defensive

    def test_extraction_failed_marker_is_attempted(self):
        """The live failure mode — extract_url returned the failure
        sentinel block. Header must NOT say 'completed'."""
        context = (
            "\n\n[TOOL: extract_url — FETCH/EXTRACTION FAILED]\n"
            "URL: https://adsm-sa.com/\n"
            "Error: timeout\n"
            "Duration: 30000ms\n"
        )
        assert _classify_tool_outcome(context) == "attempted"

    def test_short_fetch_failed_marker_is_attempted(self):
        """Alternative failure phrasing used in some tool paths."""
        assert _classify_tool_outcome("FETCH FAILED for https://x.com") == "attempted"
        assert _classify_tool_outcome("EXTRACTION FAILED — host blocked") == "attempted"

    def test_web_search_failed_marker_is_attempted(self):
        assert _classify_tool_outcome("[WEB SEARCH FAILED — all providers cooling]") == "attempted"

    def test_tool_error_marker_is_attempted(self):
        assert _classify_tool_outcome("[TOOL ERROR: timeout]") == "attempted"
        assert _classify_tool_outcome("TOOL ERROR: api_quota_exceeded") == "attempted"

    def test_no_results_marker_is_attempted(self):
        assert _classify_tool_outcome("no results returned for query") == "attempted"
        assert _classify_tool_outcome("[TOOL: NONE] no tool matched the intent") == "attempted"

    def test_tool_unavailable_marker_is_attempted(self):
        assert _classify_tool_outcome("[DD UNAVAILABLE — orchestrator import failed]") == "attempted"
        assert _classify_tool_outcome("TOOL UNAVAILABLE on this build") == "attempted"

    def test_real_extraction_output_is_completed(self):
        """A tool result that actually returned content gets the
        honest 'completed' tag — we don't want to over-flag."""
        context = (
            "[TOOL: extract_url]\n"
            "URL: https://adsm-sa.com/\n"
            "Pages crawled: 5\n"
            "Body content:\n"
            "Advanced Defence Systems for Military preparations (ADSM)...\n"
            "Slogan: 'Working with us is winning for both of us'\n"
        )
        assert _classify_tool_outcome(context) == "completed"

    def test_dd_orchestrator_output_is_completed(self):
        """DD orchestrator output with real layer findings is completed."""
        context = (
            "[DD ORCHESTRATOR: 7-layer DD on Modirum Gespi]\n"
            "Layer 1 (identity): VERIFIED — Brazilian defence company.\n"
            "Layer 4 (compliance): CLEAN — no OFAC/EU/UK matches.\n"
            "BLUF: AMBER — incomplete public footprint."
        )
        assert _classify_tool_outcome(context) == "completed"

    def test_web_search_output_is_completed(self):
        """A web_search result list counts as completed."""
        context = (
            "[TOOL: web_search]\n"
            "Query: ADSM Saudi Arabia\n"
            "Results: 35 hits\n"
            "- https://example.com/article1\n"
        )
        assert _classify_tool_outcome(context) == "completed"


class TestRF352ExtendedCoverage:
    """R-F352 (2026-05-12) — verifier-driven coverage extension.
    First-pass R-F350 only caught extract_url + web_search NO-RESULTS
    shapes. Audit of `_execute_tool` surfaced 9 more failure paths
    that still emitted 'completed' in the stream header. These tests
    pin classification of each missed shape.
    """

    def test_dd_orchestrate_failed_block_is_attempted(self):
        ctx = (
            "\n\n[TOOL: dd_orchestrate — FAILED]\n"
            "Entity: ACME Corp\n"
            "Error: ImportError on dd_orchestrator module"
        )
        assert _classify_tool_outcome(ctx) == "attempted"

    def test_dd_blocked_by_verification_gate_is_attempted(self):
        ctx = (
            "\n\n[TOOL: dd_orchestrate — BLOCKED BY VERIFICATION GATE]\n"
            "Entity: Unknown\n"
            "Reason: <2 sources for single CONFIRMED finding"
        )
        assert _classify_tool_outcome(ctx) == "attempted"

    def test_deep_research_failed_is_attempted(self):
        ctx = "\n\n[TOOL: deep_research — FAILED]\nQuery: x\nError: timeout"
        assert _classify_tool_outcome(ctx) == "attempted"

    def test_web_search_insufficient_intel_is_attempted(self):
        ctx = "\n\n[TOOL: web_search — INSUFFICIENT_PUBLIC_INTEL]\nQuery: niche"
        assert _classify_tool_outcome(ctx) == "attempted"

    def test_spawn_research_rejected_is_attempted(self):
        ctx = "\n\n[TOOL: spawn_research_task — REJECTED]\nReason: duplicate"
        assert _classify_tool_outcome(ctx) == "attempted"

    def test_raise_ticket_refused_is_attempted(self):
        ctx = "\n\n[TOOL: raise_ticket — REFUSED]\nReason: insufficient grounding"
        assert _classify_tool_outcome(ctx) == "attempted"

    def test_list_tickets_unavailable_is_attempted(self):
        ctx = "\n\n[TOOL: list_tickets — UNAVAILABLE]\nReason: gh CLI not configured"
        assert _classify_tool_outcome(ctx) == "attempted"

    def test_auto_sanctions_screen_failed_is_attempted(self):
        ctx = "\n\n[TOOL: auto_sanctions_screen — FAILED]\nError: OpenSanctions timeout"
        assert _classify_tool_outcome(ctx) == "attempted"

    def test_lowercase_generic_catchall_is_attempted(self):
        """The generic catch-all branch in _execute_tool emits lowercase
        `— failed:` (see aria.py:6042-style). Must be caught."""
        ctx = "\n\n[TOOL: registry_lookup — failed: connection refused]"
        assert _classify_tool_outcome(ctx) == "attempted"

    def test_no_usable_data_returned_is_attempted(self):
        ctx = (
            "\n\n[TOOL: extract_url]\n"
            "URL: https://x.com/\n"
            "[NO USABLE DATA RETURNED — fall back to general knowledge with disclaimer.]"
        )
        assert _classify_tool_outcome(ctx) == "attempted"

    def test_narrative_quoting_failure_word_is_NOT_misclassified(self):
        """False-positive guard: a SUCCESSFUL DD that quotes a failure
        word deep in its body must NOT be flipped to 'attempted'. The
        head-only scan (R-F352) defends this."""
        # 600-char head is "completed-looking"; failure word is at char 800
        completed_head = (
            "[TOOL: dd_orchestrate]\n"
            "Entity: Modirum Gespi\n"
            "Status: COMPLETE\n"
            "Layer 1 (identity): VERIFIED via Brazilian registry.\n"
            "Layer 2 (network): 14 directors mapped.\n"
            "Layer 3 (verification): OFAC CLEAN, EU CLEAN, UK CLEAN.\n"
            "Layer 4 (compliance): Tax filings current.\n"
            "Layer 5 (commercial): 12 procurement contracts located.\n"
            "Layer 6 (synthesis): AMBER — incomplete people coverage.\n"
        ) * 5  # pad past 600 chars
        narrative_failure_quote = (
            "\n\nIn the news section, a 2024 article noted that the company "
            "experienced a FETCH FAILED on their main portal during the "
            "January incident — not a current state."
        )
        ctx = completed_head + narrative_failure_quote
        assert len(completed_head) > 600  # confirm test pre-condition
        assert _classify_tool_outcome(ctx) == "completed", (
            "narrative deep-body quote of failure word must NOT be "
            "treated as a tool failure"
        )


class TestRF350CapabilityADSMCase:
    """Capability test: re-construct the operator's observed failure
    shape (intent detected → tool ran → tool crashed → status header
    must reflect crash) end-to-end at the classifier boundary."""

    def test_adsm_extraction_crash_reports_attempted(self):
        """ARIA chat shape 2026-05-12 23:59: extract_url 'crashed',
        body reported failure. Status header MUST follow body, not
        the invocation flag."""
        # Reconstruct the context that _execute_tool would have returned
        # on a crash — per aria.py:5593-5602:
        context_from_crash = (
            "\n\n[TOOL: extract_url — FETCH/EXTRACTION FAILED]\n"
            "URL: https://adsm-sa.com/\n"
            "Error: extraction_ok=False after retries\n"
            "Duration: 12000ms\n"
            "[NO TOOL DATA — fall back to general knowledge with disclaimer.]\n"
        )
        outcome = _classify_tool_outcome(context_from_crash)
        assert outcome == "attempted", (
            f"R-F350 capability regression: extract_url crash must "
            f"flip status to 'attempted'; got {outcome!r}"
        )
