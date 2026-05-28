"""R-F988 — tests for the ARIA Coder CLI (local Claude-Code-style agent).

Unit tests prove each building block's contract; the capability tests prove the
user-visible symptom: ARIA, launched in a directory, actually reads/edits/runs
through a tool-calling turn and the operator-approval gate is honoured.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from aria_cli.agent import Agent, AgentUI
from aria_cli.cli import find_repo_root, load_dotenv
from aria_cli.llm import LLMConfig, LLMResponse
from aria_cli.prompt import build_system_prompt
from aria_cli.safety import WriteGuard, check_truncation
from aria_cli.tools import MUTATING_TOOLS, Toolbox


# ── fakes ───────────────────────────────────────────────────────────────────
class FakeLLM:
    """Stand-in for LLMClient: replays a queue of LLMResponse objects."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        return self._responses.pop(0)

    def close(self):
        pass


def _tool_call(call_id, name, args_json):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": args_json}}


def _assistant(content="", tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return LLMResponse(content=content, tool_calls=tool_calls or [], raw_message=msg)


def _general_box(root: Path) -> Toolbox:
    return Toolbox(root=root, guard=WriteGuard(self_mode=False))


# ── truncation guard (R-F904 parity) ─────────────────────────────────────────
def test_truncation_allows_new_and_small_files():
    assert check_truncation("", "anything")[0] is True
    small = "\n".join(str(i) for i in range(10))
    assert check_truncation(small, "x")[0] is True  # < 40 lines, exempt


def test_truncation_blocks_collapse_of_large_file():
    big = "\n".join(str(i) for i in range(100))   # 100 lines
    safe, reason = check_truncation(big, "stub\n")
    assert safe is False
    assert "truncation guard" in reason


def test_truncation_allows_reasonable_edit():
    big = "\n".join(str(i) for i in range(100))
    edited = "\n".join(str(i) for i in range(95))  # only slightly smaller
    assert check_truncation(big, edited)[0] is True


# ── Toolbox file ops ──────────────────────────────────────────────────────────
def test_write_read_edit_roundtrip(tmp_path):
    box = _general_box(tmp_path)

    r = box.write_file("sub/hello.py", "print('hi')\n")
    assert not r.is_error and (tmp_path / "sub/hello.py").exists()
    assert "sub/hello.py" in box.changed_files

    r = box.read_file("sub/hello.py")
    assert "print('hi')" in r.output and r.output.startswith("1\t")

    r = box.edit_file("sub/hello.py", "hi", "bye")
    assert not r.is_error
    assert "bye" in (tmp_path / "sub/hello.py").read_text()


def test_edit_requires_unique_match(tmp_path):
    box = _general_box(tmp_path)
    box.write_file("a.txt", "x x x")
    r = box.edit_file("a.txt", "x", "y")
    assert r.is_error and "appears 3 times" in r.output
    r = box.edit_file("a.txt", "x", "y", replace_all=True)
    assert not r.is_error and (tmp_path / "a.txt").read_text() == "y y y"


def test_edit_missing_string_errors(tmp_path):
    box = _general_box(tmp_path)
    box.write_file("a.txt", "hello")
    r = box.edit_file("a.txt", "nope", "x")
    assert r.is_error and "not found" in r.output


def test_write_blocked_by_truncation_guard(tmp_path):
    box = _general_box(tmp_path)
    big = "\n".join(str(i) for i in range(100))
    box.write_file("big.py", big)
    r = box.write_file("big.py", "stub\n")
    assert r.is_error and "BLOCKED" in r.output
    # original content preserved
    assert (tmp_path / "big.py").read_text() == big


def test_list_glob_grep(tmp_path):
    box = _general_box(tmp_path)
    box.write_file("pkg/a.py", "import os\nx = 1\n")
    box.write_file("pkg/b.py", "y = 2\n")
    assert "pkg/" in box.list_dir(".").output
    g = box.glob("**/*.py")
    assert "pkg/a.py" in g.output and "pkg/b.py" in g.output
    gr = box.grep("import os")
    assert "a.py" in gr.output


def test_run_executes_shell(tmp_path):
    box = _general_box(tmp_path)
    r = box.run("echo hello-aria")
    assert "hello-aria" in r.output
    assert "exit code: 0" in r.output


def test_run_nonzero_marked_error(tmp_path):
    box = _general_box(tmp_path)
    r = box.run("python -c \"import sys; sys.exit(3)\"")
    assert r.is_error and "exit code: 3" in r.output


# ── config + repo detection ───────────────────────────────────────────────────
def test_llmconfig_deepseek_defaults(monkeypatch):
    for v in ("ARIA_CODER_LLM_PROVIDER", "LLM_PROVIDER", "ARIA_CODER_LLM_MODEL",
              "LLM_MODEL", "ARIA_CODER_LLM_BASE_URL", "OPENAI_BASE_URL",
              "ARIA_CODER_LLM_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    cfg = LLMConfig.from_env()
    assert cfg.provider == "deepseek"
    assert cfg.model == "deepseek-chat"
    assert cfg.base_url == "https://api.deepseek.com/v1"
    assert cfg.is_configured is True


def test_llmconfig_unconfigured_without_key(monkeypatch):
    for v in ("ARIA_CODER_LLM_API_KEY", "LLM_API_KEY", "DEEPSEEK_API_KEY",
              "OPENAI_API_KEY", "GROQ_API_KEY", "ARIA_CODER_LLM_PROVIDER", "LLM_PROVIDER"):
        monkeypatch.delenv(v, raising=False)
    cfg = LLMConfig.from_env()
    assert cfg.is_configured is False


def test_find_repo_root_detects_crucix():
    # This test file lives inside the crucix repo, so detection must succeed.
    root = find_repo_root(Path(__file__).resolve().parent)
    assert root is not None
    assert (root / "aria_service").is_dir() and (root / "CLAUDE.md").is_file()


def test_find_repo_root_none_outside(tmp_path):
    assert find_repo_root(tmp_path) is None


def test_self_mode_loads_constitution():
    # In the test env aria_service is importable, so self-mode must load the
    # constitutional validator (general mode must not).
    assert WriteGuard(self_mode=True).constitution_active is True
    assert WriteGuard(self_mode=False).constitution_active is False


# ── capability: ARIA actually edits a file through a tool-calling turn ────────
class _CollectUI(AgentUI):
    def __init__(self, approve=True):
        self.texts = []
        self._approve = approve
        self.approvals_requested = 0

    def assistant(self, text):
        self.texts.append(text)

    def approve(self, name, args):
        self.approvals_requested += 1
        return self._approve


def test_agent_writes_file_end_to_end(tmp_path):
    """The user-visible symptom: launch ARIA in a dir, give a task, and a real
    file appears — driven by the tool-calling loop."""
    box = _general_box(tmp_path)
    llm = FakeLLM([
        _assistant(tool_calls=[_tool_call(
            "c1", "write_file",
            '{"path": "out.txt", "content": "built by ARIA\\n"}')]),
        _assistant(content="Done — created out.txt."),
    ])
    ui = _CollectUI(approve=True)
    agent = Agent(llm=llm, toolbox=box, system_prompt="sys", ui=ui, auto_approve=True)

    result = agent.run_turn("create out.txt that says 'built by ARIA'")

    assert (tmp_path / "out.txt").read_text() == "built by ARIA\n"
    assert "out.txt" in box.changed_files
    assert "Done" in result.final_text
    # one tool message + final assistant turn observed
    assert any(m.get("role") == "tool" for m in agent.messages)


def test_agent_approval_gate_blocks_mutation_when_denied(tmp_path):
    box = _general_box(tmp_path)
    llm = FakeLLM([
        _assistant(tool_calls=[_tool_call(
            "c1", "write_file", '{"path": "nope.txt", "content": "x"}')]),
        _assistant(content="Understood, I won't write it."),
    ])
    ui = _CollectUI(approve=False)
    # auto_approve False → the UI approval gate is consulted
    agent = Agent(llm=llm, toolbox=box, system_prompt="sys", ui=ui, auto_approve=False)

    agent.run_turn("write nope.txt")

    assert ui.approvals_requested == 1
    assert not (tmp_path / "nope.txt").exists()
    # the denial is fed back to the model as a tool observation
    assert any("denied" in m.get("content", "") for m in agent.messages
               if m.get("role") == "tool")


def test_write_file_is_a_mutating_tool():
    # plan + fetch are read-only (no approval); only these three mutate.
    assert {"write_file", "edit_file", "run"} == MUTATING_TOOLS


def test_update_plan_renders_checkboxes(tmp_path):
    box = _general_box(tmp_path)
    r = box.update_plan([
        {"step": "read the file", "status": "completed"},
        {"step": "make the edit", "status": "in_progress"},
        {"step": "run tests", "status": "pending"},
        "bare string becomes pending",
    ])
    assert not r.is_error
    assert "[x] read the file" in r.output
    assert "[~] make the edit" in r.output
    assert "[ ] run tests" in r.output
    assert "[ ] bare string becomes pending" in r.output
    assert len(box.plan) == 4


def test_update_plan_rejects_non_list(tmp_path):
    box = _general_box(tmp_path)
    assert box.update_plan("not a list").is_error


def test_fetch_url_rejects_non_http(tmp_path):
    box = _general_box(tmp_path)
    r = box.fetch_url("file:///etc/passwd")
    assert r.is_error and "http" in r.output.lower()


def test_new_tools_are_advertised():
    from aria_cli.tools import TOOL_SCHEMAS
    names = {s["function"]["name"] for s in TOOL_SCHEMAS}
    assert {"update_plan", "fetch_url", "ask_claude", "check_claude"}.issubset(names)
    # the full Claude-Code-style kit
    assert {"read_file", "write_file", "edit_file", "list_dir", "glob",
            "grep", "run"}.issubset(names)


# ── Claude <-> ARIA back-door mailbox (R-F990) ───────────────────────────────
from aria_cli import bridge  # noqa: E402


def test_bridge_roundtrip_and_seen(tmp_path):
    # ARIA asks; Claude sees it once (read_new marks seen), then replies.
    q = bridge.send(tmp_path, frm="aria", to="claude", text="how?", kind="question")
    first = bridge.read_new(tmp_path, "claude")
    assert len(first) == 1 and first[0]["text"] == "how?"
    assert bridge.read_new(tmp_path, "claude") == []   # consumed once

    bridge.send(tmp_path, frm="claude", to="aria", text="like this", kind="answer",
                reply_to=q["id"])
    got = bridge.read_new(tmp_path, "aria")
    assert len(got) == 1 and got[0]["reply_to"] == q["id"]


def test_bridge_wait_for_reply_returns_match(tmp_path):
    q = bridge.send(tmp_path, frm="aria", to="claude", text="q", kind="question")
    bridge.send(tmp_path, frm="claude", to="aria", text="A", reply_to=q["id"])
    reply = bridge.wait_for_reply(tmp_path, "aria", q["id"], timeout=1.0)
    assert reply is not None and reply["text"] == "A"


def test_bridge_wait_times_out_without_reply(tmp_path):
    q = bridge.send(tmp_path, frm="aria", to="claude", text="q", kind="question")
    assert bridge.wait_for_reply(tmp_path, "aria", q["id"], timeout=0.2, interval=0.1) is None


def test_ask_and_check_claude_tools(tmp_path):
    box = Toolbox(root=tmp_path, guard=WriteGuard(self_mode=False), bridge_base=tmp_path)
    r = box.ask_claude("what's the north star here?")
    assert not r.is_error and "sent to claude" in r.output.lower()
    # Claude answers via the bridge
    pending = bridge.read_new(tmp_path, "claude")
    bridge.send(tmp_path, frm="claude", to="aria", text="ship Phase A",
                reply_to=pending[0]["id"])
    c = box.check_claude()
    assert "ship Phase A" in c.output


def test_bridge_tools_disabled_without_base(tmp_path):
    box = Toolbox(root=tmp_path, guard=WriteGuard(self_mode=False))  # no bridge_base
    assert box.ask_claude("hi").is_error
    assert box.check_claude().is_error


# ── .env auto-loading (expert-coder convenience) ─────────────────────────────
def test_load_dotenv_sets_and_respects_existing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "DEEPSEEK_API_KEY=sk-from-dotenv\n"
        'export LLM_MODEL="deepseek-chat"\n'
        "ALREADY_SET=should-not-win\n"
        "blank line below\n\n",
        encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("ALREADY_SET", "env-wins")

    n = load_dotenv(env)
    assert n >= 2
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-from-dotenv"
    assert os.environ["LLM_MODEL"] == "deepseek-chat"   # quotes + export stripped
    assert os.environ["ALREADY_SET"] == "env-wins"      # never clobbered


def test_load_dotenv_missing_file_is_noop(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == 0


# ── expert-coder: CLAUDE.md injected into the self-mode system prompt ─────────
def test_system_prompt_injects_repo_rules_in_self_mode():
    repo_root = find_repo_root(Path(__file__).resolve().parent)
    assert repo_root is not None
    prompt = build_system_prompt(root=repo_root, self_mode=True,
                                 constitution_active=True, repo_root=repo_root)
    assert "BINDING REPO RULES" in prompt
    assert "CLAUDE.md" in prompt
    # a phrase that actually appears in the repo's CLAUDE.md
    assert "R-number" in prompt


def test_system_prompt_no_repo_rules_in_general_mode():
    prompt = build_system_prompt(root=Path.cwd(), self_mode=False,
                                 constitution_active=False, repo_root=None)
    assert "BINDING REPO RULES" not in prompt
    assert "coding agent" in prompt.lower()


def test_system_prompt_injects_agents_playbook(tmp_path):
    # AGENTS.md (when present) is injected alongside CLAUDE.md so ARIA gets the
    # coder playbook. Use a synthetic repo so the test doesn't depend on the
    # live AGENTS.md wording.
    (tmp_path / "CLAUDE.md").write_text("floor rules: R-number everything", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("PLAYBOOK-MARKER ship via push", encoding="utf-8")
    prompt = build_system_prompt(root=tmp_path, self_mode=True,
                                 constitution_active=True, repo_root=tmp_path)
    assert "AGENTS.md" in prompt and "PLAYBOOK-MARKER" in prompt
    assert "CLAUDE.md" in prompt and "floor rules" in prompt


def test_self_mode_prompt_covers_shipping_and_excellence():
    repo_root = find_repo_root(Path(__file__).resolve().parent)
    prompt = build_system_prompt(root=repo_root, self_mode=True,
                                 constitution_active=True, repo_root=repo_root)
    low = prompt.lower()
    # exceptional-coder standard + the end-to-end ship path are both present
    assert "exceptional" in low
    assert "git push origin main" in low
    assert "aria-wa" in low and "fly.wa.toml" in low
    assert "verify" in low


def test_agent_handles_bad_tool_arguments(tmp_path):
    box = _general_box(tmp_path)
    llm = FakeLLM([
        _assistant(tool_calls=[_tool_call("c1", "write_file", "{not valid json")]),
        _assistant(content="Recovered."),
    ])
    ui = _CollectUI(approve=True)
    agent = Agent(llm=llm, toolbox=box, system_prompt="sys", ui=ui, auto_approve=True)
    result = agent.run_turn("do a thing")
    assert "Recovered" in result.final_text
    assert any("could not parse arguments" in m.get("content", "")
               for m in agent.messages if m.get("role") == "tool")
