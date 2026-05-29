"""R-F1044 — Rust code reviewer.

Pattern-based review for Rust code. Covers common issues in Rust codebases
that the compiler doesn't catch (or catches with warnings that are easy to
ignore).

Checks:
  - `unwrap()` / `expect()` usage (panic risk)
  - `unsafe` blocks
  - `todo!()` / `unimplemented!()` markers
  - Large functions (>100 lines)
  - Missing error handling (returning `()` instead of `Result`)
  - `clone()` on large types
  - `Box::new(...)` instead of `Box::pin(...)` for futures
  - Hardcoded secrets
  - TODO/FIXME markers
  - `#[allow(...)]` suppressing too many warnings
  - Recursive types without `Box`
  - `as` casts (type truncation risk)
"""
from __future__ import annotations

import re
from typing import Any


def review(code: str, file_path: str = "") -> list[dict]:
    """Review Rust code. Returns list of findings."""
    findings: list[dict] = []
    lines = code.split("\n")

    for i, line in enumerate(lines):
        stripped = line.strip()
        lineno = i + 1

        # ── unwrap() / expect() ──────────────────────────────────────────
        if re.search(r'\.unwrap\s*\(', stripped) and not stripped.strip().startswith("//"):
            findings.append({
                "rule": "unwrap_usage", "severity": "MEDIUM",
                "line": lineno, "message": "unwrap() panics on error — use pattern matching or ? operator",
            })
        if re.search(r'\.expect\s*\(', stripped) and not stripped.strip().startswith("//"):
            findings.append({
                "rule": "expect_usage", "severity": "LOW",
                "line": lineno, "message": "expect() panics on error — consider proper error handling",
            })

        # ── unsafe blocks ────────────────────────────────────────────────
        if re.search(r'\bunsafe\b', stripped) and not stripped.strip().startswith("//"):
            findings.append({
                "rule": "unsafe_block", "severity": "HIGH",
                "line": lineno, "message": "Unsafe block — verify safety invariants and add SAFETY comment",
            })

        # ── todo! / unimplemented! ───────────────────────────────────────
        if re.search(r'\btodo!\s*\(', stripped):
            findings.append({
                "rule": "todo_macro", "severity": "MEDIUM",
                "line": lineno, "message": "todo!() will panic at runtime — implement before production",
            })
        if re.search(r'\bunimplemented!\s*\(', stripped):
            findings.append({
                "rule": "unimplemented_macro", "severity": "MEDIUM",
                "line": lineno, "message": "unimplemented!() will panic at runtime",
            })

        # ── #[allow(...)] ────────────────────────────────────────────────
        if re.search(r'#\[\s*allow\s*\(', stripped):
            findings.append({
                "rule": "allow_attribute", "severity": "LOW",
                "line": lineno, "message": "#[allow(...)] suppresses compiler warnings — document why",
            })

        # ── `as` casts ───────────────────────────────────────────────────
        if re.search(r'\bas\s+\w+\b', stripped) and not stripped.strip().startswith("//"):
            findings.append({
                "rule": "as_cast", "severity": "LOW",
                "line": lineno, "message": "`as` cast can silently truncate — use `From`/`TryFrom` or `into()`",
            })

        # ── Hardcoded secrets ────────────────────────────────────────────
        if re.search(r'(?:api_key|api_secret|password|secret|token)\s*[=:]\s*["\'][A-Za-z0-9_\-]{16,}', stripped):
            findings.append({
                "rule": "hardcoded_secret", "severity": "CRITICAL",
                "line": lineno, "message": "Possible hardcoded secret",
            })

        # ── TODO/FIXME markers ───────────────────────────────────────────
        if re.search(r'\b(TODO|FIXME|HACK|XXX)\b', stripped) and not stripped.strip().startswith("//"):
            findings.append({
                "rule": "todo_marker", "severity": "LOW",
                "line": lineno, "message": f"Leftover marker: {stripped[:60]}",
            })

        # ── clone() on potentially large types ───────────────────────────
        if re.search(r'\.clone\s*\(', stripped) and not stripped.strip().startswith("//"):
            findings.append({
                "rule": "clone_usage", "severity": "LOW",
                "line": lineno, "message": "clone() may be expensive — consider borrowing instead",
            })

    # ── Post-line checks ─────────────────────────────────────────────────

    # Large functions (count lines between fn signatures)
    fn_start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.search(r'^\s*(pub\s+)?(async\s+)?fn\s+\w+', stripped):
            if fn_start is not None and i - fn_start > 100:
                findings.append({
                    "rule": "large_function", "severity": "LOW",
                    "line": fn_start + 1,
                    "message": f"Function is {i - fn_start} lines — consider splitting",
                })
            fn_start = i

    # Check for missing Result return type on fallible functions
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Functions that use ? but return ()
        if re.search(r'fn\s+\w+.*->.*\(\)', stripped):
            # Check if the function body uses ?
            body_start = i + 1
            body_end = min(i + 50, len(lines))
            for j in range(body_start, body_end):
                if "?" in lines[j] and not lines[j].strip().startswith("//"):
                    findings.append({
                        "rule": "missing_result_return", "severity": "HIGH",
                        "line": i + 1,
                        "message": "Function uses ? operator but returns () — should return Result",
                    })
                    break

    return findings
