# -*- coding: utf-8 -*-
"""R-F1127 — Pre-commit check logic, extracted for testability.

Contains the testable functions used by scripts/pre-commit:
- check_capability_tests: verifies every changed function has a test
- find_function_calls: finds await module.fn() calls in code
- function_exists: checks if a function exists in a module
- check_wiring_present: verifies every changed module has brain wiring
- check_windows_compat: flags known Windows-incompatible patterns
- check_false_success: scans for success:True without verification
- find_direct_function_calls: finds module.fn() calls (with or without await)

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

# Modules that are exempt from wiring checks (test files, routes, config, etc.)
WIRING_EXEMPT_MODULES = {
    "__init__", "main", "routes", "engine_wiring", "brain_hook",
    "mistake_ledger", "capability_gaps", "intel_ledger",
}

# Known Windows-incompatible patterns (R-F1268)
WINDOWS_INCOMPATIBLE_PATTERNS: list[tuple[str, str]] = [
    (r"os\.fork\s*\(", "os.fork() is not available on Windows"),
    (r"signal\.signal\s*\(", "signal.signal() has limited support on Windows"),
    (r"fcntl\.", "fcntl is not available on Windows"),
    (r"resource\.", "resource module is not available on Windows"),
    (r"pty\.", "pty module is not available on Windows"),
    (r"subprocess\.Popen\(.*shell\s*=\s*True", "shell=True in subprocess has quoting issues on Windows"),
    (r"os\.pathsep\s*!=\s*['\"];['\"]", "os.pathsep is ';' on Windows, not ':'"),
    (r"Path\(.*\)\s*/\s*['\"].*['\"]", "Path concatenation with string may produce wrong separators on Windows"),
]


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


def check_builtin_shadowing(files: list[Path]) -> list[str]:
    """R-F1518 — Check that no module-level function shadows a Python built-in.
    
    A function named `set()` shadows `builtins.set()`, causing confusing bugs
    when code inside the module calls `set(...)` expecting the built-in but
    getting the module function instead. This check scans every changed .py
    file for functions whose names collide with built-in names.
    
    Returns a list of issue strings (empty if all pass).
    """
    import builtins
    builtin_names = {name for name in dir(builtins) if not name.startswith('_')}
    issues = []
    
    for file_path in files:
        if file_path.suffix != ".py":
            continue
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name in builtin_names:
                        issues.append(
                            f"{file_path}:{node.lineno}: function '{node.name}()' "
                            f"shadows builtins.{node.name}(). Rename the function "
                            f"or use 'builtins.{node.name}' explicitly."
                        )
        except SyntaxError:
            pass
    
    return issues


def check_builtin_shadowing(files: list[Path]) -> list[str]:
    """R-F1518 — Check that no module-level function shadows a Python built-in.
    
    A function named `set()` shadows `builtins.set()`, causing confusing bugs
    when code inside the module calls `set(...)` expecting the built-in but
    getting the module function instead. This check scans every changed .py
    file for functions whose names collide with built-in names.
    
    Returns a list of issue strings (empty if all pass).
    """
    import builtins
    builtin_names = {name for name in dir(builtins) if not name.startswith('_')}
    issues = []
    
    for file_path in files:
        if file_path.suffix != ".py":
            continue
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name in builtin_names:
                        issues.append(
                            f"{file_path}:{node.lineno}: function '{node.name}()' "
                            f"shadows builtins.{node.name}(). Rename the function "
                            f"or use 'builtins.{node.name}' explicitly."
                        )
        except SyntaxError:
            pass
    
    return issues


def check_wiring_present(files: list[Path]) -> list[str]:
    """Check that every changed intel module has at least one wire_success or wire_failure call (brain wiring).

    Modules that are purely data/configuration or are themselves wiring
    infrastructure are exempt (see WIRING_EXEMPT_MODULES).

    Returns a list of issue strings (empty if all pass).
    """
    issues = []

    for file_path in files:
        # Only check intel modules
        if "intel" not in file_path.parts:
            continue
        if file_path.suffix != ".py":
            continue
        if file_path.stem in WIRING_EXEMPT_MODULES:
            continue
        if "tests" in file_path.parts:
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            continue

        # Check for wire_success or wire_failure calls
        has_wire_success = "wire_success(" in content
        has_wire_failure = "wire_failure(" in content

        if not has_wire_success and not has_wire_failure:
            issues.append(
                f"  {file_path.name}: NO brain wiring found.\n"
                f"    Every intel module must call wire_success() and/or wire_failure()\n"
                f"    to connect to the brain (CLAUDE.md §21a).\n"
                f"    Add: from .engine_wiring import wire_success, wire_failure\n"
                f"    And call wire_success() on success, wire_failure() on failure."
            )
        elif has_wire_success and not has_wire_failure:
            issues.append(
                f"  {file_path.name}: has wire_success but NO wire_failure.\n"
                f"    Both success AND failure branches must reach a brain sink\n"
                f"    (anti-hallucination law #6). Add wire_failure() calls to\n"
                f"    all exception/error paths."
            )
        elif has_wire_failure and not has_wire_success:
            issues.append(
                f"  {file_path.name}: has wire_failure but NO wire_success.\n"
                f"    Both success AND failure branches must reach a brain sink\n"
                f"    (anti-hallucination law #6). Add wire_success() call to\n"
                f"    the success path."
            )

    return issues


# R-F1791 (cross-check #40, 2026-06-23) — outbound HTTP client constructions.
# Any new external backend must be guarded by a circuit breaker, else a
# rate-limiting/dead backend gets hammered every call (the cascade breakers
# exist to stop — see R-F1790 which fixed three such unguarded paths).
HTTP_CLIENT_RE = re.compile(
    r"httpx\.(AsyncClient|Client)\s*\("
    r"|httpx\.(get|post|put|patch|delete|head)\s*\("
    r"|aiohttp\.ClientSession\s*\("
    r"|requests\.(get|post|put|patch|delete|head|Session)\s*\("
)
# A module is considered breaker-aware if it references any of these.
BREAKER_TOKENS = ("get_breaker(", "CircuitBreaker", "circuit_breaker", ".is_open(")
# Modules exempt from the breaker check (the breaker infra itself, etc.).
CB_EXEMPT_MODULES = {"circuit_breaker", "self_healing"}


def find_http_client_calls(lines: list[str]) -> list[dict]:
    """Find outbound HTTP-client constructions. Lines carrying the documented
    opt-out marker ``# no-breaker`` are skipped (e.g. an internal-only call)."""
    out: list[dict] = []
    for i, ln in enumerate(lines):
        if "# no-breaker" in ln:
            continue
        if HTTP_CLIENT_RE.search(ln):
            out.append({"line_num": i + 1, "code": ln.strip()[:120]})
    return out


def module_has_breaker(content: str) -> bool:
    return any(tok in content for tok in BREAKER_TOKENS)


def check_circuit_breaker(files: list[Path]) -> list[str]:
    """R-F1791 — every changed intel module that makes outbound HTTP calls must
    reference a circuit breaker (CLAUDE.md §1 root-cause-not-symptom; closes the
    breaker-gap class confirmed by the 2026-06-23 cross-check, item #40).

    Whole-file heuristic, mirroring check_wiring_present. Documented exception:
    put ``# no-breaker: <reason>`` on the HTTP-client line (e.g. <app>.internal
    cross-app calls that never hit a rate-limiting public backend).

    Returns a list of issue strings (empty if all pass).
    """
    issues = []
    for file_path in files:
        if file_path.suffix != ".py":
            continue
        if "intel" not in file_path.parts:
            continue
        if "tests" in file_path.parts:
            continue
        if file_path.stem in CB_EXEMPT_MODULES:
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            continue

        http_calls = find_http_client_calls(content.splitlines())
        if not http_calls:
            continue
        if module_has_breaker(content):
            continue

        issues.append(
            f"  {file_path.name}: makes outbound HTTP calls but has NO circuit breaker.\n"
            f"    e.g. line {http_calls[0]['line_num']}: {http_calls[0]['code']}\n"
            f"    Every external HTTP backend must be guarded (CLAUDE.md §1; cross-check #40):\n"
            f"      from .circuit_breaker import get_breaker\n"
            f"      cb = get_breaker('backend:name')\n"
            f"      if cb.is_open(): return ...      # then cb.record_success()/record_failure()\n"
            f"    Documented exception: add '# no-breaker: <reason>' to the HTTP-client line."
        )

    return issues


def check_windows_compat(files: list[Path]) -> list[str]:
    """R-F1268 — Check changed files for known Windows-incompatible patterns.

    Returns a list of issue strings (empty if all pass).
    """
    issues = []

    for file_path in files:
        if file_path.suffix != ".py":
            continue
        if "__pycache__" in file_path.parts:
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            continue

        lines = content.splitlines()
        for i, line in enumerate(lines):
            for pattern, message in WINDOWS_INCOMPATIBLE_PATTERNS:
                if re.search(pattern, line):
                    issues.append(
                        f"  {file_path.name}:{i + 1} — {message}\n"
                        f"    Line: {line.strip()[:100]}"
                    )

    return issues


def check_false_success(files: list[Path]) -> list[str]:
    """R-F1268 — Scan changed files for ``success: True`` or ``"success": True``
    that is NOT preceded by actual verification logic.

    Flags patterns like:
    - return {"success": True, ...} without a preceding check
    - success: True in a dict literal without verification

    Returns a list of issue strings (empty if all pass).
    """
    issues = []

    for file_path in files:
        if file_path.suffix != ".py":
            continue
        if "__pycache__" in file_path.parts:
            continue
        if "tests" in file_path.parts:
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            continue

        lines = content.splitlines()
        for i, line in enumerate(lines):
            # Look for success: True, "success": True, or 'success': True in dict literals
            if re.search(r"""(?:"success"|'success'|success)\s*:\s*True""", line):
                # Check if this is preceded by verification logic in the
                # surrounding 5 lines (a try/except, an if/else, a check call)
                start = max(0, i - 5)
                context = "\n".join(lines[start:i + 1])
                # If no verification keywords found in context, flag it
                has_verification = any(
                    kw in context
                    for kw in ["verify", "check", "validate", "confirm",
                               "assert", "try:", "except", "if ", "test",
                               "probe", "ensure", "confirm"]
                )
                if not has_verification:
                    issues.append(
                        f"  {file_path.name}:{i + 1} — success:True without verification\n"
                        f"    Line: {line.strip()[:100]}\n"
                        f"    Returning success:True without verification is a false-success\n"
                        f"    anti-pattern (anti-hallucination law #4). Add a check first."
                    )

    return issues


def find_direct_function_calls(lines: list[str]) -> list[dict]:
    """R-F1268 — Find ``module.function()`` calls (with or without await).

    Extends find_function_calls() to also catch non-awaited calls like
    ``wire_success(...)`` or ``some_module.some_function()``.

    Returns list of dicts with keys: line_num, object, function, code.
    """
    calls = []
    # Match both "await module.fn(" and "module.fn(" patterns
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
