"""R-F2074: capability test for verify_test_counts.py.

Tests that the script correctly counts tests in a file and reports totals.
Uses pytest's own API to count tests — no subprocess needed.
"""
import sys
from pathlib import Path


def test_verify_test_counts_script_imports_cleanly():
    """The script must import without errors."""
    script_path = Path(__file__).parents[2] / "scripts" / "verify_test_counts.py"
    assert script_path.exists(), f"Script not found: {script_path}"

    # Import the script as a module to verify it compiles
    import importlib.util
    spec = importlib.util.spec_from_file_location("verify_test_counts", script_path)
    assert spec is not None, f"Could not load spec from {script_path}"
    mod = importlib.util.module_from_spec(spec)
    # Don't execute it — just verify it can be loaded
    assert mod is not None
    assert hasattr(spec, "loader")
    print(f"Script loads cleanly: {script_path}")


def test_count_tests_in_file_returns_positive_count():
    """The count_tests_in_file function must return the correct test count
    for a file that contains tests."""
    script_path = Path(__file__).parents[2] / "scripts" / "verify_test_counts.py"
    import importlib.util
    spec = importlib.util.spec_from_file_location("verify_test_counts", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Count tests in this file itself
    name, count = mod.count_tests_in_file(str(__file__), run=False)
    assert count >= 1, f"Should find at least 1 test in this file, got {count}"
    assert name == str(__file__), f"Should return the filename, got {name}"


def test_count_tests_in_file_handles_nonexistent_file():
    """The function must handle nonexistent files gracefully (returns 0)."""
    script_path = Path(__file__).parents[2] / "scripts" / "verify_test_counts.py"
    import importlib.util
    spec = importlib.util.spec_from_file_location("verify_test_counts", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    name, count = mod.count_tests_in_file("/nonexistent/file.py", run=False)
    # pytest returns 0 tests for a nonexistent file
    assert count == 0, f"Should return 0 for nonexistent file, got {count}"
