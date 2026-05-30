"""R-F1120 — capability test for the dangling-tool_call wedge fix.

The bug: when the loop guard aborted mid-tool-loop it returned WITHOUT recording
tool responses for the remaining tool_calls, leaving the assistant message's
tool_calls dangling. The provider then rejects EVERY subsequent call with HTTP 400
("tool_calls must be followed by tool messages") — permanently wedging the session
(observed live 2026-05-30: 'insufficient tool messages following tool_calls').

These tests drive the REAL path: (a) the self-heal repair, and (b) a real
loop-guard abort via run_turn, asserting the history is left valid (no dangling).
"""
from __future__ import annotations

import types

from aria_cli.agent import Agent, AgentUI
from aria_cli.tools import ToolResult


class _Tb:
    """Minimal toolbox: a read-only list_dir + no bridge."""
    bridge_base = None

    def list_dir(self, **kwargs) -> ToolResult:
        return ToolResult("ok")


def _agent(llm):
    a = Agent(llm=llm, toolbox=_Tb(), system_prompt="sys", ui=AgentUI(), auto_approve=True)
    a.retry_backoff = 0
    return a


def _tc(tcid: str):
    return {"id": tcid, "type": "function",
            "function": {"name": "list_dir", "arguments": "{}"}}


def test_repair_inserts_for_dangling_tool_call():
    a = _agent(llm=None)
    # assistant asks for TWO tool calls but only ONE got a response — dangling.
    a.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [_tc("c1"), _tc("c2")]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    inserted = a._repair_dangling_tool_calls()
    assert inserted == 1
    # every tool_call now has a matching tool message
    responded = {m["tool_call_id"] for m in a.messages if m.get("role") == "tool"}
    assert responded == {"c1", "c2"}
    # idempotent: a second repair finds nothing
    assert a._repair_dangling_tool_calls() == 0


def test_repair_noop_on_clean_history():
    a = _agent(llm=None)
    a.messages = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "", "tool_calls": [_tc("c1")]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]
    assert a._repair_dangling_tool_calls() == 0


class _LoopLLM:
    """Always returns the SAME tool call → trips the loop guard."""
    def __init__(self):
        self.n = 0

    def chat(self, messages, tools=None):
        self.n += 1
        tc = _tc(f"call_{self.n}")
        return types.SimpleNamespace(
            content="", tool_calls=[tc],
            raw_message={"role": "assistant", "content": "", "tool_calls": [tc]})


def test_loop_guard_abort_leaves_history_valid():
    """A real loop-guard abort must NOT leave a dangling tool_call (the wedge)."""
    a = _agent(llm=_LoopLLM())
    result = a.run_turn("list the dir over and over")
    assert result.aborted  # the loop guard fired
    # THE KEY ASSERTION: the history has zero dangling tool_calls, so the next
    # LLM call would NOT 400. (repair finds nothing to fix.)
    assert a._repair_dangling_tool_calls() == 0
    # and concretely: every assistant tool_call id has a matching tool message
    responded = {m["tool_call_id"] for m in a.messages if m.get("role") == "tool"}
    for m in a.messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                assert tc["id"] in responded, f"dangling tool_call {tc['id']}"
