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
