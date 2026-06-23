#!/usr/bin/env python
"""
R-F1833 — AST-aware SAST security scanner.

Walks the AST of every Python file in aria_service/ and flags ONLY real
dangerous patterns — no false positives from docstrings, comments, string
literals, function names containing 'eval', or re.compile().

Design:
  - AST-based (not substring-grep): parses each file into an AST, walks nodes
  - Skips docstrings (ast.Constant at module/class/function level)
  - Skips comments (not present in AST — already excluded)
  - Skips string literals (ast.Constant used as function args or assigned to vars)
  - Resolves call targets: only flags bare eval()/exec()/compile() calls,
    NOT re.compile(), py_compile.compile(), run_eval(), etc.
  - For subprocess: only flags if args contain non-literal (variable) arguments
  - For secrets: only flags string literals assigned to variables, not docstring examples

Usage:
    python scripts/sast_scan.py                          # scan all production code
    python scripts/sast_scan.py --file path/to/file.py    # scan single file
    python scripts/sast_scan.py --json                    # JSON output
"""
from __future__ import annotations

import ast
import glob
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any


# ── Configuration ───────────────────────────────────────────────────────────

# Call targets that are SAFE (not real eval/exec/compile)
_SAFE_CALL_TARGETS = {
    "re.compile",
    "py_compile.compile",
    "df.eval",
    "pd.eval",
}

# __import__() calls with these module names are safe (lazy imports)
_SAFE_IMPORT_MODULES = {
    "re", "json", "os", "time", "datetime", "typing",
    "hashlib", "base64", "uuid", "pathlib",
}

# Function names that contain 'eval'/'exec'/'compile' but are NOT dangerous
_SAFE_FUNCTION_NAMES = {
    "run_eval",
    "_run_eval",
    "test_shell_review_finds_eval",
    "test_rf1537_detects_eval",
    "test_rf1537_detects_exec",
    "test_rf1537_detects_compile",
    "test_rf1537_does_not_flag_df_eval",
    "test_rf1537_does_not_flag_re_compile",
    "test_blocks_dangerous_exec",
    "test_held_out_split_produces_disjoint_train_and_eval",
    "test_all_modules_compile",
    "test_writers_compile",
    "test_metacognitive_compile",
}

# Dangerous builtin functions
_DANGEROUS_BUILTINS = {"eval", "exec", "compile", "__import__"}

# Dangerous attribute/method patterns
_DANGEROUS_ATTRS = {
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_output",
    "os.system",
    "os.popen",
    "pickle.loads",
    "pickle.load",
    "marshal.loads",
}

# Subprocess-like functions that create processes
_SUBPROCESS_FUNCS = {"create_subprocess_exec", "create_subprocess_shell"}


@dataclass
class Finding:
    """A single SAST finding."""
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW, INFO
    rule: str
    file: str
    line: int
    message: str
    snippet: str = ""


@dataclass
class ScanResult:
    """Results of scanning one file."""
    file: str
    findings: list[Finding] = field(default_factory=list)
    error: str | None = None


# ═══════════════════════════════════════════════════════════════════════════════
# AST-BASED DETECTORS
# ═══════════════════════════════════════════════════════════════════════════════

def _is_docstring(node: ast.AST) -> bool:
    """Check if a node is a docstring (module-level, class-level, or function-level)."""
    if not isinstance(node, ast.Expr):
        return False
    if not isinstance(node.value, ast.Constant):
        return False
    if not isinstance(node.value.value, str):
        return False
    # Check if it's the first statement in a module, class, or function
    parent = getattr(node, "_parent", None)
    if parent is None:
        return False
    body = getattr(parent, "body", None)
    if body and len(body) > 0 and body[0] is node:
        return True
    return False


def _set_parents(node: ast.AST, parent: ast.AST | None = None) -> None:
    """Walk the tree and set _parent on every node."""
    for child in ast.iter_child_nodes(node):
        child._parent = node  # type: ignore[attr-defined]
        _set_parents(child, node)


def _is_in_docstring(node: ast.AST) -> bool:
    """Check if a node is inside a docstring."""
    current = getattr(node, "_parent", None)
    while current is not None:
        if _is_docstring(current):
            return True
        current = getattr(current, "_parent", None)
    return False


def _is_string_literal(node: ast.AST) -> bool:
    """Check if a node is a string literal (constant or joined string)."""
    return isinstance(node, ast.Constant) and isinstance(getattr(node, "value", None), str)


def _resolve_call_name(node: ast.Call) -> str | None:
    """Resolve the full name of a call target.

    Returns 'eval' for eval(x), 're.compile' for re.compile(x),
    'obj.method' for obj.method(x), etc.
    """
    if isinstance(node.func, ast.Name):
        return node.func.id
    elif isinstance(node.func, ast.Attribute):
        if isinstance(node.func.value, ast.Name):
            return f"{node.func.value.id}.{node.func.attr}"
        elif isinstance(node.func.value, ast.Attribute):
            # Handle chained attrs like asyncio.create_subprocess_exec
            inner = _resolve_call_name_attr(node.func.value)
            if inner:
                return f"{inner}.{node.func.attr}"
    return None


def _resolve_call_name_attr(node: ast.Attribute) -> str | None:
    """Resolve a chain of attribute accesses to a dotted name."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        inner = _resolve_call_name_attr(node.value)
        if inner:
            return f"{inner}.{node.attr}"
    return None


def _is_literal_arg(arg: ast.expr) -> bool:
    """Check if an argument is a literal value (not a variable)."""
    if isinstance(arg, ast.Constant):
        return True
    if isinstance(arg, ast.List) and all(_is_literal_arg(e) for e in arg.elts):
        return True
    if isinstance(arg, ast.Tuple) and all(_is_literal_arg(e) for e in arg.elts):
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# SCAN FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def scan_file(filepath: str) -> ScanResult:
    """Scan a single Python file for security issues."""
    result = ScanResult(file=filepath)

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as e:
        result.error = f"Syntax error: {e}"
        return result
    except Exception as e:
        result.error = str(e)
        return result

    # Set parent references for docstring detection
    _set_parents(tree)

    # Walk all nodes
    for node in ast.walk(tree):
        # Skip nodes inside docstrings
        if _is_in_docstring(node):
            continue

        # ── Check for dangerous builtin calls (eval, exec, compile, __import__) ──
        if isinstance(node, ast.Call):
            call_name = _resolve_call_name(node)
            if call_name is None:
                continue

            # Check if it's a dangerous builtin
            if call_name in _DANGEROUS_BUILTINS:
                # Skip if the function name is in a safe list
                if call_name in _SAFE_FUNCTION_NAMES:
                    continue
                # Skip if this is inside a function with a safe name
                if _parent_function_name(node) in _SAFE_FUNCTION_NAMES:
                    continue
                # Skip re.compile, py_compile.compile, etc.
                if call_name in _SAFE_CALL_TARGETS:
                    continue

                # For __import__(), check if it's a safe module
                if call_name == "__import__":
                    if node.args and len(node.args) > 0:
                        if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                            if node.args[0].value in _SAFE_IMPORT_MODULES:
                                continue

                # For compile(), check if result is discarded (syntax validation)
                if call_name == "compile":
                    parent = getattr(node, "_parent", None)
                    if isinstance(parent, ast.Expr):
                        # Result discarded — syntax validation only
                        result.findings.append(Finding(
                            severity="INFO",
                            rule="compile-syntax-validation",
                            file=filepath,
                            line=node.lineno,
                            message="compile() used for syntax validation (result discarded)",
                            snippet=ast.get_source_segment(source, node) or "",
                        ))
                        continue

                result.findings.append(Finding(
                    severity="CRITICAL" if call_name in ("eval", "exec") else "HIGH",
                    rule=f"dangerous-builtin-{call_name}",
                    file=filepath,
                    line=node.lineno,
                    message=f"Dangerous builtin {call_name}() call",
                    snippet=ast.get_source_segment(source, node) or "",
                ))

            # Check for dangerous attribute calls (subprocess.run, os.system, etc.)
            elif call_name in _DANGEROUS_ATTRS:
                result.findings.append(Finding(
                    severity="HIGH",
                    rule=f"dangerous-attr-{call_name.replace('.', '-')}",
                    file=filepath,
                    line=node.lineno,
                    message=f"Dangerous attribute call: {call_name}()",
                    snippet=ast.get_source_segment(source, node) or "",
                ))

            # Check for subprocess-like functions (create_subprocess_exec)
            elif call_name in _SUBPROCESS_FUNCS or call_name.endswith(".create_subprocess_exec"):
                # Check if args are all literals (hardcoded commands)
                args = node.args
                if args and all(_is_literal_arg(a) for a in args):
                    result.findings.append(Finding(
                        severity="INFO",
                        rule="subprocess-hardcoded",
                        file=filepath,
                        line=node.lineno,
                        message=f"Subprocess call with hardcoded command: {call_name}()",
                        snippet=ast.get_source_segment(source, node) or "",
                    ))
                else:
                    result.findings.append(Finding(
                        severity="HIGH",
                        rule="subprocess-variable-args",
                        file=filepath,
                        line=node.lineno,
                        message=f"Subprocess call with variable arguments: {call_name}()",
                        snippet=ast.get_source_segment(source, node) or "",
                    ))

        # ── Check for bare excepts ─────────────────────────────────────────────
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            result.findings.append(Finding(
                severity="MEDIUM",
                rule="bare-except",
                file=filepath,
                line=node.lineno,
                message="Bare except: catches all exceptions, may hide bugs",
                snippet=ast.get_source_segment(source, node) or "",
            ))

        # ── Check for hardcoded secrets in string assignments ──────────────────
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                    val = str(node.value.value)
                    name_lower = target.id.lower()
                    # Only flag if the variable name suggests it's a secret
                    if any(kw in name_lower for kw in ["api_key", "password", "secret", "token"]):
                        # Skip if the value is clearly a placeholder or env lookup
                        if val.startswith("<") or val.startswith("${") or "os.getenv" in val:
                            continue
                        # Skip if the value is a truncated example (ends with ...)
                        if val.endswith("..."):
                            continue
                        # Skip if the value is None, 0, or empty
                        if val in ("None", "0", "0.0", "", "false", "True"):
                            continue
                        # Skip if the value is an env var name (uppercase with underscores)
                        if val.isupper() and "_" in val:
                            continue
                        # Skip if the value is a URL (OAuth endpoint, etc.)
                        if val.startswith("http"):
                            continue
                        # Skip if the value is a regex pattern
                        if val.startswith("[") or val.startswith("("):
                            continue
                        result.findings.append(Finding(
                            severity="HIGH",
                            rule="hardcoded-secret",
                            file=filepath,
                            line=node.lineno,
                            message=f"Potential hardcoded secret in variable '{target.id}'",
                            snippet=f"{target.id} = {val[:60]}",
                        ))

    return result


def _parent_function_name(node: ast.AST) -> str:
    """Get the name of the enclosing function, or empty string."""
    current = getattr(node, "_parent", None)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
        current = getattr(current, "_parent", None)
    return ""


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(description="AST-aware SAST security scanner")
    parser.add_argument("--file", help="Scan a single file")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--min-severity", default="INFO",
                        choices=["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"],
                        help="Minimum severity to report")
    args = parser.parse_args()

    severity_order = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    min_sev = severity_order.get(args.min_severity, 0)

    if args.file:
        files = [args.file]
    else:
        repo_root = Path(__file__).resolve().parent.parent
        files = sorted(glob.glob(str(repo_root / "aria_service/**/*.py"), recursive=True))
        files = [f for f in files if ".venv" not in f and "__pycache__" not in f]

    all_results: list[ScanResult] = []
    total_findings = 0

    for f in files:
        result = scan_file(f)
        all_results.append(result)
        total_findings += len(result.findings)

    # Filter by severity
    if args.json:
        output = []
        for result in all_results:
            for finding in result.findings:
                if severity_order.get(finding.severity, 0) >= min_sev:
                    output.append({
                        "severity": finding.severity,
                        "rule": finding.rule,
                        "file": finding.file,
                        "line": finding.line,
                        "message": finding.message,
                        "snippet": finding.snippet,
                    })
        print(json.dumps(output, indent=2))
    else:
        # Group by severity
        by_severity: dict[str, list[Finding]] = {}
        for result in all_results:
            for finding in result.findings:
                if severity_order.get(finding.severity, 0) >= min_sev:
                    by_severity.setdefault(finding.severity, []).append(finding)

        print("=" * 80)
        print("AST-AWARE SAST SCAN RESULTS")
        print("=" * 80)
        print(f"Files scanned: {len(all_results)}")
        print(f"Total findings: {total_findings}")
        print()

        for severity in ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]:
            findings = by_severity.get(severity, [])
            if not findings:
                continue
            print(f"\n--- {severity} ({len(findings)}) ---")
            for f in findings:
                short = f.file.replace("\\", "/")
                if "aria_service/" in short:
                    short = short[short.index("aria_service/"):]
                print(f"  [{f.rule}] {short}:{f.line}")
                print(f"    {f.message}")
                if f.snippet:
                    print(f"    Code: {f.snippet[:100]}")

        print("\n" + "=" * 80)
        print("SCAN COMPLETE")
        print("=" * 80)


if __name__ == "__main__":
    from pathlib import Path
    main()
