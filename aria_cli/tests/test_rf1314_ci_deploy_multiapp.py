"""R-F1314 — capability tests: ci_deploy must not claim a full deploy when the
change also touches aria-wa / aria-web (apps the CI [deploy] path does NOT build).

Root incident (2026-06-04): R-F1311 changed services/wa-listener/aria_wa_listener.mjs
(runs on aria-wa) plus aria-intel routes. ci_deploy verified aria-intel's build_rev,
reported "DEPLOYED & ALIGNED", and the WA listener was never deployed — WhatsApp
OCR/document reading broke. These tests drive the real method with a scripted
toolbox + mocked httpx (no real push/deploy).
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


class _Resp:
    def __init__(self, build_rev: str) -> None:
        self._b = build_rev

    def json(self):
        return {"status": "alive", "build_rev": self._b}


def _coder(tmp_path) -> CoderToolbox:
    return CoderToolbox(_ScriptedToolbox([], tmp_path))


# ── the path→app classifier (the core of the fix) ───────────────────────────

def test_apps_touched_wa_listener(tmp_path):
    c = _coder(tmp_path)
    assert c._apps_touched(["services/wa-listener/aria_wa_listener.mjs"]) == {"aria-wa"}


def test_apps_touched_web_tier(tmp_path):
    c = _coder(tmp_path)
    assert c._apps_touched(["server.mjs", "frontend/app.tsx"]) == {"aria-web"}


def test_apps_touched_intel_only_is_empty(tmp_path):
    """A pure aria-intel change must NOT trip the multi-app warning."""
    c = _coder(tmp_path)
    assert c._apps_touched(["aria_service/routes/aria.py",
                            "aria_service/intel/ocr.py"]) == set()


def test_apps_touched_mixed_and_pathsep_normalised(tmp_path):
    c = _coder(tmp_path)
    got = c._apps_touched([
        r"services\wa-listener\aria_wa_listener.mjs",  # backslashes
        "aria_service/routes/aria.py",
        "fly.web.toml",
    ])
    assert got == {"aria-wa", "aria-web"}


# ── end-to-end: ci_deploy must downgrade the success verdict ─────────────────

def _write_deploy_scripts(tmp_path):
    """ci_deploy's _deploy_apps requires the platform deploy script to exist."""
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "deploy.ps1").write_text("# stub", encoding="utf-8")
    (tmp_path / "scripts" / "deploy.sh").write_text("# stub", encoding="utf-8")


def test_ci_deploy_auto_deploys_touched_wa_app(monkeypatch, tmp_path):
    """aria-intel aligns AND the wa-listener change auto-deploys via the script →
    full multi-app success; the deploy script is actually invoked for aria-wa."""
    _write_deploy_scripts(tmp_path)
    tb = _ScriptedToolbox([
        ("git status --porcelain", ToolResult("exit code: 0\n M services/wa-listener/aria_wa_listener.mjs")),
        ("git commit", ToolResult("exit code: 0\n[main abc] deploy")),
        ("git push", ToolResult("exit code: 0\nmain -> main")),
        ("git rev-parse", ToolResult("exit code: 0\nabcd1234")),
        ("git show --name-only", ToolResult("exit code: 0\nservices/wa-listener/aria_wa_listener.mjs")),
        ("deploy.ps1", ToolResult("exit code: 0\naria-wa verified")),
        ("deploy.sh", ToolResult("exit code: 0\naria-wa verified")),
    ], tmp_path)
    coder = CoderToolbox(tb)
    monkeypatch.setattr("httpx.get", lambda *a, **k: _Resp("sha abcd1234"))
    monkeypatch.setattr("time.sleep", lambda s: None)

    r = coder.ci_deploy(summary="async OCR", r_number="F1311", poll_timeout=120, local=False)

    assert not r.is_error
    assert "multi-app" in r.output and "aria-wa" in r.output
    # The deploy script must have actually been invoked with the aria-wa flag.
    assert any(("deploy.ps1" in c or "deploy.sh" in c)
               and ("-Wa" in c or "--wa" in c) for c in tb.commands)


def test_ci_deploy_partial_when_followon_deploy_fails(monkeypatch, tmp_path):
    """If the touched-app deploy FAILS, the whole thing is PARTIAL — must error,
    name aria-wa, and forbid ship-marking the R-number."""
    _write_deploy_scripts(tmp_path)
    tb = _ScriptedToolbox([
        ("git status --porcelain", ToolResult("exit code: 0\n M services/wa-listener/aria_wa_listener.mjs")),
        ("git commit", ToolResult("exit code: 0\n[main abc] deploy")),
        ("git push", ToolResult("exit code: 0\nmain -> main")),
        ("git rev-parse", ToolResult("exit code: 0\nabcd1234")),
        ("git show --name-only", ToolResult("exit code: 0\nservices/wa-listener/aria_wa_listener.mjs")),
        ("deploy.ps1", ToolResult("exit code: 1\nflyctl: build failed", is_error=True)),
        ("deploy.sh", ToolResult("exit code: 1\nflyctl: build failed", is_error=True)),
    ], tmp_path)
    coder = CoderToolbox(tb)
    monkeypatch.setattr("httpx.get", lambda *a, **k: _Resp("sha abcd1234"))
    monkeypatch.setattr("time.sleep", lambda s: None)

    r = coder.ci_deploy(summary="async OCR", r_number="F1311", poll_timeout=120, local=False)

    assert r.is_error, "a failed follow-on deploy must not be reported as success"
    assert "PARTIAL DEPLOY" in r.output
    assert "aria-wa" in r.output
    assert "F1311" in r.output


def test_ci_deploy_warns_when_deploy_all_disabled(monkeypatch, tmp_path):
    """deploy_all=False keeps the old behaviour: warn, don't auto-deploy."""
    tb = _ScriptedToolbox([
        ("git status --porcelain", ToolResult("exit code: 0\n M services/wa-listener/aria_wa_listener.mjs")),
        ("git commit", ToolResult("exit code: 0\n[main abc] deploy")),
        ("git push", ToolResult("exit code: 0\nmain -> main")),
        ("git rev-parse", ToolResult("exit code: 0\nabcd1234")),
        ("git show --name-only", ToolResult("exit code: 0\nservices/wa-listener/aria_wa_listener.mjs")),
    ], tmp_path)
    coder = CoderToolbox(tb)
    monkeypatch.setattr("httpx.get", lambda *a, **k: _Resp("sha abcd1234"))
    monkeypatch.setattr("time.sleep", lambda s: None)

    r = coder.ci_deploy(summary="x", r_number="F1311", poll_timeout=120, deploy_all=False, local=False)

    assert r.is_error
    assert "PARTIAL DEPLOY" in r.output
    assert not any("deploy.ps1" in c or "deploy.sh" in c for c in tb.commands)


def test_ci_deploy_full_success_when_intel_only(monkeypatch, tmp_path):
    """A pure aria-intel change still gets the clean DEPLOYED & ALIGNED verdict."""
    tb = _ScriptedToolbox([
        ("git status --porcelain", ToolResult("exit code: 0\n M aria_service/routes/aria.py")),
        ("git commit", ToolResult("exit code: 0\n[main abc] deploy")),
        ("git push", ToolResult("exit code: 0\nmain -> main")),
        ("git rev-parse", ToolResult("exit code: 0\nabcd1234")),
        ("git show --name-only", ToolResult("exit code: 0\naria_service/routes/aria.py")),
    ], tmp_path)
    coder = CoderToolbox(tb)
    monkeypatch.setattr("httpx.get", lambda *a, **k: _Resp("sha abcd1234"))
    monkeypatch.setattr("time.sleep", lambda s: None)

    r = coder.ci_deploy(summary="route fix", r_number="F9000", poll_timeout=120, local=False)

    assert not r.is_error
    assert "DEPLOYED & ALIGNED" in r.output
