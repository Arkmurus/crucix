"""R-F1082 — capability test for real-time Claude↔ARIA collaboration.

Proves the user-visible behaviour: when Claude posts a message to the file
bridge, ARIA's agent loop surfaces it automatically (without a manual
check_claude), and injects it exactly once.

This invokes the REAL path (Agent._drain_claude_bridge + the real bridge),
not a mock — per the binding rule that a capability test must call the thing.

R-F3333 — this file was failing for TWO unrelated reasons, and the first was
hiding the second.

1. Setup: a hand-rolled `_Toolbox` stub with one attribute drifted behind
   Agent's constructor when R-F1143 added `CoderToolbox(toolbox)`, which reads
   `toolbox.root`. All four tests died with AttributeError before reaching an
   assertion. Fixed by building the REAL Toolbox (see _make_agent).

2. Behaviour, only visible once (1) was fixed: two tests sent an unsolicited
   NOTE and asserted it was injected into the conversation. R-F2051 deliberately
   stopped doing that. The bridge is a very active channel, and injecting every
   unsolicited note as "high-priority — adjust now" hijacked ARIA off the
   operator's task: she would answer an unrelated note, emit a tool-call-free
   reply, and the turn would END with the operator's actual request abandoned.
   So a note is now SURFACED (a dim info line, nothing hidden) but not injected;
   a REPLY to a question ARIA herself asked still is, because she invited it.

   The endpoint is right and the tests were stale. The injection tests below now
   drive the case that still injects — a reply — so they test what they were
   written to test, and test_rf3333_* pins R-F2051's decision in both
   directions, which nothing did before: it was a deliberate behaviour change
   whose only trace in the suite was two tests asserting its negation.
"""
from __future__ import annotations

from aria_cli import bridge
from aria_cli.agent import Agent, AgentUI
from aria_cli.safety import WriteGuard
from aria_cli.tools import Toolbox


class _CapturingUI(AgentUI):
    def __init__(self):
        self.infos: list[str] = []

    def info(self, text: str) -> None:
        self.infos.append(text)


def _make_agent(base):
    """R-F3333 — build the REAL Toolbox, the way cli.py:1900 does.

    This used to pass a hand-rolled `_Toolbox` stub carrying one attribute, with
    the comment "the drain only reads .bridge_base". That was true when written
    and stopped being true when R-F1143 added `CoderToolbox(toolbox)` to Agent's
    constructor, which reads `toolbox.root`. Every test in this file has failed
    with AttributeError ever since — not on the behaviour under test, but in
    setup, before reaching a single assertion.

    Adding `.root` to the stub would fix today's symptom and leave the class
    intact: the next dependency Agent's constructor grows breaks these tests
    again, in setup, with another AttributeError that says nothing about the
    bridge. A real Toolbox cannot drift behind the real constructor. It costs a
    tmp_path and a WriteGuard, touches no disk that tmp_path does not own, and
    keeps this a capability test of the drain rather than of a stub.
    """
    toolbox = Toolbox(root=base, guard=WriteGuard(self_mode=False), bridge_base=base)
    return Agent(llm=None, toolbox=toolbox, system_prompt="sys", ui=_CapturingUI())


def test_no_bridge_messages_is_noop(tmp_path):
    agent = _make_agent(tmp_path)
    agent._drain_claude_bridge()
    # only the system message exists; nothing injected
    assert [m["role"] for m in agent.messages] == ["system"]


def test_claude_message_is_injected_once(tmp_path):
    agent = _make_agent(tmp_path)
    # R-F3333: a REPLY (reply_to set) is the message class R-F2051 still injects
    # — ARIA asked, so the answer is on-task. An unsolicited note is covered by
    # test_rf3333_unsolicited_note_is_surfaced_but_not_injected below.
    asked = bridge.send(tmp_path, frm="aria", to="claude",
                        text="which root does the pre-commit hook use?", kind="question")
    bridge.send(tmp_path, frm="claude", to="aria",
                text="FIX scripts/githooks/pre-commit REPO_ROOT", kind="answer",
                reply_to=asked["id"])

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
    asked = bridge.send(tmp_path, frm="aria", to="claude", text="anything before I start?",
                        kind="question")
    bridge.send(tmp_path, frm="claude", to="aria", text="pre-turn guidance", kind="answer",
                reply_to=asked["id"])

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
    assert captured["roles"].count("user") == 2  # operator task + claude reply


# ── R-F3333: R-F2051's decision, pinned in both directions ───────────────────
#
# R-F2051 stopped injecting unsolicited notes because doing so hijacked ARIA off
# the operator's task. That was a deliberate behaviour change, and the only trace
# it left in this suite was two tests asserting its NEGATION — which is how it
# stayed unnoticed while they were red for other reasons. Both halves now have a
# test, so neither can be reverted silently.


def test_rf3333_unsolicited_note_is_surfaced_but_not_injected(tmp_path, monkeypatch):
    """A note must reach the operator's eyes WITHOUT redirecting the task.

    Both halves matter. Not injecting is the fix; still surfacing is what keeps
    it from being a silent drop — the failure mode CLAUDE.md §21a calls dark.
    """
    monkeypatch.delenv("ARIA_CLI_BRIDGE_NOTES", raising=False)
    agent = _make_agent(tmp_path)
    bridge.send(tmp_path, frm="claude", to="aria",
                text="unrelated SMTP observation", kind="note")

    agent._drain_claude_bridge()

    assert [m["role"] for m in agent.messages] == ["system"], (
        "an unsolicited note must NOT enter the conversation (R-F2051: it "
        "abandoned the operator's task)"
    )
    assert any("unrelated SMTP observation" in s for s in agent.ui.infos), (
        "...but it must still be shown, or the bridge silently swallows Claude"
    )


def test_rf3333_note_injection_can_be_opted_back_in(tmp_path, monkeypatch):
    """ARIA_CLI_BRIDGE_NOTES=1 restores the old behaviour deliberately."""
    monkeypatch.setenv("ARIA_CLI_BRIDGE_NOTES", "1")
    agent = _make_agent(tmp_path)
    bridge.send(tmp_path, frm="claude", to="aria", text="note with the flag on", kind="note")

    agent._drain_claude_bridge()

    user_msgs = [m for m in agent.messages if m["role"] == "user"]
    assert len(user_msgs) == 1, "the opt-in must actually opt in"
    assert "note with the flag on" in user_msgs[0]["content"]
