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
    # R-F2033 — grounding_reward is a PURE, deterministic math function (no LLM, no
    # network, no success/failure branch) used as a GRPO/DPO training reward, called
    # millions of times in a training loop. Wiring it to the brain is nonsensical and
    # would flood the ledgers — it's a computation utility, not an engine.
    "grounding_reward",
    # R-F2103 — cost_tracker is the per-LLM-call COST LEDGER (accounting infra, like
    # intel_ledger above), invoked on every call. Wiring it with per-call wire_success
    # would flood the brain ledgers; it already persists its own metrics. Same class
    # as grounding_reward — a utility/ledger, not an engine with success/failure runs.
    "cost_tracker",
}

# R-F1961 — THIS file (and any future pattern-authoring file) literally contains
# the danger-strings it scans for (os.fork regex, 'aria-internal', SSRF/url
# patterns) as DETECTION definitions, so a whole-file content check run on it
# self-flags. A check must not flag the file that defines its own patterns.
_PATTERN_AUTHORING_FILES = {"pre_commit_checks.py"}

# Known Windows-incompatible patterns (R-F1268)
WINDOWS_INCOMPATIBLE_PATTERNS: list[tuple[str, str]] = [
    (r"os\.fork\s*\(", "os.fork() is not available on Windows"),
    (r"signal\.signal\s*\(", "signal.signal() has limited support on Windows"),
    (r"fcntl\.", "fcntl is not available on Windows"),
    (r"resource\.", "resource module is not available on Windows"),
    (r"pty\.", "pty module is not available on Windows"),
    (r"subprocess\.Popen\(.*shell\s*=\s*True", "shell=True in subprocess has quoting issues on Windows"),
    (r"os\.pathsep\s*!=\s*['\"];['\"]", "os.pathsep is ';' on Windows, not ':'"),
    # R-F1961 — REMOVED a wrong pattern that flagged `Path(...) / "str"`. That is
    # the CANONICAL, correct pathlib idiom — the `/` operator yields the right
    # separator on every platform. The check punished good code and pushed toward
    # os.path string-concat (the actually-unsafe pattern). Deleting it, not weakening.
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


def check_circuit_breaker(
    files: list[Path],
    added_lines_by_file: dict[str, set[int]] | None = None,
) -> list[str]:
    """R-F1791 — every changed intel module that makes outbound HTTP calls must
    reference a circuit breaker (CLAUDE.md §1 root-cause-not-symptom; closes the
    breaker-gap class confirmed by the 2026-06-23 cross-check, item #40).

    R-F1971 — when ``added_lines_by_file`` is provided (pre-commit staged mode),
    only HTTP calls on ADDED lines are flagged, so editing a module that has a
    PRE-EXISTING unguarded httpx elsewhere isn't blocked on debt this diff didn't
    introduce. Documented exception: ``# no-breaker: <reason>`` on the call line.

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
        if added_lines_by_file is not None:
            _added = added_lines_by_file.get(file_path.name, set())
            http_calls = [c for c in http_calls if c["line_num"] in _added]
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


def check_windows_compat(
    files: list[Path],
    added_lines_by_file: dict[str, set[int]] | None = None,
) -> list[str]:
    """R-F1268 — Check changed files for known Windows-incompatible patterns.

    R-F1961 — when ``added_lines_by_file`` is provided (pre-commit staged mode),
    only lines ADDED in this diff are checked, so a touched legacy file isn't
    blocked on pre-existing patterns (and the checker file's own pattern-string
    DEFINITIONS don't self-flag). When None (CI), scans the whole file.

    Returns a list of issue strings (empty if all pass).
    """
    issues = []

    for file_path in files:
        if file_path.suffix != ".py":
            continue
        if "__pycache__" in file_path.parts:
            continue
        # R-F1961 — test files legitimately carry platform-pattern strings as
        # FIXTURES; the pattern-authoring file defines them. Skip both.
        if "tests" in file_path.parts or file_path.name in _PATTERN_AUTHORING_FILES:
            continue

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            continue

        _added = None if added_lines_by_file is None else added_lines_by_file.get(file_path.name, set())
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if _added is not None and (i + 1) not in _added:
                continue
            for pattern, message in WINDOWS_INCOMPATIBLE_PATTERNS:
                if re.search(pattern, line):
                    issues.append(
                        f"  {file_path.name}:{i + 1} — {message}\n"
                        f"    Line: {line.strip()[:100]}"
                    )

    return issues


def check_false_success(
    files: list[Path],
    added_lines_by_file: dict[str, set[int]] | None = None,
) -> list[str]:
    """R-F1268 — Scan changed files for ``success: True`` or ``"success": True``
    that is NOT preceded by actual verification logic.

    Flags patterns like:
    - return {"success": True, ...} without a preceding check
    - success: True in a dict literal without verification

    R-F1961 — when ``added_lines_by_file`` is provided (pre-commit staged mode),
    only ADDED lines are flagged, so a touched legacy file isn't blocked on a
    pre-existing success:True. When None (CI), scans the whole file.

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

        _added = None if added_lines_by_file is None else added_lines_by_file.get(file_path.name, set())
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if _added is not None and (i + 1) not in _added:
                continue
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


def check_capability_tests(
    files: list[Path],
    changed_funcs: dict[str, set[str]] | None = None,
) -> list[str]:
    """R-F1124 — For every changed function in aria_service/intel/, verify
    there's a test file that calls it.

    R-F1961 — when ``changed_funcs`` is provided (pre-commit staged mode: a map of
    filename -> set of function names whose `def` is in the ADDED diff lines),
    ONLY those genuinely-new/changed functions are checked. The old whole-file
    scan flagged EVERY untested public function in a touched legacy module, so any
    incremental commit to a big file was blocked on pre-existing debt. When None
    (CI --check-all) it scans the whole file as before.

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

        # R-F1961 — restrict to functions ADDED in this diff, when known.
        if changed_funcs is not None:
            _added = changed_funcs.get(file_path.name, set())
            func_defs = [f for f in func_defs if f in _added]

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


# ── R-F1824 (Phase-4 prevention guards) ───────────────────────────────────────
# Make the authz-review vuln classes un-reintroducible at commit time.

_TOKEN_DEFAULT_PATTERNS = (
    "|| 'aria-internal'", '|| "aria-internal"',
    'ARIA_INTERNAL_TOKEN", "aria-internal"',
    "ARIA_INTERNAL_TOKEN', 'aria-internal'",
)


def check_no_token_default(files: list[Path]) -> list[str]:
    """R-F1824 (audit H2) — no hardcoded 'aria-internal' auth-token fallback. An
    unset secret must fail closed, never fall back to a repo-public string."""
    issues = []
    for fp in files:
        if fp.suffix not in (".mjs", ".js", ".cjs", ".py"):
            continue
        if "tests" in fp.parts or fp.name in _PATTERN_AUTHORING_FILES:  # R-F1961
            continue
        try:
            content = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        norm = re.sub(r'",\s+"', '", "', content)  # collapse spaced getenv defaults
        if any(pat in content or pat in norm for pat in _TOKEN_DEFAULT_PATTERNS):
            issues.append(
                f"  {fp.name}: hardcoded 'aria-internal' auth-token default.\n"
                f"    Use `|| ''` / getenv(name, '') so an unset secret FAILS CLOSED\n"
                f"    (CLAUDE.md §1; audit H2 — 'aria-internal' is public in the repo)."
            )
    return issues


# Only DYNAMIC-URL fetches (a variable named like user input) — constant/f-string
# API calls are not an SSRF risk and must not false-positive.
# Require an http-client receiver (httpx / client / session / a one-letter client
# var like `c`) AND a genuinely-URL argument name — so dict `.get(loc)` / `.get(source)`
# do NOT false-match, and constant API-base fetches assigned to non-URL vars are skipped.
_DYNAMIC_FETCH_RE = re.compile(
    r"(?:httpx|[A-Za-z_]*client|session|\bc)\.(?:get|post|request|stream)\("
    r"\s*(?:url|uri|endpoint|href|target_url|page_url|seed_url|fetch_url)\b"
)
_SSRF_GUARD_TOKENS = ("url_safety", "safe_get", "is_safe_url", "assert_safe_url", "_ssrf_safe_url")


def check_ssrf_fetch_boundary(files: list[Path]) -> list[str]:
    """R-F1824 (audit C2) — an intel module that fetches a user-controlled URL
    variable must go through the SSRF boundary (url_safety.safe_get / is_safe_url),
    not a raw httpx call. Whole-file heuristic, dynamic-URL only; opt-out:
    '# no-ssrf-check' on the fetch line."""
    issues = []
    exempt = {"url_safety", "web_search", "dd_orchestrator", "researcher"}  # canonical guard / already-guarded
    for fp in files:
        if fp.suffix != ".py" or "intel" not in fp.parts or "tests" in fp.parts:
            continue
        if fp.stem in exempt:
            continue
        try:
            content = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        hits = [ln for ln in content.splitlines()
                if _DYNAMIC_FETCH_RE.search(ln) and "# no-ssrf-check" not in ln]
        if not hits or any(tok in content for tok in _SSRF_GUARD_TOKENS):
            continue
        issues.append(
            f"  {fp.name}: outbound fetch of a user-controlled URL with NO SSRF guard.\n"
            f"    e.g.: {hits[0].strip()[:100]}\n"
            f"    Route it through url_safety.safe_get (audit C2). Documented exception:\n"
            f"    add '# no-ssrf-check' to the fetch line."
        )
    return issues
