"""R-F1300 — capability test: the deploy tool verifies the live build_rev when
the deploy command errors/times out, instead of reporting a false failure.

This is the fix for the symptom that misled ARIA: a fly/Depot build finishes
server-side after the local deploy command times out, so a flat "timed out" reads
as failure though the deploy LANDED. deploy() now checks aria-intel's live
build_rev against HEAD and reports the truth.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aria_cli.coder_tools import CoderToolbox
from aria_cli.tools import ToolResult


class _FakeToolbox:
    """Minimal Toolbox stand-in: scripts a `run` response per command substring."""

    def __init__(self, responses: dict[str, ToolResult], root: Path) -> None:
        self.root = root
        self._responses = responses
        self.changed_files: list[str] = []

    def run(self, command: str, timeout: int = 300, cwd: str = "") -> ToolResult:
        for key, res in self._responses.items():
            if key in command:
                return res
        return ToolResult(f"exit code: 0\n(unmatched: {command})")

    def _track(self, p) -> None:  # pragma: no cover - unused here
        pass


def _coder(responses: dict[str, ToolResult]) -> CoderToolbox:
    return CoderToolbox(_FakeToolbox(responses, Path(".")))


def test_verify_returns_none_when_target_excludes_intel() -> None:
    """No build_rev to compare for web/wa-only targets."""
    coder = _coder({})
    assert coder._verify_intel_live("--web") is None
    assert coder._verify_intel_live("--wa") is None


def test_verify_landed_when_build_rev_matches_head(monkeypatch) -> None:
    coder = _coder({"git rev-parse": ToolResult("exit code: 0\n74837515")})

    class _Resp:
        def json(self):
            return {"status": "alive", "build_rev": "sha 74837515"}

    monkeypatch.setattr("httpx.get", lambda *a, **k: _Resp())
    out = coder._verify_intel_live("--intel")
    assert out is not None
    landed, note = out
    assert landed is True
    assert "74837515" in note


def test_verify_not_landed_when_build_rev_differs(monkeypatch) -> None:
    coder = _coder({"git rev-parse": ToolResult("exit code: 0\n74837515")})

    class _Resp:
        def json(self):
            return {"build_rev": "sha deadbeef"}

    monkeypatch.setattr("httpx.get", lambda *a, **k: _Resp())
    landed, note = coder._verify_intel_live("--intel")
    assert landed is False
    assert "deadbeef" in note


def test_verify_returns_none_when_health_unreachable(monkeypatch) -> None:
    coder = _coder({"git rev-parse": ToolResult("exit code: 0\n74837515")})

    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("httpx.get", _boom)
    assert coder._verify_intel_live("--intel") is None


def test_deploy_reports_landed_on_timeout_when_live_matches(monkeypatch, tmp_path) -> None:
    """End-to-end: deploy script 'times out' but the live build matches HEAD →
    deploy() reports the deploy LANDED, not a failure."""
    # Make deploy.sh "exist" so the bash branch is taken, and script the run()
    # for the deploy command to a timeout error.
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "deploy.sh").write_text("echo deploy\n")
    responses = {
        "deploy.sh": ToolResult("exit code: -1\nerror: command timed out after 600s",
                                is_error=True),
        "git rev-parse": ToolResult("exit code: 0\n74837515"),
    }
    coder = CoderToolbox(_FakeToolbox(responses, tmp_path))

    class _Resp:
        def json(self):
            return {"build_rev": "sha 74837515"}

    monkeypatch.setattr("httpx.get", lambda *a, **k: _Resp())
    # Force the non-Windows bash branch regardless of host OS.
    monkeypatch.setattr("platform.system", lambda: "Linux")
    result = coder.deploy(target="--intel", timeout=600)
    assert not result.is_error
    assert "LANDED" in result.output
    assert "Do not retry" in result.output
