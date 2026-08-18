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
from functools import lru_cache          # R-F3556 — see _module_function_names
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
ARIA_SERVICE = REPO_ROOT / "aria_service"


def check_syntax(files: list[Path]) -> list[str]:
    """R-F2127 — FAIL the commit on any staged .py with a SyntaxError.

    Every OTHER check in this module ast.parse()s the file and silently
    `continue`s on SyntaxError, so a syntactically BROKEN file passed all of
    them and got committed. On 2026-06-28 an autonomous annotation campaign
    committed 31 such files (comments inserted mid-expression, e.g.
    `httpx.AsyncClient(timeout  # no-breaker: ...=3.0)`), making the whole tree
    un-importable and any deploy a guaranteed boot failure — and the report
    claimed it was "safe to deploy". This is the structural backstop: no broken
    Python reaches a commit, regardless of which tool (annotation, autonomous
    coder, human) produced it. Runs FIRST so it can't be masked by the
    parse-and-skip checks.
    """
    issues = []
    for fp in files:
        if fp.suffix != ".py":
            continue
        try:
            src = fp.read_text(encoding="utf-8")
        except Exception:
            continue
        try:
            compile(src, str(fp), "exec")
        except SyntaxError as e:
            try:
                rel = fp.relative_to(REPO_ROOT)
            except ValueError:
                rel = fp
            issues.append(
                f"  {rel}:{e.lineno} — SyntaxError: {e.msg}\n"
                f"    {(e.text or '').rstrip()}\n"
                f"    A broken .py must NEVER be committed — it breaks import and any deploy."
            )
    return issues

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
    # ── R-F3557 — PURE TRANSFORMS. Triaged one by one, not batch-exempted. ──
    #
    # §21a asks whether a code path's SUCCESS and FAILURE reach the brain. That
    # question only has meaning for a path that DOES something externally: a
    # fetch, a store write, an engine run. A pure function of its arguments has
    # no failure the brain could act on — it returns a value, or raises to a
    # caller that is itself wired. Wiring these would add noise to the ledgers
    # and would make the audit's green tick mean less, not more.
    #
    # Every module below was read and classified individually; the ones with a
    # real external effect were WIRED instead, under the same R-number.
    #
    # Pure static analysers over source text handed to them:
    "docker_reviewer", "go_reviewer", "rust_reviewer", "shell_reviewer",
    "sql_reviewer", "ts_js_reviewer", "yaml_reviewer",
    # R-F3564 — `facts` and `structured` were exempted here as pure transforms and
    # are now WIRED instead, so the entries are gone. The justification was
    # "the fetch that obtained that HTML is the path that carries the wiring".
    # Measured, nothing carried it: BOTH modules re-raise ZERO exceptions —
    # structured swallows 13 and facts 6, each to a `logger.debug` and an EMPTY
    # substitute — and researcher.extract_url_deep, the only caller, swallowed the
    # outer call too. Nothing propagated to any wired frame.
    #
    # They are also not pure: they feed the DD evidence path, where `tables = []`
    # after a crash is byte-identical to `tables = []` from a page with no tables.
    # A broken extractor therefore manufactured an unverified absence — including
    # of reg_numbers and ceos — which is the false-clean class the DD exists to
    # prevent. The purity test in docs/wiring_backlog_2026_07_28.md is "no network,
    # no try/except"; 13 and 6 try/except blocks fail it.
    # "Name normalisation shared across all sanctions sources": pure string work.
    "normalise",
    # Offline scoring harness for the C-3 corroboration eval — not a live path.
    "dd_independence_eval",
    # Computes transponder gaps from an AIS track it is handed.
    "ais_gap_detector",
    # log_redaction runs INSIDE the logging filter chain. Wiring it would be
    # recursive: emitting a brain signal from a log filter re-enters logging.
    "log_redaction",
    # ── R-F3567 — THE WIRING MACHINERY ITSELF. Same class as engine_wiring and
    # brain_hook at the top of this set, which have always been exempt; these two
    # were simply never added.
    #
    # wire.py IS the failure transport — it is the module that defines
    # @fail_wire. Its own docstring records design constraint #1, agreed at the
    # §20 review: "FAILURE-ONLY — never wire_success on every call (would wedge
    # the loop). Success stays at path-level entry points only (§21a)." Adding a
    # wire_success to it would contradict the rule it exists to implement, and
    # per-call success telemetry from the wrapper on every wired callable is
    # precisely the flood R-F1664 removed.
    "wire",
    # wiring_harness.py is the ENFORCEMENT harness (Gates A-E) for this same
    # audit — an ast-based scanner. It matches only because its own scanner
    # carries the decorator name as a string literal to search for; it runs no
    # engine and reaches nothing external.
    "wiring_harness",
}

# R-F1961 — THIS file (and any future pattern-authoring file) literally contains
# the danger-strings it scans for (os.fork regex, 'aria-internal', SSRF/url
# patterns) as DETECTION definitions, so a whole-file content check run on it
# self-flags. A check must not flag the file that defines its own patterns.
_PATTERN_AUTHORING_FILES = {"pre_commit_checks.py"}

# Known Windows-incompatible patterns (R-F1268)
#
# R-F3888 — THE MODULE-PREFIX PATTERNS NEEDED A LEFT BOUNDARY. `pty\.` matched the
# substring inside the ordinary English word **"empty."**, and blocked a real commit
# whose only sin was a comment ending "...the endpoint STILL read empty. Two
# independent causes...". `resource\.` and `fcntl\.` had the same shape and would
# have matched `myresource.` or `self.resource.`.
#
# `(?<![\w.])` is the correct guard, not `\b`: a bare `\b` still matches
# `self.resource.x` (the preceding `.` is a non-word char, so a boundary exists
# there), which is an attribute access, not the stdlib module. Excluding a preceding
# dot as well means only a genuine bare-module reference matches.
#
# A false positive here is expensive out of proportion to its size: the hook is the
# thing standing between a defect and main, so the first instinct on a bogus block
# is `--no-verify`, and a guard that people routinely bypass protects nothing
# (R-F3858's lesson in the other direction — a guard that cannot come back clean).
WINDOWS_INCOMPATIBLE_PATTERNS: list[tuple[str, str]] = [
    (r"os\.fork\s*\(", "os.fork() is not available on Windows"),
    (r"signal\.signal\s*\(", "signal.signal() has limited support on Windows"),
    (r"(?<![\w.])fcntl\.", "fcntl is not available on Windows"),
    (r"(?<![\w.])resource\.", "resource module is not available on Windows"),
    (r"(?<![\w.])pty\.", "pty module is not available on Windows"),
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
        # R-F3556 — A COMMENT IS NOT CODE. The scan matched inside comments, so
        # prose ABOUT a call was reported as the call. Live examples, both of
        # which CI failed on: self_healing.py:781
        # "# R-F1065: rs.ping() doesn't exist, probe with get_json" and
        # company_investigator.py:690 "# (the old _nm.search() raised
        # AttributeError on every DD, swallowed)". Both are notes explaining that
        # the function is absent — the gate flagged the documentation of a fixed
        # bug as the bug.
        code_part = _strip_comment(line)
        if not code_part.strip():
            continue
        for m in pattern.finditer(code_part):
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


@lru_cache(maxsize=None)
def _import_map(file_path: Path) -> tuple:
    """Imports as ``(local_name, module_path, lo_line, hi_line)`` bindings.

    R-F3556 — two defects, one function.

    SPEED: `resolve_module` re-read and re-`ast.parse()`d THE FILE BEING SCANNED
    once per call site, so a large file was fully parsed as many times as it had
    calls (aria_engine.py: 844). Built once per file now.

    SCOPE: it was also a flat, file-wide map, so a FUNCTION-LOCAL alias leaked
    across the whole file. Live example: dd_orchestrator.py:3676 has
    `from . import researcher as _r` inside one function, while line 2963 uses
    `_r` as a local dict in a different function 700 lines earlier — and the
    checker reported "Function 'get()' not found in aria_service.intel.researcher".
    That was the CI `test` job's "FUNCTION VERIFICATION FAILED", i.e. the gate was
    failing on correct code. A guard that cries wolf gets switched off.

    A module-level import binds for the whole file; a nested one binds only
    within its enclosing function or class.
    """
    bindings: list = []
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return ()

    def _walk(node, lo: int, hi: int) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Nested scope: its imports bind only inside its own line span.
                _walk(child, child.lineno, getattr(child, "end_lineno", None) or hi)
                continue
            if isinstance(child, ast.ImportFrom):
                base = child.module or ""
                for alias in child.names:
                    bindings.append((
                        alias.asname or alias.name,
                        f"aria_service.intel.{base}.{alias.name}" if base
                        else f"aria_service.intel.{alias.name}",
                        lo, hi,
                    ))
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    bindings.append((alias.asname or alias.name, alias.name, lo, hi))
            else:
                _walk(child, lo, hi)

    _walk(tree, 0, 10 ** 9)
    return tuple(bindings)


def resolve_module(obj_name: str, file_path: Path, line_num: Optional[int] = None) -> Optional[str]:
    """Resolve a short object name to its full module path at a given line.

    `line_num` is optional so existing callers keep working; without it the
    behaviour is the old file-wide lookup.
    """
    # R-F3556 — THE FILE'S OWN IMPORT WINS. KNOWN_ALIASES used to be consulted
    # FIRST, so a hardcoded guess overrode what the file actually declared.
    # Live: `KNOWN_ALIASES["ct"] = competitor_tracker`, while both
    # competitor_tracker.py and chain_correlator.py do
    # `from . import country_taxonomy as ct`. Every ct.to_iso2() /
    # ct.iso2_to_region() / ct._name_to_iso2_table() call was therefore checked
    # against the WRONG module and reported missing — 14 of 30 findings, all
    # false, including competitor_tracker.py flagged against itself. All three
    # functions do exist in country_taxonomy.
    #
    # The table is a fallback for names no import declares, never an override.
    best = None
    best_span = None
    for name, module, lo, hi in _import_map(file_path):
        if name != obj_name:
            continue
        if line_num is not None and not (lo <= line_num <= hi):
            continue
        span = hi - lo
        if best_span is None or span < best_span:   # narrowest enclosing scope wins
            best, best_span = module, span
    if best is not None:
        return best
    return KNOWN_ALIASES.get(obj_name)


def _resolve_module_uncached(obj_name: str, file_path: Path) -> Optional[str]:
    """Reference implementation kept so the cached path can be proved equivalent."""
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


@lru_cache(maxsize=None)
def _module_function_names(module_path: str) -> Optional[frozenset]:
    """Every function/method name defined in a module. None = module not found.

    R-F3556 — this used to be inlined in `function_exists`, which meant a FULL
    `ast.parse()` of the target module for EVERY CALL SITE. Measured on the real
    tree: aria_engine.py took 94.6s for its 844 calls (~112ms each) and main.py
    88.2s for 876. Only 37 of 588 files completed in 230s, so
    `pre-commit --check-all` ran for HOURS — which is what hung every CI run.

    Cached because it is pure within a run: the files do not change while the
    scan is executing.
    """
    parts = module_path.split(".")
    for base in [ARIA_SERVICE, REPO_ROOT]:
        file_path = base / f"{'/'.join(parts[1:] if parts[0] == 'aria_service' else parts)}.py"
        if file_path.exists():
            try:
                tree = ast.parse(file_path.read_text(encoding="utf-8"))
            except SyntaxError:
                return frozenset()
            # R-F3556 — CLASSES COUNT. Only FunctionDef/AsyncFunctionDef were
            # collected, so instantiating an imported class was reported as a
            # missing function: "Function 'DealContext()' not found", plus
            # UniversalWebCrawler and ARIADeceptionAnalyser. A constructor call
            # is the commonest thing a module exports after a function.
            return frozenset(
                node.name for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            )
    return None


# R-F3556 — names that are container/str/protocol methods, never a module-level
# API anyone means to verify. They appear here only when a LOCAL variable shadows
# an import alias in the same scope (live: `nm` is news_monitor at module level
# and a local string inside a function, so `nm.upper()` was reported as
# "Function 'upper()' not found in aria_service.intel.news_monitor").
# Scope tracking cannot fix that case — the shadowing is an assignment, not an
# import — so the honest filter is on the method name itself.
_BUILTIN_METHOD_NAMES = frozenset({
    "get", "keys", "values", "items", "pop", "popitem", "setdefault", "update",
    "append", "extend", "insert", "remove", "index", "count", "sort", "reverse",
    "add", "discard", "union", "intersection", "difference", "copy", "clear",
    "upper", "lower", "title", "casefold", "capitalize", "strip", "lstrip",
    "rstrip", "split", "rsplit", "splitlines", "join", "replace", "format",
    "startswith", "endswith", "encode", "decode", "find", "rfind", "zfill",
    "read", "write", "close", "flush", "seek", "tell", "readlines",
    "isdigit", "isalpha", "isalnum", "isspace", "islower", "isupper",
    # re.Match — live: `tm.group(1)` at routes/aria.py:6446, where `tm` is a
    # match object shadowing the tender_monitor alias.
    "group", "groups", "groupdict", "start", "end", "span",
})


# R-F3556 — KNOWN DEAD CALL SITES. This list may only SHRINK.
#
# Each entry is a call to a function that genuinely does NOT exist in the target
# module, sitting inside try/except, so it raises AttributeError and is
# swallowed — a silently dead branch. This codebase already carries a note about
# one of them ("the old _nm.search() raised AttributeError on every DD,
# swallowed"), so the class is known and real.
#
# They are NOT silenced and NOT auto-fixed. Several have a plausible intended
# target (knowledge.add_fact -> store_fact, feedback.get_stats ->
# get_feedback_stats, cert_transparency.search -> search_certs), but every one
# has never executed, so "fixing" it ACTIVATES a code path that has never run —
# a behaviour change per site, needing its own review and test, not a bulk
# rename to turn CI green. Rushing 21 of those onto a live system would be worse
# than the dead branches.
#
# The gate still FAILS on any call site not listed here, so the class cannot
# grow. And `test_known_dead_calls_are_still_dead` asserts every entry is STILL
# missing: the moment someone implements or renames one, its entry becomes stale
# and the test fails until it is removed. The list cannot rot into an excuse.
KNOWN_DEAD_CALLS = {
    ("aria_service.intel.knowledge", "search"),
    ("aria_service.intel.knowledge", "query"),
    ("aria_service.intel.knowledge", "add_fact"),
    ("aria_service.intel.news_monitor", "recall"),
    # R-F4068/R-F4069 (C-109/C-123) — `redis_store.hdel` REMOVED from this list
    # because it now exists. It was not merely a dead reference: the two live
    # callers in dd_trigger_pipeline.resolve_operator_pending() raised
    # AttributeError into a bare `except Exception: return False`, so an entity
    # stuck in `operator_pending` could never be resolved and the function
    # always reported failure. Third instance of this exact family in that one
    # module after R-F2486 (hget) and R-F2625 (hincrby). This is the shrink-only
    # contract working as intended: an entry that comes alive must be deleted.
    ("aria_service.intel.eval_golden_seed", "get_all"),
    ("aria_service.intel.correction_learner", "get_all"),
    ("aria_service.intel.feedback", "get_stats"),
    ("aria_service.intel.stale_knowledge_alerts", "check_stale"),
    ("aria_service.intel.reasoning_library", "store_case"),
    ("aria_service.intel.sources.cert_transparency", "search"),
}


def _strip_comment(line: str) -> str:
    """Drop a trailing ``#`` comment, respecting quotes.

    Naive ``split('#')`` would truncate a legitimate string containing a hash
    (URLs, colour codes, f-string fragments), so quote state is tracked.
    """
    out = []
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\":
                out.append(line[i:i + 2]); i += 2; continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#":
            break
        out.append(ch)
        i += 1
    return "".join(out)


def function_exists(module_path: str, func_name: str) -> bool:
    """Check if a function exists in a module by parsing its AST."""
    if func_name in _BUILTIN_METHOD_NAMES:
        return True          # a container/str method, not a module API claim
    names = _module_function_names(module_path)
    if names is None:
        return True  # Can't find module — pass through
    return func_name in names


def _is_capability_guarded(lines: list, line_num: int, func_name: str) -> bool:
    """True when the call is guarded by an explicit `hasattr(...)` probe.

    R-F3556 — an OPTIONAL capability, deliberately probed before use, is correct
    code and must not be reported as a missing function. Live example
    (entity_resolver.py:154):

        if hasattr(intel_ledger, "search_signals"):
            res = intel_ledger.search_signals(query)

    `search_signals` genuinely does not exist in intel_ledger — and the author
    handled exactly that. Flagging it is the checker misreading a deliberate
    fallback as a defect.
    """
    lo = max(0, line_num - 6)
    window = "\n".join(lines[lo:line_num])
    return f'hasattr(' in window and f'"{func_name}"' in window or \
           f"hasattr(" in window and f"'{func_name}'" in window


# R-F4069 (C-123) — deliberate, documented shadows, keyed on (path suffix,
# function name). Narrow by construction: allowlisting one name in one file must
# not exempt every builtin in that file, nor the same name elsewhere. Every
# entry states WHY, and a test asserts the reason is non-trivial.
BUILTIN_SHADOW_ALLOWLIST: dict[tuple[str, str], str] = {
    ("aria_service/intel/redis_store.py", "set"): (
        "redis_store deliberately mirrors the Redis command surface "
        "(set/get/delete/expire) so call sites read as Redis. The module "
        "already applies this checker's own remedy — `import builtins` at the "
        "top — which is the convention state_store.py uses too. Renaming it "
        "would churn every call site in the tree for no safety gain."
    ),
}


def _shadow_allowed(file_path: Path, func_name: str) -> bool:
    posix = str(file_path).replace("\\", "/")
    for (suffix, name), _reason in BUILTIN_SHADOW_ALLOWLIST.items():
        if name == func_name and posix.endswith(suffix):
            return True
    return False


def _non_method_functions(tree: ast.AST):
    """Yield every function definition that is NOT a class method.

    R-F4069 — this used `ast.walk`, which also yields methods, contradicting the
    docstring below and producing 31 false positives across the tree (21 of them
    a `set` method on some store/cache class). A method named `set` cannot
    shadow `builtins.set` at module scope. Nested functions ARE yielded: a `def
    sorted(...)` inside a function body really does rebind the name for the rest
    of that scope.
    """
    stack = list(getattr(tree, "body", []))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.ClassDef):
            # Descend past the class into its methods' BODIES (a nested
            # function inside a method still shadows), but never treat the
            # methods themselves as shadowing.
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    stack.extend(member.body)
                else:
                    stack.append(member)
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node
            stack.extend(node.body)
            continue
        for child in ast.iter_child_nodes(node):
            stack.append(child)


def check_builtin_shadowing(files: list[Path]) -> list[str]:
    """R-F1518 — Check that no module-level function shadows a Python built-in.

    A function named `set()` shadows `builtins.set()`, causing confusing bugs
    when code inside the module calls `set(...)` expecting the built-in but
    getting the module function instead. This check scans every changed .py
    file for functions whose names collide with built-in names.

    R-F4069 (C-123): three faults fixed.
      * It walked the whole AST, so it flagged METHODS as well — 31 false
        positives tree-wide, against exactly ONE real module-level shadow. Any
        commit touching one of those files was blocked.
      * No allowlist, so the one real shadow (`redis_store.set`, a deliberate
        Redis-API mirror that already `import builtins`) made that file
        permanently uncommittable. R-F4068 hit this adding an `hdel` wrapper.
      * It was defined TWICE, verbatim; the first copy was dead.

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
            for node in _non_method_functions(tree):
                if node.name not in builtin_names:
                    continue
                if _shadow_allowed(file_path, node.name):
                    continue
                issues.append(
                    f"{file_path}:{node.lineno}: function '{node.name}()' "
                    f"shadows builtins.{node.name}(). Rename the function "
                    f"or use 'builtins.{node.name}' explicitly."
                )
        except SyntaxError:
            pass

    return issues


def _wiring_call_aliases(content: str) -> dict:
    """Local names bound to wire_success / wire_failure, including aliases.

    R-F3565 — `from ..engine_wiring import wire_failure as _wf` binds the sink to
    `_wf`, and a literal `"wire_failure(" in content` check can never see the
    resulting `_wf(...)` call. AST rather than a regex so an import inside a
    function body (the lazy-import style used throughout intel/) is found too.
    """
    out = {"success": set(), "failure": set()}
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name in ("wire_success", "wire_failure") and alias.asname:
                out["success" if alias.name == "wire_success" else "failure"].add(alias.asname)
    return out


#: Brain sinks that report an OUTCOME rather than being failure-side by nature.
#: These are brain_hook's four public sink functions (brain_hook.py:571, 993,
#: 1155, 1186) plus the private forwarder brain_hook_bg calls. Enumerated from
#: that module rather than guessed — `absorb_silent` is the same sink as
#: `absorb` under a quieter name and sipri_ingest.py:214 wires its success
#: branch entirely through it.
_DIRECTIONAL_SINKS = (
    "absorb", "absorb_silent", "observe_self_event",
    "record_signal", "_record_signal",
)


def _absorb_success_directions(content: str) -> set[str]:
    """Which §21a branches an `absorb(..., success=...)` call actually reports.

    R-F3567 — `brain_hook.absorb` is listed in §21a as a qualifying sink and is
    DIRECTIONAL: the call states which branch it is reporting. Lumping it in with
    `@fail_wire` (failure-side by construction) made the gate tell
    calibration_auto_tune.py and registration_check.py to add a success signal
    they already emit.

      success=True    -> the SUCCESS branch
      success=False   -> the FAILURE branch
      success=<expr>  -> BOTH: the value is computed from the outcome, which is
                        the same contract `@wired` provides
      (no kwarg)      -> neither; nothing is claimed about direction

    AST-based, so `success=True` inside a docstring, a comment or a log string
    cannot count.
    """
    found: set[str] = set()
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func).split(".")[-1]
        if name not in _DIRECTIONAL_SINKS:
            continue
        for kw in node.keywords:
            if kw.arg != "success":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, bool):
                found.add("success" if kw.value.value else "failure")
            else:
                found.update(("success", "failure"))
    return found


def check_wiring_present(files: list[Path], *, require_intel: bool = True) -> list[str]:
    """Check that every changed intel module has at least one wire_success or wire_failure call (brain wiring).

    Modules that are purely data/configuration or are themselves wiring
    infrastructure are exempt (see WIRING_EXEMPT_MODULES).

    Returns a list of issue strings (empty if all pass).
    """
    issues = []

    for file_path in files:
        # R-F3727 — scope is now a PARAMETER, and there is still exactly ONE
        # definition of "wired" (the comment below warns that two definitions in
        # one repo is a hazard that has bitten here before — so this widens the
        # SCOPE, never the vocabulary).
        #
        # §21b says "no new module, engine, route, guard, or feature ships dark",
        # but enforcement only ever looked at aria_service/intel/. Measured
        # 2026-08-05: 33 modules OUTSIDE intel/ reach no brain sink at all and
        # swallow at least one failure — including 7 in metacognitive/ and 6 in
        # learning/, which are core cognition, not peripheral tooling. CI has
        # been reporting "All intel modules have brain wiring" the whole time,
        # which is true and reads as "everything is wired".
        #
        # Default stays intel-only so the pre-commit hook's behaviour is
        # unchanged; the CI audit passes require_intel=False.
        if require_intel and "intel" not in file_path.parts:
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

        # R-F3381 — §21a defines a wired path as one emitting ANY of
        # brain_hook.absorb / capability_gaps.record_gap / mistake_ledger.record /
        # a metric / a brain signal. This checked ONLY the literal strings
        # `wire_success(` / `wire_failure(`, so it measured one IMPLEMENTATION of
        # wiring rather than wiring, and reported modules that do reach a sink.
        #
        # Measured on the live tree: of 42 modules flagged "NO brain wiring
        # found", 3 reach a §21a sink another way — ecosystem_map.py (@fail_wire,
        # brain_hook, record_gap AND a brain signal), brain_ingest_queue.py,
        # regional_drift_monitor.py. The other 39 are genuinely dark, so this is a
        # precision fix, NOT a way to make the backlog disappear: the count goes
        # 42 -> 39, and the remaining 39 are real §21 violations.
        #
        # scripts/ecosystem_audit.py already carries the canonical token list.
        # Two definitions of "wired" in one repo is the two-writers hazard that
        # has bitten here before, so the alternates are spelled out once, here,
        # next to the rule they enforce.
        # R-F3385 — WHY THIS CHECK IS PER-MODULE AND NOT PER-FUNCTION.
        #
        # The obvious criticism of a textual, per-module check is that a module
        # decorating SOME entry points and leaving others bare reads as fully
        # wired. That is true. It was MEASURED before being "fixed", and the fix
        # would have been worse than the limit:
        #
        #   every public async entry point in intel/     1053, of which 562 (53%)
        #                                                unwired across 157 modules
        #   narrowed to those with a real failure mode    725, of which 342 (47%)
        #   (own try/except or external I/O)              unwired across 121 modules
        #
        # versus 54 modules from this check. The per-function lists are dominated
        # by accessors and bookkeeping — absorption_quarantine.list_pending,
        # api_query_monitor.record_query, audit_log.record — which have no engine
        # outcome to report. Wiring them would flood the ledgers, which is exactly
        # why grounding_reward is exempt (R-F2033), and a gate that fires on half
        # of all functions gets muted, after which it protects nothing.
        #
        # §21a is about ENGINE paths, not every callable. Per-module is the
        # honest granularity for a textual gate; per-function correctness is the
        # BACKLOG's job (docs/wiring_backlog_2026_07_28.md), decided per module by
        # someone who knows which entry points are engines. Do not "close" this
        # limit by widening the gate — it was tried, measured, and rejected.
        #
        # R-F3382 — @wired is the PREFERRED mechanism and covers BOTH branches.
        # engine_wiring.wired()'s own docstring calls it "the PREFERRED way to
        # wire a module ... guarantees both paths are covered", and the body does
        # exactly that: wire_failure() in the `except`, wire_success() on the
        # success path (plus a falsy-success check). A module using it has
        # NEITHER literal in its source, so R-F3381's fix — which recognised the
        # failure-side sinks — still flagged 11 correctly-wired modules:
        # academic, acled, cert_transparency, court_records, fcdo_sanctions,
        # sec_edgar, un_sc_sanctions and the worldbank_* family. Every one of
        # them had been written into the R-F3381 backlog as a real violation.
        #
        # Unlike @fail_wire/record_gap (failure-side only, so they may clear only
        # the "no wiring at all" verdict), @wired satisfies both categories by
        # construction and therefore clears the module outright.
        if re.search(r"@wired\b", content):
            continue

        # R-F3565 — ALIASED IMPORTS COUNT. The literal-token check cannot see
        # `from ..engine_wiring import wire_success, wire_failure as _wf` followed
        # by `_wf(...)`. Both knowledge packs (balkans_seed, latam_asia_pac_seed)
        # wire their failure branch exactly that way and were reported as missing
        # it — the gate demanding work that was already done, three lines below a
        # wire_success it DID see.
        _aliases = _wiring_call_aliases(content)
        has_wire_success = "wire_success(" in content or any(
            f"{a}(" in content for a in _aliases["success"])
        has_wire_failure = "wire_failure(" in content or any(
            f"{a}(" in content for a in _aliases["failure"])
        # Decorator/other sinks cover BOTH branches by construction: @fail_wire
        # wraps the callable so any unhandled exception reaches the brain, and a
        # record_gap/absorb call is a sink in its own right.
        has_other_sink = any(tok in content for tok in (
            "@fail_wire",
            "brain_hook.absorb", "brain_hook.observe_self_event",
            "capability_gaps.record_gap", "record_gap(",
            "mistake_ledger.record",
            "brain_signal", "/api/aria/brain/signal",
        ))
        # IMPORTANT — this clears ONLY the "no wiring at all" verdict. The first
        # cut of this fix did `if has_other_sink: continue`, which also cleared
        # the HALF-WIRED categories and took the report from 72 modules to 52. It
        # looked like a better fix and was a clamp: @fail_wire and record_gap are
        # FAILURE-side sinks, so they say nothing about whether the SUCCESS branch
        # reaches the brain — which is the half of §21a those categories exist to
        # check. Measured, the honest correction is 42 -> 39 in this category and
        # the 30 half-wired modules stay flagged.
        if has_other_sink and not has_wire_success and not has_wire_failure:
            continue

        # R-F3565 — A FAILURE-SIDE SINK SATISFIES THE FAILURE BRANCH.
        #
        # The comment above states the rule correctly — `@fail_wire`, `record_gap`
        # and friends ARE failure-side sinks — and then never lets that fact
        # satisfy the failure requirement; it only clears the "nothing at all"
        # verdict. So a module with `@fail_wire` (every unhandled exception
        # reaches the brain) PLUS `wire_success` has both branches covered and was
        # still reported as "has wire_success but NO wire_failure". Live: ocr.py,
        # deep_researcher.py and document_reader.py, all three decorated.
        #
        # The asymmetry is deliberate and stays: a FAILURE sink says nothing about
        # whether the SUCCESS branch is wired, so `has_other_sink` must NOT
        # satisfy has_wire_success. That was the R-F3382 clamp (72 -> 52) and it
        # is not being re-introduced — this credits one direction only.
        if has_other_sink:
            has_wire_failure = True

        # R-F3567 — `absorb(success=...)` IS DIRECTIONAL, and the token list above
        # treats it as failure-side. §21a's own definition names brain_hook.absorb
        # as a qualifying sink, and the call carries an explicit `success=` kwarg
        # saying WHICH branch it reports. Live: calibration_auto_tune.py:284 and
        # registration_check.py:411 both call `absorb(..., success=True)` — an
        # explicit success report to the brain — and were told to add a second
        # success signal. Read from the AST, so a `success=` inside a string or a
        # comment cannot count.
        _absorbed = _absorb_success_directions(content)
        if "success" in _absorbed:
            has_wire_success = True
        if "failure" in _absorbed:
            has_wire_failure = True

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
            # R-F3888 — match CODE, not prose. This scanned the raw line, so a
            # comment merely DISCUSSING a Windows-incompatible API was flagged as
            # using it. `_strip_comment` already exists in this file for exactly
            # this (it tracks quote state, so a `#` inside a string survives) and
            # was simply never applied here.
            code = _strip_comment(line)
            if not code.strip():
                continue
            for pattern, message in WINDOWS_INCOMPATIBLE_PATTERNS:
                if re.search(pattern, code):
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
        if "scripts" in file_path.parts:
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


# ── R-F2135: Pre-commit checklist checks ─────────────────────────────────────
# These implement the AGENTS.md section 8.7 pre-commit checklist as automated
# gates. Each check is a function that takes a list of staged file paths and
# returns a list of issue strings (empty = pass).
# NOTE: checks needing subprocess.run() live in scripts/pre-commit (where
# subprocess is already imported). This file has only pure-Python logic.


def check_capability_test_present(
    files: list[Path],
    changed_funcs: dict[str, set[str]] | None = None,
) -> list[str]:
    """R-F2135 — If the diff adds or modifies a function in aria_service/intel/,
    verify there is a corresponding test file that references it.

    This is the structural guard for CLAUDE.md section 3c: every fix MUST include
    a capability test that invokes the broken path. Unlike the existing
    check_capability_tests() which checks ALL functions in a changed file,
    this only checks functions whose names appear in the staged diff added
    lines (R-F1961 scoping via the changed_funcs parameter).

    When changed_funcs is None (CI --check-all mode), falls back to
    checking all public functions in the file.

    Returns a list of issue strings (empty if all pass).
    """
    issues = []
    test_dir = ARIA_SERVICE / "tests"

    for fp in files:
        if "intel" not in fp.parts or "tests" in fp.parts:
            continue
        if "scripts" in fp.parts:
            continue
        if fp.name in ("__init__.py", "main.py"):
            continue
        if fp.suffix != ".py":
            continue

        try:
            content = fp.read_text(encoding="utf-8")
        except Exception:
            continue

        all_funcs = set()
        for line in content.splitlines():
            m = re.match(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(", line)
            if m:
                all_funcs.add(m.group(1))

        if changed_funcs is not None:
            added = changed_funcs.get(fp.name, set())
            funcs_to_check = all_funcs & added
        else:
            funcs_to_check = all_funcs

        if not funcs_to_check:
            continue

        for func_name in sorted(funcs_to_check):
            if func_name.startswith("_"):
                continue
            if func_name in ("main", "lifespan", "setup", "teardown"):
                continue

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
                issues.append(
                    f"  {fp.name}: function '{func_name}()' has NO capability test.\n"
                    f"    Add a test in {test_dir}/test_rfXXXX_{fp.stem}.py that calls\n"
                    f"    {func_name}() and asserts the user-visible outcome\n"
                    f"    (CLAUDE.md section 3c — capability test requirement)."
                )

    return issues


def check_powershell_safety(
    files: list[Path],
    added_lines_by_file: dict[str, set[int]] | None = None,
) -> list[str]:
    """R-F2135/R-F4155 — Flag PowerShell-incompatible patterns where relevant.

    Bash ``.sh`` files are deliberately excluded: ``curl`` and ``&&`` are valid
    there, and replacing them with PowerShell syntax breaks Linux pod runners.
    The guard applies to PowerShell scripts and human-facing documentation where
    an unqualified command may be copied into the Windows development shell.

    Checks for:
    - curl without .exe (PowerShell aliases curl to Invoke-WebRequest)
    - double-ampersand as command separator (PowerShell uses semicolon)

    Only checks .ps1 and .md files. When added_lines_by_file is provided
    (pre-commit staged mode), only ADDED lines are checked.

    Returns a list of issue strings (empty if all pass).
    """
    issues = []
    check_extensions = {".ps1", ".md"}

    for fp in files:
        if fp.suffix not in check_extensions:
            continue
        if "tests" in fp.parts:
            continue
        if fp.name in _PATTERN_AUTHORING_FILES:
            continue

        try:
            content = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        _added = None if added_lines_by_file is None else added_lines_by_file.get(fp.name, set())
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if _added is not None and (i + 1) not in _added:
                continue
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue

            if re.search(r"(?<!\w)curl\s+(?!\.exe)", stripped) and "curl.exe" not in stripped:
                issues.append(
                    f"  {fp.name}:{i + 1} — bare curl (PowerShell aliases to Invoke-WebRequest)\n"
                    f"    Line: {stripped[:100]}\n"
                    f"    Use curl.exe on Windows, or python -c urllib for cross-platform."
                )
            if re.search(r"(?<!\$)(?<!\w)&&(?!\$)", stripped):
                issues.append(
                    f"  {fp.name}:{i + 1} — double-ampersand is not valid in PowerShell\n"
                    f"    Line: {stripped[:100]}\n"
                    f"    Use semicolon for sequencing, or if (dollar?) for conditional."
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


# ── R-F3878 — C-NUMBER COLLISION GATE ───────────────────────────────────────
#
# THE DEFECT: a C-number was claimed by writing a heading into
# docs/cure/defects.md. That is exactly the mechanism §2 abolished for R-numbers
# after 9 collisions in 50h — and C-numbers, having no allocator, went on
# colliding unnoticed FOUR times: C-18, C-19, C-22 and C-23 are each claimed
# twice by unrelated work. The register now cites itself ambiguously ("the C-18
# XSS residual" names one of two C-18s), which costs it the property that makes
# it a register — and §26 makes this file the record of what may be worked on.
#
# The four existing collisions are BASELINED in c_number_registry.LEGACY_COLLISIONS
# so this gate can be enabled today rather than after someone renumbers four
# entries and breaks every citation to them. Shrink-only, same contract as
# KNOWN_DEAD_CALLS: a THIRD claim on C-18 still fails, so it is recorded debt and
# never an amnesty.
def check_c_number_collisions(files=None, *, register=None) -> list[str]:
    """A C-number claimed twice by unrelated work. `files` is accepted and ignored:
    this is a property of one document, not of the staged set — a collision lands
    whether or not defects.md is in this commit."""
    # A LOCAL `import sys`, and the reason matters. The first draft of this gate
    # used `sys.path` without it — `pre_commit_checks.py` never imports sys — so it
    # raised NameError, which a bare `except Exception: return []` swallowed. The
    # gate would have silently certified "no collisions" FOREVER. That is the
    # R-F3791 blind-guard defect (a guard whose universe is empty always passes),
    # and it is the same shape as the three Phase A gates §1 records as certified by
    # an absence. Hence also: the failure branch below REPORTS, it never returns [].
    import sys
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from aria_service.intel import c_number_registry as _cnr
    except Exception as exc:
        return [
            f"  C-number collision gate COULD NOT RUN: {type(exc).__name__}: {exc}\n"
            f"    This is reported rather than passed silently — a guard that cannot\n"
            f"    see must never certify (§22). Fix the import or run:\n"
            f"      python scripts/admin/reserve_c_number.py audit"
        ]

    claims, readable = _cnr.claims_in_register(register)
    if not readable:
        return [
            "  C-number collision gate COULD NOT READ docs/cure/defects.md.\n"
            "    Reported, not passed: an unreadable register has 25+ invisible\n"
            "    claims, so 'no collisions found' would be meaningless (§22)."
        ]
    issues = []
    for num, count in sorted(_cnr.new_collisions(claims).items()):
        titles = claims.get(num, [])
        issues.append(
            f"  docs/cure/defects.md: C-{num:02d} is claimed {count}x by unrelated work.\n"
            + "".join(f"      · {t[:90]}\n" for t in titles)
            + f"    A C-number claimed by writing a heading is not claimed (§2).\n"
            f"    Get one with:  python scripts/admin/reserve_c_number.py reserve \"<title>\"\n"
            f"    Inspect with:  python scripts/admin/reserve_c_number.py audit"
        )
    return issues
