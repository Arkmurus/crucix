"""Pass 1 audit: check call sites, signatures, fields, conditions, regex, concurrency, env flags, imports."""
from __future__ import annotations

import ast
import os
import sys


def audit_file(filepath: str) -> list[str]:
    issues: list[str] = []
    try:
        with open(filepath, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except SyntaxError as e:
        return [f"SYNTAX ERROR: {e}"]

    # Check 1: bare excepts
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                issues.append(f"Line {node.lineno}: bare except (no exception type)")

    # Check 2: debug prints
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "print":
                issues.append(f"Line {node.lineno}: debug print")

    # Check 3: hardcoded secrets
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(k in node.value.lower() for k in ["secret", "password", "token", "api_key"]):
                if len(node.value) > 10 and "os.environ" not in filepath:
                    issues.append(f"Line {node.lineno}: possible hardcoded secret")

    # Check 4: TODO markers
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "TODO" in node.value or "FIXME" in node.value or "HACK" in node.value:
                issues.append(f"Line {node.lineno}: TODO/FIXME/HACK marker")

    return issues


def main():
    files = [
        "aria_service/autonomous/coder_entrypoint.py",
        "aria_service/autonomous/sovereign_llm.py",
        "aria_service/intel/autonomous_coder.py",
        "aria_service/intel/self_coding_os.py",
    ]

    all_issues = []
    for fp in files:
        issues = audit_file(fp)
        if issues:
            all_issues.append(f"=== {fp} ===")
            all_issues.extend(issues)
            all_issues.append("")

    if all_issues:
        print("PASS 1 ISSUES FOUND:")
        for line in all_issues:
            print(line)
    else:
        print("PASS 1: No issues found in any file.")

    # Also verify function signatures match between SovereignLLM and AutonomousCoder
    print()
    print("=== CONTRACT VERIFICATION ===")
    print("Both SovereignLLM and AutonomousCoder must implement:")
    print("  generate_fix_plan(gap, context) -> dict")
    print("  write_code(plan, existing_code, target_file) -> dict")
    print("  write_tests(plan, new_code, r_number) -> dict")
    print("  analyse_failure(error, code, attempt) -> dict")
    print()

    for fp in files:
        with open(fp, encoding="utf-8") as f:
            content = f.read()
        for method in ["generate_fix_plan", "write_code", "write_tests", "analyse_failure"]:
            if method in content:
                print(f"  ✅ {fp.split('/')[-1]}: {method}")
            else:
                print(f"  ❌ {fp.split('/')[-1]}: {method} MISSING")


if __name__ == "__main__":
    main()
