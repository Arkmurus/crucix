"""R-F4368 + R-F4369 (C-314) — the sovereign could never take a SECOND step,
so she could read code but never change it.

MEASURED LIVE 2026-08-26, operator's own config (``aria-llm-v0.4-dpo`` on the
RunPod vLLM). Through the real CLI, four coding tasks::

    aria-llm    1/4    every failure stopped at EXACTLY one tool call
    deepseek    4/4    same CLI, same tasks, same prompts

The one that passed was the single-step ``run``. Two failures ended with her
telling the operator "I cannot execute or modify files. You must manually edit
the calc.py file" — while holding ``edit_file`` and ``run`` in her advertised
tool set.

TWO defects compound, and each alone leaves her stuck:

1. R-F4369 — SHE IS NEVER NUDGED. R-F4337's narration retry is gated on
   ``messages[-2]["role"] == "user"`` (R-F4341, which correctly found that
   appending a user message after a ``tool`` message makes Mistral 400 with
   "conversation roles must alternate"). That gate switches the retry OFF for
   precisely the mid-task case — after a tool result — which is where all
   multi-step coding lives. Measured, second turn, identical history:

       no steer   -> prose: "replace the line ... After the fix, run ..."
       steer      -> a real edit_file call with the right arguments

   The steer therefore goes on the LAST TOOL MESSAGE, not a new user message:
   ``user -> assistant(tool_calls) -> tool(result + steer)`` keeps the roles
   Mistral-legal, so it recovers the case R-F4341 had to give up on rather than
   re-breaking it.

2. R-F4368 — AND THEN THE CALL IS THROWN AWAY. Once steered she does emit a
   tool call, but on the second turn it never reaches the ``tool_calls``
   channel. Measured 5/5 as ``[TOOL_CALLS]`` text, in a MALFORMED array that
   vLLM's Mistral parser is right to refuse — ``name`` and ``arguments`` split
   across sibling objects, or no ``name`` at all::

       [{"arguments": {...}}, {"name": "edit_file", "id": "104be20cf"}]
       [{"arguments": {"command": "python hello.py"}}]

   R-F4329 recovers the canonical ``{"name":..., "arguments":{...}}`` array and
   is deliberately strict; these two shapes fall outside it, so every steered
   second step was discarded.

The pairing repair is NOT prose parsing and NOT a guess: in the split shape the
name is present, just in the next object. Where no name is emitted at all it is
DERIVED from the offered schemas and only when exactly one tool can accept
those keys — ambiguity refuses, because a fabricated ``run`` EXECUTES.
"""
from __future__ import annotations

import json

import pytest

from aria_cli.agent import looks_like_stalled_after_tool
from aria_cli.llm import recover_tool_calls_from_content

TOOLS = [
    {"type": "function", "function": {"name": "read_file", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"}},
        "required": ["path"]}}},
    {"type": "function", "function": {"name": "edit_file", "parameters": {
        "type": "object", "properties": {
            "path": {"type": "string"}, "old_string": {"type": "string"},
            "new_string": {"type": "string"}},
        "required": ["path", "old_string", "new_string"]}}},
    {"type": "function", "function": {"name": "run", "parameters": {
        "type": "object", "properties": {"command": {"type": "string"},
                                         "timeout": {"type": "integer"}},
        "required": ["command"]}}},
]


def _names(calls):
    return [c["function"]["name"] for c in calls]


def _args(call):
    return json.loads(call["function"]["arguments"])


# ── R-F4368: the split shape (name in a sibling object) ─────────────────────

def test_split_name_and_arguments_are_paired(monkeypatch):
    """THE MEASURED SHAPE. The name is present — in the next object."""
    content = ('[TOOL_CALLS] [{"arguments": {"path": "calc.py", '
               '"old_string": "return a - b", "new_string": "return a + b"}}, '
               '{"name": "edit_file", "id": "104be20cf"}]')
    calls, _ = recover_tool_calls_from_content(content, TOOLS, "aria-llm")

    assert _names(calls) == ["edit_file"]
    assert _args(calls[0]) == {"path": "calc.py", "old_string": "return a - b",
                               "new_string": "return a + b"}


def test_split_shape_naming_an_unoffered_tool_is_refused():
    """The pairing may not widen what R-F4329 allows: an unoffered name is
    still an unoffered name, whichever object carries it."""
    content = ('[TOOL_CALLS] [{"arguments": {"path": "x"}}, '
               '{"name": "delete_everything", "id": "a"}]')
    calls, out = recover_tool_calls_from_content(content, TOOLS, "aria-llm")

    assert calls == []
    assert out == content


# ── R-F4368: the nameless shape (derive, or refuse) ─────────────────────────

def test_nameless_arguments_derive_the_only_tool_that_fits():
    """``command`` is required by exactly one offered tool, so the name is
    DERIVED, not guessed — there is no second candidate to be wrong about."""
    content = '[TOOL_CALLS] [{"arguments": {"command": "python hello.py"}}]'
    calls, _ = recover_tool_calls_from_content(content, TOOLS, "aria-llm")

    assert _names(calls) == ["run"]
    assert _args(calls[0]) == {"command": "python hello.py"}


def test_nameless_arguments_are_refused_when_two_tools_fit():
    """SAFETY. ``{"path": ...}`` fits read_file, and would also fit any other
    single-path tool. With more than one candidate there is nothing to derive,
    and picking one would be invention."""
    ambiguous = TOOLS + [{"type": "function", "function": {
        "name": "delete_file", "parameters": {
            "type": "object", "properties": {"path": {"type": "string"}},
            "required": ["path"]}}}]
    content = '[TOOL_CALLS] [{"arguments": {"path": "calc.py"}}]'
    calls, out = recover_tool_calls_from_content(content, ambiguous, "aria-llm")

    assert calls == [], "an ambiguous nameless call was executed"
    assert out == content


def test_nameless_arguments_matching_no_tool_are_refused():
    """Keys no offered tool accepts cannot name a tool. Refuse."""
    content = '[TOOL_CALLS] [{"arguments": {"wormhole": 3}}]'
    calls, out = recover_tool_calls_from_content(content, TOOLS, "aria-llm")

    assert calls == []
    assert out == content


def test_nameless_arguments_missing_a_required_field_are_refused():
    """``edit_file`` needs three fields. A partial object is not that call —
    executing it would edit with a field the model never supplied."""
    content = '[TOOL_CALLS] [{"arguments": {"old_string": "a", "new_string": "b"}}]'
    calls, out = recover_tool_calls_from_content(content, TOOLS, "aria-llm")

    assert calls == []
    assert out == content


# ── R-F4368: nothing R-F4329 already guarantees may regress ─────────────────

def test_canonical_array_still_recovers():
    """R-F4329's shape is untouched."""
    content = ('[{"name": "run", "arguments": {"command": "git status"}}, '
               '{"name": "read_file", "arguments": {"path": "a.py"}}]')
    calls, _ = recover_tool_calls_from_content(content, TOOLS, "aria-llm")

    assert _names(calls) == ["run", "read_file"]


def test_prose_that_merely_quotes_a_call_is_left_alone():
    """R-F4329's central prohibition: this must never become a prose parser."""
    content = 'I could run `git status` for you, or edit_file on calc.py. Shall I?'
    calls, out = recover_tool_calls_from_content(content, TOOLS, "aria-llm")

    assert calls == []
    assert out == content


def test_recovery_still_requires_tools_to_have_been_offered():
    content = '[TOOL_CALLS] [{"arguments": {"command": "rm -rf /"}}]'
    calls, out = recover_tool_calls_from_content(content, [], "aria-llm")

    assert calls == []
    assert out == content


# ── R-F4369: the trigger that decides whether to steer ──────────────────────

OFFERED = ["read_file", "list_dir", "grep", "run", "edit_file"]


def test_a_capability_denial_is_a_stall():
    """THE OPERATOR'S OWN FAILURE, verbatim from the live session."""
    body = ("The current implementation of `add` subtracts `b` from `a`. "
            "If you need help with the actual code edit, I cannot execute or "
            "modify files. You must manually edit the `calc.py` file.")
    assert looks_like_stalled_after_tool(body, OFFERED)


def test_a_described_action_is_a_stall():
    """She wrote the plan instead of executing it — R-F4337's shape, reached
    through a tool result rather than a user turn."""
    body = ("Replace `return a - b` with `return a + b`.\n\n```python\n"
            "def add(a, b):\n    return a + b\n```\nThen run it.")
    assert looks_like_stalled_after_tool(body, OFFERED)


def test_a_finished_answer_is_not_a_stall():
    """THE GUARD THAT MATTERS. Measured: steering a turn that had already
    finished made her invent an edit to a file nobody mentioned. A completed
    report must end the turn."""
    assert not looks_like_stalled_after_tool(
        "Git version 2.55.0.windows.3 is installed.", OFFERED)
    assert not looks_like_stalled_after_tool(
        "The file has 42 lines.", OFFERED)
    assert not looks_like_stalled_after_tool(
        "Fixed and verified — calc.add(2, 3) now returns 5.", OFFERED)


def test_empty_content_is_not_a_stall():
    assert not looks_like_stalled_after_tool("", OFFERED)
    assert not looks_like_stalled_after_tool("   ", OFFERED)


def test_a_denial_about_something_else_is_not_a_stall():
    """"I cannot" must be about ACTING, not about knowing. Steering someone who
    said they lack information just makes them guess."""
    assert not looks_like_stalled_after_tool(
        "I cannot tell which branch you meant — there are three.", OFFERED)


# ── R-F4369: the capability test — the loop must actually steer (§3c) ───────

class _FakeLLM:
    """Replays scripted turns. Records the messages it was handed each call."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.seen = []
        self.config = type("C", (), {"provider": "aria-llm"})()
        self.total_output_tokens = 0

    @property
    def supports_tools(self):
        return True

    def chat(self, messages, tools=None):
        self.seen.append([dict(m) for m in messages])
        from aria_cli.llm import LLMResponse
        content, calls = self._turns.pop(0) if self._turns else ("done", [])
        raw = {"role": "assistant", "content": content}
        if calls:
            raw["tool_calls"] = calls
        return LLMResponse(content=content, tool_calls=calls, raw_message=raw)


def _agent(turns, tmp_path):
    from aria_cli.agent import Agent, AgentUI
    from aria_cli.safety import WriteGuard
    from aria_cli.tools import Toolbox
    llm = _FakeLLM(turns)
    toolbox = Toolbox(root=tmp_path, guard=WriteGuard(self_mode=False))
    ag = Agent(llm=llm, toolbox=toolbox, system_prompt="sys",
               ui=AgentUI(), auto_approve=True)
    ag.retry_backoff = 0
    return ag, llm


READ_CALL = [{"id": "104be20cf", "type": "function",
              "function": {"name": "read_file", "arguments": '{"path": "calc.py"}'}}]
EDIT_CALL = [{"id": "204be20cf", "type": "function",
              "function": {"name": "edit_file", "arguments": json.dumps(
                  {"path": "calc.py", "old_string": "a - b", "new_string": "a + b"})}}]
DENIAL = ("I cannot execute or modify files. You must manually edit the "
          "`calc.py` file.")


def test_the_loop_steers_a_stall_and_the_edit_lands(tmp_path):
    """THE OPERATOR'S FAILURE, end to end: read -> stall -> steer -> edit.
    Before this fix the turn ended at the stall with `files: 0 changed`."""
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    ag, llm = _agent([("", READ_CALL), (DENIAL, []), ("Fixed.", EDIT_CALL)],
                     tmp_path)
    ag.run_turn("Fix the subtraction bug in calc.py.")

    assert "a + b" in (tmp_path / "calc.py").read_text(), \
        "the edit never landed — the stall was not steered"


def test_the_steer_rides_on_the_tool_message_not_a_new_user_turn(tmp_path):
    """R-F4341's constraint. `tool -> user` is an HTTP 400 on Mistral, so the
    steer must be appended to the tool result and the roles must still
    alternate."""
    from aria_cli.agent import TOOL_RESULT_STEER
    (tmp_path / "calc.py").write_text("x = 1\n")
    ag, llm = _agent([("", READ_CALL), (DENIAL, []), ("Fixed.", EDIT_CALL)],
                     tmp_path)
    ag.run_turn("Fix calc.py.")

    steered = llm.seen[2]           # the messages sent AFTER the steer
    assert steered[-1]["role"] == "tool", "the steer created a non-tool tail"
    assert TOOL_RESULT_STEER in steered[-1]["content"]
    assert not any(m["role"] == "user" for m in steered[2:]), \
        "a user message was appended after a tool message (Mistral 400)"


def test_the_stall_text_leaves_the_history(tmp_path):
    """Same reason as R-F4337: at temperature 0 she copies her own last answer,
    so leaving the refusal in context asks her to repeat it."""
    (tmp_path / "calc.py").write_text("x = 1\n")
    ag, llm = _agent([("", READ_CALL), (DENIAL, []), ("Fixed.", EDIT_CALL)],
                     tmp_path)
    ag.run_turn("Fix calc.py.")

    assert not any(DENIAL in (m.get("content") or "") for m in llm.seen[2]), \
        "the refusal was left in context"


def test_a_tool_message_is_steered_at_most_once(tmp_path):
    """Bounded by POSITION, not a counter: a steer that does not help must not
    loop, while a genuinely new step can still be nudged."""
    (tmp_path / "calc.py").write_text("x = 1\n")
    ag, llm = _agent([("", READ_CALL), (DENIAL, []), (DENIAL, []),
                      ("giving up", [])], tmp_path)
    result = ag.run_turn("Fix calc.py.")

    assert len(llm.seen) == 3, f"steered more than once ({len(llm.seen)} calls)"
    assert result.final_text == DENIAL


def test_a_finished_report_is_not_steered(tmp_path):
    """The guard that keeps this from causing harm: measured live, steering a
    finished turn made her invent an edit to a file nobody mentioned."""
    (tmp_path / "calc.py").write_text("x = 1\n")
    ag, llm = _agent([("", READ_CALL), ("calc.py sets x to 1.", [])], tmp_path)
    result = ag.run_turn("What does calc.py do?")

    assert len(llm.seen) == 2, "a finished answer was needlessly steered"
    assert result.final_text == "calc.py sets x to 1."


# ── R-F4368: two holes a mutation run found in the tests above ──────────────

def test_a_count_mismatch_between_names_and_arguments_is_refused():
    """SAFETY (mutation-found). Two argument objects and one name is not a
    pairing — zipping them would attach that name to the FIRST call and drop
    the second, i.e. execute a call under a name the model gave something
    else. With the counts unequal there is no correspondence to read off."""
    content = ('[TOOL_CALLS] [{"arguments": {"command": "git status"}}, '
               '{"arguments": {"path": "calc.py"}}, '
               '{"name": "run", "id": "104be20cf"}]')
    calls, out = recover_tool_calls_from_content(content, TOOLS, "aria-llm")

    assert calls == [], "mismatched name/argument counts were paired anyway"
    assert out == content


def test_nameless_arguments_with_an_undeclared_key_are_refused():
    """SAFETY (mutation-found). ``run`` declares command+timeout only. An
    object carrying an extra key is not a call to it, and deriving one anyway
    would EXECUTE ``ls`` while silently discarding the field the model thought
    it was constraining the call with."""
    content = '[TOOL_CALLS] [{"arguments": {"command": "ls", "sudo": true}}]'
    calls, out = recover_tool_calls_from_content(content, TOOLS, "aria-llm")

    assert calls == [], "a call with an undeclared argument was derived"
    assert out == content
