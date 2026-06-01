"""R-F1283 — Capability test: the agent strips ORPHAN tool messages so the
provider doesn't 400.

Reproduces the live error the operator hit in the `aria` CLI:
    HTTP 400: Messages with role 'tool' must be a response to a preceding
    message with 'tool_calls'

Cause: a `tool` message existed in history without a preceding assistant
`tool_calls` block (mid-turn timeout/abort, compaction, or a streamed turn that
lost its tool_calls). The repair pass appended every message verbatim, so the
orphan reached DeepSeek and wedged every subsequent call. Fix: drop orphans,
while still inserting synthetic responses for dangling tool_calls (R-F1120).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from aria_cli.agent import Agent


def _bare_agent(messages):
    """Build an Agent without running __init__ (the repair method only needs
    self.messages); avoids constructing llm/toolbox/ui."""
    a = Agent.__new__(Agent)
    a.messages = messages
    return a


def _assert_valid_tool_ordering(messages):
    """Every tool message must follow (contiguously) an assistant tool_calls
    block whose ids include its tool_call_id — the provider's invariant."""
    open_ids: set[str] = set()
    prev_role = None
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            open_ids = {tc.get("id") for tc in m["tool_calls"]}
        elif role == "tool":
            assert prev_role in ("assistant", "tool"), (
                f"orphan tool message after {prev_role}: {m}")
            assert m.get("tool_call_id") in open_ids, (
                f"tool message id not in open tool_calls: {m}")
        else:
            open_ids = set()
        prev_role = role


def test_orphan_tool_message_is_dropped():
    """The exact reported bug: a tool message with no preceding tool_calls."""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "tool", "tool_call_id": "orphan1", "content": "stale result"},
        {"role": "user", "content": "are you there?"},
    ]
    a = _bare_agent(msgs)
    repairs = a._repair_dangling_tool_calls()
    assert repairs >= 1
    assert all(m.get("role") != "tool" for m in a.messages), a.messages
    _assert_valid_tool_ordering(a.messages)


def test_valid_tool_sequence_is_preserved():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "list files"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "function": {"name": "glob", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "f1.py"},
        {"role": "assistant", "content": "done"},
    ]
    a = _bare_agent(msgs)
    repairs = a._repair_dangling_tool_calls()
    assert repairs == 0, a.messages
    _assert_valid_tool_ordering(a.messages)


def test_dangling_tool_call_still_gets_synthetic_response():
    """R-F1120 direction must still hold: a tool_call with no response gets one."""
    msgs = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "x", "function": {"name": "run", "arguments": "{}"}}]},
        # no tool message for id "x"
    ]
    a = _bare_agent(msgs)
    repairs = a._repair_dangling_tool_calls()
    assert repairs >= 1
    tool_msgs = [m for m in a.messages if m.get("role") == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "x"
    _assert_valid_tool_ordering(a.messages)


def test_orphan_plus_valid_mixed():
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "tool", "tool_call_id": "ghost", "content": "leftover"},  # orphan
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "b", "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "b", "content": "ok"},  # valid
    ]
    a = _bare_agent(msgs)
    a._repair_dangling_tool_calls()
    _assert_valid_tool_ordering(a.messages)
    # the valid pair survived
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "b" for m in a.messages)
    # the orphan is gone
    assert not any(m.get("tool_call_id") == "ghost" for m in a.messages)
