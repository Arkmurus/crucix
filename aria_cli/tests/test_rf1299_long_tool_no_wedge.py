"""R-F1299 — capability test: a tool that runs longer than the per-call LLM
watchdog must NOT abort the turn or corrupt message history.

Reproduces the live wedge the operator hit: ARIA ran `deploy` (a 600s tool); the
old run_turn watchdog (60s, whole-turn) abandoned the still-running inner thread
and popped a message, while the orphan thread kept appending — leaving a dangling
tool_call that made DeepSeek 400 on every subsequent turn
("tool_calls must be followed by tool messages").

After the fix, the watchdog covers only the LLM network call; tool execution runs
synchronously and completes. We drive a real Toolbox with a fake LLM that asks for
a slow `run`, with the per-call watchdog set far below the tool's duration. The
turn must still finish cleanly with valid history.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

from aria_cli.agent import Agent
from aria_cli.llm import LLMResponse
from aria_cli.safety import WriteGuard
from aria_cli.tools import Toolbox


class _FakeLLM:
    """Returns scripted responses instantly (no network). No chat_stream attr,
    so the agent uses the non-streaming path."""

    def __init__(self, script: list[LLMResponse]) -> None:
        self._script = script
        self._i = 0

    def chat(self, messages, tools=None) -> LLMResponse:
        r = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return r


def _slow_run_call() -> LLMResponse:
    # A command that sleeps ~1s — longer than the 0.2s per-call watchdog below.
    cmd = ("Start-Sleep -Milliseconds 1000; Write-Output done"
           if sys.platform == "win32" else "sleep 1; echo done")
    return LLMResponse(
        content="",
        tool_calls=[{
            "id": "call_slow_1",
            "type": "function",
            "function": {"name": "run", "arguments": json.dumps({"command": cmd, "timeout": 30})},
        }],
        raw_message={
            "role": "assistant", "content": "",
            "tool_calls": [{
                "id": "call_slow_1", "type": "function",
                "function": {"name": "run", "arguments": json.dumps({"command": cmd, "timeout": 30})},
            }],
        },
    )


def _final_answer() -> LLMResponse:
    return LLMResponse(content="all done", tool_calls=[],
                       raw_message={"role": "assistant", "content": "all done"})


class _SilentUI:
    def assistant(self, text=""): ...
    def tool_call(self, name="", args=None): ...
    def tool_result(self, name="", result=None): ...
    def info(self, text=""): ...
    def thinking_start(self, label="thinking"): ...
    def thinking_stop(self): ...
    def tool_output(self, line=""): ...
    def stream_delta(self, text=""): ...
    def stream_end(self): ...
    def set_step_context(self, current=0, total=0): ...
    def progress_bar(self, current=0, total=0, label=""): ...
    def progress_end(self): ...
    def approve(self, name="", args=None): return True


def test_long_tool_completes_without_corrupting_history() -> None:
    """A 1s tool with a 0.2s per-call watchdog: the turn finishes (not aborted)
    and history stays valid (no dangling tool_call / orphan tool message)."""
    tmp = tempfile.mkdtemp()
    tb = Toolbox(root=Path(tmp), guard=WriteGuard(self_mode=False))
    llm = _FakeLLM([_slow_run_call(), _final_answer()])
    a = Agent(llm=llm, toolbox=tb, ui=_SilentUI(), system_prompt="sys", auto_approve=True)

    # Watchdog of 0.2s bounds the (instant) fake LLM call, NOT the 1s tool.
    result = a.run_turn("run the slow command", timeout=0.2)

    assert not result.aborted, f"turn was aborted: {result.final_text}"
    assert result.final_text == "all done"

    # History must be self-consistent — the fix's whole point.
    repairs = a._repair_dangling_tool_calls()
    assert repairs == 0, f"history was corrupt (needed {repairs} repairs): {a.messages}"

    # The slow tool's call + response pair must both be present and matched.
    assistant_with_calls = [m for m in a.messages
                            if m.get("role") == "assistant" and m.get("tool_calls")]
    assert assistant_with_calls, "missing assistant tool_calls message"
    tool_msgs = [m for m in a.messages if m.get("role") == "tool"]
    assert any(m.get("tool_call_id") == "call_slow_1" for m in tool_msgs), \
        "missing tool response for the slow call"


def test_next_turn_works_after_long_tool() -> None:
    """After a long-tool turn, a follow-up turn proceeds (no HTTP-400-class wedge:
    _chat_with_retry's pre-call repair finds nothing to fix)."""
    tmp = tempfile.mkdtemp()
    tb = Toolbox(root=Path(tmp), guard=WriteGuard(self_mode=False))
    llm = _FakeLLM([_slow_run_call(), _final_answer(), _final_answer()])
    a = Agent(llm=llm, toolbox=tb, ui=_SilentUI(), system_prompt="sys", auto_approve=True)

    a.run_turn("first: slow command", timeout=0.2)
    # The second turn must run cleanly to a final answer.
    result2 = a.run_turn("second: anything", timeout=5.0)
    assert not result2.aborted
    assert result2.final_text == "all done"
    assert a._repair_dangling_tool_calls() == 0
