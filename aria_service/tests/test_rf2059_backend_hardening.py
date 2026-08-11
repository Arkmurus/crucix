"""R-F2059: capability test — verify every search backend has a circuit breaker
and wires its failures to the brain (§21a).

This test reads the source code to confirm the property. It does NOT call the
live backends.

R-F3858 (2026-08-11) — THIS GUARD WAS BLIND, AND HAD BEEN FAILING FALSELY.

It reported `_search_searxng` as "missing wire_failure" while that function
contained TWO failure wires. Both of its heuristics were the cause, independently:

  1. FIXED LINE WINDOW. `_function_has_wire_failure` scanned exactly 80 lines from
     the `async def`. `_search_searxng` begins at line 662, so the window ended at
     742 — and its literal `wire_failure(` call is at line 754. Twelve lines out of
     reach. A function only has to grow for the guard to stop seeing its own
     subject; nothing about the code being checked changed.

  2. LITERAL NAME MATCHING. The other wire, at line 739 and comfortably inside the
     window, is called through an aliased import
     (`from .engine_wiring import wire_failure as _wf1657`), so the substring
     `wire_failure(` never appears at the call site.

Two failure modes worth naming, because the repo has both on record. The first is
the §16 line-number fragility that corrupted two full-suite baselines (R-F3597:
`inspect.getsource` slicing a moved file returns a DIFFERENT function's body,
silently). The second is the §16/R-F3791 "a guard that enumerates something can go
blind rather than fail" class — the same shape as the three Phase A gates in §1
that were certified by an absence.

A permanently-red guard is strictly worse than no guard. It sat in
`docs/suite_baseline.json` as a KNOWN failure, so it could never go green, which
means it could never carry information either: had a backend genuinely lost its
breaker or its wiring, this test would have looked exactly the same. It was
costing a baseline slot to assert nothing.

The fix is to ask the AST what the function actually contains — the same move
`_source_probe.py` made for the §16 victims, and the one this file's unused
`import ast` shows was intended from the start.
"""
from __future__ import annotations

import ast
from pathlib import Path

#: Names that constitute a circuit-breaker guard at a call site.
_BREAKER_NAMES = {"get_breaker", "CircuitBreaker"}
#: The brain-wiring call every backend must make on its failure path (§21a).
_WIRE_NAME = "wire_failure"


def _load(path: str = "aria_service/intel/web_search.py") -> tuple[ast.Module, str]:
    src = Path(path).read_text(encoding="utf-8")
    return ast.parse(src), src


def _search_backends(tree: ast.Module) -> list[ast.AsyncFunctionDef]:
    """Every module-level `async def _search_*`, from the AST rather than a regex
    over lines — so a moved or reformatted definition cannot hide one."""
    return [n for n in tree.body
            if isinstance(n, ast.AsyncFunctionDef) and n.name.startswith("_search_")]


def _local_aliases_for(fn: ast.AST, target: str) -> set[str]:
    """Every local name bound to `target`, including aliased imports.

    `from .engine_wiring import wire_failure as _wf1657` binds the wire to
    `_wf1657`; a literal-substring check cannot see the call and reports a wired
    backend as dark.
    """
    names = {target}
    for node in ast.walk(fn):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == target and a.asname:
                    names.add(a.asname)
        elif isinstance(node, ast.Assign):          # _wf = wire_failure
            if isinstance(node.value, ast.Name) and node.value.id in names:
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
    return names


def _called_names(fn: ast.AST) -> set[str]:
    """Every name invoked anywhere in the function body, at any nesting depth."""
    out: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name):
            out.add(f.id)
        elif isinstance(f, ast.Attribute):
            out.add(f.attr)
    return out


def _references(fn: ast.AST) -> set[str]:
    """Every identifier mentioned — covers a breaker held as an object
    (`_sx_cb.record_failure()`) whose construction happens at module level."""
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
    return out


def test_all_search_backends_have_circuit_breakers():
    """Every _search_* backend must guard its upstream with a circuit breaker.

    R-F2059: backends without circuit breakers burn requests on every search call
    when the upstream is down, with no cooldown.
    """
    tree, _ = _load()
    backends = _search_backends(tree)
    assert backends, "no _search_* backends found — the guard has gone blind again"

    # R-F3868 — NOTHING IS EXEMPT ANY MORE. This set held "_search_brave",
    # described as "a permanent stub returning [] (R-F320 removal)". That is FALSE
    # and had become dangerous: Brave was reinstated as the paid primary (R-F2318)
    # and is now the sole DD search engine (R-F3847). Verified against the AST —
    # `_search_brave` has a circuit breaker, wires its failures, and is 100+ lines
    # of live code. The exemption was excluding ARIA's most important paid backend
    # from the very guard that protects backends, on the strength of a stale
    # comment. It passes on its own merits.
    exempt: set[str] = set()

    missing = []
    for fn in backends:
        if fn.name in exempt:
            continue
        seen = _called_names(fn) | _references(fn)
        if not (seen & _BREAKER_NAMES) and not any(
                n.endswith("_cb") or "breaker" in n.lower() for n in seen):
            missing.append(f"{fn.name} (line {fn.lineno})")

    assert not missing, f"Backends missing circuit breakers: {', '.join(missing)}"


def test_all_search_backends_wire_their_failures():
    """§21a — a backend that fails silently is DARK. Split from the breaker
    assertion so a failure names which property broke."""
    tree, _ = _load()
    exempt: set[str] = set()

    missing = []
    for fn in _search_backends(tree):
        if fn.name in exempt:
            continue
        if not (_called_names(fn) & _local_aliases_for(fn, _WIRE_NAME)):
            missing.append(f"{fn.name} (line {fn.lineno})")

    assert not missing, f"Backends missing {_WIRE_NAME}: {', '.join(missing)}"


def test_the_guard_can_still_see_a_genuinely_dark_backend():
    """The half that matters after R-F3858. A guard that cannot fail is not a
    guard — and this one spent its life red while asserting nothing, so the ONLY
    thing that makes it trustworthy is proof it still detects the real defect."""
    tree = ast.parse(
        "async def _search_dark(q):\n"
        "    return []\n"
    )
    fn = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)][0]

    assert not (_called_names(fn) & _local_aliases_for(fn, _WIRE_NAME))
    assert not (_called_names(fn) & _BREAKER_NAMES)


def test_an_aliased_wire_is_recognised():
    """The exact construct that made this guard false-fail: the wire is imported
    under another name, so the literal substring never appears at the call site."""
    tree = ast.parse(
        "async def _search_x(q):\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        from .engine_wiring import wire_failure as _wf1657\n"
        "        _wf1657(module='x')\n"
    )
    fn = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)][0]

    assert _called_names(fn) & _local_aliases_for(fn, _WIRE_NAME) == {"_wf1657"}


def test_a_wire_beyond_the_old_80_line_window_is_found():
    """The other half of the false failure: `_search_searxng`'s literal wire sits
    at line 754 against a definition at 662, i.e. 12 lines past the old window.
    Growth alone used to blind the guard."""
    body = "\n".join(f"    x{i} = {i}" for i in range(120))
    tree = ast.parse(
        "async def _search_long(q):\n" + body + "\n    wire_failure(module='x')\n"
    )
    fn = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)][0]

    assert _WIRE_NAME in _called_names(fn)


def test_the_real_searxng_backend_is_wired_and_breakered():
    """Pins the specific false negative that motivated R-F3858, against the real
    file — so a regression in either the guard or the backend is caught here."""
    tree, _ = _load()
    fn = [f for f in _search_backends(tree) if f.name == "_search_searxng"]
    assert fn, "_search_searxng not found"
    fn = fn[0]

    assert _called_names(fn) & _local_aliases_for(fn, _WIRE_NAME), (
        "_search_searxng wires failures at lines 739 (aliased) and 754 (literal)")
    seen = _called_names(fn) | _references(fn)
    assert (seen & _BREAKER_NAMES) or any(n.endswith("_cb") for n in seen)
