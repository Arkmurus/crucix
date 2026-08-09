"""R-F1068 — Full ecosystem audit.

Checks every module, function, test, and wiring point systematically.
Reports all gaps, bugs, and regressions in a structured format.
No subprocess — pure Python analysis.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)
sys.path.insert(0, str(REPO_ROOT))

# ── Configuration ──────────────────────────────────────────────────────

SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules", "data"}

# R-F3381 — modules that are unreferenced BY DESIGN, each with the reason.
# An entry here is a standing exemption, so it must name why; a bare list of
# names is how a gate quietly stops meaning anything.
DEAD_CODE_EXEMPT = {
    # R-F1859: a controlled, reproducible bug for the autonomous coder to fix
    # end-to-end. Its own docstring says "imported by NOTHING in production" and
    # "Remove after first gold is verified" — deliberate, not rot.
    "seeded_defect",
}
# ── R-F3573 — ORPHAN BASELINE. These lists may only SHRINK. ─────────────────
#
# The dead-code gate above reported **0 dead modules on a tree with 64 orphans**
# because it substring-matched names (a mention in a comment counted) and, since
# R-F3381, counted the TEST corpus as a referrer. check_orphan_modules() reads
# the import graph instead and keeps the two states apart.
#
# The debt is pre-existing and real, so it is PINNED rather than gated to zero —
# a gate that fails on day one gets muted, and then it protects nothing. Any
# module that becomes newly orphaned FAILS the build; anything fixed must be
# deleted from these lists (the audit prints FIXED: for entries that have gone).
#
# Spot-verified 2026-07-31, the instrument is not crying wolf: audit_trail.py's
# only apparent referrer is `case.audit_trail.append(...)`, a dict FIELD, not the
# module. continuous_learner (R-F1064, "Cost-Free Continuous Learning Engine"),
# github_search (R-F1061 OSINT) and tenant_namespace (R-F81, a MULTI-TENANT
# ISOLATION primitive) are likewise built, tested and unreachable.
ORPHAN_BASELINE_NEVER = {
    'intel/audit_trail.py',
    'intel/continuous_learner.py',
    'intel/dd_case_library.py',
    'intel/engagement.py',
    'intel/geoip_lookup.py',
    'intel/github_search.py',
    'intel/global_defence_knowledge.py',
    'intel/global_export_control.py',
    'intel/kaspersky_mitigation.py',
    'intel/osint_email_breach.py',
    'intel/portal_knowledge.py',
    'intel/sipri_knowledge.py',
    'intel/sources/worldbank_documents.py',
    'intel/tenant_namespace.py',
    'intel/vetting_standard_knowledge.py',
    'utils/command_cache.py',
}

# Imported ONLY by the test suite. Not automatically wrong — dd_independence_eval
# is an offline scoring harness and seeded_defect is a deliberate coder target —
# but a test-only ENGINE is dead code that happens to own a test. Each needs its
# own decision; three of these (behavioural_anomaly, quarantine_network,
# credential_self_destruct) are SECURITY subsystems with no production caller.
ORPHAN_BASELINE_TEST_ONLY = {
    'autonomous/autonomous_deploy.py',
    'env_bootstrap.py',
    'intel/antivirus.py',
    'intel/behavioural_anomaly.py',
    'intel/brave_answers.py',
    'intel/coder_canary.py',
    'intel/corroboration.py',
    'intel/credential_self_destruct.py',
    'intel/dd_independence_eval.py',
    'intel/dependency_integrity.py',
    'intel/eu_sanctions_ingest.py',
    'intel/infra_health.py',
    'intel/intel_expander.py',
    'intel/intel_quality.py',
    'intel/knowledge_learner.py',
    'intel/llm_builder.py',
    'intel/llm_pipeline.py',
    'intel/multi_lang_coder.py',
    'intel/multi_user_os.py',
    'intel/news_claims.py',
    'intel/performance_optimizer.py',
    'intel/product_page.py',
    'intel/protective_reply_drafter.py',
    'intel/provenance_watermark.py',
    'intel/public_api.py',
    'intel/quarantine_network.py',
    'intel/regional_compliance.py',
    'intel/self_protection.py',
    'intel/sipri_ingest.py',
    'intel/strategic_evolution.py',
    'intel/system_health.py',
    # R-F3799 — 'intel/training_orchestrator.py' removed. It is not an orphan that
    # got wired; the file has NEVER existed. Absent from disk AND from the entire
    # git history (`git log --all --diff-filter=A` finds no add). The baseline was
    # pinning debt for a module that was never in the repo, which is the decay
    # R-F3573's own test is written against: "a pinned list that outlives the debt
    # it records is a lie". An allowlist entry nothing can ever match is worse than
    # stale — it can never be discharged.
    'intel/truth_verifier.py',
    'intel/uk_ofsi_ingest.py',
    'intel/un_sanctions_ingest.py',
    'intel/wa_formatter.py',
    'intel/wiring_harness.py',
    'learning/deepseek_clients.py',
    'learning/held_out_split.py',
    'learning/pair_builder.py',
    'llm/local_llm.py',
    'utils/powershell_master.py',
    'vetting/packs/builtin.py',
    'writers/writer_orchestrator.py',
}

SKIP_FILES = {"__init__.py", "conftest.py"}
ARIA_SERVICE = REPO_ROOT / "aria_service"

WIRING_TOKENS = [
    "wire_success", "wire_failure",
    "brain_hook.absorb", "brain_hook.observe_self_event",
    "capability_gaps.record_gap", "mistake_ledger.record",
    "record_gap", "observe_self_event",
]

# ── Helpers ────────────────────────────────────────────────────────────

def _get_module_name(path: Path) -> str:
    """Get dotted module name from file path."""
    rel = path.relative_to(ARIA_SERVICE)
    return str(rel.with_suffix("")).replace(os.sep, ".")


def _read_file(path: Path) -> str:
    """Read file with UTF-8 fallback."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


# ── 1. Module inventory ────────────────────────────────────────────────

def scan_modules() -> list[Path]:
    """Scan all Python modules in aria_service."""
    modules = []
    for path in ARIA_SERVICE.rglob("*.py"):
        if any(skip in path.parts for skip in SKIP_DIRS):
            continue
        if path.name in SKIP_FILES:
            continue
        if "tests" in path.parts:
            continue
        modules.append(path)
    return modules


def scan_test_files() -> list[Path]:
    """Scan all test files."""
    tests_dir = ARIA_SERVICE / "tests"
    if not tests_dir.exists():
        return []
    return list(tests_dir.rglob("test_*.py"))


# ── 2. Syntax check ────────────────────────────────────────────────────

def check_syntax(path: Path) -> list[str]:
    """Check a Python file for syntax errors."""
    try:
        source = _read_file(path)
        ast.parse(source)
        return []
    except SyntaxError as e:
        return [f"{_get_module_name(path)}: {e}"]


# ── 3. Function inventory ──────────────────────────────────────────────

def scan_functions(path: Path) -> list[dict]:
    """Scan all function definitions in a file."""
    functions = []
    try:
        source = _read_file(path)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append({
                    "name": node.name,
                    "line": node.lineno,
                    "async": isinstance(node, ast.AsyncFunctionDef),
                    "has_docstring": bool(ast.get_docstring(node)),
                    "has_return": any(
                        isinstance(n, ast.Return) for n in ast.walk(node)
                    ),
                })
    except SyntaxError:
        pass
    return functions


# ── 4. Brain wiring check ──────────────────────────────────────────────

def check_wiring(path: Path) -> list[str]:
    """Check if a module has brain wiring tokens."""
    try:
        content = _read_file(path)
        return [t for t in WIRING_TOKENS if t in content]
    except Exception:
        return []


# ── 5. Test coverage ───────────────────────────────────────────────────

def check_test_coverage(modules: list[Path], test_files: list[Path]) -> dict:
    """Check which modules have corresponding test files."""
    coverage = {}
    test_contents = {}
    for tf in test_files:
        try:
            test_contents[tf.stem] = _read_file(tf)
        except Exception:
            test_contents[tf.stem] = ""

    for mod in modules:
        mod_name = mod.stem
        has_test = any(mod_name in content for content in test_contents.values())
        coverage[str(mod.relative_to(ARIA_SERVICE))] = has_test

    return coverage


# ── 6. Env flag inventory ──────────────────────────────────────────────

def scan_env_flags(modules: list[Path]) -> dict[str, list[str]]:
    """Find all ARIA_* env var gates."""
    env_flags: dict[str, list[str]] = {}
    pattern = re.compile(r'os\.(?:getenv|environ\.get)\s*\(\s*["\'](ARIA_\w+)["\']')

    for mod in modules:
        try:
            content = _read_file(mod)
            for m in pattern.finditer(content):
                key = m.group(1)
                env_flags.setdefault(key, []).append(
                    str(mod.relative_to(ARIA_SERVICE))
                )
        except Exception:
            continue

    return env_flags


# ── 7. Route inventory ─────────────────────────────────────────────────

def scan_routes() -> list[dict]:
    """Find all route definitions."""
    routes = []
    routes_file = ARIA_SERVICE / "routes" / "aria.py"
    if not routes_file.exists():
        return routes

    try:
        content = _read_file(routes_file)
        pattern = re.compile(
            r'@router\.(get|post|put|delete|patch)\(["\']([^"\']+)["\']\)'
        )
        for m in pattern.finditer(content):
            routes.append({
                "method": m.group(1).upper(),
                "path": m.group(2),
            })
    except Exception:
        pass

    return routes


# ── 8. Autonomous tasks ────────────────────────────────────────────────

def scan_autonomous_tasks() -> list[str]:
    """Find all autonomous task definitions."""
    tasks_file = ARIA_SERVICE / "autonomous" / "tasks.py"
    if not tasks_file.exists():
        return []

    try:
        content = _read_file(tasks_file)
        pattern = re.compile(r'tool_kind\s*==\s*["\']([^"\']+)["\']')
        return sorted(set(pattern.findall(content)))
    except Exception:
        return []


# ── 9. Brain hook registry ─────────────────────────────────────────────

def scan_brain_hook_registry() -> dict:
    """Check brain_hook module registry."""
    registry = {"modules": [], "topics": [], "weights": []}
    bh_file = ARIA_SERVICE / "intel" / "brain_hook.py"
    if not bh_file.exists():
        return registry

    try:
        content = _read_file(bh_file)
        # R-F2488: brain_hook.py declares `_MODULE_TOPICS: dict[...] = {...}` and
        # `_MODULE_WEIGHT: dict[...] = {...}` — NOT MODULE_REGISTRY / TOPIC_WEIGHTS.
        # The old names never matched, so the audit falsely reported 0 registered
        # modules / 0 topic weights. `[^=]*` skips the type annotation before `=`.
        mod_match = re.search(r"_MODULE_TOPICS[^=]*=\s*\{([^}]+)\}", content, re.DOTALL)
        if mod_match:
            registry["modules"] = re.findall(r'["\'](\S+?)["\']\s*:', mod_match.group(1))

        weight_match = re.search(r"_MODULE_WEIGHT[^=]*=\s*\{([^}]+)\}", content, re.DOTALL)
        if weight_match:
            registry["weights"] = re.findall(r'["\'](\S+?)["\']\s*:', weight_match.group(1))
    except Exception:
        pass

    return registry


# ── 10. Dead code check ────────────────────────────────────────────────

def _imported_stems(path: Path) -> set[str]:
    """Module stems this file actually IMPORTS, read from the AST.

    R-F3573 — a substring scan cannot tell an import from a mention. Every one of
    behavioural_anomaly, quarantine_network and credential_self_destruct "matched"
    only because `wiring_harness.py` lists them in a gap_type registry and
    `capability_gaps.py` names one in a COMMENT. Three security subsystems with no
    caller read as live because their names appear in a comment.
    """
    stems: set[str] = set()
    try:
        tree = ast.parse(_read_file(path))
    except Exception:
        return stems
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                stems.add(alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                stems.add(node.module.split(".")[-1])
            for alias in node.names:
                stems.add(alias.name.split(".")[-1])
        elif isinstance(node, ast.Call):
            # importlib.import_module("pkg.mod") — a real, resolvable import.
            try:
                func = ast.unparse(node.func)
            except Exception:
                continue
            if "import_module" in func:
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        stems.add(arg.value.split(".")[-1])
    return stems


def check_orphan_modules(modules: list[Path]) -> dict[str, list[str]]:
    """Modules nothing imports, split by WHO still imports them.

    R-F3573 — `check_dead_code` reported **0 dead modules on a tree with 64
    orphans** (19 imported by nothing at all, 45 imported only by the test
    suite). Two compounding reasons, and the second was introduced as a fix:

      1. SUBSTRING, NOT IMPORT. `if mod_name not in all_source` matches a name in
         a comment, a docstring, a log string or a registry key. See
         `_imported_stems` above for the three security subsystems this hid.

      2. TESTS COUNTED AS PRODUCTION. R-F3381 added the test corpus to the
         haystack because a scoring harness used only by tests was being called
         dead — correct as far as it went, but it collapsed two different states
         into one. A test-only HARNESS is fine; a test-only ENGINE is dead code
         that happens to own a test, and after R-F3381 the gate could not tell
         them apart. Kept separate here rather than picking one answer.

    Returns {"never_imported": [...], "test_only": [...]}.
    """
    prod_imports: set[str] = set()
    for mod in modules:
        prod_imports |= _imported_stems(mod)

    test_imports: set[str] = set()
    for tf in scan_test_files():
        test_imports |= _imported_stems(tf)

    never_imported: list[str] = []
    test_only: list[str] = []
    for mod in modules:
        stem = mod.stem
        if stem.startswith("_") or stem in ("main", "config"):
            continue
        if stem in DEAD_CODE_EXEMPT:
            continue
        # Entry points are EXECUTED, never imported — `python -m aria_service.cli.x`
        # and the standalone TUI client. Absence of an importer is their normal
        # state, not rot.
        if any(part in ("cli", "static", "scripts") for part in mod.parts):
            continue
        if stem in prod_imports:
            continue
        # R-F3573 — POSIX separators, always. `str(Path)` yields backslashes on
        # Windows and forward slashes on the Linux CI runner, so a baseline pinned
        # from a dev machine would match NOTHING in CI and report all 61 entries
        # as newly orphaned — a gate that fails 100% of the time on the platform
        # that actually runs it.
        (test_only if stem in test_imports else never_imported).append(
            mod.relative_to(ARIA_SERVICE).as_posix()
        )
    return {"never_imported": sorted(never_imported), "test_only": sorted(test_only)}


def check_dead_code(modules: list[Path]) -> list[str]:
    """Check for modules that are never imported anywhere."""
    all_source = ""
    for mod in modules:
        try:
            all_source += _read_file(mod) + "\n"
        except Exception:
            continue

    # R-F3381 — TESTS COUNT AS REFERENCES, and declared fixtures are not dead.
    #
    # This scanned only non-test modules (scan_modules skips tests/), so anything
    # used exclusively by the test suite read as "dead". Measured 2026-07-28, the
    # gate reported 3 dead modules and NONE of them was dead code:
    #   intel/dd_independence_eval.py  — a scoring harness imported by FOUR tests
    #   coder_demo/seeded_defect.py    — documented in its own docstring as
    #                                    "imported by NOTHING in production",
    #                                    a deliberate target for the autonomous
    #                                    coder (R-F1859)
    #   intel/auto/test_rf1191_new.py  — not a module at all: test_rf855 WROTE it
    #                                    into the production tree on every run
    #                                    (fixed by R-F3380)
    # A gate that is 0-for-3 on its own subject gets muted, and then it protects
    # nothing — the failure mode CLAUDE.md keeps returning to.
    for tf in scan_test_files():
        try:
            all_source += _read_file(tf) + "\n"
        except Exception:
            continue

    dead = []
    for mod in modules:
        mod_name = mod.stem
        if mod_name.startswith("_") or mod_name in ("main", "config"):
            continue
        if mod_name in DEAD_CODE_EXEMPT:
            continue
        if mod_name not in all_source:
            dead.append(str(mod.relative_to(ARIA_SERVICE)))

    return dead


# ── 11. Bug pattern check ──────────────────────────────────────────────

def check_bug_patterns(modules: list[Path]) -> list[dict]:
    """Check for common bug patterns."""
    bugs = []
    patterns = [
        (r"except\s*:\s*\n\s*pass", "Bare except: pass — hides errors"),
        (r"await\s+\w+\.\w+\(.*\)\s*#\s*type:\s*ignore", "Possible wrong async call"),
        (r"from\s+\.\w+\s+import\s+\*", "Wildcard import — namespace pollution"),
    ]

    for mod in modules:
        try:
            content = _read_file(mod)
            for pattern, desc in patterns:
                for m in re.finditer(pattern, content, re.MULTILINE):
                    line_num = content[:m.start()].count("\n") + 1
                    bugs.append({
                        "file": str(mod.relative_to(ARIA_SERVICE)),
                        "line": line_num,
                        "pattern": desc,
                        "code": m.group().strip()[:80],
                    })
        except Exception:
            continue

    return bugs


# ── 12. Cross-reference check ──────────────────────────────────────────

def check_cross_references(modules: list[Path]) -> list[dict]:
    """Check that function calls match real function names."""
    issues = []
    # Build function name index
    func_index: dict[str, list[str]] = {}
    for mod in modules:
        mod_name = _get_module_name(mod)
        funcs = scan_functions(mod)
        for f in funcs:
            func_index.setdefault(f["name"], []).append(mod_name)

    # Check for calls to non-existent functions (heuristic)
    call_pattern = re.compile(r'await\s+(\w+)\.(\w+)\(')
    for mod in modules:
        try:
            content = _read_file(mod)
            for m in call_pattern.finditer(content):
                obj = m.group(1)
                func = m.group(2)
                # Skip known external libraries
                if obj in ("httpx", "asyncio", "json", "os", "sys", "re",
                           "time", "datetime", "Path", "logging", "hashlib",
                           "random", "math", "copy", "typing"):
                    continue
                # Check if function exists in any module
                if func not in func_index and not func.startswith("_"):
                    # Could be a method on an object — only flag if obj is
                    # a known module alias
                    if obj in ("rs", "il", "ct", "tm", "pri", "nm", "cc",
                               "dp", "kn", "bh", "cg", "ml", "si", "sc"):
                        issues.append({
                            "file": str(mod.relative_to(ARIA_SERVICE)),
                            "line": content[:m.start()].count("\n") + 1,
                            "call": f"{obj}.{func}()",
                            "note": f"Function '{func}' not found in any module",
                        })
        except Exception:
            continue

    return issues


# ── MAIN ────────────────────────────────────────────────────────────────

def main() -> dict:
    """Run the full ecosystem audit."""
    start = time.time()
    results: dict[str, Any] = {}

    print("=" * 70)
    print("ARIA ECOSYSTEM AUDIT — R-F1068")
    print("=" * 70)

    # 1. Module inventory
    print("\n--- 1. Module Inventory ---")
    modules = scan_modules()
    test_files = scan_test_files()
    results["modules"] = {"total": len(modules), "test_files": len(test_files)}
    print(f"  Total modules: {len(modules)}")
    print(f"  Test files: {len(test_files)}")

    # 2. Syntax check
    print("\n--- 2. Syntax Check ---")
    syntax_errors = []
    for mod in modules:
        syntax_errors.extend(check_syntax(mod))
    results["syntax_errors"] = syntax_errors
    print(f"  Syntax errors: {len(syntax_errors)}")
    for e in syntax_errors[:10]:
        print(f"    {e}")

    # 3. Function inventory
    print("\n--- 3. Function Inventory ---")
    total_funcs = 0
    async_funcs = 0
    funcs_by_module: dict[str, int] = {}
    for mod in modules:
        funcs = scan_functions(mod)
        count = len(funcs)
        total_funcs += count
        async_funcs += sum(1 for f in funcs if f["async"])
        funcs_by_module[str(mod.relative_to(ARIA_SERVICE))] = count
    results["functions"] = {
        "total": total_funcs,
        "async": async_funcs,
        "sync": total_funcs - async_funcs,
        "by_module": funcs_by_module,
    }
    print(f"  Total functions: {total_funcs} ({async_funcs} async, {total_funcs - async_funcs} sync)")

    # 4. Brain wiring
    print("\n--- 4. Brain Wiring ---")
    wired = 0
    dark = []
    for mod in modules:
        tokens = check_wiring(mod)
        if tokens:
            wired += 1
        else:
            dark.append(str(mod.relative_to(ARIA_SERVICE)))
    results["wiring"] = {"wired": wired, "dark": dark}
    pct = wired / len(modules) * 100 if modules else 0
    print(f"  Wired: {wired}/{len(modules)} ({pct:.0f}%)")
    print(f"  Dark: {len(dark)}")
    for d in dark[:30]:
        print(f"    {d}")

    # 5. Test coverage
    print("\n--- 5. Test Coverage ---")
    coverage = check_test_coverage(modules, test_files)
    covered = sum(1 for v in coverage.values() if v)
    uncovered = [k for k, v in coverage.items() if not v]
    results["test_coverage"] = {"covered": covered, "uncovered": uncovered}
    pct = covered / len(coverage) * 100 if coverage else 0
    print(f"  Covered: {covered}/{len(coverage)} ({pct:.0f}%)")
    print(f"  Uncovered: {len(uncovered)}")
    for u in uncovered[:30]:
        print(f"    {u}")

    # 6. Env flags
    print("\n--- 6. Environment Flags ---")
    env_flags = scan_env_flags(modules)
    results["env_flags"] = {k: len(v) for k, v in env_flags.items()}
    print(f"  Total env flags: {len(env_flags)}")
    for key in sorted(env_flags.keys())[:40]:
        print(f"    {key}")

    # 7. Routes
    print("\n--- 7. Routes ---")
    routes = scan_routes()
    results["routes"] = {"total": len(routes)}
    prefixes = {}
    for r in routes:
        prefix = r["path"].split("/")[1] if r["path"].count("/") > 1 else "/"
        prefixes.setdefault(prefix, []).append(r)
    results["routes"]["by_prefix"] = {p: len(v) for p, v in prefixes.items()}
    print(f"  Total endpoints: {len(routes)}")
    for prefix in sorted(prefixes.keys()):
        print(f"    /{prefix}: {len(prefixes[prefix])} endpoints")

    # 8. Autonomous tasks
    print("\n--- 8. Autonomous Tasks ---")
    tasks = scan_autonomous_tasks()
    results["autonomous_tasks"] = tasks
    print(f"  Total tasks: {len(tasks)}")
    for t in sorted(tasks):
        print(f"    {t}")

    # 9. Brain hook registry
    print("\n--- 9. Brain Hook Registry ---")
    registry = scan_brain_hook_registry()
    results["brain_hook_registry"] = registry
    print(f"  Registered modules: {len(registry['modules'])}")
    print(f"  Topic weights: {len(registry['weights'])}")

    # 10. Dead code
    print("\n--- 10. Dead Code Check ---")
    dead = check_dead_code(modules)
    results["dead_modules"] = dead
    print(f"  Potentially dead modules: {len(dead)}")
    for d in dead[:30]:
        print(f"    {d}")

    # 10b. Orphan modules — R-F3573. The check above reported 0 on a tree with
    # 64 orphans; this one reads the IMPORT GRAPH. Pinned, not gated to zero:
    # the debt is real and pre-existing, so it may only SHRINK.
    print("\n--- 10b. Orphan Module Check (import graph) ---")
    orphans = check_orphan_modules(modules)
    results["orphan_modules"] = orphans
    never, test_only = orphans["never_imported"], orphans["test_only"]
    print(f"  Imported by NOTHING: {len(never)} (baseline {len(ORPHAN_BASELINE_NEVER)})")
    for d in never:
        mark = " " if d in ORPHAN_BASELINE_NEVER else " *NEW*"
        print(f"    {d}{mark}")
    print(f"  Imported ONLY by tests: {len(test_only)} (baseline {len(ORPHAN_BASELINE_TEST_ONLY)})")
    new_never = sorted(set(never) - ORPHAN_BASELINE_NEVER)
    new_test_only = sorted(set(test_only) - ORPHAN_BASELINE_TEST_ONLY)
    results["orphan_regressions"] = new_never + new_test_only
    if new_never or new_test_only:
        print(f"  REGRESSION — {len(new_never + new_test_only)} module(s) newly orphaned:")
        for d in new_never + new_test_only:
            print(f"    NEW: {d}")
    # Anti-rot: an entry that is no longer orphaned must be REMOVED from the
    # baseline, or the list decays into a permanent excuse. Reported, not fatal,
    # so a genuine fix is never punished — but it is loud.
    fixed = sorted((ORPHAN_BASELINE_NEVER | ORPHAN_BASELINE_TEST_ONLY)
                   - set(never) - set(test_only))
    if fixed:
        print(f"  {len(fixed)} baseline entry(ies) NO LONGER orphaned — remove them:")
        for d in fixed:
            print(f"    FIXED: {d}")

    # 11. Bug patterns
    print("\n--- 11. Bug Pattern Check ---")
    bugs = check_bug_patterns(modules)
    results["bug_patterns"] = bugs
    print(f"  Potential bugs: {len(bugs)}")
    for b in bugs[:30]:
        print(f"    {b['file']}:{b['line']} — {b['pattern']}: {b['code']}")

    # 12. Cross-reference check
    print("\n--- 12. Cross-Reference Check ---")
    xrefs = check_cross_references(modules)
    results["cross_reference_issues"] = xrefs
    print(f"  Cross-reference issues: {len(xrefs)}")
    for x in xrefs[:30]:
        print(f"    {x['file']}:{x['line']} — {x['call']} — {x['note']}")

    # Summary
    duration = time.time() - start
    print("\n" + "=" * 70)
    print(f"AUDIT COMPLETE — {duration:.1f}s")
    print("=" * 70)
    print(f"  Modules: {results['modules']['total']}")
    print(f"  Functions: {results['functions']['total']}")
    print(f"  Wired: {results['wiring']['wired']}/{results['modules']['total']}")
    print(f"  Test coverage: {results['test_coverage']['covered']}/{results['modules']['total']}")
    print(f"  Routes: {results['routes']['total']}")
    print(f"  Tasks: {len(results['autonomous_tasks'])}")
    print(f"  Env flags: {len(results['env_flags'])}")
    print(f"  Syntax errors: {len(results['syntax_errors'])}")
    print(f"  Dead modules: {len(results['dead_modules'])}")
    print(f"  Bug patterns: {len(results['bug_patterns'])}")
    print(f"  Cross-ref issues: {len(results['cross_reference_issues'])}")

    return results


if __name__ == "__main__":
    main_results = main()
    output_path = REPO_ROOT / "data" / "ecosystem_audit_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(main_results, f, indent=2, default=str)
    print(f"\nResults saved to {output_path}")

    # R-F1073: exit 1 if any issues found (CI enforcement)
    exit_code = 0
    if main_results.get("syntax_errors"):
        print(f"FAIL: {len(main_results['syntax_errors'])} syntax errors")
        exit_code = 1
    if main_results.get("cross_reference_issues"):
        print(f"FAIL: {len(main_results['cross_reference_issues'])} cross-reference issues")
        exit_code = 1
    if main_results.get("bug_patterns"):
        print(f"FAIL: {len(main_results['bug_patterns'])} bug patterns")
        exit_code = 1
    if main_results.get("dead_modules"):
        print(f"FAIL: {len(main_results['dead_modules'])} dead modules")
        exit_code = 1
    # R-F3573 — only a REGRESSION fails. The 61 pre-existing orphans are pinned
    # in ORPHAN_BASELINE_*; gating on the total would make this red on day one
    # and it would be muted within a week. A module that becomes newly
    # unreachable is a different thing: it is a change someone just made.
    if main_results.get("orphan_regressions"):
        regressions = main_results["orphan_regressions"]
        print(f"FAIL: {len(regressions)} module(s) newly orphaned — nothing imports them:")
        for d in regressions:
            print(f"       {d}")
        print("       Either wire it into a caller, or — if it is an entry point or a")
        print("       deliberate harness — add it to ORPHAN_BASELINE_* WITH ITS REASON.")
        exit_code = 1
    sys.exit(exit_code)
