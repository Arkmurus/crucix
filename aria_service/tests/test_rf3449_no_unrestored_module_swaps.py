"""R-F3449 — a test may not permanently replace another module's callable.

THE CLASS. `some_module.func = fake` with no restore poisons every later test in the
session. That is the root of order-dependent failures: the test passes alone and fails
in-suite, and the failure surfaces in a DIFFERENT file, so the cost lands on whoever is
unlucky enough to be reading the victim.

It produced 11 of the 15 order-dependent failures in the R-F3448 baseline, from just TWO
offenders:
  * test_rf2441_fatf_ingest        replaced knowledge.store_fact AND both engine_wiring
                                  wire_success/wire_failure -> 3 failures
  * test_rf1771_eval_external_result  replaced routes.aria.rs with a _FakeRS implementing
                                  only set_json/get_json -> 4 failures across two files
                                  ("no attribute 'get'" and a 503 from rs.zadd)

`monkeypatch.setattr` and `unittest.mock.patch` both auto-restore and are the correct tools.
A bare assignment is not.

THIS GUARD EXISTS SO THE CLASS CANNOT GROW. The remaining known offenders are allowlisted
below with the reason each is still tolerated; the allowlist may only ever SHRINK. A new
unrestored swap fails this test immediately, in the file that introduced it, instead of
silently breaking something else a thousand tests later.

Scoped deliberately to CALLABLE replacement. Resetting module state to a clean value
(`_cache = {}`, `_flag = None`) is correct test isolation and is idempotent, so it cannot
poison anything — an un-narrowed version of this scan reported 416 hits, nearly all benign.
"""
from __future__ import annotations

import ast
import pathlib

TESTS = pathlib.Path(__file__).resolve().parent

#: Names that mark a value as a STUB rather than real state. Matched case-insensitively and
#: after stripping leading underscores — the first cut of this scanner was lowercase-only
#: and constructor-blind, so it MISSED `_a.rs = _FakeRS()`, the very leak that caused four
#: of the failures above. An instrument that misses the case you already know about has not
#: been verified.
_STUBBY = ("fake", "stub", "mock", "no_op", "noop", "counting", "boom", "dummy", "spy",
           "patched")

#: VERIFIED SAFE — the assignment IS restored, by a wrapper or fixture in a DIFFERENT
#: function than the one assigning, which this scanner cannot see (it only inspects the
#: assigning function's own try/finally). Each was read and confirmed by hand:
#:
#:   test_rf2092  `finally: _reset(eo)`, and _reset sets eo._pool = None
#:   test_rf2423  `_run_all` snapshots both modules and restores in a finally (R-F3314
#:                fixed this exact thing already)
#:   test_rf2433  a fixture saves and restores BOTH the sys.modules entries AND the two
#:                student functions
#:
#: These are the scanner's FALSE POSITIVES, kept here so nobody "fixes" a correct test.
#: Three of the original ten flagged entries turned out to be this — verify before fixing.
#:   test_rf459   an autouse TEARDOWN fixture in the same file calls _reset_state(), which
#:                sets semantic_search._embedder = None. Added by R-F3449 precisely because
#:                the original code only reset on the way IN, so the last test's fake
#:                survived the file. A fixture-based restore is a fourth shape the scanner
#:                cannot see, alongside helper-in-finally and wrapper-function restores.
RESTORED_ELSEWHERE: frozenset[str] = frozenset({
    "test_rf2423_diagnostic_no_inline_run.py::intel_pkg.redis_store",
    "test_rf2423_diagnostic_no_inline_run.py::intel_pkg.self_diagnostic",
    "test_rf2433_seed_all_regions.py::student.update_regional_mastery",
    "test_rf2433_seed_all_regions.py::student.get_regional_heatmap",
    "test_rf459_embedder_race_safe.py::semantic_search._embedder",
})

#: GENUINELY UNRESTORED and still tolerated — MAY ONLY SHRINK. Currently EMPTY: every real
#: leak found in the R-F3449 sweep has been fixed (test_rf2441, test_rf1771 — which together
#: caused 11 of the 15 order-dependent failures — plus test_rf2178, test_rf2438, test_rf2817,
#: test_rf410 and test_rf459, which were latent: they poisoned modules that no later test
#: happened to re-use, which is luck rather than safety).
KNOWN_UNRESTORED: frozenset[str] = frozenset()

#: Everything the guard tolerates, for whichever reason.
_EXEMPT = RESTORED_ELSEWHERE | KNOWN_UNRESTORED


def _stubby(name: str) -> bool:
    return name.lstrip("_").lower().startswith(_STUBBY)


def _module_aliases(tree: ast.AST) -> set[str]:
    """Names in this file bound to a MODULE (import x / from pkg import mod)."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith(("aria_service", "scripts")):
                for a in node.names:
                    out.add(a.asname or a.name)
    return out


#: Helper names that, called in a `finally`, count as putting module state back.
#: Needed because a restore is often factored into a helper: test_rf2092 does
#: `finally: _reset(eo)` where `_reset` sets `eo._pool = None`. The first version of this
#: scanner could only see a direct reassignment, so it reported that CORRECT test as an
#: offender — a false positive, and the reason this list exists. Verify before "fixing":
#: the code was right and the instrument was wrong.
_RESTORE_HELPERS = ("reset", "restore", "teardown", "cleanup", "undo", "unpatch")


def _has_restore(fn: ast.AST, dotted: str) -> bool:
    """A try/finally in the same function that puts the attribute back."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Try) and node.finalbody:
            for stmt in node.finalbody:
                for t in ast.walk(stmt):
                    if isinstance(t, ast.Attribute):
                        try:
                            if ast.unparse(t) == dotted:
                                return True
                        except Exception:
                            pass
                    if isinstance(t, ast.Name) and t.id.startswith("_orig"):
                        return True
                    # a reset/restore HELPER invoked in the finally
                    if isinstance(t, ast.Call):
                        fname = ""
                        if isinstance(t.func, ast.Name):
                            fname = t.func.id
                        elif isinstance(t.func, ast.Attribute):
                            fname = t.func.attr
                        if fname.lstrip("_").lower().startswith(_RESTORE_HELPERS):
                            return True
    return False


def find_unrestored_swaps() -> set[str]:
    """Every `<module>.<attr> = <stub>` in a test function with no restore."""
    found: set[str] = set()
    for path in sorted(TESTS.glob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        aliases = _module_aliases(tree)
        if not aliases:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            local = aliases | _module_aliases(
                ast.Module(body=list(ast.walk(fn)), type_ignores=[]))
            for node in ast.walk(fn):
                if not isinstance(node, ast.Assign):
                    continue
                for tgt in node.targets:
                    if not isinstance(tgt, ast.Attribute):
                        continue
                    if not isinstance(tgt.value, ast.Name) or tgt.value.id not in local:
                        continue
                    try:
                        dotted = ast.unparse(tgt)
                    except Exception:
                        continue
                    if _has_restore(fn, dotted):
                        continue
                    v = node.value
                    swap = (
                        isinstance(v, ast.Lambda)
                        or (isinstance(v, ast.Name) and _stubby(v.id))
                        or (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                            and (_stubby(v.func.id)
                                 or v.func.id in ("Mock", "MagicMock", "AsyncMock")))
                        or (isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
                            and (_stubby(v.func.attr)
                                 or v.func.attr in ("Mock", "MagicMock", "AsyncMock")))
                    )
                    if swap:
                        found.add(f"{path.name}::{dotted}")
    return found


def test_no_new_unrestored_module_swaps():
    """THE GUARD. A new bare swap fails HERE, in review, not in an unrelated file later."""
    new = sorted(find_unrestored_swaps() - _EXEMPT)
    assert not new, (
        "these tests permanently replace another module's callable with no restore, which "
        "poisons every later test in the session (the root of order-dependent failures):\n  "
        + "\n  ".join(new)
        + "\n\nUse `monkeypatch.setattr(mod, 'attr', fake)` — it restores at teardown. If "
          "the file must stay runnable as a script (no fixtures), use an explicit "
          "try/finally that puts the original back, as test_rf2441_fatf_ingest.py now does."
    )


def test_the_exempt_list_only_shrinks():
    """A tolerated entry that no longer offends must LEAVE the list, so it stays an honest
    inventory rather than drifting into decoration.

    Subtracts `_EXEMPT`, not `KNOWN_UNRESTORED`. That distinction matters: once every real
    leak was fixed KNOWN_UNRESTORED became empty, so a version of this test that subtracted
    only KNOWN_UNRESTORED could never fail — a vacuous guard, the exact defect class this
    whole R-number exists to remove. It has to validate the entries that are actually
    exempt, which today are all in RESTORED_ELSEWHERE.
    """
    stale = sorted(_EXEMPT - find_unrestored_swaps())
    assert not stale, (
        "these are exempt but the scanner no longer flags them — delete them from "
        "RESTORED_ELSEWHERE / KNOWN_UNRESTORED:\n  " + "\n  ".join(stale)
    )


def test_no_genuinely_unrestored_swaps_are_tolerated():
    """THE GOAL STATE: KNOWN_UNRESTORED is EMPTY.

    Every real leak the R-F3449 sweep found is fixed — test_rf2441 and test_rf1771 (which
    together caused 11 of the 15 order-dependent failures), plus test_rf2178, test_rf2438,
    test_rf2817, test_rf410 and test_rf459, which were latent: they poisoned modules no
    later test happened to re-use, which is luck rather than safety. Only scanner false
    positives remain exempt. Re-adding an entry here means a real leak is being tolerated
    and needs a stated reason.
    """
    assert KNOWN_UNRESTORED == frozenset(), (
        f"a genuinely unrestored swap is being tolerated again: {sorted(KNOWN_UNRESTORED)}")


def test_the_scanner_can_actually_detect_a_swap(tmp_path):
    """Verify the instrument. A guard that cannot see a violation certifies everything.

    This is not hypothetical: the first version of this scanner matched value prefixes
    lowercase-only and its Call branch knew only Mock/MagicMock/AsyncMock, so it silently
    missed `_a.rs = _FakeRS()` — the leak responsible for four of the fifteen failures.
    """
    src = (
        "from aria_service.intel import knowledge as kb\n"
        "class _FakeThing:\n    pass\n"
        "def test_x():\n"
        "    kb.store_fact = _FakeThing()\n"
        "    kb.other = lambda **k: None\n"
    )
    probe = TESTS / "test_rf3449_scanner_selfcheck_tmp.py"
    probe.write_text(src, encoding="utf-8")
    try:
        found = find_unrestored_swaps()
    finally:
        probe.unlink(missing_ok=True)

    assert "test_rf3449_scanner_selfcheck_tmp.py::kb.store_fact" in found, (
        "the scanner cannot see a CONSTRUCTOR swap (`= _FakeThing()`) — this is the exact "
        "blind spot that let the _FakeRS leak through")
    assert "test_rf3449_scanner_selfcheck_tmp.py::kb.other" in found, (
        "the scanner cannot see a lambda swap")


def test_a_benign_state_reset_is_not_flagged(tmp_path):
    """The other half: resetting a cache is CORRECT isolation and must not be reported, or
    the guard drowns in noise and gets switched off (416 hits before this narrowing)."""
    src = (
        "from aria_service.intel import knowledge as kb\n"
        "def test_y():\n"
        "    kb._cache = {}\n"
        "    kb._flag = None\n"
        "    kb._n = 0\n"
    )
    probe = TESTS / "test_rf3449_benign_selfcheck_tmp.py"
    probe.write_text(src, encoding="utf-8")
    try:
        found = {f for f in find_unrestored_swaps() if "benign_selfcheck" in f}
    finally:
        probe.unlink(missing_ok=True)
    assert not found, f"benign state resets must not be flagged: {sorted(found)}"
