"""R-F4147 guard for deploy health-check interpreter selection."""
from pathlib import Path


def test_windows_deploy_uses_project_python_for_live_health_suite() -> None:
    source = Path("scripts/deploy.ps1").read_text(encoding="utf-8")

    assert 'Join-Path $REPO_ROOT ".venv\\Scripts\\python.exe"' in source
    assert "Test-Path -LiteralPath $healthPython" in source
    assert 'Invoke-Native { & $healthPython "$REPO_ROOT/scripts/live_health_check.py"' in source
    assert 'Invoke-Native { python "$REPO_ROOT/scripts/live_health_check.py"' not in source
