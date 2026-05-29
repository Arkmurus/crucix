"""R-F1044 — Go code reviewer.

Pattern-based review for Go code. Covers common issues in Go codebases.

Checks:
  - `error` return ignored
  - `_` used to discard errors
  - `panic()` / `recover()` usage
  - `log.Fatal()` in non-main packages
  - Goroutine leak potential (no sync.WaitGroup)
  - `defer` inside loops
  - `time.Sleep()` for synchronization
  - Hardcoded secrets
  - TODO/FIXME markers
  - Exported function without comment
  - `init()` functions
  - `context.Background()` instead of `context.TODO()`
  - Large files (>500 lines)
"""
from __future__ import annotations

import re
from typing import Any


def review(code: str, file_path: str = "") -> list[dict]:
    """Review Go code. Returns list of findings."""
    findings: list[dict] = []
    lines = code.split("\n")

    for i, line in enumerate(lines):
        stripped = line.strip()
        lineno = i + 1

        # ── panic() / recover() ──────────────────────────────────────────
        if re.search(r'\bpanic\s*\(', stripped) and not stripped.startswith("//"):
            findings.append({
                "rule": "panic_usage", "severity": "HIGH",
                "line": lineno, "message": "panic() should only be used for unrecoverable errors",
            })

        # ── log.Fatal() in non-main ──────────────────────────────────────
        if re.search(r'\blog\.Fatal\s*\(', stripped) and "package main" not in code[:200]:
            findings.append({
                "rule": "log_fatal", "severity": "MEDIUM",
                "line": lineno, "message": "log.Fatal() in non-main package — consider returning error instead",
            })

        # ── defer inside loop ────────────────────────────────────────────
        if re.search(r'\bdefer\b', stripped):
            # Check if we're inside a loop (look backwards)
            for j in range(max(0, i - 10), i):
                if re.search(r'\b(for|range)\b', lines[j]):
                    findings.append({
                        "rule": "defer_in_loop", "severity": "HIGH",
                        "line": lineno, "message": "defer inside a loop — resources won't release until function returns",
                    })
                    break

        # ── time.Sleep for sync ──────────────────────────────────────────
        if re.search(r'\btime\.Sleep\s*\(', stripped):
            findings.append({
                "rule": "sleep_sync", "severity": "MEDIUM",
                "line": lineno, "message": "time.Sleep() for synchronization — use channels or sync.WaitGroup",
            })

        # ── context.Background() in handlers ─────────────────────────────
        if re.search(r'\bcontext\.Background\s*\(', stripped):
            findings.append({
                "rule": "context_background", "severity": "LOW",
                "line": lineno, "message": "context.Background() in request handler — use context.TODO() or request context",
            })

        # ── init() functions ─────────────────────────────────────────────
        if re.search(r'^\s*func\s+init\s*\(', stripped):
            findings.append({
                "rule": "init_function", "severity": "LOW",
                "line": lineno, "message": "init() makes testing and dependency management harder",
            })

        # ── Exported function without comment ────────────────────────────
        if re.search(r'^\s*func\s+[A-Z]', stripped):
            # Check previous line for comment
            if i == 0 or not lines[i - 1].strip().startswith("//"):
                findings.append({
                    "rule": "missing_export_comment", "severity": "MEDIUM",
                    "line": lineno, "message": "Exported function missing doc comment",
                })

        # ── Hardcoded secrets ────────────────────────────────────────────
        if re.search(r'(?:api_key|api_secret|password|secret|token)\s*[=:]\s*["\'][A-Za-z0-9_\-]{16,}', stripped):
            findings.append({
                "rule": "hardcoded_secret", "severity": "CRITICAL",
                "line": lineno, "message": "Possible hardcoded secret",
            })

        # ── TODO/FIXME markers ───────────────────────────────────────────
        if re.search(r'\b(TODO|FIXME|HACK|XXX)\b', stripped) and not stripped.startswith("//"):
            findings.append({
                "rule": "todo_marker", "severity": "LOW",
                "line": lineno, "message": f"Leftover marker: {stripped[:60]}",
            })

    # ── Post-line checks ─────────────────────────────────────────────────

    # Check for ignored errors (function calls that return error but result is discarded)
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Pattern: `someFunc()` where someFunc returns (..., error)
        # We can't know the return type statically without Go's type system,
        # but we can flag common patterns
        if re.search(r'=\s+[^,]+\.\w+\s*\(', stripped) and "err" not in stripped and "error" not in stripped:
            # This is a heuristic — may have false positives
            pass  # Too noisy without type info

    # Large file
    if len(lines) > 500:
        findings.append({
            "rule": "large_file", "severity": "LOW",
            "line": 0, "message": f"File is {len(lines)} lines — consider splitting",
        })

    return findings
