"""R-F4320 / C-268 - the sovereign CLI tool loop 400'd on EVERY round-trip.

Found while clearing the way for ARIA_LLM_PRIMARY_ALL. Measured live against the
served endpoint 2026-08-25, and neither failure is visible from reading the code:

    system + user + user                    -> HTTP 400 "After the optional system
                                               message, conversation roles must
                                               alternate user/assistant/..."
    tool_call_id "c1"        (len 2)        -> HTTP 400 "Tool call IDs should be
    tool_call_id "call_abc12345" (len 13)      alphanumeric strings with length 9!"
    tool_call_id "abc123456" (len 9)        -> 200

Mistral's chat template enforces both. The id rule is the one that bites: our
ids are "call_..." shaped, so the CLI could ISSUE a tool call and then 400 the
moment it fed the result back - a tool loop that can never complete a single
turn. She emits valid ids herself; it is the ids we ECHO BACK that are rejected.

WHY THE CLI ONLY. The server-side provider builds `[system?, user]` and cannot
violate alternation. The CLI builds long tool-loop histories, so this is its
problem alone - scoping it there is the narrow fix, not a platform-wide one.

MERGE, NEVER DROP. A consecutive same-role turn is merged, because dropping one
would silently discard something the user or the model actually said - the same
class as inventing a prompt (C-257) or truncating to fit (C-265).
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_cli import llm as cli_llm  # noqa: E402


# -- tool-call ids ---------------------------------------------------------

def test_a_long_id_is_rewritten_to_nine_alphanumeric() -> None:
    """THE CAPABILITY TEST. 'call_abc12345' is what we actually send."""
    out = cli_llm._mistral_tool_id("call_abc12345")
    assert len(out) == 9 and out.isalnum(), out


def test_a_short_id_is_rewritten_too() -> None:
    out = cli_llm._mistral_tool_id("c1")
    assert len(out) == 9 and out.isalnum(), out


def test_an_already_valid_id_is_left_alone() -> None:
    """Rewriting a valid id would churn ids for no reason."""
    assert cli_llm._mistral_tool_id("abc123456") == "abc123456"


def test_the_rewrite_is_deterministic() -> None:
    """The assistant entry and its tool result are rewritten independently; if
    the mapping were not stable they would stop matching and we would trade one
    400 for another."""
    a = cli_llm._mistral_tool_id("call_xyz99")
    b = cli_llm._mistral_tool_id("call_xyz99")
    assert a == b


def test_distinct_ids_stay_distinct() -> None:
    ids = {cli_llm._mistral_tool_id(f"call_{i:06d}") for i in range(500)}
    assert len(ids) == 500


def test_the_pairing_survives_the_rewrite() -> None:
    """The whole point: assistant.tool_calls[].id and tool.tool_call_id must
    still refer to each other after the repair."""
    msgs = [
        {"role": "user", "content": "read a.txt"},
        {"role": "assistant", "tool_calls": [
            {"id": "call_abc12345", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_abc12345", "content": "body"},
    ]
    out = cli_llm._mistral_contract(msgs)
    assert out[1]["tool_calls"][0]["id"] == out[2]["tool_call_id"]
    assert len(out[2]["tool_call_id"]) == 9


# -- alternation -----------------------------------------------------------

def test_consecutive_user_turns_are_merged_not_dropped() -> None:
    msgs = [{"role": "system", "content": "S"},
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"}]
    out = cli_llm._mistral_contract(msgs)
    roles = [m["role"] for m in out]
    assert roles == ["system", "user"], roles
    assert "first" in out[1]["content"] and "second" in out[1]["content"], (
        "a turn was DROPPED; every token the user sent must survive")


def test_consecutive_assistant_turns_are_merged() -> None:
    msgs = [{"role": "user", "content": "u"},
            {"role": "assistant", "content": "a1"},
            {"role": "assistant", "content": "a2"}]
    out = cli_llm._mistral_contract(msgs)
    assert [m["role"] for m in out] == ["user", "assistant"]
    assert "a1" in out[1]["content"] and "a2" in out[1]["content"]


def test_merging_assistants_keeps_every_tool_call() -> None:
    """A single assistant turn may legitimately request several tools; losing
    the later ones would silently drop work the model asked for."""
    mk = lambda i: {"id": f"call_{i}", "type": "function",
                    "function": {"name": "f", "arguments": "{}"}}
    msgs = [{"role": "user", "content": "u"},
            {"role": "assistant", "tool_calls": [mk(1)]},
            {"role": "assistant", "tool_calls": [mk(2)]}]
    out = cli_llm._mistral_contract(msgs)
    assert len(out) == 2
    assert len(out[1]["tool_calls"]) == 2


def test_a_valid_alternating_history_is_unchanged() -> None:
    """The repair must not churn a conversation that was already legal."""
    msgs = [{"role": "system", "content": "S"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"}]
    out = cli_llm._mistral_contract(msgs)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "user"]
    assert [m.get("content") for m in out] == ["S", "u1", "a1", "u2"]


def test_tool_messages_are_not_merged() -> None:
    """`tool` is how the template answers an assistant tool_calls turn; merging
    two results would corrupt the pairing."""
    msgs = [{"role": "user", "content": "u"},
            {"role": "assistant", "tool_calls": [
                {"id": "a", "type": "function",
                 "function": {"name": "f", "arguments": "{}"}},
                {"id": "b", "type": "function",
                 "function": {"name": "g", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "a", "content": "r1"},
            {"role": "tool", "tool_call_id": "b", "content": "r2"}]
    out = cli_llm._mistral_contract(msgs)
    assert [m["role"] for m in out].count("tool") == 2


def test_the_input_is_not_mutated() -> None:
    msgs = [{"role": "user", "content": "u"}, {"role": "user", "content": "v"}]
    before = json.dumps(msgs, sort_keys=True)
    cli_llm._mistral_contract(msgs)
    assert json.dumps(msgs, sort_keys=True) == before


# -- wiring: BOTH paths, and only for the provider that needs it -----------

def test_the_repair_is_applied_for_the_sovereign() -> None:
    msgs = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
    out = cli_llm._wire_messages(msgs, "aria-llm")
    assert len(out) == 1, "the Mistral repair did not run for aria-llm"


def test_deepseek_history_is_left_alone() -> None:
    """Merging DeepSeek's turns would lose the turn boundary for no reason - it
    accepts the unrepaired array."""
    msgs = [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]
    out = cli_llm._wire_messages(msgs, "deepseek")
    assert len(out) == 2


def test_both_transport_paths_use_the_same_wiring() -> None:
    """§13 stream-bypass: a guard on one path and not the other is this repo's
    repeat failure, and a tool loop that 400s only when streaming would be
    near-impossible to attribute."""
    src = (ROOT / "aria_cli/llm.py").read_text(encoding="utf-8")
    assert src.count('"messages": _wire_messages(messages, self.config.provider)') == 2, (
        "the blocking and streaming payloads must be built the same way")
    assert '"messages": _sanitize_messages(messages)' not in src, (
        "a raw sanitize-only payload survives on one path")


# -- LIVE proof lives OUTSIDE the suite ------------------------------------
#
# Every rule above was discovered by PROBING, not by reading: vLLM's Mistral
# tokenizer enforces them and nothing in our tree documents them. A unit test
# over our own helper proves we implement what we BELIEVE the contract to be;
# only a live probe proves the belief.
#
# It cannot live here. R-F3433 blocks live DNS for the whole suite with no
# escape hatch, and it is right to — a blocking getaddrinfo with no application
# timeout makes a stalled run look hung rather than failed. A test carrying an
# opt-in flag that then hits that block is a trap for the next reader.
#
# Run the proof deliberately:
#     python scripts/admin/probe_sovereign_contract.py


# -- stray system turns (found by the LIVE run, not by the unit tests) ------

def test_a_mid_conversation_system_turn_is_hoisted() -> None:
    """THE ONE THE UNIT TESTS MISSED.

    `agent.py:462` appends the code-RAG block as a SYSTEM message after the
    user's task. Mistral permits one system message, leading only, so the very
    first real CLI run against the sovereign 400'd on alternation even with the
    merge in place. Every test above passed at the time.
    """
    msgs = [{"role": "system", "content": "PROMPT"},
            {"role": "user", "content": "task"},
            {"role": "system", "content": "RAG BLOCK"}]
    out = cli_llm._mistral_contract(msgs)
    assert [m["role"] for m in out] == ["system", "user"], [m["role"] for m in out]
    assert "PROMPT" in out[0]["content"] and "RAG BLOCK" in out[0]["content"], (
        "the RAG block was DROPPED instead of hoisted")


def test_a_leading_system_turn_is_left_where_it_is() -> None:
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "u"}]
    out = cli_llm._mistral_contract(msgs)
    assert [m["role"] for m in out] == ["system", "user"]
    assert out[0]["content"] == "S"


def test_a_system_turn_that_is_not_first_is_promoted() -> None:
    """No leading system at all, one appearing later — it must move to the
    front rather than sit mid-conversation."""
    msgs = [{"role": "user", "content": "u"},
            {"role": "system", "content": "S"}]
    out = cli_llm._mistral_contract(msgs)
    assert out[0]["role"] == "system" and out[0]["content"] == "S"


def test_system_hoisting_preserves_order() -> None:
    msgs = [{"role": "system", "content": "one"},
            {"role": "user", "content": "u"},
            {"role": "system", "content": "two"},
            {"role": "assistant", "content": "a"},
            {"role": "system", "content": "three"}]
    out = cli_llm._mistral_contract(msgs)
    c = out[0]["content"]
    assert c.index("one") < c.index("two") < c.index("three")
    assert [m["role"] for m in out] == ["system", "user", "assistant"]


def test_the_full_agent_shape_is_legal() -> None:
    """The exact array the CLI builds: prompt, task, RAG block, tool loop."""
    msgs = [
        {"role": "system", "content": "PROMPT"},
        {"role": "user", "content": "read a.txt"},
        {"role": "system", "content": "RAG"},
        {"role": "assistant", "tool_calls": [
            {"id": "call_abc12345", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_abc12345", "content": "body"},
    ]
    out = cli_llm._mistral_contract(msgs)
    roles = [m["role"] for m in out]
    assert roles == ["system", "user", "assistant", "tool"], roles
    # alternation is legal: no two adjacent user/assistant turns
    for a, b in zip(roles, roles[1:]):
        assert not (a == b and a in ("user", "assistant")), roles
    assert out[2]["tool_calls"][0]["id"] == out[3]["tool_call_id"]
