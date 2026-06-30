# -*- coding: utf-8 -*-
"""Capability tests for the ARIA CLI coder lift (R-F2160–R-F2166).

Each test drives the actual changed path and asserts the user-visible outcome,
per CLAUDE.md §3c. Fast and deterministic — no live network (HTTP clients are
exercised in their unconfigured/None branch), no real LLM.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import aria_cli.agent as agent_mod
import aria_cli.prompt as prompt_mod
from aria_cli.agent import Agent
from aria_cli.llm import LLMClient, LLMConfig, LLMResponse
from aria_cli.safety import WriteGuard, check_truncation
from aria_cli.tools import Toolbox, _resolve_powershell

REPO_ROOT = Path(__file__).resolve().parents[2]


class _SilentUI(agent_mod.AgentUI):
    """Local no-op UI for driving the agent in tests."""


class _FakeLLM:
    """Returns scripted responses instantly. No chat_stream attr → non-streaming."""

    def __init__(self, script: list[LLMResponse]) -> None:
        self._script = script
        self._i = 0
        self.total_output_tokens = 0

    def chat(self, messages, tools=None) -> LLMResponse:
        r = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return r


def _final(text: str) -> LLMResponse:
    return LLMResponse(content=text, tool_calls=[],
                       raw_message={"role": "assistant", "content": text})


def _tool_call(call_id: str, name: str, args: dict) -> LLMResponse:
    fn = {"name": name, "arguments": json.dumps(args)}
    tc = {"id": call_id, "type": "function", "function": fn}
    return LLMResponse(content="", tool_calls=[tc],
                       raw_message={"role": "assistant", "content": "", "tool_calls": [tc]})


def _make_agent(*, task_rag: bool = False, script=None) -> Agent:
    llm = _FakeLLM(script or [_final("done")])
    guard = WriteGuard(self_mode=True, repo_relative_resolver=lambda p: p)
    tb = Toolbox(root=REPO_ROOT, guard=guard, bridge_base=None)
    a = Agent(llm=llm, toolbox=tb, system_prompt="sys", ui=_SilentUI(),
              auto_approve=True, task_rag=task_rag)
    a.retry_backoff = 0
    return a


# ── R-F2160 — constitution un-truncate ──────────────────────────────────────

def test_rf2160_clip_under_cap_unchanged():
    assert prompt_mod._clip_guidance("abc", 100) == "abc"


def test_rf2160_clip_over_cap_keeps_head_and_tail():
    text = "HEAD_MARKER" + ("x" * 5000) + "TAIL_MARKER"
    out = prompt_mod._clip_guidance(text, 1000)
    assert "HEAD_MARKER" in out          # top floor survives
    assert "TAIL_MARKER" in out          # operational tail survives
    assert "ELIDED" in out               # elision marked, not silent
    assert len(out) < len(text)


def test_rf2160_full_constitution_now_injected():
    """The operational back-half of CLAUDE.md (truncated out by the old 16000
    cap) must now reach the prompt. §25 'proprioception' lives well past 16000."""
    guidance = prompt_mod.load_repo_guidance(REPO_ROOT)
    assert "CLAUDE.md" in guidance and "AGENTS.md" in guidance
    claude_text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    if len(claude_text) > 16000:
        # 'proprioception' (§25) sits beyond the old cut point — prove it's present.
        assert claude_text.find("proprioception") > 16000  # sanity: it IS in the tail
        assert "proprioception" in guidance


# ── R-F2163 — PowerShell steering + pwsh + utf-8 pipe ────────────────────────

def test_rf2163_powershell_block_on_windows(monkeypatch):
    monkeypatch.setattr(prompt_mod.platform, "system", lambda: "Windows")
    sp = prompt_mod.build_system_prompt(root=REPO_ROOT, self_mode=False)
    assert "PowerShell" in sp and "bash" in sp
    assert "curl.exe" in sp  # the concrete law-19 trap is present


def test_rf2163_no_powershell_block_off_windows(monkeypatch):
    monkeypatch.setattr(prompt_mod.platform, "system", lambda: "Linux")
    sp = prompt_mod.build_system_prompt(root=REPO_ROOT, self_mode=False)
    assert "SHELL DIALECT — YOU ARE ON WINDOWS POWERSHELL" not in sp


def test_rf2163_resolve_powershell_prefers_pwsh(monkeypatch):
    import aria_cli.tools as t
    monkeypatch.setattr(t, "_POWERSHELL_EXE", None)
    monkeypatch.delenv("ARIA_CODER_POWERSHELL", raising=False)
    monkeypatch.setattr(t.shutil, "which", lambda exe: "/usr/bin/pwsh" if exe == "pwsh" else None)
    assert _resolve_powershell() == "pwsh"
    monkeypatch.setattr(t, "_POWERSHELL_EXE", None)
    monkeypatch.setattr(t.shutil, "which", lambda exe: None)
    assert _resolve_powershell() == "powershell"


def test_rf2163_unicode_output_survives_pipe():
    """utf-8 pipe: non-ASCII child output must come back intact, not corrupted."""
    guard = WriteGuard(self_mode=False)
    tb = Toolbox(root=REPO_ROOT, guard=guard)
    if sys.platform == "win32":
        res = tb.run('Write-Output "cafe_check_✓"', timeout=30)
    else:
        res = tb.run('printf "cafe_check_✓\\n"', timeout=30)
    assert "✓" in res.output  # the checkmark glyph survived decoding


# ── R-F2165 — provider tool-support flag (loud aria fallback) ────────────────

def test_rf2165_aria_provider_has_no_tools():
    c = LLMClient(LLMConfig(provider="aria", api_key="x", model="aria-coder"))
    assert c.supports_tools is False


def test_rf2165_deepseek_provider_supports_tools():
    c = LLMClient(LLMConfig(provider="deepseek", api_key="x", model="deepseek-chat"))
    assert c.supports_tools is True


# ── R-F2161 / R-F2162 — RAG HTTP clients + task-aware injection ──────────────

def test_rf2161_rag_http_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("ARIA_SERVICE_URL", raising=False)
    monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("ARIA_CODER_LLM_API_KEY", raising=False)
    assert prompt_mod._query_coding_rag_http("anything") is None


def test_rf2162_record_http_false_when_unconfigured(monkeypatch):
    monkeypatch.delenv("ARIA_SERVICE_URL", raising=False)
    monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("ARIA_CODER_LLM_API_KEY", raising=False)
    assert prompt_mod.record_coding_outcome_http("fix", {"r_number": "F1"}) is False


def test_rf2162_format_rag_renders_all_kinds():
    block = prompt_mod._format_rag(
        [{"rule": "always verify"}],
        [{"content": "module X does Y"}],
        [{"content": "past fix Z"}],
    )
    assert "CONSTITUTIONAL RULE" in block
    assert "codebase structure" in block
    assert "past fix" in block


def test_rf2162_build_system_prompt_accepts_task_hint(monkeypatch):
    # task_hint must be threaded through without error; RAG unconfigured → no net.
    monkeypatch.delenv("ARIA_SERVICE_URL", raising=False)
    monkeypatch.setattr(prompt_mod, "_query_coding_rag", lambda *a, **k: "")
    sp = prompt_mod.build_system_prompt(root=REPO_ROOT, self_mode=True,
                                        repo_root=REPO_ROOT, task_hint="fix the parser")
    assert isinstance(sp, str) and len(sp) > 100


def test_rf2162_task_rag_injected_into_history(monkeypatch):
    monkeypatch.setattr(prompt_mod, "_query_coding_rag",
                        lambda hint: "\n\nRAG_BLOCK_FOR:" + hint)
    a = _make_agent(task_rag=True, script=[_final("done")])
    a.run_until_complete("refactor the authentication module")
    injected = [m for m in a.messages
                if m.get("role") == "system" and "RAG_BLOCK_FOR" in str(m.get("content"))]
    assert injected, "task-relevant RAG block should be injected as a system note"


def test_rf2162_task_rag_skips_trivial_input(monkeypatch):
    called = {"n": 0}
    def _fake(hint):
        called["n"] += 1
        return "x"
    monkeypatch.setattr(prompt_mod, "_query_coding_rag", _fake)
    a = _make_agent(task_rag=True, script=[_final("ok")])
    a.run_until_complete("ok")  # trivial control word → no RAG query
    assert called["n"] == 0


# ── R-F2164 — auto context compaction ───────────────────────────────────────

def test_rf2164_compact_stubs_old_tool_output_preserves_pairing():
    a = _make_agent()
    big = "X" * 5000
    # 8 assistant/tool pairs; recent ones must be preserved, old ones stubbed.
    for i in range(8):
        a.messages.append({"role": "assistant", "content": "",
                           "tool_calls": [{"id": f"c{i}", "type": "function",
                                           "function": {"name": "grep", "arguments": "{}"}}]})
        a.messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": big})
    before = len(a.messages)
    reclaimed = a._compact(force=True)
    assert reclaimed > 0
    assert len(a.messages) == before  # non-destructive: no messages removed
    tool_msgs = [m for m in a.messages if m.get("role") == "tool"]
    assert any("elided" in str(m["content"]) for m in tool_msgs)   # old ones stubbed
    assert any(m["content"] == big for m in tool_msgs)             # recent ones kept


# ── R-F2166 — no-progress loop guard + truncation char-collapse ──────────────

def test_rf2166_no_progress_guard_aborts_oscillation(monkeypatch):
    monkeypatch.setattr(agent_mod, "NO_PROGRESS_ABORT", 4)
    # Alternate two valid, distinct, non-mutating list_dir calls forever.
    a_call = _tool_call("a", "list_dir", {"path": "."})
    b_call = _tool_call("b", "list_dir", {"path": "aria_cli"})
    a = _make_agent(script=[a_call, b_call] * 20)
    res = a.run_turn("loop please")
    assert res.aborted
    assert "no NEW action" in res.final_text


def test_rf2166_truncation_char_collapse_blocks_small_file_gut():
    old = "".join(f"x{i} = {'a' * 50}\n" for i in range(20))  # 20 lines, ~1.1k chars
    assert len(old) >= 800 and old.count("\n") < 40  # below the line-guard floor
    safe, reason = check_truncation(old, "def f(): pass")
    assert safe is False and "R-F2166" in reason


def test_rf2166_truncation_allows_legit_rewrite():
    old = "x = 1\n" * 200
    new = "x = 2\n" * 190  # similar size → not a collapse
    safe, _ = check_truncation(old, new)
    assert safe is True


def test_rf2166_line_guard_still_fires():
    old = "line\n" * 100
    safe, reason = check_truncation(old, "line\n" * 5)
    assert safe is False and "R-F904" in reason


# ── R-F2166 — ci_deploy description honesty + git_commit no blanket add -A ────

def test_rf2166_ci_deploy_description_is_honest():
    from aria_cli.coder_tools import CODER_TOOL_SCHEMAS
    desc = next(s["function"]["description"] for s in CODER_TOOL_SCHEMAS
                if s["function"]["name"] == "ci_deploy")
    assert "no local flyctl" not in desc          # the old false claim is gone
    assert "deploy.ps1" in desc or "deploy.sh" in desc


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


@pytest.mark.skipif(not __import__("shutil").which("git"), reason="git not available")
def test_rf2166_git_commit_does_not_sweep_untracked_artifacts():
    from aria_cli.coder_tools import CoderToolbox
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _git(["init"], root)
        _git(["config", "user.email", "t@t.t"], root)
        _git(["config", "user.name", "t"], root)
        (root / "tracked.py").write_text("x = 1\n", encoding="utf-8")
        _git(["add", "tracked.py"], root)
        _git(["commit", "-m", "init"], root)
        # Now: edit the tracked file AND drop an untracked runtime artifact.
        (root / "tracked.py").write_text("x = 2\n", encoding="utf-8")
        (root / "runtime.db").write_text("BINARY-ARTIFACT", encoding="utf-8")
        guard = WriteGuard(self_mode=False)
        tb = Toolbox(root=root, guard=guard)
        cbox = CoderToolbox(tb)
        res = cbox.git_commit("R-test: edit tracked")
        assert not res.is_error, res.output
        committed = _git(["show", "--name-only", "--pretty=format:", "HEAD"], root).stdout
        assert "tracked.py" in committed          # the real edit landed
        assert "runtime.db" not in committed      # the artifact was NOT swept in
