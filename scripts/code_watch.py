#!/usr/bin/env python
"""R-F1230 — File-watcher that re-runs verification on save (--watch mode).

Borrowed from deepseek_bash_20260531_29fe99.sh's `--watch` concept.
Monitors the project tree for file changes and re-runs:
  1. pytest on the changed file's test counterpart
  2. self_review (syntax, bare excepts, debug prints, secrets)
  3. ecosystem_audit (wiring gaps, dark modules)

Usage:
    python scripts/code_watch.py                          # watch aria_service/
    python scripts/code_watch.py --path scripts/           # watch scripts/
    python scripts/code_watch.py --path aria_service/intel/ --ext .py
    python scripts/code_watch.py --once                    # single pass, no watch

Exit code: 0 if all checks pass, 1 if any fail.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)
sys.path.insert(0, str(REPO_ROOT))

# ── Configuration ──────────────────────────────────────────────────────

DEFAULT_WATCH_DIR = "aria_service"
WATCH_EXTENSIONS = {".py", ".toml", ".yaml", ".yml", ".json", ".cfg", ".ini"}
DEBOUNCE_SECONDS = 1.0  # wait for file writes to settle
SCAN_INTERVAL = 2.0     # how often to poll for changes

# ── Helpers ────────────────────────────────────────────────────────────


def _get_test_path(src_path: Path) -> Path | None:
    """Map a source file to its test counterpart."""
    try:
        rel = src_path.relative_to(REPO_ROOT)
    except ValueError:
        return None  # file outside repo root
    parts = list(rel.parts)
    # aria_service/foo/bar.py -> aria_service/tests/test_foo_bar.py
    if parts[0] == "aria_service" and src_path.suffix == ".py":
        module_name = "_".join(parts[1:])
        if module_name.endswith(".py"):
            module_name = module_name[:-3]
        test_path = REPO_ROOT / "aria_service" / "tests" / f"test_{module_name}.py"
        if test_path.exists():
            return test_path
        # Try without subdir flattening: aria_service/foo/bar.py -> aria_service/tests/test_bar.py
        test_path = REPO_ROOT / "aria_service" / "tests" / f"test_{parts[-1]}"
        if test_path.exists():
            return test_path
    return None


def _run_pytest(test_path: Path | None = None, timeout: int = 120) -> dict[str, Any]:
    """Run pytest on a specific test file or discover all."""
    cmd = [sys.executable, "-m", "pytest", "-q", "--no-header", "--tb=short"]
    if test_path:
        cmd.append(str(test_path))
    else:
        cmd.append("aria_service/tests/")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(REPO_ROOT),
        )
        out = result.stdout + result.stderr
        passed = out.count("PASSED")
        failed = out.count("FAILED")
        errors = out.count("ERROR")
        return {
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "output": out[-2000:],
            "success": result.returncode == 0,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 0, "errors": 0, "output": "TIMEOUT", "success": False, "returncode": -1}
    except Exception as e:
        return {"passed": 0, "failed": 0, "errors": 0, "output": str(e), "success": False, "returncode": -1}


def _self_review_file(file_path: Path) -> list[dict[str, Any]]:
    """Run self-review checks on a single file."""
    findings: list[dict[str, Any]] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        findings.append({"severity": "error", "check": "read", "message": f"Cannot read: {e}"})
        return findings

    if file_path.suffix != ".py":
        return findings

    # 1. Syntax check
    try:
        ast.parse(content)
    except SyntaxError as e:
        findings.append({"severity": "error", "check": "syntax", "message": f"SyntaxError: {e}"})

    # 2. Bare excepts
    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if stripped == "except:" or stripped.startswith("except :"):
            findings.append({"severity": "warn", "check": "bare_except", "message": f"Line {i}: bare except"})

    # 3. Debug prints
    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("print(") and "logger" not in stripped and "logging" not in stripped:
            findings.append({"severity": "warn", "check": "debug_print", "message": f"Line {i}: debug print"})

    # 4. Hardcoded secrets
    secret_patterns = ["api_key", "api_secret", "password", "token=", "secret="]
    for i, line in enumerate(content.splitlines(), 1):
        lower = line.lower()
        if any(k in lower for k in secret_patterns) and "os.getenv" not in line and "os.environ" not in line:
            findings.append({"severity": "warn", "check": "hardcoded_secret", "message": f"Line {i}: possible hardcoded secret"})

    # 5. Missing type hints on public functions
    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if re.match(r"^async? def [a-zA-Z]", stripped) and "->" not in stripped and not stripped.startswith("def _"):
            findings.append({"severity": "info", "check": "missing_type_hint", "message": f"Line {i}: public function without return type hint"})

    return findings


def _run_ecosystem_audit() -> dict[str, Any]:
    """Run the ecosystem audit script and return summary."""
    try:
        result = subprocess.run(
            [sys.executable, "scripts/ecosystem_audit.py"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(REPO_ROOT),
        )
        return {
            "success": result.returncode == 0,
            "output": result.stdout[-1000:] + result.stderr[-1000:],
            "returncode": result.returncode,
        }
    except Exception as e:
        return {"success": False, "output": str(e), "returncode": -1}


def _format_timestamp() -> str:
    return time.strftime("%H:%M:%S")


def _print_banner(title: str):
    print(f"\n{'=' * 60}")
    print(f"  [{_format_timestamp()}] {title}")
    print(f"{'=' * 60}")


def _print_findings(findings: list[dict[str, Any]]):
    if not findings:
        print("  ✅ No issues found")
        return
    for f in findings:
        icon = {"error": "❌", "warn": "⚠️", "info": "ℹ️"}.get(f["severity"], "•")
        print(f"  {icon} [{f['check']}] {f['message']}")


# ── Main verification pass ────────────────────────────────────────────


def _wire_to_brain(event: str, details: dict[str, Any]):
    """Emit a brain signal on success or failure branch (lazy import)."""
    try:
        from aria_service.intel import brain_hook as _bh
        _bh.observe_self_event(
            source="code_watch",
            event=event,
            details=details,
        )
    except Exception:
        pass  # brain unreachable — don't crash the watcher


def run_verification_pass(changed_files: list[Path] | None = None) -> dict[str, Any]:
    """Run a full verification pass. Returns structured results."""
    results: dict[str, Any] = {
        "timestamp": time.time(),
        "pytest": None,
        "self_review": {},
        "ecosystem_audit": None,
        "overall": True,
    }

    # 1. Self-review on changed files (or all if none specified)
    if changed_files:
        for fp in changed_files:
            findings = _self_review_file(fp)
            if findings:
                try:
                    rel = str(fp.relative_to(REPO_ROOT))
                except ValueError:
                    rel = str(fp)  # file outside repo root (e.g. temp file)
                results["self_review"][rel] = findings
                if any(f["severity"] == "error" for f in findings):
                    results["overall"] = False
    else:
        # Quick scan of recently modified files
        now = time.time()
        for fp in sorted((REPO_ROOT / "aria_service").rglob("*.py")):
            if "__pycache__" in str(fp) or ".venv" in str(fp):
                continue
            try:
                mtime = fp.stat().st_mtime
            except OSError:
                continue
            if now - mtime < 300:  # modified in last 5 minutes
                findings = _self_review_file(fp)
                if findings:
                    results["self_review"][str(fp.relative_to(REPO_ROOT))] = findings
                    if any(f["severity"] == "error" for f in findings):
                        results["overall"] = False

    # 2. Run pytest (targeted if we have a changed file with a test counterpart)
    test_path = None
    if changed_files and len(changed_files) == 1:
        test_path = _get_test_path(changed_files[0])

    pytest_result = _run_pytest(test_path)
    results["pytest"] = pytest_result
    if not pytest_result["success"]:
        results["overall"] = False

    # 3. Ecosystem audit (quick mode — just check wiring)
    audit_result = _run_ecosystem_audit()
    results["ecosystem_audit"] = audit_result
    if not audit_result["success"]:
        results["overall"] = False

    # Wire to brain — both success and failure branches
    _wire_to_brain(
        event="code_watch.verification_pass",
        details={
            "overall": results["overall"],
            "pytest_success": results.get("pytest", {}).get("success"),
            "pytest_passed": results.get("pytest", {}).get("passed", 0),
            "pytest_failed": results.get("pytest", {}).get("failed", 0),
            "self_review_files": len(results.get("self_review", {})),
            "ecosystem_audit_success": results.get("ecosystem_audit", {}).get("success"),
            "changed_files": [
                str(f.relative_to(REPO_ROOT)) if REPO_ROOT in f.parents else str(f)
                for f in (changed_files or [])
            ],
        },
    )

    return results


def print_verification_results(results: dict[str, Any]):
    """Print verification results to console."""
    _print_banner("VERIFICATION RESULTS")

    # Self-review findings
    if results["self_review"]:
        print(f"\n📝 Self-review ({len(results['self_review'])} files with issues):")
        for file_path, findings in results["self_review"].items():
            print(f"\n  --- {file_path} ---")
            _print_findings(findings)
    else:
        print("\n📝 Self-review: ✅ Clean")

    # Pytest results
    pr = results["pytest"]
    if pr:
        status = "✅" if pr["success"] else "❌"
        print(f"\n🧪 Pytest: {status}  ({pr['passed']} passed, {pr['failed']} failed, {pr['errors']} errors)")
        if not pr["success"]:
            # Show last few lines of failure output
            lines = pr["output"].splitlines()
            failure_lines = [l for l in lines if "FAILED" in l or "ERROR" in l or "AssertionError" in l]
            for fl in failure_lines[-5:]:
                print(f"     {fl.strip()}")

    # Ecosystem audit
    ar = results["ecosystem_audit"]
    if ar:
        status = "✅" if ar["success"] else "❌"
        print(f"\n🔍 Ecosystem audit: {status}")

    overall = "✅ PASS" if results["overall"] else "❌ FAIL"
    print(f"\n{'─' * 40}")
    print(f"  OVERALL: {overall}")
    print(f"{'─' * 40}\n")


# ── Watch loop ────────────────────────────────────────────────────────


def watch_loop(watch_dir: Path, extensions: set[str]):
    """Poll for file changes and re-run verification on each change."""
    print(f"\n👁️  Watching: {watch_dir}")
    print(f"   Extensions: {', '.join(sorted(extensions))}")
    print(f"   Scan interval: {SCAN_INTERVAL}s")
    print(f"   Press Ctrl+C to stop\n")

    # Track file modification times
    last_mtimes: dict[Path, float] = {}
    changed_queue: list[Path] = []
    last_scan_time = 0.0

    try:
        while True:
            now = time.time()

            # Scan for changes
            for fp in sorted(watch_dir.rglob("*")):
                if not fp.is_file():
                    continue
                if fp.suffix not in extensions:
                    continue
                if any(p.startswith(".") or p == "__pycache__" for p in fp.parts):
                    continue
                if ".venv" in fp.parts or "node_modules" in fp.parts:
                    continue

                try:
                    mtime = fp.stat().st_mtime
                except OSError:
                    continue

                prev = last_mtimes.get(fp)
                if prev is not None and mtime > prev:
                    # File changed — add to queue with debounce
                    if fp not in changed_queue:
                        changed_queue.append(fp)
                        print(f"  📂 Changed: {fp.relative_to(REPO_ROOT)}")
                last_mtimes[fp] = mtime

            # Process queue with debounce
            if changed_queue and (now - last_scan_time) >= DEBOUNCE_SECONDS:
                files_to_check = list(changed_queue)
                changed_queue.clear()
                last_scan_time = now

                results = run_verification_pass(files_to_check)
                print_verification_results(results)

            time.sleep(SCAN_INTERVAL)

    except KeyboardInterrupt:
        print("\n\n👋 Watch stopped.")


# ── CLI ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="R-F1230 — Code verification watcher & reporter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--path", "-p",
        default=DEFAULT_WATCH_DIR,
        help=f"Directory to watch (default: {DEFAULT_WATCH_DIR})",
    )
    parser.add_argument(
        "--ext", "-e",
        action="append",
        dest="extensions",
        help="File extensions to watch (can repeat, default: .py .toml .yaml .yml .json)",
    )
    parser.add_argument(
        "--once", "-1",
        action="store_true",
        help="Run a single verification pass and exit (no watch loop)",
    )
    parser.add_argument(
        "--file", "-f",
        help="Run verification on a specific file and exit",
    )

    args = parser.parse_args()

    watch_dir = REPO_ROOT / args.path
    if not watch_dir.exists():
        print(f"❌ Directory not found: {watch_dir}")
        sys.exit(1)

    extensions = set(args.extensions) if args.extensions else WATCH_EXTENSIONS

    if args.file:
        # Single file verification
        fp = REPO_ROOT / args.file
        if not fp.exists():
            print(f"❌ File not found: {fp}")
            sys.exit(1)
        _print_banner(f"VERIFYING: {args.file}")
        findings = _self_review_file(fp)
        _print_findings(findings)
        if args.file.endswith(".py"):
            test_path = _get_test_path(fp)
            if test_path:
                print(f"\n🧪 Running tests: {test_path.relative_to(REPO_ROOT)}")
                pr = _run_pytest(test_path)
                status = "✅" if pr["success"] else "❌"
                print(f"   {status}  ({pr['passed']} passed, {pr['failed']} failed)")
        sys.exit(0 if not any(f["severity"] == "error" for f in findings) else 1)

    if args.once:
        results = run_verification_pass()
        print_verification_results(results)
        sys.exit(0 if results["overall"] else 1)

    watch_loop(watch_dir, extensions)


if __name__ == "__main__":
    main()
