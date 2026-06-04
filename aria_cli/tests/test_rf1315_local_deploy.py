"""R-F1315 — ci_deploy(local=True, the DEFAULT) deploys to Fly via the local
authed flyctl (scripts/deploy.ps1|deploy.sh), bypassing the dead CI [deploy]
path (stale GitHub FLY_API_TOKEN). This is what makes Aria's commit->deploy
hands-free with no operator step.

Driven with a scripted toolbox (no real push/deploy); the deploy script is
mocked, so we assert on WIRING: that the right apps get the right flags and that
success/failure are reported honestly.
"""
from __future__ import annotations

from pathlib import Path

from aria_cli.coder_tools import CoderToolbox
from aria_cli.tools import ToolResult


class _ScriptedToolbox:
    def __init__(self, responses, root: Path) -> None:
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


def _scripts(tmp_path):
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "deploy.ps1").write_text("# stub", encoding="utf-8")
    (tmp_path / "scripts" / "deploy.sh").write_text("# stub", encoding="utf-8")


def _coder(tmp_path):
    return CoderToolbox(_ScriptedToolbox([], tmp_path))


# ── deploy-target classification (intel + wa/web) ───────────────────────────

def test_deploy_targets_includes_intel_for_service_change(tmp_path):
    c = _coder(tmp_path)
    assert c._deploy_targets(["aria_service/intel/brain_hook.py"]) == {"aria-intel"}


def test_deploy_targets_intel_plus_wa(tmp_path):
    c = _coder(tmp_path)
    got = c._deploy_targets(["aria_service/routes/aria.py",
                             "services/wa-listener/aria_wa_listener.mjs"])
    assert got == {"aria-intel", "aria-wa"}


def test_deploy_targets_empty_for_cli_only(tmp_path):
    """Pure aria_cli/ changes don't run on a Fly app → nothing to deploy."""
    c = _coder(tmp_path)
    assert c._deploy_targets(["aria_cli/bridge.py"]) == set()


# ── end-to-end: default ci_deploy uses local flyctl ─────────────────────────

def test_ci_deploy_local_deploys_intel_via_script(monkeypatch, tmp_path):
    _scripts(tmp_path)
    tb = _ScriptedToolbox([
        ("git status --porcelain", ToolResult("exit code: 0\n M aria_service/intel/brain_hook.py")),
        ("git commit", ToolResult("exit code: 0\n[main abc] deploy")),
        ("git push", ToolResult("exit code: 0\nmain -> main")),
        ("git rev-parse", ToolResult("exit code: 0\nabcd1234")),
        ("git show --name-only", ToolResult("exit code: 0\naria_service/intel/brain_hook.py")),
        ("deploy.ps1", ToolResult("exit code: 0\naria-intel verified live")),
        ("deploy.sh", ToolResult("exit code: 0\naria-intel verified live")),
    ], tmp_path)
    coder = CoderToolbox(tb)

    def _no_http(*a, **k):
        raise AssertionError("local mode must NOT poll the CI health endpoint")
    monkeypatch.setattr("httpx.get", _no_http)

    r = coder.ci_deploy(summary="wire brain_hook", r_number="F1316")  # local=True default

    assert not r.is_error
    assert "DEPLOYED & ALIGNED" in r.output and "local flyctl" in r.output
    # It invoked the deploy script with the -Intel flag, and pushed first.
    assert any("git push origin main" in c for c in tb.commands)
    assert any(("deploy.ps1" in c or "deploy.sh" in c)
               and ("-Intel" in c or "--intel" in c) for c in tb.commands)


def test_ci_deploy_local_reports_failure_honestly(monkeypatch, tmp_path):
    _scripts(tmp_path)
    tb = _ScriptedToolbox([
        ("git status --porcelain", ToolResult("exit code: 0\n M aria_service/intel/brain_hook.py")),
        ("git commit", ToolResult("exit code: 0\nok")),
        ("git push", ToolResult("exit code: 0\nmain -> main")),
        ("git rev-parse", ToolResult("exit code: 0\nabcd1234")),
        ("git show --name-only", ToolResult("exit code: 0\naria_service/intel/brain_hook.py")),
        ("deploy.ps1", ToolResult("exit code: 1\nNOT VERIFIED LIVE", is_error=True)),
        ("deploy.sh", ToolResult("exit code: 1\nNOT VERIFIED LIVE", is_error=True)),
    ], tmp_path)
    coder = CoderToolbox(tb)

    r = coder.ci_deploy(summary="x", r_number="F1316")

    assert r.is_error
    assert "DEPLOY FAILED" in r.output
    assert "F1316" in r.output  # forbids ship-marking


def test_ci_deploy_local_noop_for_cli_only_change(monkeypatch, tmp_path):
    """A pushed CLI-only change deploys nothing but is reported honestly."""
    _scripts(tmp_path)
    tb = _ScriptedToolbox([
        ("git status --porcelain", ToolResult("exit code: 0\n M aria_cli/bridge.py")),
        ("git commit", ToolResult("exit code: 0\nok")),
        ("git push", ToolResult("exit code: 0\nmain -> main")),
        ("git rev-parse", ToolResult("exit code: 0\nabcd1234")),
        ("git show --name-only", ToolResult("exit code: 0\naria_cli/bridge.py")),
    ], tmp_path)
    coder = CoderToolbox(tb)

    r = coder.ci_deploy(summary="bridge fix")

    assert not r.is_error
    assert "nothing to deploy" in r.output
    assert not any("deploy.ps1" in c or "deploy.sh" in c for c in tb.commands)
