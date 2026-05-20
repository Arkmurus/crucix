"""R-F760 — tool_context now threaded into aria_chat_stream's apply_stream_honesty.

Audit on 2026-05-20 (P1) flagged that chat_stream_ep at line ~8019
hardcoded `tool_context=""` when calling
`stream_honesty.apply_stream_honesty(...)`. The comment said "not
threaded into stream today". This meant the ground_truth_guard
inside the honesty pass couldn't compare CONFIRMED-tagged claims
against the underlying extract — the exact 2026-04-18 CSG-Group
incident class.

The fix is one line: the `tool_context` variable is already in
scope inside the SSE generator (assigned at line ~7920 from
`await _execute_tool(intent, llm)`), and is already used to build
`message_for_llm` for the LLM call. R-F760 just passes the same
variable to apply_stream_honesty.

Source-level test enforces:
  1. apply_stream_honesty is no longer called with tool_context=""
  2. apply_stream_honesty IS called with `tool_context=tool_context or ""`
     (or equivalent expression — must reference the local variable)
  3. the misleading "not threaded into stream today" comment is gone
"""
from __future__ import annotations

from pathlib import Path


def _aria_source() -> str:
    p = Path(__file__).resolve().parents[1] / "routes" / "aria.py"
    return p.read_text(encoding="utf-8")


def test_rf760_no_hardcoded_empty_tool_context():
    src = _aria_source()
    # Must not have the pre-R-F760 hardcoded empty pass.
    assert 'tool_context="",  # not threaded into stream today' not in src, (
        "R-F760 regression: chat_stream_ep still hardcodes tool_context=\"\" "
        "with the 'not threaded into stream today' comment. The audit's "
        "P1 verification-parity finding is back."
    )


def test_rf760_tool_context_variable_is_passed():
    src = _aria_source()
    # Look for the apply_stream_honesty kwarg using the local var.
    # Accept either `tool_context=tool_context` or `tool_context=tool_context or ""`.
    assert 'tool_context=tool_context' in src, (
        "R-F760 regression: apply_stream_honesty no longer receives "
        "tool_context=tool_context. The local variable populated at "
        "line ~7920 must be threaded through."
    )


def test_rf760_r446_call_site_has_tool_context_kwarg():
    """The kwarg should be inside the same apply_stream_honesty call
    block (line ~8016-8025), not somewhere unrelated."""
    src = _aria_source()
    call_idx = src.find("_sh.apply_stream_honesty(")
    assert call_idx > 0, "R-F760: apply_stream_honesty call site not found"
    # Take a generous window after the call site (the R-F760 inline
    # comment is long, so 1800 chars is needed to reach the kwarg).
    window = src[call_idx:call_idx + 1800]
    assert "tool_context=tool_context" in window, (
        "R-F760 regression: tool_context=tool_context exists somewhere "
        "in aria.py but NOT inside the apply_stream_honesty call. "
        "Threading regressed."
    )


def test_rf760_kept_chat_path_parity_with_non_stream():
    """For symmetry — non-stream chat_ep already passes tool_context to
    its verification gate / honesty pass. The audit asked us to MATCH
    that, not exceed it. Just confirm the non-stream path has been
    untouched by this commit."""
    src = _aria_source()
    # Non-stream calls verifier with `tool_context=tool_context or ""`.
    assert 'tool_context=tool_context or ""' in src, (
        "R-F760 regression: non-stream chat path no longer threads "
        "tool_context — would break the parity this commit was meant "
        "to restore in the OPPOSITE direction (stream → non-stream)."
    )
