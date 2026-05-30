"""R-F1082 — capability test for real-time Claude↔ARIA collaboration.

Proves the user-visible behaviour: when Claude posts a message to the file
bridge, ARIA's agent loop surfaces it into the conversation automatically
(without a manual check_claude), and does so exactly once per message.

This invokes the REAL path (Agent._drain_claude_bridge + the real bridge),
not a mock — per the binding rule that a capability test must call the thing.
"""
from __future__ import annotations

from aria_cli import bridge
from aria_cli.agent import Agent, AgentUI


class _Toolbox:
    """Minimal toolbox stub: the drain only reads .bridge_base."""
    def __init__(self, base):
        self.bridge_base = base


class _CapturingUI(AgentUI):
    def __init__(self):
        self.infos: list[str] = []

    def info(self, text: str) -> None:
        self.infos.append(text)


def _make_agent(base):
    return Agent(llm=None, toolbox=_Toolbox(base), system_prompt="sys", ui=_CapturingUI())


def test_no_bridge_messages_is_noop(tmp_path):
    agent = _make_agent(tmp_path)
    agent._drain_claude_bridge()
    # only the system message exists; nothing injected
    assert [m["role"] for m in agent.messages] == ["system"]


def test_claude_message_is_injected_once(tmp_path):
    agent = _make_agent(tmp_path)
    bridge.send(tmp_path, frm="claude", to="aria",
                text="FIX scripts/githooks/pre-commit REPO_ROOT", kind="note")

    agent._drain_claude_bridge()
    user_msgs = [m for m in agent.messages if m["role"] == "user"]
    assert len(user_msgs) == 1
    assert "REPO_ROOT" in user_msgs[0]["content"]
    assert "FROM CLAUDE" in user_msgs[0]["content"]
    # surfaced to the UI so the operator sees it in ARIA's terminal
    assert any("Claude" in s for s in agent.ui.infos)

    # draining again must NOT re-inject (message already marked seen)
    agent._drain_claude_bridge()
    assert len([m for m in agent.messages if m["role"] == "user"]) == 1


def test_only_messages_to_aria_are_drained(tmp_path):
    agent = _make_agent(tmp_path)
    # a message ARIA sent to Claude must not be echoed back into ARIA's context
    bridge.send(tmp_path, frm="aria", to="claude", text="question for claude", kind="question")
    agent._drain_claude_bridge()
    assert [m["role"] for m in agent.messages] == ["system"]


def test_drain_runs_inside_run_turn_before_llm(tmp_path):
    """run_turn must drain the bridge before its first LLM call, so guidance that
    arrived before the turn started is already in context."""
    agent = _make_agent(tmp_path)
    bridge.send(tmp_path, frm="claude", to="aria", text="pre-turn guidance", kind="note")

    captured = {}

    def _fake_chat(steps, on_delta=None):
        # snapshot the messages the LLM would see, then end the turn cleanly
        captured["roles"] = [m["role"] for m in agent.messages]
        captured["text"] = " ".join(m.get("content", "") for m in agent.messages)
        from aria_cli.agent import TurnResult
        return TurnResult(final_text="done", steps=steps)

    agent._chat_with_retry = _fake_chat  # type: ignore[assignment]
    agent.run_turn("operator task")

    # the Claude message was injected BEFORE the LLM was called
    assert "pre-turn guidance" in captured["text"]
    assert captured["roles"].count("user") == 2  # operator task + claude note
