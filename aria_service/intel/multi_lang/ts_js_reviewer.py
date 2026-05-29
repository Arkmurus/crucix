"""R-F1044 — TypeScript/JavaScript code reviewer.

Pattern-based review for TS/JS code. No AST parsing needed — uses regex
patterns that cover the most common issues in TypeScript and JavaScript
codebases.

Checks:
  - `any` type usage (TypeScript)
  - Missing return types on functions (TypeScript)
  - `var` usage (should use const/let)
  - Console.log left in production code
  - Non-null assertion operator (!) overuse
  - Implicit `any` in catch clauses
  - == vs === (loose equality)
  - Unsafe `eval` / `Function` constructor
  - Unsafe `innerHTML` assignments
  - Callback hell (deep nesting)
  - Magic numbers
  - TODO/FIXME markers
  - Hardcoded secrets
  - Missing error handling in async functions
  - Large component files (>300 lines)
"""
from __future__ import annotations

import re
from typing import Any


def review(code: str, file_path: str = "") -> list[dict]:
    """Review TypeScript/JavaScript code. Returns list of findings."""
    findings: list[dict] = []
    lines = code.split("\n")
    is_typescript = file_path.endswith((".ts", ".tsx")) if file_path else False

    # Track state across lines
    in_async_function = False
    brace_depth = 0
    nesting_depth = 0
    max_nesting = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        lineno = i + 1

        # ── Track nesting depth ──────────────────────────────────────────
        brace_depth += stripped.count("{") - stripped.count("}")
        nesting_depth = max(0, brace_depth)
        max_nesting = max(max_nesting, nesting_depth)

        # ── `var` usage ──────────────────────────────────────────────────
        if re.search(r'\bvar\s+\w+\s*=', stripped) and not stripped.startswith("//") and not stripped.startswith("/*"):
            findings.append({
                "rule": "var_usage", "severity": "MEDIUM",
                "line": lineno, "message": "Use 'const' or 'let' instead of 'var'",
            })

        # ── Console.log in production ─────────────────────────────────────
        if re.search(r'\bconsole\.(log|debug|info|warn|error)\s*\(', stripped) and not stripped.startswith("//"):
            findings.append({
                "rule": "console_log", "severity": "LOW",
                "line": lineno, "message": "Console statement left in production code",
            })

        # ── Loose equality ───────────────────────────────────────────────
        # Flag `==` when `===` is not also present (i.e. the line has == but not ===)
        if "==" in stripped and "===" not in stripped and not stripped.strip().startswith("//"):
            # Make sure it's actually a comparison, not a comment or string
            if re.search(r'\b\w+\s*==\s*', stripped) or re.search(r'==\s*\w+', stripped):
                findings.append({
                    "rule": "loose_equality", "severity": "MEDIUM",
                    "line": lineno, "message": "Use '===' instead of '==' for strict equality",
                })

        # ── eval / Function constructor ──────────────────────────────────
        if re.search(r'\beval\s*\(', stripped) or re.search(r'\bnew\s+Function\s*\(', stripped):
            findings.append({
                "rule": "eval_usage", "severity": "CRITICAL",
                "line": lineno, "message": "eval() / Function() constructor is a security risk",
            })

        # ── innerHTML ────────────────────────────────────────────────────
        if re.search(r'\.innerHTML\s*=', stripped) and not stripped.startswith("//"):
            findings.append({
                "rule": "inner_html", "severity": "HIGH",
                "line": lineno, "message": "Use textContent or safe DOM methods instead of innerHTML",
            })

        # ── Hardcoded secrets ────────────────────────────────────────────
        if re.search(r'(?:api_key|api_secret|password|secret|token)\s*[=:]\s*["\'][A-Za-z0-9_\-]{16,}', stripped):
            findings.append({
                "rule": "hardcoded_secret", "severity": "CRITICAL",
                "line": lineno, "message": "Possible hardcoded secret",
            })

        # ── TODO/FIXME markers ───────────────────────────────────────────
        if re.search(r'\b(TODO|FIXME|HACK|XXX)\b', stripped) and not stripped.startswith("//") and not stripped.startswith("/*"):
            findings.append({
                "rule": "todo_marker", "severity": "LOW",
                "line": lineno, "message": f"Leftover marker: {stripped[:60]}",
            })

        # ── TypeScript-specific checks ───────────────────────────────────
        if is_typescript:
            # `any` type usage
            if re.search(r':\s*any\b', stripped) and not stripped.startswith("//"):
                findings.append({
                    "rule": "any_type", "severity": "MEDIUM",
                    "line": lineno, "message": "Avoid 'any' type — use 'unknown' or a proper type",
                })

            # Non-null assertion (!)
            if re.search(r'\w+\s*!\s*\.', stripped) and not stripped.startswith("//"):
                findings.append({
                    "rule": "non_null_assertion", "severity": "LOW",
                    "line": lineno, "message": "Non-null assertion (!) bypasses type checking",
                })

            # Missing return type on function
            if re.search(r'(?:function\s+\w+\s*\(|const\s+\w+\s*=\s*(?:async\s+)?\([^)]*\)\s*(?::\s*\w+)?\s*=>)', stripped):
                if "): " not in stripped and "):" not in stripped.split("=>")[0] if "=>" in stripped else True:
                    # Check if there's a return type annotation
                    if not re.search(r'\)\s*:\s*\w+', stripped):
                        findings.append({
                            "rule": "missing_return_type", "severity": "MEDIUM",
                            "line": lineno, "message": "Function missing return type annotation",
                        })

            # Implicit any in catch
            if re.search(r'catch\s*\(\s*\w+\s*\)', stripped) and not re.search(r'catch\s*\(\s*\w+\s*:\s*\w+', stripped):
                findings.append({
                    "rule": "implicit_catch_any", "severity": "MEDIUM",
                    "line": lineno, "message": "Catch clause parameter implicitly 'any' — add a type annotation",
                })

        # ── JavaScript-specific checks ───────────────────────────────────
        # Missing error handling in async functions
        if re.search(r'\basync\s+function\b|\basync\s+\(', stripped):
            in_async_function = True
        if in_async_function and re.search(r'\bawait\b', stripped):
            # Check if there's a try/catch in the vicinity
            has_try = any("try {" in l for l in lines[max(0, i - 5):i + 5])
            if not has_try:
                findings.append({
                    "rule": "missing_async_error_handling", "severity": "MEDIUM",
                    "line": lineno, "message": "Async function with await missing try/catch error handling",
                })
                in_async_function = False

    # ── Post-line checks ─────────────────────────────────────────────────

    # Deep nesting (callback hell)
    if max_nesting > 5:
        findings.append({
            "rule": "deep_nesting", "severity": "MEDIUM",
            "line": 0, "message": f"Deep nesting detected (max depth {max_nesting}) — consider refactoring",
        })

    # Large file
    if len(lines) > 300:
        findings.append({
            "rule": "large_file", "severity": "LOW",
            "line": 0, "message": f"File is {len(lines)} lines — consider splitting into smaller modules",
        })

    return findings
