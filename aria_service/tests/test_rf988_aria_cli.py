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
from aria_cli.llm import LLMConfig, LLMError, LLMResponse
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
def test_llmconfig_defaults_to_arias_own_model(monkeypatch):
    """R-F4370 (C-315) — was `test_llmconfig_deepseek_defaults`. The coder now
    defaults to ARIA's own sovereign model; DeepSeek was removed from the CLI
    by operator directive ("aria must use her own reasoning now"). A stray
    DEEPSEEK_API_KEY in the environment must no longer select anything."""
    for v in ("ARIA_CODER_LLM_PROVIDER", "LLM_PROVIDER", "ARIA_CODER_LLM_MODEL",
              "LLM_MODEL", "ARIA_CODER_LLM_BASE_URL", "OPENAI_BASE_URL",
              "ARIA_CODER_LLM_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("ARIA_LLM_URL", "https://pod.example/v1")
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")
    cfg = LLMConfig.from_env()
    assert cfg.provider == "aria-llm"
    assert cfg.model == "aria-llm-v0.4-dpo"
    assert cfg.base_url == "https://pod.example/v1"
    assert cfg.is_configured is True


def test_llmconfig_unconfigured_refuses_rather_than_substituting(monkeypatch):
    """R-F4370 — with nothing configured the CLI now RAISES. It used to return
    a config for whichever vendor happened to have a key, which is how the
    coder ended up on a model the operator had not chosen."""
    for v in ("ARIA_CODER_LLM_API_KEY", "LLM_API_KEY", "DEEPSEEK_API_KEY",
              "OPENAI_API_KEY", "GROQ_API_KEY", "ARIA_CODER_LLM_PROVIDER",
              "LLM_PROVIDER", "ARIA_LLM_URL", "ARIA_LLM_MODEL"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(LLMError):
        LLMConfig.from_env()


def test_find_repo_root_detects_crucix():
    # This test file lives inside the crucix repo, so detection must succeed.
    root = find_repo_root(Path(__file__).resolve().parent)
    assert root is not None
    assert (root / "aria_service").is_dir() and (root / "CLAUDE.md").is_file()


def test_find_repo_root_none_outside(tmp_path):
    assert find_repo_root(tmp_path) is None


def test_self_mode_constitution_active():
    """R-F1699: the constitutional validator is RE-ARMED in self-mode (closing the
    R-F995-class bypass that let the CLI overwrite honesty-critical files).
    General-mode (arbitrary user projects) stays validator-free — only the
    truncation guard applies there."""
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


def test_autonomous_mode_never_asks_approval(tmp_path):
    """Free rein: with auto_approve=True the approval gate is never consulted —
    mutating tools just run."""
    box = _general_box(tmp_path)
    llm = FakeLLM([
        _assistant(tool_calls=[_tool_call(
            "c1", "write_file", '{"path": "a.txt", "content": "x"}')]),
        _assistant(content="done"),
    ])

    class _RaiseUI(_CollectUI):
        def approve(self, name, args):
            raise AssertionError("approval must not be requested in autonomous mode")

    agent = Agent(llm=llm, toolbox=box, system_prompt="s", ui=_RaiseUI(), auto_approve=True)
    agent.run_turn("write a.txt")
    assert (tmp_path / "a.txt").read_text() == "x"


def test_agent_streams_tokens_and_does_not_double_print(tmp_path):
    """R-F1028 — when the provider supports chat_stream, the agent streams each
    token to the UI (never silent) and does NOT also call assistant() (no dupe)."""
    class _StreamLLM:
        total_input_tokens = 0
        total_output_tokens = 0

        def chat_stream(self, messages, tools=None, on_delta=None):
            for ch in ["hel", "lo ", "wor", "ld"]:
                if on_delta:
                    on_delta(ch)
            return LLMResponse(content="hello world", tool_calls=[],
                               raw_message={"role": "assistant", "content": "hello world"})

        def close(self):
            pass

    class _StreamUI(AgentUI):
        def __init__(self):
            self.deltas = []
            self.assistant_calls = []
            self._streamed_this_turn = False

        def thinking_start(self):
            self._streamed_this_turn = False

        def stream_delta(self, text):
            self.deltas.append(text)
            self._streamed_this_turn = True

        def assistant(self, text):
            self.assistant_calls.append(text)

    ui = _StreamUI()
    agent = Agent(llm=_StreamLLM(), toolbox=_general_box(tmp_path),
                  system_prompt="s", ui=ui, auto_approve=True)
    result = agent.run_turn("hi")
    assert "".join(ui.deltas) == "hello world", "tokens must stream live"
    assert ui.assistant_calls == [], "must not double-print streamed content"
    assert result.final_text == "hello world"


def test_prompt_carries_engineering_standard():
    prompt = build_system_prompt(root=Path.cwd(), self_mode=False,
                                 repo_root=None)
    low = prompt.lower()
    assert "engineering standard" in low
    assert "timeout" in low and "idempotent" in low and "capability test" in low


def test_prompt_declares_full_autonomy():
    repo_root = find_repo_root(Path(__file__).resolve().parent)
    prompt = build_system_prompt(root=repo_root, self_mode=True,
                                 repo_root=repo_root)
    low = prompt.lower()
    assert "full autonomy" in low or "free rein" in low
    assert "do not ask" in low


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
                                 repo_root=repo_root)
    assert "BINDING REPO RULES" in prompt
    assert "CLAUDE.md" in prompt
    # a phrase that actually appears in the repo's CLAUDE.md
    assert "R-number" in prompt


def test_system_prompt_no_repo_rules_in_general_mode():
    prompt = build_system_prompt(root=Path.cwd(), self_mode=False,
                                 repo_root=None)
    assert "BINDING REPO RULES" not in prompt
    assert "coding agent" in prompt.lower()


def test_system_prompt_injects_agents_playbook(tmp_path):
    # AGENTS.md (when present) is injected alongside CLAUDE.md so ARIA gets the
    # coder playbook. Use a synthetic repo so the test doesn't depend on the
    # live AGENTS.md wording.
    (tmp_path / "CLAUDE.md").write_text("floor rules: R-number everything", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("PLAYBOOK-MARKER ship via push", encoding="utf-8")
    prompt = build_system_prompt(root=tmp_path, self_mode=True,
                                 repo_root=tmp_path)
    assert "AGENTS.md" in prompt and "PLAYBOOK-MARKER" in prompt
    assert "CLAUDE.md" in prompt and "floor rules" in prompt


def test_self_mode_prompt_covers_shipping_and_excellence():
    repo_root = find_repo_root(Path(__file__).resolve().parent)
    prompt = build_system_prompt(root=repo_root, self_mode=True,
                                 repo_root=repo_root)
    low = prompt.lower()
    # exceptional-coder standard + the end-to-end ship path are both present
    assert "exceptional" in low
    assert "git push origin main" in low
    assert "aria-wa" in low and "fly.wa.toml" in low
    assert "verify" in low


def test_turn_abort_surfaces_reason_at_step_cap(tmp_path, monkeypatch):
    """A turn that never stops calling tools hits MAX_STEPS, aborts gracefully,
    surfaces the reason (via ui.info) and keeps it in final_text — no silent stop.
    MAX_STEPS is patched low so the test is fast (the default is effectively
    unlimited)."""
    import aria_cli.agent as ag
    monkeypatch.setattr(ag, "MAX_STEPS", 3)

    class _LoopLLM:
        total_input_tokens = 0
        total_output_tokens = 0

        def chat(self, messages, tools=None):
            return _assistant(tool_calls=[_tool_call("c", "list_dir", '{"path": "."}')])

        def close(self):
            pass

    class _InfoUI(_CollectUI):
        def __init__(self):
            super().__init__()
            self.infos = []

        def info(self, text):
            self.infos.append(text)

    ui = _InfoUI()
    agent = Agent(llm=_LoopLLM(), toolbox=_general_box(tmp_path),
                  system_prompt="s", ui=ui, auto_approve=True)
    result = agent.run_turn("loop forever")
    assert result.aborted
    assert "3" in result.final_text                     # the cap value is named
    assert any("limit" in m.lower() for m in ui.infos)  # reason was shown, not swallowed


def test_llm_transient_error_is_retried(tmp_path):
    """A transient LLM error self-heals: the agent retries and the turn succeeds."""
    from aria_cli.llm import LLMError

    class _FlakyLLM:
        total_input_tokens = 0
        total_output_tokens = 0

        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools=None):
            self.calls += 1
            if self.calls < 3:
                raise LLMError("read timeout while contacting endpoint")
            return _assistant(content="recovered after retry")

        def close(self):
            pass

    agent = Agent(llm=_FlakyLLM(), toolbox=_general_box(tmp_path),
                  system_prompt="s", ui=_CollectUI(), auto_approve=True)
    agent.retry_backoff = 0  # no real sleeping in tests
    result = agent.run_turn("do it")
    assert not result.aborted
    assert "recovered" in result.final_text


def test_hard_llm_error_does_not_retry_or_resume(tmp_path):
    """A non-transient error (auth) is not retried and is not resumable."""
    from aria_cli.llm import LLMError

    class _AuthFailLLM:
        total_input_tokens = 0
        total_output_tokens = 0

        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools=None):
            self.calls += 1
            # R-F2368: replicate PRODUCTION — llm.py raises a 401 with
            # transient=False (line ~273: 401 is neither 429 nor >=500). The
            # old mock omitted it, so LLMError defaulted to transient=True and
            # _is_transient (R-F1418, trusts the typed flag) correctly returned
            # True — the test was asserting a contract the mock didn't create.
            raise LLMError("HTTP 401 authentication_error: invalid api key",
                           transient=False)

        def close(self):
            pass

    llm = _AuthFailLLM()
    agent = Agent(llm=llm, toolbox=_general_box(tmp_path), system_prompt="s",
                  ui=_CollectUI(), auto_approve=True)
    agent.retry_backoff = 0
    result = agent.run_until_complete("do it")
    assert result.aborted and not result.resumable
    assert llm.calls == 1  # no retry, no resume on a hard auth failure


def test_run_until_complete_self_resumes_on_resumable_abort(tmp_path, monkeypatch):
    """If a turn ends incomplete-but-resumable, the agent auto-continues itself
    instead of stopping (the self-start trigger)."""
    import aria_cli.agent as ag
    monkeypatch.setattr(ag, "MAX_STEPS", 1)        # force a step-cap abort per turn
    monkeypatch.setattr(ag, "AUTO_RESUME_MAX", 3)

    calls = {"n": 0}

    class _ToolThenDoneLLM:
        total_input_tokens = 0
        total_output_tokens = 0

        def chat(self, messages, tools=None):
            calls["n"] += 1
            # First two model calls request a tool (each turn caps at 1 step and
            # aborts-resumable); the third finishes.
            if calls["n"] < 3:
                return _assistant(tool_calls=[_tool_call("c", "list_dir", '{"path": "."}')])
            return _assistant(content="all done")

        def close(self):
            pass

    agent = Agent(llm=_ToolThenDoneLLM(), toolbox=_general_box(tmp_path),
                  system_prompt="s", ui=_CollectUI(), auto_approve=True)
    result = agent.run_until_complete("big task")
    assert not result.aborted
    assert "all done" in result.final_text


def test_thinking_hooks_exist_on_ui():
    # The agent calls these around every model call to show live activity.
    ui = AgentUI()
    ui.thinking_start()
    ui.thinking_stop()  # no-ops on the base UI, must not raise


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


def test_loop_guard_breaks_repeated_identical_tool_calls(tmp_path):
    """R-F1042 — a model that repeats the SAME tool call with identical args must
    not loop forever; the guard nudges then aborts the turn (non-resumable)."""
    import aria_cli.agent as ag
    box = _general_box(tmp_path)

    class _LoopLLM:
        total_input_tokens = 0
        total_output_tokens = 0
        def chat(self, messages, tools=None):
            return _assistant(tool_calls=[_tool_call("c", "grep", '{"pattern": "safety"}')])
        def close(self): pass

    agent = Agent(llm=_LoopLLM(), toolbox=box, system_prompt="s",
                  ui=_CollectUI(), auto_approve=True)
    result = agent.run_turn("loop please")
    assert result.aborted and not result.resumable
    assert "loop" in result.final_text.lower()
    # aborted at the hard cap, not after thousands of calls
    assert result.steps <= ag.LOOP_ABORT_AT + 1
