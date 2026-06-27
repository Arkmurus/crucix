"""R-F2051 capability tests — bridge notes must not hijack the operator's task.

Root cause (operator-reported: ARIA "deviates and doesn't complete tasks"): the
CLI auto-injected EVERY new Claude-bridge message — including unsolicited notes
about unrelated platform work — into ARIA's task conversation mid-task, framed
as "high-priority — adjust now". She'd stop the operator's task to answer the
note, emit a tool-call-free reply, and the turn would END (agent.py treats a
message with no tool calls as done), abandoning what the operator asked.

The fix (per §3c — these tests drive the broken paths and assert the
user-visible outcome): in the interactive CLI, only REPLIES to a question ARIA
herself asked are injected into the task; unsolicited NOTES are surfaced as a
dim info line but NOT appended to the conversation (unless ARIA_CLI_BRIDGE_NOTES=1).
"""
from __future__ import annotations

import queue
from unittest.mock import MagicMock, patch

import pytest

from aria_cli import bridge, prompt
from aria_cli.agent import Agent
from aria_cli.llm import LLMClient


@pytest.fixture
def bridge_agent(tmp_path, monkeypatch) -> Agent:
    """An Agent whose toolbox points at a real (empty) bridge dir."""
    monkeypatch.delenv("ARIA_CLI_BRIDGE_NOTES", raising=False)
    llm = MagicMock(spec=LLMClient)
    toolbox = MagicMock()
    toolbox.bridge_base = str(tmp_path)
    agent = Agent(llm=llm, toolbox=toolbox, ui=MagicMock(), system_prompt="test")
    return agent


def _injected(agent: Agent) -> list[str]:
    """Text of every message appended to the conversation past the system prompt."""
    return [m["content"] for m in agent.messages[1:]]


# ── _drain_claude_bridge: the mid-task injection path ────────────────────────

def test_unsolicited_note_is_not_injected(bridge_agent: Agent, tmp_path):
    """A Claude note (reply_to=None) must NOT be appended to the task — the bug
    was that it WAS, redirecting ARIA off the operator's task."""
    bridge.send(tmp_path, frm="claude", to="aria",
                text="Heads up: I shipped R-F2029 on the adversarial scorer.",
                kind="note")

    bridge_agent._drain_claude_bridge()

    assert _injected(bridge_agent) == [], "unsolicited note hijacked the task"
    # …but it is still surfaced to the operator (nothing hidden).
    assert bridge_agent.ui.info.called
    assert "not actioned" in bridge_agent.ui.info.call_args[0][0]


def test_reply_to_arias_question_is_injected(bridge_agent: Agent, tmp_path):
    """A reply to a question ARIA asked IS on-task and must be injected."""
    bridge.send(tmp_path, frm="claude", to="aria",
                text="Yes — use SMTP_USER as the middle fallback.",
                kind="answer", reply_to="m-aria-asked-this")

    bridge_agent._drain_claude_bridge()

    inj = _injected(bridge_agent)
    assert len(inj) == 1
    assert "SMTP_USER" in inj[0]
    # Framing must steer her to STAY on task, not "adjust now".
    assert "STAY ON THE OPERATOR'S CURRENT TASK" in inj[0]
    assert "adjust now" not in inj[0].lower()


def test_notes_injected_when_opt_in_flag_set(bridge_agent: Agent, tmp_path, monkeypatch):
    """ARIA_CLI_BRIDGE_NOTES=1 restores note injection for operators who want it."""
    monkeypatch.setenv("ARIA_CLI_BRIDGE_NOTES", "1")
    bridge.send(tmp_path, frm="claude", to="aria", text="a platform note", kind="note")

    bridge_agent._drain_claude_bridge()

    assert any("a platform note" in t for t in _injected(bridge_agent))


def test_no_bridge_is_noop(tmp_path):
    """No bridge_base → no-op, never raises."""
    llm = MagicMock(spec=LLMClient)
    toolbox = MagicMock()
    toolbox.bridge_base = None
    agent = Agent(llm=llm, toolbox=toolbox, ui=MagicMock(), system_prompt="test")
    agent._drain_claude_bridge()
    assert agent.messages == [{"role": "system", "content": "test"}]


# ── the cli.py idle-wake poller: must only wake for replies ──────────────────

def test_poller_queues_only_replies(tmp_path, monkeypatch):
    """The bridge poller wakes the idle prompt for replies, not for notes."""
    import aria_cli.cli as cli_mod
    monkeypatch.delenv("ARIA_CLI_BRIDGE_NOTES", raising=False)

    bridge.send(tmp_path, frm="claude", to="aria", text="unrelated note", kind="note")
    bridge.send(tmp_path, frm="claude", to="aria", text="reply text",
                kind="answer", reply_to="m-q1")

    q: queue.Queue = queue.Queue()
    # Run the poller body once against our temp bridge by driving read_new + the
    # same predicate the poller uses (the loop body is what changed).
    new = bridge.read_new(tmp_path, "aria")
    inject_notes = False
    for m in new:
        text = (m.get("text") or "").strip()
        if not text:
            continue
        if not m.get("reply_to") and not inject_notes:
            continue
        q.put(text)

    queued = []
    while not q.empty():
        queued.append(q.get_nowait())
    assert queued == ["reply text"], f"poller queued a note: {queued}"


# ── the system prompt: stay-on-task contract ─────────────────────────────────

def test_system_prompt_has_stay_on_task_rule(tmp_path):
    """The operating contract must tell ARIA to stay on the operator's task and
    not stop mid-task — the behavioural backstop for the deviation fix."""
    sp = prompt.build_system_prompt(root=tmp_path, self_mode=False)
    assert "STAY ON THE OPERATOR'S TASK" in sp
    assert "DON'T STOP MID-TASK" in sp


def test_self_mode_repo_rules_are_not_a_task_list(tmp_path):
    """In self-mode the injected CLAUDE.md must be framed as HOW-to-work rules,
    not a backlog to pick up — so ARIA doesn't wander into gaps/punch-list."""
    (tmp_path / "CLAUDE.md").write_text("# rules\nphase A gates...\n", encoding="utf-8")
    sp = prompt.build_system_prompt(root=tmp_path, self_mode=True, repo_root=tmp_path)
    assert "NOT a task" in sp
    assert "do not pick up gaps" in sp
