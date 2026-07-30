"""R-F3459 — an inner timeout above the per-test budget kills the RUN, not the test.

THE CLASS. `pytest.ini` sets `timeout = 120`. A test that bounds its own work at a LARGER
number can never have that bound fire first, so the per-test timeout wins — and on
Windows pytest-timeout uses the thread method, which dumps stacks and KILLS THE PROCESS.
No summary, no failure count, no attribution to the test responsible. The run simply
stops, and every test after it never runs.

THE INSTANCE. `test_lifespan_smoke.py` ran its boot subprocess with `timeout=600` under
that 120s cap — 5x the budget governing it. It passed for months because boot usually
finishes inside 120s. On 2026-07-30 it did not, and the full-suite measurement died at
~14% with no summary; three earlier attempts had died for a different reason
(background-process reaping), which masked this one.

WHY A GUARD AND NOT JUST A FIX. The failure is invisible in the ordinary case: the test
goes green, and the inversion only shows up on the slow run — where it destroys the whole
measurement rather than reporting one red. Nothing in a normal green suite reveals it, so
only a scanner can.

THE RULE: for any test that bounds a subprocess, the bound must be strictly LESS than the
budget governing that test — the ini default, or a `@pytest.mark.timeout(N)` on the test
itself, which is how a genuinely long test declares its need.
"""
from __future__ import annotations

import ast
import configparser
import pathlib

TESTS = pathlib.Path(__file__).resolve().parent
REPO = TESTS.parents[1]

#: Calls whose `timeout=` bounds a child process. asyncio.wait_for is excluded on purpose:
#: it raises inside the test and is reported normally, so it cannot kill the process.
_BOUNDED_CALLS = {"run", "call", "check_call", "check_output", "wait", "communicate"}


def _ini_timeout() -> int:
    cfg = configparser.ConfigParser()
    cfg.read(REPO / "pytest.ini", encoding="utf-8")
    for section in ("pytest", "tool:pytest"):
        if cfg.has_option(section, "timeout"):
            return int(cfg.get(section, "timeout"))
    raise AssertionError("pytest.ini no longer declares a `timeout` — this guard is blind")


def _marker_timeout(fn: ast.AST, consts: dict[str, int]) -> int | None:
    """`@pytest.mark.timeout(N)` on this test, if any.

    Resolves a NAMED constant as well as a literal. The first version handled only
    literals, so it could not see `@pytest.mark.timeout(_LIFESPAN_TEST_TIMEOUT_S)` — and
    promptly reported the very test this R-number fixes as an offender. An instrument
    that misreads the case you built it for has not been verified.
    """
    for dec in getattr(fn, "decorator_list", []) or []:
        if not isinstance(dec, ast.Call):
            continue
        f = dec.func
        if isinstance(f, ast.Attribute) and f.attr == "timeout":
            for node in list(dec.args) + [kw.value for kw in dec.keywords]:
                got = _resolve(node, consts)
                if got is not None:
                    return got
    return None


def _resolve(node: ast.AST, consts: dict[str, int]) -> int | None:
    """A literal, or a module-level int constant referred to by name."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return int(node.value)
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    return None


def find_timeout_inversions() -> list[str]:
    budget_default = _ini_timeout()
    out: list[str] = []
    for path in sorted(TESTS.glob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        # Module-level int constants, so a named budget is resolvable.
        consts: dict[str, int] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, (int, float)):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        consts[t.id] = int(node.value.value)

        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            budget = _marker_timeout(fn, consts) or budget_default
            for call in ast.walk(fn):
                if not isinstance(call, ast.Call):
                    continue
                name = (call.func.attr if isinstance(call.func, ast.Attribute)
                        else getattr(call.func, "id", ""))
                if name not in _BOUNDED_CALLS:
                    continue
                for kw in call.keywords:
                    if kw.arg != "timeout":
                        continue
                    val = _resolve(kw.value, consts)
                    if val is None:
                        continue
                    if val >= budget:
                        out.append(
                            f"{path.name}::{fn.name} bounds {name}(timeout={val}) at or "
                            f"above its {budget}s per-test budget — a slow run kills the "
                            f"pytest process with no summary instead of failing this test"
                        )
    return out


def test_no_test_bounds_a_subprocess_above_its_own_budget():
    """THE GUARD. Fails in the file that introduced the inversion."""
    bad = find_timeout_inversions()
    assert not bad, (
        "timeout inversion — the inner bound can never fire first:\n  "
        + "\n  ".join(bad)
        + "\n\nEither lower the inner timeout below the per-test budget, or raise this "
          "test's budget with @pytest.mark.timeout(N) where N is larger than the inner "
          "bound. The inner bound must always trip first so a slow run produces one "
          "named failure rather than a dead process."
    )


def test_the_scanner_can_actually_see_an_inversion(tmp_path):
    """VERIFY THE INSTRUMENT. A guard that cannot see a violation certifies everything."""
    probe = TESTS / "test_rf3459_probe_tmp.py"
    probe.write_text(
        "import subprocess\n"
        "def test_bad():\n"
        "    subprocess.run(['x'], timeout=600)\n",
        encoding="utf-8")
    try:
        found = [f for f in find_timeout_inversions() if "probe_tmp" in f]
    finally:
        probe.unlink(missing_ok=True)
    assert found, "the scanner cannot see a 600s subprocess bound under a 120s budget"


def test_a_declared_longer_budget_is_accepted(tmp_path):
    """The other half: a test that DECLARES a bigger budget is correct, not an offender.
    Without this the guard would push people to shrink real work instead of declaring it."""
    probe = TESTS / "test_rf3459_probe_ok_tmp.py"
    probe.write_text(
        "import subprocess\n"
        "import pytest\n"
        "@pytest.mark.timeout(300)\n"
        "def test_ok():\n"
        "    subprocess.run(['x'], timeout=200)\n",
        encoding="utf-8")
    try:
        found = [f for f in find_timeout_inversions() if "probe_ok_tmp" in f]
    finally:
        probe.unlink(missing_ok=True)
    assert not found, f"a correctly declared longer budget was flagged: {found}"


def test_the_lifespan_smoke_test_is_the_fixed_instance():
    """The instance that cost the measurement, asserted directly."""
    src = (TESTS / "test_lifespan_smoke.py").read_text(encoding="utf-8")
    # Asserted BEHAVIOURALLY, not by scanning for the string "timeout=600" — that string
    # legitimately appears in the docstring explaining the defect, and a guard that
    # forbids describing the bug it prevents is a guard nobody can document.
    assert not [f for f in find_timeout_inversions() if "test_lifespan_smoke" in f]
    assert "@pytest.mark.timeout(_LIFESPAN_TEST_TIMEOUT_S)" in src
    # The exact numbers are set from MEASURED timings (see that file); what this guard
    # pins is the ORDERING, which is the property that keeps the suite reportable.
    import re as _re
    inner = int(_re.search(r"_LIFESPAN_SUBPROCESS_TIMEOUT_S = (\d+)", src).group(1))
    budget = int(_re.search(r"_LIFESPAN_TEST_TIMEOUT_S = (\d+)", src).group(1))
    assert inner < budget, (
        f"inner bound {inner}s must stay strictly below the declared per-test budget "
        f"{budget}s, or a slow boot kills the process again")
