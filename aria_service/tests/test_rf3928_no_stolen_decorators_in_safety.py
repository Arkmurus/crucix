"""R-F3928 — R-F3919 stole a @fail_wire decorator by inserting above a `def`.

R-F3919 added `release_rate_slot` immediately above `check_and_increment_rate`. The
edit anchored on the `async def` line rather than the decorator above it, so the
pre-existing `@fail_wire` ended up on the NEW function and
`check_and_increment_rate` was silently un-wired.

Gate A caught it in CI:

    safety.py:254 public async function 'check_and_increment_rate()' has no
    @fail_wire and is not in HARD_EXEMPT

THIS IS A DOCUMENTED CLASS, REPRODUCED VERBATIM. CLAUDE.md §16: "R-F3842: the
mid-session re-record captured three wiring-gate failures caused by my own
stolen-decorator defect, i.e. it would have enshrined the regression as 'known
good'." Same defect, same week, different function — which is precisely why that
gate must never be muted or baselined away.

WHY IT IS DANGEROUS OUT OF PROPORTION TO ITS SIZE. A decorator theft is invisible in
review — both functions look decorated — and it silently UN-WIRES a path that was
wired. The module keeps reporting health it no longer measures: the
absence-reads-as-health shape this whole codebase is written against (§1, §21a).

The rule: WHEN INSERTING A FUNCTION ABOVE ANOTHER, ANCHOR ON THE DECORATOR, NOT THE
`def`.
"""
from __future__ import annotations

import ast

from aria_service.tests._source_probe import repo_path

_TARGET = "aria_service/autonomous/safety.py"


def _public_async_defs(path: str):
    tree = ast.parse(repo_path(path).read_text(encoding="utf-8"))
    return [n for n in tree.body
            if isinstance(n, ast.AsyncFunctionDef) and not n.name.startswith("_")]


def _decorator_names(node) -> set[str]:
    out = set()
    for d in node.decorator_list:
        f = d.func if isinstance(d, ast.Call) else d
        out.add(getattr(f, "id", None) or getattr(f, "attr", ""))
    return out


def test_the_two_functions_involved_are_both_wired():
    """The specific regression: neither may lose its wire to the other."""
    by_name = {n.name: n for n in _public_async_defs(_TARGET)}
    for fn in ("release_rate_slot", "check_and_increment_rate"):
        assert fn in by_name, f"{fn} missing from {_TARGET}"
        assert "fail_wire" in _decorator_names(by_name[fn]), (
            f"{fn} has no @fail_wire — a decorator was stolen by an insertion "
            f"above it (R-F3928). Anchor edits on the DECORATOR, not the `def`.")


def test_every_public_async_function_in_safety_is_wired():
    """The CLASS, not the instance. Any future insertion that steals a decorator
    from any other function in this module fails here, not three commits later in
    a CI baseline diff."""
    unwired = [n.name for n in _public_async_defs(_TARGET)
               if "fail_wire" not in _decorator_names(n)]
    assert not unwired, (
        f"public async functions in {_TARGET} with no @fail_wire: {unwired}. "
        f"Either wire them or add them to wiring_harness.HARD_EXEMPT with a reason "
        f"(§21a).")


def test_the_guard_can_actually_fail():
    """R-F3858 — proves the AST detector sees an undecorated function, so a green
    result means something."""
    tree = ast.parse(
        "async def wired_one(): ...\n"
        "@fail_wire(module='x')\n"
        "async def also_wired(): ...\n")
    fns = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)]
    undecorated = [n.name for n in fns if "fail_wire" not in _decorator_names(n)]
    assert undecorated == ["wired_one"], undecorated
