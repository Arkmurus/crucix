"""R-F615 — DIALOGUE prompt branch (conversational mode block).

Pins:
  - build_response_mode_block(intent) emits the right block per bucket
  - DIALOGUE block tells LLM to drop BLUF, ≤200 words
  - REPORT block preserves existing BLUF format
  - COMMAND block keeps tool-dispatch terse
  - Constitution clauses still referenced in DIALOGUE (no honesty relax)
  - Both _aria_chat_impl and _aria_chat_stream_impl invoke the mode block
    per §13 stream-bypass discipline
"""
from __future__ import annotations

import pathlib

import pytest

from ._source_probe import repo_path


# ══════════════════════════════════════════════════════════════════
# PART A — mode block content
# ══════════════════════════════════════════════════════════════════

def test_rf615_dialogue_block_drops_bluf_and_caps_length():
    from aria_service.intel.dialogue_router import (
        DialogueIntent, build_response_mode_block,
    )
    block = build_response_mode_block(DialogueIntent.DIALOGUE)
    block_lc = block.lower()
    assert "[ARIA RESPONSE MODE: DIALOGUE]" in block
    assert "bluf" in block_lc, "DIALOGUE block must drop the BLUF banner"
    assert "200 words" in block_lc or "≤200 words" in block, (
        "DIALOGUE block must cap reply length"
    )
    # No section headers / emoji anchors
    assert "section header" in block_lc or "section heading" in block_lc


def test_rf615_dialogue_block_keeps_constitution_binding():
    """DIALOGUE doesn't relax honesty — clauses still bind."""
    from aria_service.intel.dialogue_router import (
        DialogueIntent, build_response_mode_block,
    )
    block = build_response_mode_block(DialogueIntent.DIALOGUE)
    assert "Constitution" in block or "constitution" in block
    # Must explicitly mention key clauses by anchor
    assert "Clause 14" in block or "fabrication" in block.lower()
    assert "R-F604" in block or "self-capability denial" in block.lower()
    assert "Clause 25" in block or "health/perf" in block.lower()


def test_rf615_dialogue_block_allows_one_question_at_end():
    """The DIALOGUE block must explicitly authorise one clarifying
    question — that's the whole point of the conversational style."""
    from aria_service.intel.dialogue_router import (
        DialogueIntent, build_response_mode_block,
    )
    block = build_response_mode_block(DialogueIntent.DIALOGUE)
    block_lc = block.lower()
    assert "one" in block_lc and "question" in block_lc


def test_rf615_report_block_preserves_bluf_format():
    from aria_service.intel.dialogue_router import (
        DialogueIntent, build_response_mode_block,
    )
    block = build_response_mode_block(DialogueIntent.REPORT)
    assert "[ARIA RESPONSE MODE: REPORT]" in block
    assert "BLUF" in block
    assert "section" in block.lower()


def test_rf615_command_block_keeps_terse():
    from aria_service.intel.dialogue_router import (
        DialogueIntent, build_response_mode_block,
    )
    block = build_response_mode_block(DialogueIntent.COMMAND)
    assert "[ARIA RESPONSE MODE: COMMAND]" in block
    assert "terse" in block.lower() or "tool" in block.lower()


def test_rf615_all_three_blocks_are_distinct():
    """Defensive — the three modes must produce different blocks."""
    from aria_service.intel.dialogue_router import (
        DialogueIntent, build_response_mode_block,
    )
    d = build_response_mode_block(DialogueIntent.DIALOGUE)
    r = build_response_mode_block(DialogueIntent.REPORT)
    c = build_response_mode_block(DialogueIntent.COMMAND)
    assert d != r
    assert r != c
    assert d != c


def test_rf615_defensive_fallback_returns_dialogue_block():
    """If somehow an unknown intent reaches the function, default to
    DIALOGUE (safest behavioural default)."""
    from aria_service.intel.dialogue_router import build_response_mode_block
    # Pass a garbage value — should still return a usable string
    result = build_response_mode_block("garbage_value")  # type: ignore[arg-type]
    assert "ARIA RESPONSE MODE" in result
    assert "DIALOGUE" in result


# ══════════════════════════════════════════════════════════════════
# PART B — wiring into both chat handlers (§13 stream-bypass)
# ══════════════════════════════════════════════════════════════════

def _aria_engine_src() -> str:
    return pathlib.Path(
        repo_path("aria_service/aria_engine.py")
    ).read_text(encoding="utf-8", errors="ignore")


def test_rf615_wired_into_aria_chat_impl():
    """The non-streaming chat impl must invoke dialogue_router + prepend
    the mode block to context."""
    src = _aria_engine_src()
    impl_idx = src.find("async def _aria_chat_impl")
    assert impl_idx > 0
    # Stream impl starts after — narrow the window
    stream_idx = src.find("async def _aria_chat_stream_impl", impl_idx)
    region = src[impl_idx:stream_idx] if stream_idx > 0 else src[impl_idx:impl_idx + 50000]
    assert "dialogue_router" in region, (
        "R-F615: _aria_chat_impl missing dialogue_router import/call"
    )
    assert "build_response_mode_block" in region, (
        "R-F615: _aria_chat_impl missing build_response_mode_block call"
    )
    # Must prepend the mode block to context
    assert "_mode_block" in region


def test_rf615_wired_into_aria_chat_stream_impl():
    """§13 stream-bypass rule: every guard / mode injection must mirror
    into the streaming path."""
    src = _aria_engine_src()
    stream_idx = src.find("async def _aria_chat_stream_impl")
    assert stream_idx > 0
    region = src[stream_idx:stream_idx + 30000]
    assert "dialogue_router" in region, (
        "R-F615: _aria_chat_stream_impl missing dialogue_router import/call "
        "— STREAM-BYPASS VIOLATION (§13)"
    )
    assert "build_response_mode_block" in region


def test_rf615_mode_block_comes_after_self_introspect_guard_in_both_impls():
    """Ordering matters: self_introspect block comes first (so injected
    data is visible), then mode block as the final prepend (so the
    mode directive is the FIRST line the LLM sees)."""
    src = _aria_engine_src()
    for impl_name in ("_aria_chat_impl", "_aria_chat_stream_impl"):
        impl_idx = src.find(f"async def {impl_name}")
        # Bound the region carefully
        next_def = src.find("\nasync def ", impl_idx + 10)
        region = (
            src[impl_idx:next_def]
            if next_def > 0
            else src[impl_idx:impl_idx + 30000]
        )
        introspect_idx = region.find("_introspect_block")
        mode_idx = region.find("_mode_block")
        assert introspect_idx > 0, (
            f"R-F615: {impl_name} missing self_introspect block"
        )
        assert mode_idx > 0, (
            f"R-F615: {impl_name} missing mode block"
        )
        assert introspect_idx < mode_idx, (
            f"R-F615 ordering bug in {impl_name}: mode block must come "
            f"AFTER self_introspect block so it lands as the FIRST line"
        )


def test_rf615_tool_block_detection_used_in_wiring():
    """The wiring must compute has_tool_block from the message body
    so a [TOOL:] in the message triggers COMMAND mode."""
    src = _aria_engine_src()
    # Both impls must check '[TOOL:' in message
    occurrences = src.count('"[TOOL:" in (message or "")')
    assert occurrences >= 2, (
        f"R-F615: expected ≥2 has_tool_block detections "
        f"(both _aria_chat_impl + stream); got {occurrences}"
    )


# ══════════════════════════════════════════════════════════════════
# PART C — exception handling (R-F615 must NEVER break the chat turn)
# ══════════════════════════════════════════════════════════════════

def test_rf615_dialogue_router_call_wrapped_in_try_except():
    """If dialogue_router import or call fails, the chat turn must
    still complete (per the defensive pattern of every other guard)."""
    src = _aria_engine_src()
    # Find the dialogue_router invocation in _aria_chat_impl
    impl_idx = src.find("async def _aria_chat_impl")
    stream_idx = src.find("async def _aria_chat_stream_impl", impl_idx)
    region = src[impl_idx:stream_idx]
    dr_idx = region.find("dialogue_router")
    assert dr_idx > 0
    # The 30 lines around dialogue_router must contain a try/except
    surrounding = region[max(0, dr_idx - 200):dr_idx + 500]
    assert "try:" in surrounding
    assert "except" in surrounding
