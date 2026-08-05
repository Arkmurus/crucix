"""
R-F1550 capability test: prove Eagle Eye guardian is wired and functional.

Drives the REAL entry points:
1. eagle_eye.start() — wired into lifespan
2. eagle_eye.scan_once() — actual scan against a temp project
3. eagle_eye.get_report() — returns structured results
"""
import tempfile
from pathlib import Path

import pytest

# R-F3754/§16 — NOT inspect.getsource: it slices at the line numbers captured
# AT IMPORT, so an edit mid-run returns a DIFFERENT function's body, silently.
from ._source_probe import function_source


@pytest.mark.asyncio
async def test_eagle_eye_start_stop():
    """Eagle Eye start/stop must not raise."""
    from aria_service.intel import eagle_eye

    # Start with a temp project root
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        await eagle_eye.start(project_root)
        await eagle_eye.stop()
    # No assertion needed — if no exception, it worked


@pytest.mark.asyncio
async def test_eagle_eye_scan_detects_syntax_error():
    """A file with a syntax error must be detected."""
    from aria_service.intel import eagle_eye

    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        # Create a file with a syntax error
        bad_file = project_root / "bad_syntax.py"
        bad_file.write_text("def foo():\n    return {\n")  # missing closing brace

        guardian = eagle_eye.EagleEyeGuardian(project_root)
        report = await guardian.scan_once()

        assert report["metrics"]["trouble_spotted"] >= 1, (
            "Should detect at least 1 trouble spot (syntax error)"
        )
        # Check that the syntax error was found
        syntax_spots = [t for t in guardian.trouble_spots if t.type == "syntax"]
        assert len(syntax_spots) >= 1, (
            "Should have at least 1 syntax trouble spot"
        )
        assert "bad_syntax.py" in syntax_spots[0].file_path, (
            f"Syntax error should be in bad_syntax.py, got {syntax_spots[0].file_path}"
        )


@pytest.mark.asyncio
async def test_eagle_eye_scan_detects_security_issue():
    """A file with eval() must be detected as a security issue."""
    from aria_service.intel import eagle_eye

    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        bad_file = project_root / "unsafe.py"
        bad_file.write_text("result = eval(user_input)\n")

        guardian = eagle_eye.EagleEyeGuardian(project_root)
        report = await guardian.scan_once()

        security_spots = [t for t in guardian.trouble_spots if t.type == "security"]
        assert len(security_spots) >= 1, (
            "Should detect eval() as a security issue"
        )
        assert "eval" in security_spots[0].description.lower(), (
            f"Security issue should mention eval, got {security_spots[0].description}"
        )


@pytest.mark.asyncio
async def test_eagle_eye_scan_detects_nested_loop():
    """A file with nested loops must be detected as a performance issue."""
    from aria_service.intel import eagle_eye

    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        bad_file = project_root / "slow.py"
        bad_file.write_text(
            "def process(items):\n"
            "    for i in items:\n"
            "        for j in items:\n"
            "            print(i, j)\n"
        )

        guardian = eagle_eye.EagleEyeGuardian(project_root)
        report = await guardian.scan_once()

        perf_spots = [t for t in guardian.trouble_spots if t.type == "performance"]
        assert len(perf_spots) >= 1, (
            "Should detect nested loop as a performance issue"
        )
        assert "nested loop" in perf_spots[0].description.lower(), (
            f"Performance issue should mention nested loop, got {perf_spots[0].description}"
        )


@pytest.mark.asyncio
async def test_eagle_eye_get_report():
    """get_report must return a structured dict."""
    from aria_service.intel import eagle_eye

    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        guardian = eagle_eye.EagleEyeGuardian(project_root)
        report = guardian.get_report()

        assert "metrics" in report, "Report must have metrics"
        assert "active_trouble_spots" in report, "Report must have active_trouble_spots"
        assert "code_smells" in report, "Report must have code_smells"
        assert "high_severity_issues" in report, "Report must have high_severity_issues"
        assert "top_issues" in report, "Report must have top_issues"


@pytest.mark.asyncio
async def test_eagle_eye_disabled_via_env(monkeypatch):
    """When ARIA_EAGLE_EYE_ENABLED=0, start must not create a guardian."""
    from aria_service.intel import eagle_eye

    monkeypatch.setenv("ARIA_EAGLE_EYE_ENABLED", "0")
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        await eagle_eye.start(project_root)
        # Guardian should not be active
        report = eagle_eye.get_report()
        assert "active" not in report or report.get("active") is False, (
            "Eagle Eye should not be active when disabled"
        )
        await eagle_eye.stop()


@pytest.mark.asyncio
async def test_eagle_eye_wired_in_lifespan():
    """Eagle Eye start/stop must be referenced in main.py lifespan."""
    from aria_service import main

    import inspect
    source = function_source(main, "lifespan")
    assert "eagle_eye.start" in source or "eagle_eye" in source, (
        "lifespan must call eagle_eye.start()"
    )
    assert "eagle_eye.stop" in source or "eagle_eye" in source, (
        "lifespan must call eagle_eye.stop()"
    )
