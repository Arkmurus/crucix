"""R-F4155 capability tests for language-aware PowerShell safety checks."""
from pathlib import Path

from scripts.pre_commit_checks import check_powershell_safety


def test_bash_runner_keeps_valid_bash_commands(tmp_path: Path) -> None:
    runner = tmp_path / "pod_runner.sh"
    runner.write_text(
        "#!/usr/bin/env bash\ncurl -fsS https://example.test && echo ready\n",
        encoding="utf-8",
    )

    assert check_powershell_safety([runner]) == []


def test_powershell_script_still_rejects_bash_only_commands(tmp_path: Path) -> None:
    script = tmp_path / "deploy.ps1"
    script.write_text(
        "curl -fsS https://example.test && Write-Output ready\n",
        encoding="utf-8",
    )

    issues = check_powershell_safety([script])
    assert any("bare curl" in issue for issue in issues)
    assert any("double-ampersand" in issue for issue in issues)
