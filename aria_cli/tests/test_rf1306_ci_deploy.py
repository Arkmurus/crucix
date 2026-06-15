"""R-F1306 — capability tests for ci_deploy, the hands-free autonomous
commit→push→CI→fly→verify chain.

ci_deploy must: tag the commit with [deploy] (that's the CI trigger), push to
origin/main, then poll the live build_rev and claim success ONLY when it matches
HEAD. These tests drive the real method with a scripted toolbox + mocked httpx —
no real push, no real deploy.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aria_cli.coder_tools import CoderToolbox
from aria_cli.tools import ToolResult


class _ScriptedToolbox:
    """Toolbox stand-in: returns a scripted ToolResult per command substring and
    records every command for assertions."""

    def __init__(self, responses: list[tuple[str, ToolResult]], root: Path) -> None:
        self.root = root
        self._responses = responses
        self.commands: list[str] = []
        self.changed_files: list[str] = []

    def run(self, command: str, timeout: int = 300, cwd: str = "") -> ToolResult:
        self.commands.append(command)
        for key, res in self._responses:
            if key in command:
                return res
        return ToolResult("exit code: 0\n")


class _Resp:
    def __init__(self, build_rev: str) -> None:
        self._b = build_rev

    def json(self):
        return {"status": "alive", "build_rev": self._b}


def _no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)


def test_ci_deploy_commits_with_deploy_tag_and_verifies(monkeypatch) -> None:
    """Pending changes → committed with [deploy], pushed, and success only after
    the live build_rev matches HEAD."""
    tb = _ScriptedToolbox([
        ("git status --porcelain", ToolResult("exit code: 0\n M aria_service/x.py")),
        ("git commit", ToolResult("exit code: 0\n[main abc] deploy")),
        ("git push", ToolResult("exit code: 0\nmain -> main")),
        ("git rev-parse", ToolResult("exit code: 0\nabcd1234")),
    ], Path("."))
    coder = CoderToolbox(tb)
    monkeypatch.setattr("httpx.get", lambda *a, **k: _Resp("sha abcd1234"))
    _no_sleep(monkeypatch)

    r = coder.ci_deploy(summary="test fix", r_number="F9999", poll_timeout=120, local=False)

    assert not r.is_error
    assert "DEPLOYED & ALIGNED" in r.output
    # The commit message must contain the CI trigger tag and the R-number.
    commit_cmd = next(c for c in tb.commands if "git commit" in c)
    assert "[deploy]" in commit_cmd
    assert "F9999" in commit_cmd
    # And it must have pushed.
    assert any("git push origin main" in c for c in tb.commands)


def test_ci_deploy_empty_trigger_commit_when_clean(monkeypatch) -> None:
    """No pending changes and HEAD lacks [deploy] → an --allow-empty trigger
    commit is created so CI still fires for the already-committed batch."""
    tb = _ScriptedToolbox([
        ("git status --porcelain", ToolResult("exit code: 0\n")),
        ("git log -1", ToolResult("exit code: 0\nfix: something (no tag)")),
        ("git commit --allow-empty", ToolResult("exit code: 0\n[main def] deploy")),
        ("git push", ToolResult("exit code: 0\nmain -> main")),
        ("git rev-parse", ToolResult("exit code: 0\nfeed5678")),
    ], Path("."))
    coder = CoderToolbox(tb)
    monkeypatch.setattr("httpx.get", lambda *a, **k: _Resp("sha feed5678"))
    _no_sleep(monkeypatch)

    r = coder.ci_deploy(summary="batch deploy", poll_timeout=120, local=False)

    assert not r.is_error
    assert any("--allow-empty" in c for c in tb.commands)


def test_ci_deploy_push_failure_stops_and_reports(monkeypatch) -> None:
    """A failed push means NO deploy was triggered — must error out honestly and
    never poll/claim success."""
    tb = _ScriptedToolbox([
        ("git status --porcelain", ToolResult("exit code: 0\n M x.py")),
        ("git commit", ToolResult("exit code: 0\nok")),
        ("git push", ToolResult("exit code: 1\nerror: failed to push (non-fast-forward)",
                                is_error=True)),
    ], Path("."))
    coder = CoderToolbox(tb)

    def _boom(*a, **k):
        raise AssertionError("must not poll health after a failed push")

    monkeypatch.setattr("httpx.get", _boom)
    r = coder.ci_deploy(summary="x", poll_timeout=120, local=False)

    assert r.is_error
    assert "push" in r.output.lower()
    assert "NOT triggered" in r.output


def test_ci_deploy_timeout_reports_unverified_not_success(monkeypatch) -> None:
    """If the live build_rev never reaches HEAD in the window, report honestly
    (build may still be running) — never claim success."""
    tb = _ScriptedToolbox([
        ("git status --porcelain", ToolResult("exit code: 0\n M x.py")),
        ("git commit", ToolResult("exit code: 0\nok")),
        ("git push", ToolResult("exit code: 0\nmain -> main")),
        ("git rev-parse", ToolResult("exit code: 0\ncafe9999")),
    ], Path("."))
    coder = CoderToolbox(tb)
    monkeypatch.setattr("httpx.get", lambda *a, **k: _Resp("sha 00000000"))  # never matches
    _no_sleep(monkeypatch)
    # Fake the clock so the (clamped ≥120s) deadline expires after a few polls
    # instead of busy-spinning for 2 real minutes.
    tick = {"t": 0.0}

    def _fast_monotonic():
        tick["t"] += 40.0
        return tick["t"]

    monkeypatch.setattr("time.monotonic", _fast_monotonic)

    r = coder.ci_deploy(summary="x", poll_timeout=1, local=False)

    assert r.is_error
    assert "did not reach" in r.output
    assert "DEPLOY LAG" in r.output
    assert "ACTION REQUIRED" in r.output
