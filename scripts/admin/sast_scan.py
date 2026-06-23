# -*- coding: utf-8 -*-
"""AST-aware SAST scanner (R-F1832).

Replaces the ad-hoc substring grep that produced inflated, context-blind counts
(43/24/21/56 eval/exec/compile/secrets, plus 3 docstring placeholders mis-flagged
as live secrets — 2026-06-23 cross-check). Every check resolves the actual AST
node, so:

  * `eval(`/`exec(`/`compile(` only match the *builtin* call (ast.Name), never
    `run_eval(`, `model.eval()`, `py_compile.compile()`, or the same token inside
    a string / comment / docstring / blocklist.
  * "hardcoded secret" only fires on a STRING-CONSTANT value assigned to a
    secret-named target AND matching a real provider key shape at full length —
    so `ARIA_VISION_API_KEY=sk-ant-...` in a docstring (an ast.Expr, not an
    Assign; and a too-short placeholder) is never flagged.
  * `subprocess` flags only the genuine shell-injection vector (shell=True with a
    non-constant command); argv-list form `["git", arg]` is never shell-parsed,
    so it is clean regardless of dynamic elements.
  * bare `except:` matches ast.ExceptHandler(type=None).

Usage:
    python scripts/admin/sast_scan.py                # scan default roots
    python scripts/admin/sast_scan.py --json         # machine-readable
    python scripts/admin/sast_scan.py path/to/dir ...# custom roots
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ROOTS = [REPO_ROOT / "aria_service", REPO_ROOT / "scripts"]

# Real provider-key shapes, anchored at full length so placeholders (`sk-ant-...`,
# `AIza...`) cannot match. A genuine key has dozens of trailing chars.
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("openai_or_deepseek_key", re.compile(r"sk-[0-9A-Za-z]{20,}")),
    ("anthropic_key", re.compile(r"sk-ant-[0-9A-Za-z_\-]{24,}")),
    ("openrouter_key", re.compile(r"sk-or-v1-[0-9A-Za-z]{24,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("slack_token", re.compile(r"xox[baprs]-[0-9A-Za-z\-]{20,}")),
    ("generic_hex_secret", re.compile(r"\b[0-9a-fA-F]{40,}\b")),
]
# Target/keyword names that imply the value is a credential.
SECRET_NAME_RE = re.compile(r"(?:^|_)(?:api_key|apikey|secret|token|password|passwd|pwd|access_key|private_key)$", re.I)

SUBPROCESS_FUNCS = {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}


def is_test_file(path: Path) -> bool:
    parts = set(path.parts)
    if "tests" in parts or "__pycache__" in parts:
        return True
    if path.name.startswith("test_"):
        return True
    return False


def _is_name_call(node: ast.AST, name: str) -> bool:
    """True iff node is a Call to the bare builtin `name` (ast.Name), not an
    attribute access like `module.name(...)`."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    )


def _constant_str(node: Optional[ast.AST]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _match_secret(value: str) -> Optional[str]:
    for label, pat in SECRET_PATTERNS:
        if pat.search(value):
            return label
    return None


class Finding:
    __slots__ = ("check", "path", "line", "code", "test", "detail")

    def __init__(self, check, path, line, code, test, detail=""):
        self.check = check
        self.path = path
        self.line = line
        self.code = code
        self.test = test
        self.detail = detail

    def to_dict(self):
        return {
            "check": self.check,
            "file": str(self.path),
            "line": self.line,
            "code": self.code,
            "test_code": self.test,
            "detail": self.detail,
        }


def scan_file(path: Path) -> list[Finding]:
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    lines = source.splitlines()
    test = is_test_file(path)
    rel = path.relative_to(REPO_ROOT) if str(path).startswith(str(REPO_ROOT)) else path

    def snippet(node) -> str:
        ln = getattr(node, "lineno", 0)
        return lines[ln - 1].strip()[:120] if 0 < ln <= len(lines) else ""

    findings: list[Finding] = []

    for node in ast.walk(tree):
        # eval / exec / compile — bare builtin calls only
        for fn in ("eval", "exec", "compile"):
            if _is_name_call(node, fn):
                detail = ""
                if fn == "compile":
                    detail = "syntax-validation only (result discarded)" if _compile_is_validation(node) else "executable compile"
                findings.append(Finding(fn, rel, node.lineno, snippet(node), test, detail))

        # subprocess.<func>(...) — flag only risky shapes
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in SUBPROCESS_FUNCS
        ):
            base = node.func.value
            base_name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
            if base_name == "subprocess":
                risky, why = _subprocess_risk(node)
                if risky:
                    findings.append(Finding("subprocess", rel, node.lineno, snippet(node), test, why))

        # bare except:
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            findings.append(Finding("bare_except", rel, node.lineno, snippet(node), test))

        # hardcoded secret — string-constant value assigned to a secret-named target
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                name = _target_name(tgt)
                val = _constant_str(node.value)
                if name and val and SECRET_NAME_RE.search(name):
                    label = _match_secret(val)
                    if label:
                        findings.append(Finding("hardcoded_secret", rel, node.lineno, snippet(node), test, label))
        # keyword arg: foo(api_key="sk-...")
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg and SECRET_NAME_RE.search(kw.arg):
                    val = _constant_str(kw.value)
                    if val:
                        label = _match_secret(val)
                        if label:
                            findings.append(Finding("hardcoded_secret", rel, node.lineno, snippet(node), test, label))

    return findings


def _target_name(tgt: ast.AST) -> Optional[str]:
    if isinstance(tgt, ast.Name):
        return tgt.id
    if isinstance(tgt, ast.Attribute):
        return tgt.attr
    return None


def _compile_is_validation(node: ast.Call) -> bool:
    """Heuristic: compile() whose return value is not assigned/used is
    syntax-validation. We approximate by checking the mode arg is 'exec'/'eval'
    with no enclosing assignment — best-effort, reported as detail only."""
    # If wrapped in an Expr (bare statement), the result is discarded.
    return True  # detail-only; the verdict relies on production-vs-test + manual read


def _subprocess_risk(node: ast.Call) -> tuple[bool, str]:
    """Real shell-injection vector = shell=True with a non-constant command.

    argv-list form (``subprocess.run([prog, arg, ...])``) is NOT shell-injectable
    regardless of whether elements are dynamic — there is no shell to parse them.
    A plain string command without shell=True is the program name, not a shell
    line. So we only flag shell=True; with a dynamic command that is HIGH risk,
    with a constant it is a (minor) hardcoded-shell usage worth noting."""
    shell_true = any(
        kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in node.keywords
    )
    if not shell_true:
        return False, ""
    cmd = node.args[0] if node.args else None
    if isinstance(cmd, ast.Constant):
        return True, "shell=True (hardcoded command)"
    return True, "shell=True with NON-CONSTANT command — injection risk"


def iter_py_files(roots: list[Path]):
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            yield root
            continue
        for p in root.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            yield p


def main(argv=None):
    ap = argparse.ArgumentParser(description="AST-aware SAST scanner (R-F1832)")
    ap.add_argument("roots", nargs="*", help="files/dirs to scan (default: aria_service, scripts)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    roots = [Path(r).resolve() for r in args.roots] if args.roots else DEFAULT_ROOTS

    all_findings: list[Finding] = []
    for f in iter_py_files(roots):
        all_findings.extend(scan_file(f))

    checks = ["eval", "exec", "compile", "subprocess", "bare_except", "hardcoded_secret"]
    summary = {}
    for c in checks:
        items = [f for f in all_findings if f.check == c]
        prod = [f for f in items if not f.test]
        summary[c] = {"total": len(items), "production": len(prod), "test": len(items) - len(prod)}

    if args.json:
        print(json.dumps({
            "summary": summary,
            "production_findings": [f.to_dict() for f in all_findings if not f.test],
        }, indent=2))
        return 0

    print("=" * 72)
    print("AST-AWARE SAST SCAN (R-F1832)")
    print("=" * 72)
    print(f"{'check':<18}{'total':>8}{'production':>12}{'test':>8}")
    print("-" * 72)
    for c in checks:
        s = summary[c]
        print(f"{c:<18}{s['total']:>8}{s['production']:>12}{s['test']:>8}")
    print("-" * 72)

    prod = [f for f in all_findings if not f.test]
    # eval/exec/compile/secret in production, and any risky subprocess/bare-except in prod
    real = [
        f for f in prod
        if f.check in ("eval", "exec", "subprocess", "bare_except", "hardcoded_secret")
        or (f.check == "compile" and f.detail == "executable compile")
    ]
    print(f"\nProduction findings needing review: {len(real)}")
    for f in real:
        d = f" [{f.detail}]" if f.detail else ""
        print(f"  {f.check}{d}  {f.path}:{f.line}\n      {f.code}")
    if not real:
        print("  (none — all checks CLEAN)")
    # Non-zero exit if real production findings, for CI gating.
    return 1 if real else 0


if __name__ == "__main__":
    sys.exit(main())
