"""R-F1127 — Pre-commit check logic, extracted for testability.

Contains the testable functions used by scripts/pre-commit:
- check_capability_tests: verifies every changed function has a test
- find_function_calls: finds await module.fn() calls in code
- function_exists: checks if a function exists in a module

These are imported by scripts/pre-commit and by tests.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
ARIA_SERVICE = REPO_ROOT / "aria_service"

KNOWN_ALIASES = {
    "rs": "aria_service.intel.redis_store",
    "il": "aria_service.intel.intel_ledger",
    "ct": "aria_service.intel.competitor_tracker",
    "tm": "aria_service.intel.tender_monitor",
    "pri": "aria_service.intel.political_risk_index",
    "nm": "aria_service.intel.news_monitor",
    "cc": "aria_service.intel.commercial_coherence",
    "dp": "aria_service.intel.deal_pipeline",
    "kn": "aria_service.intel.knowledge",
    "bh": "aria_service.intel.brain_hook",
    "cg": "aria_service.intel.capability_gaps",
    "ml": "aria_service.intel.mistake_ledger",
    "si": "aria_service.intel.self_improve",
    "sc": "aria_service.intel.sanctions_canonical",
}

EXEMPT_MODULES = {
    "httpx", "asyncio", "json", "os", "sys", "re", "time", "datetime",
    "Path", "logging", "hashlib", "random", "math", "copy", "typing",
    "uuid", "base64", "ssl", "smtplib", "imaplib", "email", "html",
    "socket", "ast", "inspect", "collections", "pathlib",
}


def find_function_calls(lines: list[str]) -> list[dict]:
    """Find ``await module.function()`` calls in source lines.

    Returns list of dicts with keys: line_num, object, function, code.
    """
    calls = []
    pattern = re.compile(r"(?:await\s+)?(\w+)\.(\w+)\s*\(")
    for i, line in enumerate(lines):
        for m in pattern.finditer(line):
            obj = m.group(1)
            func = m.group(2)
            if func.startswith("__"):
                continue
            if obj in EXEMPT_MODULES:
                continue
            calls.append({
                "line_num": i + 1,
                "object": obj,
                "function": func,
                "code": line.strip()[:100],
            })
    return calls


def resolve_module(obj_name: str, file_path: Path) -> Optional[str]:
    """Resolve a short object name to its full module path."""
    if obj_name in KNOWN_ALIASES:
        return KNOWN_ALIASES[obj_name]
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.asname == obj_name or (alias.name == obj_name and not alias.asname):
                        base = node.module or ""
                        return f"aria_service.intel.{base}.{alias.name}" if base else f"aria_service.intel.{alias.name}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname == obj_name or (alias.name == obj_name and not alias.asname):
                        return alias.name
    except SyntaxError:
        pass
    return None


def function_exists(module_path: str, func_name: str) -> bool:
    """Check if a function exists in a module by parsing its AST."""
    parts = module_path.split(".")
    for base in [ARIA_SERVICE, REPO_ROOT]:
        file_path = base / f"{'/'.join(parts[1:] if parts[0] == 'aria_service' else parts)}.py"
        if file_path.exists():
            try:
                source = file_path.read_text(encoding="utf-8")
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if node.name == func_name:
                            return True
            except SyntaxError:
                pass
            return False
    return True  # Can't find module — pass through


def check_capability_tests(files: list[Path]) -> list[str]:
    """R-F1124 — For every changed function in aria_service/intel/, verify
    there's a test file that calls it.

    Returns a list of issue strings (empty if all pass).
    """
    issues = []
    test_dir = ARIA_SERVICE / "tests"

    for file_path in files:
        # Only check intel modules (not tests, not routes, not main)
        if "tests" in file_path.parts or "routes" in file_path.parts:
            continue
        if file_path.name in ("main.py", "__init__.py"):
            continue
        if not file_path.name.endswith(".py"):
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            continue

        # Find function definitions
        func_defs = []
        for line in content.splitlines():
            m = re.match(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(", line)
            if m:
                func_defs.append(m.group(1))

        if not func_defs:
            continue

        # For each function, check there's a test that calls it
        for func_name in func_defs:
            # Skip private/dunder methods and known exempt patterns
            if func_name.startswith("_") and not func_name.startswith("__"):
                continue
            if func_name in ("main", "lifespan", "setup", "teardown"):
                continue

            # Search test files for calls to this function
            found = False
            for test_file in sorted(test_dir.glob("test_*.py")):
                try:
                    test_content = test_file.read_text(encoding="utf-8")
                    if func_name in test_content:
                        found = True
                        break
                except Exception:
                    continue

            if not found:
                module_name = file_path.stem
                issues.append(
                    f"  {file_path.name}: new function '{func_name}()' has NO capability test.\n"
                    f"    Add a test in {test_dir}/test_rfXXXX_{module_name}.py that calls {func_name}()\n"
                    f"    and asserts the user-visible outcome (anti-hallucination law #3)."
                )

    return issues
