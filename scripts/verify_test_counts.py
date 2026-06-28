#!/usr/bin/env python3
"""R-F2074: test-count verifier — ground truth for per-file test counts.

Usage:
    python scripts/verify_test_counts.py <file1.py> [file2.py ...]

    If no files given, reads from stdin (one file per line).

Output:
    For each file: the exact test count via pytest collection.
    Final line: total across all files.

Purpose:
    Prevents the "per-file test count hallucination" failure class
    (R-F2071 post-mortem): when I claimed 18 internal search tests
    but the actual count was 12, and 22 active engine tests when
    actual was 40. The total (94) was correct but the per-file
    breakdown was asserted from memory.

    Anti-hallucination law #17: every status claim cites its live source.
    This script IS the live source for per-file test counts.

    Run this before any sign-off that includes per-file test counts.
    Pipe the output directly into the commit message or report.

Example:
    python scripts/verify_test_counts.py ^
        aria_service/tests/test_search_dd_capability.py ^
        aria_service/tests/test_rf1660_search_sovereignty_guard.py

    # Or pipe from a file list:
    cat test_files.txt | python scripts/verify_test_counts.py
"""
from __future__ import annotations

import sys
from pathlib import Path


def count_tests_in_file(filepath: str, run: bool = False) -> tuple[str, int]:
    """Return (filename, test_count) by collecting tests via pytest API.

    If run=True, actually executes the tests and returns (filename, passed_count).
    """
    try:
        import pytest
        import io
        from contextlib import redirect_stdout, redirect_stderr

        class _CountPlugin:
            def __init__(self):
                self.count = 0
                self.passed = 0
                self.failed = 0
            def pytest_collection_modifyitems(self, items):
                self.count = len(items)

        plugin = _CountPlugin()
        f_out = io.StringIO()
        f_err = io.StringIO()
        args = ["-q", filepath, "-p", "no:cacheprovider", "--tb=no"]
        if not run:
            args.insert(0, "--collect-only")
        with redirect_stdout(f_out), redirect_stderr(f_err):
            exit_code = pytest.main(args, plugins=[plugin])
        if exit_code == 5:  # no tests collected
            return (filepath, 0)
        if run:
            # Parse the last line: "N passed in X.XXs" or "N failed in ..."
            output = f_out.getvalue()
            last_line = [l for l in output.split("\n") if l.strip()][-1] if output.strip() else ""
            if "passed" in last_line:
                passed = int(last_line.split()[0])
                return (filepath, passed)
            elif "failed" in last_line:
                return (filepath, 0)  # 0 passed
            return (filepath, plugin.count)  # fallback
        return (filepath, plugin.count)
    except Exception as e:
        return (filepath, -2)


def main() -> None:
    run_tests = "--run" in sys.argv
    if run_tests:
        sys.argv.remove("--run")
    if len(sys.argv) > 1:
        files = sys.argv[1:]
    else:
        files = [l.strip() for l in sys.stdin if l.strip()]

    if not files:
        print("Usage: python scripts/verify_test_counts.py <file1.py> [file2.py ...]")
        print("   or: cat file_list.txt | python scripts/verify_test_counts.py")
        sys.exit(1)

    results: list[tuple[str, int]] = []
    for f in files:
        path = Path(f)
        if not path.exists():
            print(f"  {f}: FILE NOT FOUND", file=sys.stderr)
            results.append((f, -3))
            continue
        name, count = count_tests_in_file(f, run=run_tests)
        results.append((name, count))

    # Print per-file counts
    total = 0
    for name, count in results:
        if count >= 0:
            print(f"  {count:4d}  {name}")
            total += count
        elif count == -2:
            print(f"  ERR   {name} (exception)")
        elif count == -3:
            print(f"  MISS  {name} (not found)")

    print(f"  ----")
    print(f"  {total:4d}  TOTAL")


if __name__ == "__main__":
    main()
