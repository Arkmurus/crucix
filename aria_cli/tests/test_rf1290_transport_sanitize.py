"""R-F1290 — transport-layer message sanitization is the last line of defense
against the DeepSeek 400 "Messages with role 'tool' must be a response to a
preceding message with 'tool_calls'".

Even if a corrupted history reaches llm.py (zombie timeout thread, resumed
session, future caller bug), the wire payload sent to the provider must be valid.
This test feeds the sanitizer the exact corrupt shapes and asserts the result
satisfies the provider contract.
"""
from __future__ import annotations

from aria_cli.llm import _sanitize_messages


def _valid(messages):
    """Replicate the provider's invariant: every tool message follows an
    assistant tool_calls block whose ids include its tool_call_id."""
    open_ids: set[str] = set()
    prev = None
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            open_ids = {tc.get("id") for tc in m["tool_calls"]}
        elif role == "tool":
            if prev not in ("assistant", "tool"):
                return False
            if m.get("tool_call_id") not in open_ids:
                return False
        else:
            open_ids = set()
        prev = role
    return True


def test_orphan_tool_message_is_dropped():
    corrupt = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "orphan", "content": "stale"},
        {"role": "user", "content": "again"},
    ]
    out = _sanitize_messages(corrupt)
    assert all(m.get("role") != "tool" for m in out)
    assert _valid(out)


def test_input_not_mutated():
    corrupt = [{"role": "tool", "tool_call_id": "x", "content": "y"}]
    before = list(corrupt)
    _sanitize_messages(corrupt)
    assert corrupt == before, "sanitizer must not mutate its input"


def test_valid_tool_sequence_preserved():
    good = [
        {"role": "user", "content": "list"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "function": {"name": "glob", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "f.py"},
    ]
    out = _sanitize_messages(good)
    assert out == good
    assert _valid(out)


def test_dangling_tool_call_gets_synthetic_response():
    dangling = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "z", "function": {"name": "run", "arguments": "{}"}}]},
    ]
    out = _sanitize_messages(dangling)
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "z" for m in out)
    assert _valid(out)
