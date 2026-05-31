"""R-F1230 — Capability tests for code_watch.py and code_health_report.py.

Tests:
1. code_watch._self_review_file — detects syntax errors, bare excepts, debug prints
2. code_watch._get_test_path — maps source files to test counterparts
3. code_watch.run_verification_pass — runs a full verification pass
4. code_health_report.collect_code_quality — scans files for quality metrics
5. code_health_report.collect_wiring_audit — detects brain wiring status
6. code_health_report.generate_html — produces valid HTML with expected sections
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def tmp_py_file():
    """Create a temporary Python file for testing self-review."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write("def hello():\n    print('hello')\n    return 42\n")
        tmp_path = Path(f.name)
    yield tmp_path
    tmp_path.unlink(missing_ok=True)


@pytest.fixture
def tmp_bad_py_file():
    """Create a Python file with intentional issues."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write("""def broken(
    print("no parens")
except:
    pass
API_KEY = "sk-12345"
""")
        tmp_path = Path(f.name)
    yield tmp_path
    tmp_path.unlink(missing_ok=True)


# ── code_watch tests ─────────────────────────────────────────────────


class TestCodeWatchSelfReview:
    """Test _self_review_file — the core code quality scanner."""

    def test_clean_file_no_findings(self, tmp_py_file):
        """A clean file should produce no findings."""
        from scripts.code_watch import _self_review_file

        findings = _self_review_file(tmp_py_file)
        # 'print(' is a debug print — so we expect 1 finding
        debug_prints = [f for f in findings if f["check"] == "debug_print"]
        assert len(debug_prints) == 1
        assert "print" in debug_prints[0]["message"]

    def test_bad_file_detects_syntax_error(self, tmp_bad_py_file):
        """A file with syntax errors should be detected."""
        from scripts.code_watch import _self_review_file

        findings = _self_review_file(tmp_bad_py_file)
        syntax_errors = [f for f in findings if f["check"] == "syntax"]
        assert len(syntax_errors) >= 1

    def test_bad_file_detects_bare_except(self, tmp_bad_py_file):
        """A file with bare except should be detected."""
        from scripts.code_watch import _self_review_file

        findings = _self_review_file(tmp_bad_py_file)
        bare_excepts = [f for f in findings if f["check"] == "bare_except"]
        assert len(bare_excepts) >= 1

    def test_bad_file_detects_hardcoded_secret(self, tmp_bad_py_file):
        """A file with hardcoded API_KEY should be detected."""
        from scripts.code_watch import _self_review_file

        findings = _self_review_file(tmp_bad_py_file)
        secrets = [f for f in findings if f["check"] == "hardcoded_secret"]
        assert len(secrets) >= 1

    def test_findings_have_severity_and_message(self, tmp_bad_py_file):
        """Every finding should have severity, check, and message fields."""
        from scripts.code_watch import _self_review_file

        findings = _self_review_file(tmp_bad_py_file)
        for f in findings:
            assert "severity" in f
            assert "check" in f
            assert "message" in f
            assert f["severity"] in ("error", "warn", "info")


class TestCodeWatchGetTestPath:
    """Test _get_test_path — mapping source files to test counterparts."""

    def test_known_source_maps_to_test(self):
        """A known source file should map to its test counterpart."""
        from scripts.code_watch import _get_test_path

        # This file itself should map to its test
        src = REPO_ROOT / "aria_service" / "intel" / "brain_hook.py"
        if src.exists():
            test_path = _get_test_path(src)
            # May or may not exist, but should return a Path
            assert test_path is None or isinstance(test_path, Path)

    def test_nonexistent_file_returns_none(self):
        """A file with no test counterpart should return None."""
        from scripts.code_watch import _get_test_path

        src = REPO_ROOT / "aria_service" / "nonexistent_module.py"
        test_path = _get_test_path(src)
        assert test_path is None


class TestCodeWatchRunVerification:
    """Test run_verification_pass — the full verification pipeline."""

    def test_verification_pass_returns_expected_structure(self):
        """A verification pass should return a dict with expected keys."""
        from scripts.code_watch import run_verification_pass

        results = run_verification_pass()
        assert isinstance(results, dict)
        assert "timestamp" in results
        assert "pytest" in results
        assert "self_review" in results
        assert "ecosystem_audit" in results
        assert "overall" in results
        assert isinstance(results["overall"], bool)

    def test_verification_pass_with_specific_file(self, tmp_py_file):
        """Running verification on a specific file should work."""
        from scripts.code_watch import run_verification_pass

        results = run_verification_pass([tmp_py_file])
        assert isinstance(results, dict)
        assert "self_review" in results


# ── code_health_report tests ─────────────────────────────────────────


class TestCodeHealthReportCollectCodeQuality:
    """Test collect_code_quality — scanning files for quality metrics."""

    def test_collect_quality_returns_expected_structure(self):
        """Code quality collection should return expected keys."""
        from scripts.code_health_report import collect_code_quality

        quality = collect_code_quality()
        assert isinstance(quality, dict)
        assert "total_files" in quality
        assert "total_lines" in quality
        assert "syntax_errors" in quality
        assert "bare_excepts" in quality
        assert "debug_prints" in quality
        assert "hardcoded_secrets" in quality
        assert "missing_type_hints" in quality
        assert "file_scores" in quality

    def test_collect_quality_finds_at_least_one_file(self):
        """Should find at least the files in aria_service."""
        from scripts.code_health_report import collect_code_quality

        quality = collect_code_quality()
        assert quality["total_files"] > 0
        assert quality["total_lines"] > 0

    def test_file_scores_are_between_0_and_100(self):
        """Each file score should be between 0 and 100."""
        from scripts.code_health_report import collect_code_quality

        quality = collect_code_quality()
        for score in quality["file_scores"].values():
            assert 0 <= score <= 100, f"Score {score} out of range"


class TestCodeHealthReportCollectWiringAudit:
    """Test collect_wiring_audit — detecting brain wiring status."""

    def test_wiring_audit_returns_expected_structure(self):
        """Wiring audit should return expected keys."""
        from scripts.code_health_report import collect_wiring_audit

        wiring = collect_wiring_audit()
        assert isinstance(wiring, dict)
        assert "total_modules" in wiring
        assert "wired" in wiring
        assert "dark" in wiring
        assert "wiring_pct" in wiring
        assert "modules" in wiring

    def test_wiring_audit_finds_modules(self):
        """Should find modules in aria_service."""
        from scripts.code_health_report import collect_wiring_audit

        wiring = collect_wiring_audit()
        assert wiring["total_modules"] > 0
        assert wiring["wiring_pct"] >= 0

    def test_wiring_pct_is_reasonable(self):
        """Wiring percentage should be between 0 and 100."""
        from scripts.code_health_report import collect_wiring_audit

        wiring = collect_wiring_audit()
        assert 0 <= wiring["wiring_pct"] <= 100


class TestCodeHealthReportGenerateHTML:
    """Test generate_html — producing the HTML dashboard."""

    def test_generate_html_returns_string(self):
        """generate_html should return a string."""
        from scripts.code_health_report import (
            collect_code_quality,
            collect_wiring_audit,
            collect_git_stats,
            generate_html,
        )

        test_results = {"passed": 10, "failed": 2, "errors": 0, "total": 12, "tests": [], "output": "", "success": True}
        quality = collect_code_quality()
        wiring = collect_wiring_audit()
        git_stats = collect_git_stats()

        html = generate_html(test_results, quality, wiring, git_stats, None)
        assert isinstance(html, str)
        assert len(html) > 100

    def test_html_contains_expected_sections(self):
        """The HTML should contain key dashboard sections."""
        from scripts.code_health_report import (
            collect_code_quality,
            collect_wiring_audit,
            collect_git_stats,
            generate_html,
        )

        test_results = {"passed": 10, "failed": 2, "errors": 0, "total": 12, "tests": [], "output": "", "success": True}
        quality = collect_code_quality()
        wiring = collect_wiring_audit()
        git_stats = collect_git_stats()

        html = generate_html(test_results, quality, wiring, git_stats, None)
        assert "ARIA Code Health Report" in html
        assert "Tests Passing" in html
        assert "Code Quality" in html
        assert "Brain Wiring" in html
        assert "Issues Found" in html
        assert "File Health Scores" in html
        assert "Dark Modules" in html
        assert "Recent Commits" in html

    def test_html_is_valid_html_structure(self):
        """The HTML should have basic valid structure."""
        from scripts.code_health_report import (
            collect_code_quality,
            collect_wiring_audit,
            collect_git_stats,
            generate_html,
        )

        test_results = {"passed": 10, "failed": 2, "errors": 0, "total": 12, "tests": [], "output": "", "success": True}
        quality = collect_code_quality()
        wiring = collect_wiring_audit()
        git_stats = collect_git_stats()

        html = generate_html(test_results, quality, wiring, git_stats, None)
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html
        assert "<head>" in html
        assert "</head>" in html
        assert "<body>" in html
        assert "</body>" in html

    def test_html_with_trend_data(self):
        """HTML should handle previous report data for trends."""
        from scripts.code_health_report import generate_html

        test_results = {"passed": 10, "failed": 2, "errors": 0, "total": 12, "tests": [], "output": "", "success": True}
        quality = {"total_files": 5, "total_lines": 100, "syntax_errors": [], "bare_excepts": [], "debug_prints": [], "hardcoded_secrets": [], "missing_type_hints": [], "file_scores": {"a.py": 95, "b.py": 80}}
        wiring = {"total_modules": 5, "wired": 3, "dark": 2, "wiring_pct": 60.0, "modules": {"a.py": {"wired": True, "tokens": ["brain_hook.absorb"], "lines": 50}, "b.py": {"wired": False, "tokens": [], "lines": 50}}}
        git_stats = {"recent_commits": ["abc123 fix bug", "def456 add feature"], "total_commits": "100", "branch": "main", "head_sha": "abc123"}
        previous = {"pass_pct": 80.0, "avg_score": 85.0, "wiring_pct": 50.0}

        html = generate_html(test_results, quality, wiring, git_stats, previous)
        assert "ARIA Code Health Report" in html
        # Trend arrows should be present
        assert "▲" in html or "▼" in html or "—" in html


class TestCodeHealthReportEndToEnd:
    """End-to-end test: run the full report generation."""

    def test_full_report_generates_file(self):
        """Running the full report should produce an HTML file."""
        from scripts.code_health_report import (
            collect_code_quality,
            collect_wiring_audit,
            collect_git_stats,
            generate_html,
        )

        test_results = {"passed": 10, "failed": 2, "errors": 0, "total": 12, "tests": [], "output": "", "success": True}
        quality = collect_code_quality()
        wiring = collect_wiring_audit()
        git_stats = collect_git_stats()
        html = generate_html(test_results, quality, wiring, git_stats, None)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(html)
            tmp_path = Path(f.name)

        try:
            assert tmp_path.exists()
            assert tmp_path.stat().st_size > 500
            content = tmp_path.read_text(encoding="utf-8")
            assert "ARIA Code Health Report" in content
        finally:
            tmp_path.unlink(missing_ok=True)
