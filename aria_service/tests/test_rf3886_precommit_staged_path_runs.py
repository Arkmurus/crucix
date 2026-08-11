"""R-F3886 — the pre-commit staged path crashed, so every local check failed open.

THE DEFECT, MEASURED 2026-08-11 by running the real entry point on a real staged set:

    $ python scripts/pre-commit
    NameError: name 'lines' is not defined. Did you mean: 'files'?   (scripts/pre-commit:502)

Two sites in the staged path passed `lines` to `_is_capability_guarded`. That name
does not exist in those scopes — the variable is `added_lines`. `lines` belongs to
`check_all_files()`, where the guard call was copied from.

WHY IT WAS WORSE THAN A CRASH. The git hook is deliberately FAIL-OPEN: it blocks only
when the checker prints the explicit `VERIFICATION FAILED` sentinel, so that a tooling
bug can never wedge commits. A crash prints no sentinel. So the checker died on the
first staged .py with any resolvable call, and **all ~12 checks were skipped and the
commit allowed** — silently, every time.

AND CI COULD NOT SEE IT. `--check-all` runs `check_all_files()`, which never reaches
these lines, so the CI step stayed green while the staged path was dead. Green in one
mode, broken in the other, with nothing comparing them (§23: a test that is green
while the live flow fails is a WRONG test).

TWO FAILURES HAD STACKED. `core.hooksPath` was also unset (R-F3885), so the hook was
not invoked at all. Fixing either alone would have bought nothing: activate the hook
and it crashes; fix the crash and nothing calls it. That is why "the checks are
enforced" survived as a belief for so long — every individual artefact existed.

THE FIRST THING IT CAUGHT, one second after being revived, was a REAL §21a violation
in `brave_usage.py` (wire_failure on every error path, wire_success on none) — a
module shipped earlier the same day.
"""
from __future__ import annotations

import ast
import subprocess
import sys

from aria_service.tests._source_probe import repo_path


def _functions_with_guard_calls():
    """Every `_is_capability_guarded(<name>, ...)` call, with its enclosing scope."""
    src = repo_path("scripts/pre-commit").read_text(encoding="utf-8")
    tree = ast.parse(src)
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bound = {a.arg for a in fn.args.args}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        bound.add(t.id)
            elif isinstance(node, (ast.For, ast.comprehension)):
                tgt = getattr(node, "target", None)
                if isinstance(tgt, ast.Name):
                    bound.add(tgt.id)
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_is_capability_guarded"
                    and node.args
                    and isinstance(node.args[0], ast.Name)):
                out.append((fn.name, node.args[0].id, bound, node.lineno))
    return out


def test_every_guard_call_uses_a_name_bound_in_its_own_scope():
    """Pins the CLASS, not the instance. An AST check catches the next copy-paste of
    this call into a scope that names its lines differently; a string match for
    `_is_capability_guarded(lines` would not."""
    calls = _functions_with_guard_calls()
    assert calls, "no _is_capability_guarded call sites found — did the checker move?"
    bad = [(fn, arg, ln) for fn, arg, bound, ln in calls if arg not in bound]
    assert not bad, (
        "scripts/pre-commit passes an UNBOUND name to _is_capability_guarded — the "
        "staged path will die with NameError and, because the hook is fail-open, "
        f"every local check will be silently skipped: {bad}")


def test_the_staged_path_and_the_ci_path_use_different_line_sources():
    """The reason the bug existed AND the reason CI could not see it: two call sites
    over two different lists. Documents the asymmetry so the next reader does not
    'unify' them by passing whole-file lines to diff-derived line numbers."""
    by_fn = {fn: arg for fn, arg, _b, _l in _functions_with_guard_calls()}
    assert by_fn.get("check_all_files") == "lines", (
        "check_all_files walks whole files; its line numbers index content.splitlines()")
    assert "added_lines" in set(by_fn.values()), (
        "the staged path walks diff-added lines; its line numbers index those")


def test_the_real_entry_point_runs_without_crashing():
    """CAPABILITY TEST — drives the actual command the hook runs (§3c/§23), not a
    helper. Asserts only that it does not CRASH: exit 1 on real findings is the
    checker working, and this test must not depend on what happens to be staged."""
    proc = subprocess.run(
        [sys.executable, str(repo_path("scripts/pre-commit"))],
        # R-F3459 — the inner bound must fire BEFORE the per-test budget (120s), or
        # a hang kills the pytest process with no summary instead of failing this
        # test. A first draft used 600s: timeout inversion, caught by that guard.
        # 90s is generous — the staged checker runs in ~1s.
        capture_output=True, text=True, timeout=90,
        cwd=str(repo_path(".")), encoding="utf-8", errors="replace",
    )
    blob = (proc.stdout or "") + (proc.stderr or "")
    for fatal in ("NameError", "Traceback (most recent call last)"):
        assert fatal not in blob, (
            f"scripts/pre-commit crashed with {fatal}. The hook is fail-open, so a "
            f"crash means EVERY local check is skipped and the commit is allowed:\n"
            f"{blob[-1500:]}")
    assert proc.returncode in (0, 1), f"unexpected exit {proc.returncode}: {blob[-600:]}"


def test_the_hook_only_blocks_on_the_explicit_sentinel():
    """The fail-open contract is what turned this crash into silence, so it must stay
    deliberate and visible rather than being 'hardened' into fail-closed — a checker
    bug must never wedge every commit in the repo."""
    hook = repo_path("scripts/git-hooks/pre-commit").read_text(encoding="utf-8")
    assert "VERIFICATION FAILED" in hook
    assert "--no-verify" in hook, "the documented bypass must remain discoverable"
