"""R-F2398 — the aria CLI tool list sent to the LLM must have UNIQUE names.

Live symptom (operator paste): every CLI turn failed with
    LLM endpoint .../chat/completions returned HTTP 400:
    {"error":{"message":"Tool names must be unique.", ...}}
so the terminal UI could never produce a response ("task finished — the input
box is live", but no answer). Root cause: `fetch_url` was registered in BOTH
TOOL_SCHEMAS (base) and CODER_TOOL_SCHEMAS (coder); the agent concatenated them
with no dedup and DeepSeek/OpenAI reject the WHOLE request on any duplicate name.

Fix: dedup the merged schema list (agent._dedup_tool_schemas, base wins to match
_dispatch order) + remove the redundant coder fetch_url schema. These tests drive
the ACTUAL list handed to the LLM (Agent._all_schemas, used at agent.py stream/
chat call sites) and assert the user-visible outcome: no duplicate tool names.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from aria_cli.agent import Agent, _dedup_tool_schemas
from aria_cli.coder_tools import CODER_TOOL_SCHEMAS
from aria_cli.safety import WriteGuard
from aria_cli.tools import TOOL_SCHEMAS, Toolbox


class _FakeLLM:
    def chat(self, messages, tools=None):  # never called here
        raise AssertionError("LLM must not be called by this test")


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
    def approve(self, name="", args=None): return True


def _names(schemas):
    return [s["function"]["name"] for s in schemas]


def test_merged_raw_schemas_have_no_duplicate_names():
    """Regression guard at SOURCE: the base + coder schema lists must not carry
    the same name twice. This is what silently shipped the fetch_url overlap —
    it would have caught it before the LLM ever saw the malformed request."""
    names = _names(TOOL_SCHEMAS) + _names(CODER_TOOL_SCHEMAS)
    dups = sorted({n for n in names if names.count(n) > 1})
    assert not dups, f"duplicate tool names across base+coder schemas: {dups}"


def test_agent_all_schemas_unique():
    """Capability: the EXACT list the agent passes to the LLM (self._all_schemas,
    used at the stream_fn/llm.chat call sites) has all-unique function names, so
    the provider cannot 400 with 'Tool names must be unique.'."""
    tmp = tempfile.mkdtemp()
    tb = Toolbox(root=Path(tmp), guard=WriteGuard(self_mode=False))
    agent = Agent(llm=_FakeLLM(), toolbox=tb, ui=_SilentUI(),
                  system_prompt="sys", auto_approve=True)
    names = _names(agent._all_schemas)
    assert len(names) == len(set(names)), (
        f"Agent._all_schemas has duplicate tool names: "
        f"{sorted({n for n in names if names.count(n) > 1})}"
    )
    # fetch_url must still be present exactly once (base survived, coder dropped).
    assert names.count("fetch_url") == 1


def test_dedup_keeps_first_occurrence():
    """Unit: the guard keeps the FIRST occurrence (base) and drops later dups —
    matching _dispatch, which checks the base toolbox before the coder toolbox."""
    schemas = [
        {"function": {"name": "fetch_url", "src": "base"}},
        {"function": {"name": "read_file", "src": "base"}},
        {"function": {"name": "fetch_url", "src": "coder"}},  # duplicate
    ]
    out = _dedup_tool_schemas(schemas)
    assert _names(out) == ["fetch_url", "read_file"]
    assert out[0]["function"]["src"] == "base", "base occurrence must win"


def test_dedup_eliminates_future_accidental_overlap():
    """The failure CLASS is eliminated: even if a NEW duplicate is added to
    either list tomorrow, the merged list handed to the LLM stays unique."""
    poisoned = list(TOOL_SCHEMAS) + list(CODER_TOOL_SCHEMAS) + [
        {"function": {"name": "read_file", "description": "accidental dup"}},
    ]
    out = _dedup_tool_schemas(poisoned)
    names = _names(out)
    assert len(names) == len(set(names)), "guard must neutralise any future overlap"


def test_dedup_passes_through_malformed_schema():
    """A schema without function.name is passed through untouched (never crashes
    the assembly that feeds the LLM)."""
    out = _dedup_tool_schemas([{"no_function": True}, {"function": {"name": "x"}}])
    assert len(out) == 2
