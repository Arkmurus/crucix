# -*- coding: utf-8 -*-
"""R-F4313 — capability tests for CLI sub-agents (spawn_subagent).

These drive the REAL CoderToolbox.spawn_subagent and the REAL Agent wiring to
prove the user-visible behaviour:

1. spawn_subagent fails CLOSED when no factory is registered (a sub-agent
   cannot be spawned in a context that cannot build one).
2. spawn_subagent calls the registered factory and returns its result.
3. The spawn_subagent tool schema is present and well-formed.
4. Agent.__init__ registers the sub-agent factory on its CoderToolbox, so the
   tool is actually usable in the real agent loop.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from aria_cli.coder_tools import CODER_TOOL_SCHEMAS, CoderToolbox  # noqa: E402
from aria_cli.tools import ToolResult  # noqa: E402


class _FakeTB:
    """Minimal stand-in for the Toolbox that CoderToolbox wraps."""

    def __init__(self, root):
        self.root = root


def test_rf4313_spawn_subagent_fails_closed_without_factory(tmp_path):
    """Without a registered factory, spawn_subagent returns an error (never a
    silent success)."""
    box = CoderToolbox(_FakeTB(tmp_path))
    res = box.spawn_subagent("reviewer", "verify the fix")
    assert res.is_error
    assert "no sub-agent factory" in res.output


def test_rf4313_spawn_subagent_calls_factory(tmp_path):
    """With a factory registered, spawn_subagent returns its result."""
    box = CoderToolbox(_FakeTB(tmp_path))
    captured = {}

    def fake_factory(*, name, task, focus, max_steps):
        captured["name"] = name
        captured["task"] = task
        captured["focus"] = focus
        captured["max_steps"] = max_steps
        return ToolResult("sub-agent says: all tests pass")

    box.subagent_factory = fake_factory
    res = box.spawn_subagent("reviewer", "verify the fix", focus="run pytest",
                             max_steps=5)
    assert not res.is_error
    assert "all tests pass" in res.output
    assert captured == {"name": "reviewer", "task": "verify the fix",
                        "focus": "run pytest", "max_steps": 5}


def test_rf4313_spawn_subagent_surfaces_factory_error(tmp_path):
    """A factory that raises is surfaced as an error, not swallowed."""
    box = CoderToolbox(_FakeTB(tmp_path))

    def boom(**kwargs):
        raise RuntimeError("factory exploded")

    box.subagent_factory = boom
    res = box.spawn_subagent("reviewer", "task")
    assert res.is_error
    assert "factory exploded" in res.output


def test_rf4313_spawn_subagent_schema_present():
    """The spawn_subagent tool schema is registered and well-formed."""
    names = [s["function"]["name"] for s in CODER_TOOL_SCHEMAS]
    assert "spawn_subagent" in names
    schema = next(s for s in CODER_TOOL_SCHEMAS
                  if s["function"]["name"] == "spawn_subagent")
    props = schema["function"]["parameters"]["properties"]
    assert "name" in props and "task" in props
    assert schema["function"]["parameters"]["required"] == ["name", "task"]


def test_rf4313_agent_registers_subagent_factory():
    """Agent.__init__ registers the sub-agent factory on its CoderToolbox, so
    spawn_subagent is usable in the real loop."""
    from pathlib import Path
    from aria_cli.agent import Agent, AgentUI
    from aria_cli.llm import LLMClient
    from aria_cli.tools import Toolbox, WriteGuard

    class _UI(AgentUI):
        def __init__(self):
            pass

    llm = LLMClient()
    tb = Toolbox(root=Path.cwd(), guard=WriteGuard(self_mode=False))
    agent = Agent(llm=llm, toolbox=tb, system_prompt="p", ui=_UI())
    assert agent.coder_toolbox.subagent_factory is not None
    assert callable(agent.coder_toolbox.subagent_factory)
