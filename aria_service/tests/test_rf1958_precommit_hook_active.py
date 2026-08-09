"""R-F1958 — activate the orphaned 10-check pre-commit hook (capability test).

The playbook checks (scripts/pre_commit_checks.py, driven by scripts/pre-commit)
NEVER RAN: core.hooksPath is scripts/git-hooks/ (which only had pre-push), while
the checks' own installer pointed at the look-alike scripts/githooks/ dir — the
two near-identical names diverged and orphaned the checks. R-F1958 adds a
pre-commit hook to the ACTIVE dir.

These tests assert (a) the hook is wired into the active path and is fail-safe,
and (b) the underlying enforcement it activates actually detects a violation.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))


def test_hook_present_in_active_hookspath_dir():
    hook = _REPO / "scripts" / "git-hooks" / "pre-commit"
    assert hook.exists(), "pre-commit hook must live in the ACTIVE core.hooksPath dir (git-hooks)"
    body = hook.read_text(encoding="utf-8")
    # It must invoke the real checker...
    assert "scripts/pre-commit" in body
    # ...and be FAIL-SAFE: block only on the explicit sentinel, never on a crash.
    assert "VERIFICATION FAILED" in body


def test_underlying_checks_actually_catch_a_violation(tmp_path):
    """Prove the enforcement the hook now activates is real, not theatre."""
    import pre_commit_checks as p

    bad = tmp_path / "bad_module.py"
    bad.write_text(
        "def do_thing():\n"
        "    return {'success': True, 'data': 1}\n",  # false success, no verification
        encoding="utf-8",
    )
    issues = p.check_false_success([bad])
    assert issues, "check_false_success must flag an unverified success:True (the hook now runs this)"


def test_checker_script_exists_and_imports_checks():
    checker = _REPO / "scripts" / "pre-commit"
    assert checker.exists()
    body = checker.read_text(encoding="utf-8")
    for fn in ("check_wiring_present", "check_false_success",
               "check_windows_compat", "find_direct_function_calls"):
        assert fn in body, f"{fn} must be wired into the checker the hook runs"


def test_checker_imports_every_check_it_calls():
    """Regression guard for the NameError that crashed the whole checker:
    check_builtin_shadowing was CALLED but never imported, so every run died
    before any check ran. Assert each called check is also imported."""
    import ast

    checker = _REPO / "scripts" / "pre-commit"
    missing = _unresolved_checks(checker.read_text(encoding="utf-8"))
    assert not missing, (
        f"checker calls but never imports or defines: {sorted(missing)} "
        f"(NameError at runtime)")


def _unresolved_checks(source: str) -> set[str]:
    """Names the checker CALLS but neither imports nor defines — i.e. NameErrors.

    R-F3797 — a check DEFINED in the checker is just as available as an imported
    one, so definitions are resolved from the AST. This previously subtracted a
    hand-written `{"check_all_files"}` exemption, correct for the one local check
    that existed when it was written; when R-F3683 added a second
    (`check_committed_secrets`, scripts/pre-commit:292) the guard reported
    "NameError at runtime" for a function that is defined at module level and runs
    fine — `python scripts/pre-commit` exits 0. A deny-list that must be edited
    whenever correct code is added encodes a snapshot, not the rule.
    """
    import ast

    tree = ast.parse(source)
    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(a.asname or a.name for a in node.names)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id.startswith(("check_", "find_")):
                called.add(node.func.id)
    defined = {n.name for n in tree.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return called - imported - defined


def test_the_unresolved_check_detector_still_catches_the_original_defect():
    """R-F3797 must not have widened the exemption into a hole.

    The defect this file exists for was `check_builtin_shadowing` CALLED but never
    imported, which killed the whole checker before any check ran. Resolving local
    definitions must not make that undetectable."""
    assert _unresolved_checks(
        "from pre_commit_checks import check_a\n"
        "check_a(x)\n"
        "check_builtin_shadowing(x)\n"      # neither imported nor defined
    ) == {"check_builtin_shadowing"}

    # ...and a locally defined check is correctly treated as available.
    assert _unresolved_checks(
        "def check_local(paths):\n    return []\n"
        "check_local(x)\n"
    ) == set()
