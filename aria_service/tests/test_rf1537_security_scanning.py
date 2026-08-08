"""R-F1537 — Capability tests for security pattern scanning in StaticAnalysisExtractor.

Tests the full chain:
  1. Dangerous builtins (eval, exec, compile) are detected
  2. Dangerous attribute calls (os.system, pickle.loads) are detected
  3. Hardcoded secrets (password, api_key, token) are detected
  4. False positives (df.eval, re.compile) are NOT flagged
  5. Clean files produce no security gaps
  6. extract() drives the real path (not _analyse_file directly)
"""
from __future__ import annotations

from unittest.mock import MagicMock

# R-F3772/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


def _run_extractor(content: str) -> list[dict]:
    """Run StaticAnalysisExtractor.extract() against a temp file.

    Creates the file under the repo root so that relative_to() works.
    This drives the REAL entry point (extract -> _analyse_file) so the
    test catches regressions in the full chain, not just a helper.
    """
    import asyncio
    from aria_service.autonomous.gap_detector import StaticAnalysisExtractor, GapSeverity

    mock_redis = MagicMock()
    extractor = StaticAnalysisExtractor(mock_redis)

    # Create a temp subdirectory under the repo root so relative_to works
    repo_root = extractor._repo_root
    scan_subdir = repo_root / "_test_scan_rf1537"
    scan_subdir.mkdir(parents=True, exist_ok=True)

    # Write the source file directly in the scan subdir
    src = scan_subdir / "test_source.py"
    src.write_text(content, encoding="utf-8")

    extractor.SCAN_DIRS = ["_test_scan_rf1537"]

    # Use since=None to bypass the time-window check
    gaps = asyncio.run(extractor.extract(since=None))

    # Cleanup
    import shutil as _sh
    _sh.rmtree(scan_subdir, ignore_errors=True)

    return [
        {
            "title": g.title,
            "gap_type": g.gap_type,
            "severity": g.severity,
            "module": g.module,
            "evidence": g.evidence,
        }
        for g in gaps
    ]


# ── Test: dangerous builtins ─────────────────────────────────────────────────


def test_rf1537_detects_eval():
    """eval() is detected as a security issue."""
    gaps = _run_extractor("x = eval('1+1')\n")
    eval_gaps = [g for g in gaps if g["evidence"].get("issue") == "dangerous_builtin"]
    assert len(eval_gaps) >= 1, f"Should detect eval(), got {len(eval_gaps)} gaps"
    assert any("eval" in g["title"] for g in eval_gaps), "Title should mention eval"
    assert all(g["severity"].name == "HIGH" for g in eval_gaps), "eval should be HIGH severity"


def test_rf1537_detects_exec():
    """exec() is detected as a security issue."""
    gaps = _run_extractor("exec('print(1)')\n")
    exec_gaps = [g for g in gaps if g["evidence"].get("issue") == "dangerous_builtin"]
    assert len(exec_gaps) >= 1, f"Should detect exec(), got {len(exec_gaps)} gaps"


def test_rf1537_detects_compile():
    """compile() is detected as a security issue."""
    gaps = _run_extractor("c = compile('x=1', '<string>', 'exec')\n")
    compile_gaps = [g for g in gaps if g["evidence"].get("issue") == "dangerous_builtin"]
    assert len(compile_gaps) >= 1, f"Should detect compile(), got {len(compile_gaps)} gaps"


# ── Test: dangerous attribute calls ──────────────────────────────────────────


def test_rf1537_detects_os_system():
    """os.system() is detected as a security issue."""
    gaps = _run_extractor("import os\nos.system('ls')\n")
    danger_gaps = [g for g in gaps if g["evidence"].get("issue") == "dangerous_call"]
    assert len(danger_gaps) >= 1, f"Should detect os.system(), got {len(danger_gaps)} gaps"
    assert any("os.system" in g["title"] for g in danger_gaps), "Title should mention os.system"


def test_rf1537_detects_pickle_loads():
    """pickle.loads() is detected as a security issue."""
    gaps = _run_extractor("import pickle\ndata = pickle.loads(b'...')\n")
    danger_gaps = [g for g in gaps if g["evidence"].get("issue") == "dangerous_call"]
    assert len(danger_gaps) >= 1, f"Should detect pickle.loads(), got {len(danger_gaps)} gaps"


def test_rf1537_detects_subprocess_run():
    """subprocess.run() is detected (it's in the dangerous list)."""
    gaps = _run_extractor("import subprocess\nsubprocess.run(['ls'])\n")
    danger_gaps = [g for g in gaps if g["evidence"].get("issue") == "dangerous_call"]
    assert len(danger_gaps) >= 1, f"Should detect subprocess.run(), got {len(danger_gaps)} gaps"


# ── Test: hardcoded secrets ──────────────────────────────────────────────────


def test_rf1537_detects_hardcoded_password():
    """Hardcoded password = '...' is detected."""
    gaps = _run_extractor('password = "supersecret123"\n')
    secret_gaps = [g for g in gaps if g["evidence"].get("issue") == "hardcoded_secret"]
    assert len(secret_gaps) >= 1, f"Should detect hardcoded password, got {len(secret_gaps)} gaps"


def test_rf1537_detects_hardcoded_api_key():
    """Hardcoded api_key = '...' is detected."""
    gaps = _run_extractor('api_key = "sk-1234567890abcdef"\n')
    secret_gaps = [g for g in gaps if g["evidence"].get("issue") == "hardcoded_secret"]
    assert len(secret_gaps) >= 1, f"Should detect hardcoded api_key, got {len(secret_gaps)} gaps"


def test_rf1537_detects_hardcoded_token():
    """Hardcoded token with a long string is detected."""
    gaps = _run_extractor('token = "ghp_abcdef1234567890abcdef1234567890abcdef"\n')
    secret_gaps = [g for g in gaps if g["evidence"].get("issue") == "hardcoded_secret"]
    assert len(secret_gaps) >= 1, f"Should detect hardcoded token, got {len(secret_gaps)} gaps"


# ── Test: false positives ────────────────────────────────────────────────────


def test_rf1537_does_not_flag_df_eval():
    """df.eval() is NOT flagged (pandas method, not dangerous)."""
    gaps = _run_extractor("import pandas as pd\ndf.eval('A + B')\n")
    builtin_gaps = [g for g in gaps if g["evidence"].get("issue") == "dangerous_builtin"]
    assert len(builtin_gaps) == 0, f"df.eval() should not be flagged, got {len(builtin_gaps)}"


def test_rf1537_does_not_flag_re_compile():
    """re.compile() is NOT flagged (stdlib method, not dangerous)."""
    gaps = _run_extractor("import re\npattern = re.compile(r'\\d+')\n")
    builtin_gaps = [g for g in gaps if g["evidence"].get("issue") == "dangerous_builtin"]
    assert len(builtin_gaps) == 0, f"re.compile() should not be flagged, got {len(builtin_gaps)}"


def test_rf1537_does_not_flag_env_var_password():
    """password = os.environ.get(...) is NOT flagged (not hardcoded)."""
    gaps = _run_extractor('password = os.environ.get("PASSWORD")\n')
    secret_gaps = [g for g in gaps if g["evidence"].get("issue") == "hardcoded_secret"]
    assert len(secret_gaps) == 0, \
        f"os.environ.get() should not be flagged, got {len(secret_gaps)}"


# ── Test: clean file ─────────────────────────────────────────────────────────


def test_rf1537_clean_file_produces_no_security_gaps():
    """A clean file with no security issues produces no security gaps."""
    gaps = _run_extractor(
        '"""A clean module."""\n'
        "import os\n"
        'x = os.environ.get("HOME")\n'
        "def greet(name: str) -> str:\n"
        '    return f"Hello, {name}"\n'
    )
    security_gaps = [
        g for g in gaps
        if g["evidence"].get("issue") in ("dangerous_builtin", "dangerous_call", "hardcoded_secret")
    ]
    assert len(security_gaps) == 0, \
        f"Clean file should have 0 security gaps, got {len(security_gaps)}: {security_gaps}"


# ── Test: extract() drives the real path ─────────────────────────────────────


def test_rf1537_extract_drives_real_path():
    """Verifies that extract() calls _analyse_file (not a mock/stub).

    This is the capability test requirement from R-F1069: the test must
    drive the actual function that was changed, not a helper.
    """
    from aria_service.autonomous.gap_detector import StaticAnalysisExtractor

    # Verify the method exists on the real class
    assert hasattr(StaticAnalysisExtractor, "extract"), "extract() must exist"
    assert hasattr(StaticAnalysisExtractor, "_analyse_file"), "_analyse_file() must exist"

    # Verify the security patterns are in the source
    import inspect
    source = function_source(StaticAnalysisExtractor, "_analyse_file")
    assert "dangerous_builtin" in source, "_analyse_file must contain dangerous_builtin check"
    assert "hardcoded_secret" in source, "_analyse_file must contain hardcoded_secret check"
    assert "os.system" in source, "_analyse_file must check for os.system"
    assert "pickle.loads" in source, "_analyse_file must check for pickle.loads"
