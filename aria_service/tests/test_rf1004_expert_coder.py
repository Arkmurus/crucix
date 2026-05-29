"""R-F1004 — Tests for ARIA Expert Coder."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestCodeReview:
    """Test the code review engine."""

    def test_review_clean_code(self):
        """Clean code should pass review."""
        from aria_service.intel.expert_coder import CodeReview
        reviewer = CodeReview()
        code = '''def my_function(param1: str) -> dict:
    """My function."""
    result = {"key": "value"}
    return result
'''
        findings = reviewer.review(code)
        critical = [f for f in findings if f["severity"] == "CRITICAL"]
        assert len(critical) == 0

    def test_review_syntax_error(self):
        """Code with syntax errors should be flagged."""
        from aria_service.intel.expert_coder import CodeReview
        reviewer = CodeReview()
        code = "def broken(\n"
        findings = reviewer.review(code)
        assert any(f["rule"] == "syntax_error" for f in findings)

    def test_review_bare_except(self):
        """Bare except should be flagged."""
        from aria_service.intel.expert_coder import CodeReview
        reviewer = CodeReview()
        code = '''def my_func():
    try:
        x = 1
    except:
        pass
'''
        findings = reviewer.review(code)
        assert any(f["rule"] == "bare_except" for f in findings)

    def test_review_missing_docstring(self):
        """Missing docstring should be flagged."""
        from aria_service.intel.expert_coder import CodeReview
        reviewer = CodeReview()
        code = '''def my_func():
    return 1
'''
        findings = reviewer.review(code)
        assert any(f["rule"] == "missing_docstring" for f in findings)

    def test_review_hardcoded_secret(self):
        """Hardcoded secrets should be flagged."""
        from aria_service.intel.expert_coder import CodeReview
        reviewer = CodeReview()
        code = 'api_key = "abcdefghijklmnopqrstuvwxyz"\n'
        findings = reviewer.review(code)
        assert any(f["rule"] == "hardcoded_secret" for f in findings)

    def test_format_findings(self):
        """format_findings should return a readable report."""
        from aria_service.intel.expert_coder import CodeReview
        reviewer = CodeReview()
        findings = [
            {"rule": "bare_except", "severity": "HIGH", "line": 5, "message": "Bare except"},
            {"rule": "missing_docstring", "severity": "MEDIUM", "line": 1, "message": "Missing docstring"},
        ]
        report = reviewer.format_findings(findings)
        assert "Code Review Report" in report
        assert "Bare except" in report
        assert "Missing docstring" in report

    def test_review_file_not_found(self):
        """review_file should handle missing files."""
        from aria_service.intel.expert_coder import CodeReview
        reviewer = CodeReview()
        findings = reviewer.review_file("/nonexistent/file.py")
        assert any(f["rule"] == "file_not_found" for f in findings)


class TestCodeRefactor:
    """Test the code refactoring engine."""

    def test_add_missing_docstring(self):
        """add_missing_docstring should add a docstring."""
        from aria_service.intel.expert_coder import CodeRefactor
        refactor = CodeRefactor()
        code = "def my_func():\n    return 1\n"
        result = refactor.add_missing_docstring(code, "my_func", "My function.")
        assert '"""My function."""' in result

    def test_wrap_in_try_except(self):
        """wrap_in_try_except should wrap function body."""
        from aria_service.intel.expert_coder import CodeRefactor
        refactor = CodeRefactor()
        code = "async def my_func():\n    x = 1\n    return x\n"
        result = refactor.wrap_in_try_except(code, "my_func")
        assert "try:" in result


class TestDebugEngine:
    """Test the debug engine."""

    def test_diagnose_syntax_error(self):
        """SyntaxError should be diagnosed."""
        from aria_service.intel.expert_coder import DebugEngine
        debug = DebugEngine()
        result = debug.diagnose("SyntaxError: invalid syntax (test.py, line 10)")
        assert result["error_type"] == "SyntaxError"
        assert result["fix_type"] == "fix_indentation"
        assert result["confidence"] >= 0.8

    def test_diagnose_name_error(self):
        """NameError should be diagnosed."""
        from aria_service.intel.expert_coder import DebugEngine
        debug = DebugEngine()
        result = debug.diagnose("NameError: name 'x' is not defined")
        assert result["error_type"] == "NameError"
        assert result["fix_type"] == "add_missing_import"

    def test_diagnose_import_error(self):
        """ImportError should be diagnosed."""
        from aria_service.intel.expert_coder import DebugEngine
        debug = DebugEngine()
        result = debug.diagnose("ImportError: No module named 'foo'")
        assert result["error_type"] == "ImportError"

    def test_diagnose_unknown_error(self):
        """Unknown errors should return generic advice."""
        from aria_service.intel.expert_coder import DebugEngine
        debug = DebugEngine()
        result = debug.diagnose("Some random error message")
        assert result["error_type"] == "unknown"
        assert result["confidence"] == 0.0

    def test_diagnose_with_code_snippet(self):
        """Diagnose should extract code snippet around the error line."""
        from aria_service.intel.expert_coder import DebugEngine
        debug = DebugEngine()
        code = "line1\nline2\nline3\nline4\nline5\n"
        result = debug.diagnose("SyntaxError: invalid syntax (test.py, line 3)", code)
        assert result["line"] == 3
        assert ">>>" in result["snippet"]


class TestCodeOptimizer:
    """Test the code optimizer."""

    def test_optimize_bare_except(self):
        """Bare except should be flagged."""
        from aria_service.intel.expert_coder import CodeOptimizer
        optimizer = CodeOptimizer()
        code = "try:\n    x = 1\nexcept:\n    pass\n"
        findings = optimizer.optimize(code)
        assert any("except" in f["message"].lower() for f in findings)

    def test_optimize_clean_code(self):
        """Clean code should have no findings."""
        from aria_service.intel.expert_coder import CodeOptimizer
        optimizer = CodeOptimizer()
        code = "x = 1\ny = 2\n"
        findings = optimizer.optimize(code)
        assert len(findings) == 0


class TestPatternLearner:
    """Test the pattern learner."""

    def test_get_stats(self):
        """get_stats should return pattern statistics."""
        from aria_service.intel.expert_coder import PatternLearner
        learner = PatternLearner()
        stats = learner.get_stats()
        assert "total_patterns" in stats
        assert "categories" in stats
        assert stats["total_patterns"] > 0

    def test_get_best_pattern(self):
        """get_best_pattern should return a pattern for known categories."""
        from aria_service.intel.expert_coder import PatternLearner
        learner = PatternLearner()
        pattern = learner.get_best_pattern("query")
        assert pattern is not None
        assert "name" in pattern
        assert "type" in pattern
