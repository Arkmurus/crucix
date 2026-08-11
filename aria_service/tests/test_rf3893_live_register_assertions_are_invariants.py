"""R-F3893 — a test pinned a LIVE REGISTER COUNT and broke when the register was used.

`test_rf3878_c_number_allocator.py` asserted `len(claims) == 26` against the real
`docs/cure/defects.md`. Hours later a peer legitimately added C-27 — correctly, via
the allocator R-F3878 had just shipped — and the test went red.

**A test that fails whenever the thing it guards is USED is worse than no test.** The
only way to green it is to bump a magic number, which proves nothing, costs a commit,
and trains the next person to treat that file's failures as noise. It is the same
family as a stale hand-maintained list (§27d) and as the baseline that must be
diffed as a SET rather than a count (§16).

The fix was to assert INVARIANTS instead:
  * the wide (`#{2,4}`) and narrow (`###`) heading patterns must read the LIVE
    document identically — which is exactly what "widening changed nothing" means;
  * live collisions are compared against `LEGACY_COLLISIONS` (the shrink-only
    baseline constant) rather than a literal `{18, 19, 22, 23}`.

This file guards the CLASS: no assertion may re-couple a test to a count of the
living register.
"""
from __future__ import annotations

import ast

from aria_service.tests._source_probe import repo_path

_GUARDED = "aria_service/tests/test_rf3878_c_number_allocator.py"


def _live_register_functions(tree: ast.Module) -> list[ast.FunctionDef]:
    """Test functions that read the REAL register (not a tmp_path fixture)."""
    out = []
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        src = ast.dump(fn)
        if "docs/cure/defects.md" in src or "repo_path" in src:
            out.append(fn)
    return out


def test_no_test_pins_a_count_of_the_living_register():
    """THE REGRESSION GUARD. `len(...) == <int>` against the live register is the
    exact shape that broke; a `== <int>` comparison on a len() in a live-register
    test may not come back."""
    tree = ast.parse(repo_path(_GUARDED).read_text(encoding="utf-8"))
    offenders = []
    for fn in _live_register_functions(tree):
        # ASSERT + EQUALITY only. A first cut matched any len()-vs-int Compare and
        # immediately flagged `len(t) > 1` inside a legitimate comprehension filter
        # — a guard that cries wolf gets muted (R-F3888, same lesson). The defect is
        # specifically an ASSERTION that the live register has EXACTLY N entries.
        for node in ast.walk(fn):
            if not isinstance(node, ast.Assert):
                continue
            for cmp_ in ast.walk(node.test):
                if not isinstance(cmp_, ast.Compare):
                    continue
                if not any(isinstance(o, ast.Eq) for o in cmp_.ops):
                    continue
                left = cmp_.left
                is_len = (isinstance(left, ast.Call)
                          and isinstance(left.func, ast.Name)
                          and left.func.id == "len")
                has_int_const = any(
                    isinstance(c, ast.Constant) and isinstance(c.value, int)
                    and not isinstance(c.value, bool)
                    for c in cmp_.comparators)
                if is_len and has_int_const:
                    offenders.append(f"{fn.name}:{cmp_.lineno}")
    assert not offenders, (
        "a test re-pinned a COUNT of the live defect register — it will break the "
        f"next time someone legitimately adds an entry: {offenders}. Assert an "
        f"invariant instead (R-F3893).")


def test_the_guard_can_actually_fire():
    """R-F3858 — a guard that cannot fail is not a guard. Proves the AST detector
    catches the exact pattern that was removed."""
    tree = ast.parse(
        "from aria_service.tests._source_probe import repo_path\n"
        "def test_bad():\n"
        "    claims = read(repo_path('docs/cure/defects.md'))\n"
        "    assert len(claims) == 26\n")
    fns = _live_register_functions(tree)
    assert fns, "the detector must recognise a live-register test"
    found = any(
        isinstance(a, ast.Assert)
        and any(isinstance(n, ast.Compare)
                and any(isinstance(o, ast.Eq) for o in n.ops)
                and isinstance(n.left, ast.Call)
                and getattr(n.left.func, "id", "") == "len"
                and any(isinstance(c, ast.Constant) and isinstance(c.value, int)
                        for c in n.comparators)
                for n in ast.walk(a.test))
        for fn in fns for a in ast.walk(fn))
    assert found, "the detector failed to flag `len(claims) == 26`"


def test_the_invariants_that_replaced_the_count_are_present():
    """...and the replacement must still be doing real work, not merely absent."""
    src = repo_path(_GUARDED).read_text(encoding="utf-8")
    assert "LEGACY_COLLISIONS" in src, (
        "live collisions must be compared against the shrink-only baseline constant")
    assert "set(claims) == narrow" in src, (
        "the wide/narrow pattern equivalence check is the invariant that replaced "
        "the hardcoded count")
