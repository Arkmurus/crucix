"""R-F2254 — DD network + digital layers run CONCURRENTLY (dd-reviewer #1 speed fix).

Safe because both only READ report.identity + write DISJOINT sections; compliance
(which MUTATES report.identity) runs SERIALLY before them. These are contract locks
so a future edit cannot reintroduce the serial chain or move a layer into the race
window.

R-F4284 — REWRITTEN AS AST ASSERTIONS. The original locks were source SUBSTRINGS
and character-offset arithmetic:

    assert "await asyncio.gather(_run_network_layer(), _run_digital_layer(), "
           "return_exceptions=True)" in _DDO
    assert (i_gather - guard) < 4000, "gather must be under an 'if not hard_stop' guard"

All three went red, and NOT ONE named a real defect: the call had simply been
wrapped across two lines. The concurrency, the guard and the ordering were all
exactly as intended at dd_orchestrator.py:16143. A reformat broke the lock while
the behaviour it protects never moved — and a 4000-CHARACTER window between a
guard and its body is a measurement of formatting, not of structure.

This is the R-F3597 lesson (line-number fragility, fixed by resolving through the
AST) and the R-F3858 one (a guard that scans a fixed window goes blind as code
grows). A structural assertion cannot be broken by a line break, and still fails
loudly if the gather, the guard or the ordering genuinely changes.
"""
from __future__ import annotations

import ast
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "intel" / "dd_orchestrator.py"
_SRC = _PATH.read_text(encoding="utf-8")
_TREE = ast.parse(_SRC)

#: child node -> parent, so a node can be asked what encloses it.
_PARENT: dict[ast.AST, ast.AST] = {}
for _node in ast.walk(_TREE):
    for _child in ast.iter_child_nodes(_node):
        _PARENT[_child] = _node


def _called_name(node: ast.AST) -> str:
    """The dotted name being called, e.g. 'asyncio.gather' or '_run_network_layer'."""
    if not isinstance(node, ast.Call):
        return ""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = func.value
        return f"{base.id}.{func.attr}" if isinstance(base, ast.Name) else func.attr
    return ""


def _ancestors(node: ast.AST):
    while node in _PARENT:
        node = _PARENT[node]
        yield node


def _the_layer_gather() -> ast.Call:
    """The single `asyncio.gather` that runs the network and digital layers."""
    found = []
    for node in ast.walk(_TREE):
        if _called_name(node) != "asyncio.gather":
            continue
        arg_names = {_called_name(a) for a in node.args}
        if {"_run_network_layer", "_run_digital_layer"} <= arg_names:
            found.append(node)
    assert len(found) == 1, (
        f"expected exactly one network||digital gather, found {len(found)}. "
        f"Two would mean the layers can be raced twice; none would mean the "
        f"concurrency fix was reverted."
    )
    return found[0]


def test_network_and_digital_run_in_a_gather() -> None:
    names = {n.name for n in ast.walk(_TREE)
             if isinstance(n, ast.AsyncFunctionDef)}
    assert "_run_network_layer" in names
    assert "_run_digital_layer" in names

    gather = _the_layer_gather()
    # awaited, not fire-and-forget: an un-awaited gather would return a coroutine
    # and let the DD continue before either layer had run.
    assert isinstance(_PARENT.get(gather), ast.Await), "the gather is not awaited"
    # exceptions must come back as results, or one failing layer kills the other
    assert any(kw.arg == "return_exceptions"
               and isinstance(kw.value, ast.Constant) and kw.value.value is True
               for kw in gather.keywords), "return_exceptions=True is missing"


def test_gather_is_gated_off_hard_stop() -> None:
    """The concurrent block must not run on a sanctions short-circuit."""
    gather = _the_layer_gather()
    guarded = False
    for parent in _ancestors(gather):
        if isinstance(parent, ast.If) and isinstance(parent.test, ast.UnaryOp) \
                and isinstance(parent.test.op, ast.Not) \
                and isinstance(parent.test.operand, ast.Name) \
                and parent.test.operand.id == "hard_stop":
            guarded = True
            break
    assert guarded, (
        "the network||digital gather is not enclosed by `if not hard_stop:` — "
        "a sanctions short-circuit would still race both layers"
    )


def test_compliance_runs_serial_BEFORE_the_concurrent_readers() -> None:
    """Compliance MUTATES report.identity, so it must finish before the readers."""
    compliance = [n for n in ast.walk(_TREE)
                  if _called_name(n) == "_run_compliance"]
    assert compliance, "_run_compliance call not found"
    gather = _the_layer_gather()
    assert min(c.lineno for c in compliance) < gather.lineno, (
        "compliance must run before the network||digital gather"
    )


def test_no_serial_network_await_remains_outside_the_closure() -> None:
    """The OLD serial `_run_network(...)` call must live ONLY in the closure."""
    calls = [n for n in ast.walk(_TREE) if _called_name(n) == "_run_network"]
    assert len(calls) == 1, f"expected 1 _run_network call site, found {len(calls)}"
    enclosing = [p.name for p in _ancestors(calls[0])
                 if isinstance(p, (ast.AsyncFunctionDef, ast.FunctionDef))]
    assert enclosing and enclosing[0] == "_run_network_layer", (
        f"the serial network call escaped its closure; it now sits in {enclosing[:1]}"
    )
