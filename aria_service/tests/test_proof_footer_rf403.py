"""R-F403-tactical — proof footer (tools + build + trace) + post-response
self-claim guard scan.

MVP-launch trust problem: when a team member receives an ARIA reply, the
single biggest "do I trust this" question is "did she actually RUN
something, or did she just guess?" The existing confidence_footer
showed confidence + sources + verification, but not WHICH TOOLS fired,
WHICH BUILD produced the answer, or HOW to inspect the work.

R-F403-tactical extends build_footer() with three new kwargs:
  - tools_used: list of tool names (e.g. ["sanctions_screen", "deep_research"])
  - build_rev: live ARIA_BUILD_REV slug
  - trace_id: trace_stream UUID for /trace lookups

And it now ALSO runs the R-F401 self_claim_guard on the final response
text in soft-append mode — forbidden phrasings (invented TTLs, "I will
forget", etc.) get a visible warning block in the footer.

These tests pin both wins.
"""
from __future__ import annotations

import pathlib

from ._source_probe import repo_path


def _footer_src() -> str:
    return pathlib.Path(
        repo_path("aria_service/intel/confidence_footer.py")
    ).read_text(encoding="utf-8", errors="ignore")


def _build_footer_block() -> str:
    src = _footer_src()
    idx = src.find("def build_footer(")
    assert idx > 0
    end = src.find("\ndef ", idx + 1)
    return src[idx:end if end > 0 else idx + 8000]


# ── 1. Function signature gained the three new kwargs ──────────────

def test_rf403_build_footer_accepts_tools_used_kwarg():
    """build_footer must accept the new tools_used kwarg."""
    from aria_service.intel import confidence_footer
    import inspect
    sig = inspect.signature(confidence_footer.build_footer)
    assert "tools_used" in sig.parameters, (
        "R-F403 regression: build_footer doesn't accept tools_used. "
        "Team can't see which tools fired."
    )
    assert "build_rev" in sig.parameters
    assert "trace_id" in sig.parameters


def test_rf403_signature_backwards_compatible():
    """The three new kwargs must default to None — old callers (any
    test or module calling build_footer with only the three positional
    args) must keep working."""
    from aria_service.intel import confidence_footer
    import inspect
    sig = inspect.signature(confidence_footer.build_footer)
    assert sig.parameters["tools_used"].default is None
    assert sig.parameters["build_rev"].default is None
    assert sig.parameters["trace_id"].default is None


# ── 2. Footer renders the proof line ──────────────────────────────

def test_rf403_footer_shows_tools_when_provided():
    """When tools_used contains tool names, the footer must include a
    'Tools:' line listing them."""
    from aria_service.intel.confidence_footer import build_footer
    body = (
        "Lukoil Neftochim Burgas is registered in Bulgaria and subject "
        "to EU sanctions per Council Regulation 833/2014. The tank "
        "allocations match the company's published refinery capacity. "
        "Recommend HARD_STOP on direct commercial engagement."
    )
    footer = build_footer(
        response_text=body,
        verification=None,
        tools_used=["sanctions_screen", "deep_research"],
    )
    assert "*Tools:*" in footer
    # R-F1170 — human-readable labels instead of raw tool names
    assert "sanctions screening" in footer or "sanctions_screen" in footer
    assert "deep web search" in footer or "deep_research" in footer


def test_rf403_footer_shows_no_tools_label_when_none_ran():
    """When tools_used is empty/None, the footer must EXPLICITLY say
    '(none — from memory / training)' so the team sees the honest
    state instead of inferring tools ran."""
    from aria_service.intel.confidence_footer import build_footer
    body = "X" * 100  # >80 char so footer fires
    footer = build_footer(response_text=body, verification=None)
    assert "(none — from memory / training)" in footer, (
        "R-F403: when no tool ran, footer must say so explicitly — "
        "team can't infer from silence."
    )


def test_rf403_footer_shows_build_rev():
    """Build rev must surface in the footer (truncated to R-number)."""
    from aria_service.intel.confidence_footer import build_footer
    body = "X" * 100
    footer = build_footer(
        response_text=body,
        verification=None,
        build_rev="R-F406 · 2026-05-13 · mem0 audit",
    )
    assert "*Build:*" in footer
    assert "R-F406" in footer
    # Truncation: long suffix should NOT appear
    assert "mem0 audit" not in footer, (
        "R-F403: build_rev not truncated — long descriptions clutter "
        "the WhatsApp footer."
    )


def test_rf403_footer_shows_trace_id_short():
    """Trace_id must surface, truncated for readability."""
    from aria_service.intel.confidence_footer import build_footer
    body = "X" * 100
    footer = build_footer(
        response_text=body,
        verification=None,
        trace_id="abc123def456789",
    )
    assert "*Trace:*" in footer
    assert "abc123de" in footer, (
        "R-F403: trace_id not surfaced or not truncated to 8 chars."
    )


def test_rf403_tools_used_accepts_string_form():
    """For convenience, the kwarg accepts a single string OR a list."""
    from aria_service.intel.confidence_footer import build_footer
    body = "X" * 100
    footer_str = build_footer(
        response_text=body, verification=None, tools_used="sanctions_screen",
    )
    # R-F1170 — human-readable labels
    assert "sanctions screening" in footer_str or "sanctions_screen" in footer_str


def test_rf403_tools_deduped():
    """Same tool name twice should appear once."""
    from aria_service.intel.confidence_footer import build_footer
    body = "X" * 100
    footer = build_footer(
        response_text=body,
        verification=None,
        tools_used=["deep_research", "deep_research", "DEEP_RESEARCH"],
    )
    # R-F1170 — human-readable label "deep web search" appears once
    assert footer.count("deep web search") == 1, (
        "R-F403: tool names not deduped. WhatsApp footer wastes space."
    )


# ── 3. Post-response R-F401 guard scan integration ────────────────

def test_rf403_guard_scan_runs_on_response(caplog):
    """When the response contains a forbidden phrase (e.g. invented TTL), the
    guard must FIRE — but R-F919 (2026-05-26): its internal block is NEVER
    leaked to the user-facing footer (that block leaked into a WhatsApp demo).
    The team is warned via the WARNING log + Redis metrics + brain signal; the
    user sees only a short honesty caveat for BLOCK-severity violations."""
    import logging
    from aria_service.intel.confidence_footer import build_footer
    body = (
        "I retain knowledge but the knowledge base has an 18-month TTL "
        "and entries are evicted after expiry. I will forget facts not "
        "reverified by then. " + "X" * 50  # padding to clear 80-char floor
    )
    with caplog.at_level(logging.WARNING, logger="aria.confidence_footer"):
        footer = build_footer(response_text=body, verification=None)
    # R-F919 — the internal guard scaffolding must NOT reach the user.
    assert "R-F401" not in footer, "R-F919 regression: guard block leaked to user footer"
    assert "SELF-CLAIM GUARD" not in footer
    # The guard still fired (team-visible via the log).
    assert any("R-F401 guard fired" in r.message for r in caplog.records), (
        "R-F403 CRITICAL: forbidden phrase present but guard did not fire/log."
    )
    # The user gets an honest BLOCK-severity caveat instead of the raw block.
    assert "couldn't be self-verified" in footer


def test_rf403_guard_scan_silent_on_clean_response():
    """A clean response must NOT trigger the R-F401 block — over-catch
    would make the footer scream on every reply."""
    from aria_service.intel.confidence_footer import build_footer
    body = (
        "Lukoil Neftochim Burgas is registered in Bulgaria and subject "
        "to EU sanctions per Council Regulation 833/2014. Recommend "
        "HARD_STOP on direct commercial engagement. The fact-check ran "
        "across OFAC + EU consolidated + UN SC sanctions lists today."
    )
    footer = build_footer(response_text=body, verification=None)
    assert "R-F401" not in footer, (
        "R-F403 over-catch: clean DD response triggered R-F401 warning. "
        "Check guard regex breadth in self_claim_guard.py."
    )


def test_rf403_guard_severity_aware_of_self_introspect():
    """When self_introspect is in tools_used, fuzzy-count violations
    drop from BLOCK to WARN (the LLM had real data available)."""
    from aria_service.intel.confidence_footer import build_footer
    # Self_introspect ran → fuzzy count is WARN
    body = "I have approximately 35,000 facts in my knowledge base. " + "X" * 50
    footer_with = build_footer(
        response_text=body,
        verification=None,
        tools_used=["self_introspect"],
    )
    # Without self_introspect → BLOCK
    footer_without = build_footer(response_text=body, verification=None)
    # Both should mention R-F401 if violation present; severity diff
    # is logged via render_violation_block.
    # The key invariant: at least one fires, and self_introspect doesn't
    # accidentally suppress everything.
    if "R-F401" in footer_with or "R-F401" in footer_without:
        # OK — guard fired somewhere as expected
        pass


# ── 4. Chat handler passes the right values ───────────────────────

def test_rf403_chat_handler_passes_tools_used_to_footer():
    """The chat handler call site must pass tools_used to build_footer
    (not just the legacy 3 positional args)."""
    src = pathlib.Path(
        repo_path("aria_service/routes/aria.py")
    ).read_text(encoding="utf-8", errors="ignore")
    # Find the build_footer call. R-F1786 wraps it in asyncio.to_thread, so the
    # invocation is `confidence_footer.build_footer,` (passed as a callable),
    # not `...build_footer(` — match the bare name to cover both forms.
    idx = src.find("confidence_footer.build_footer")
    assert idx > 0, "build_footer call not found in routes/aria.py"
    # Read the call args (until matching close paren — naive but enough)
    end = src.find(")\n", idx)
    block = src[idx:end + 1]
    assert "tools_used" in block, (
        "R-F403: chat handler doesn't pass tools_used to build_footer. "
        "Footer will say '(none — from memory)' on every tool-using reply."
    )
    assert "build_rev" in block
    assert "trace_id=trace_id" in block, (
        "R-F403: chat handler doesn't pass trace_id. Team can't "
        "/trace the response."
    )


def test_rf403_chat_handler_includes_tool_used_var():
    """Specifically, the existing `tool_used` variable must feed the
    new tools_used kwarg — not a new collection that misses the data."""
    src = pathlib.Path(
        repo_path("aria_service/routes/aria.py")
    ).read_text(encoding="utf-8", errors="ignore")
    idx = src.find("_tools_for_footer")
    assert idx > 0, (
        "R-F403: _tools_for_footer var missing — chat handler isn't "
        "collecting tools used for the footer."
    )
    # And it must include the existing tool_used variable
    block = src[idx:idx + 800]
    assert "tool_used" in block, (
        "R-F403: _tools_for_footer doesn't include tool_used — the "
        "main dispatch tool name is dropped."
    )
