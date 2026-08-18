"""R-F1910 — G4 VACCINE: no known GIL-heavy sync function may be called directly
inside an async function (that runs it on the event loop = the recurring wedge).

The G4 gene ("sync CPU on the single event loop") kept re-expressing one instance
at a time: scan_keys (R-F1871), inline search_knowledge (R-F1877),
_strip_html_to_text (R-F1882), get_stats round-trips (R-F1885/1901). The global
admission cap (run_in_thread_cpu, R-F1882) is half the vaccine; this is the other
half — a regression guard so a NEW inline sync-CPU call can never ship.

Rule: each DENYLISTED heavy function must only be invoked OFF the loop — i.e.
passed as an argument to asyncio.to_thread / run_in_thread_throttled /
run_in_thread_cpu (a Name arg, not a Call), OR called inside a plain `def` helper
(which is itself offloaded). A DIRECT call `fn(...)` whose nearest enclosing
function is `async def` runs on the event loop and is a G4 regression.
"""
from __future__ import annotations

import ast
import pathlib

# GIL-heavy sync functions (from the wedge stacks + known encoders/parsers).
# Calling any of these directly on the event loop is the G4 gene.
DENYLIST = {
    "search_knowledge",      # O(570k-fact) linear scan (knowledge.py), 2.28s live
    "search_fact_records",   # R-F4141 — same scan, programmatic entry point
    "verify_premises",       # R-F4141 — calls search_fact_records under it (C-170)
    "_get_embedder",         # R-F4143 (C-172) — cold `import transformers` +
                             # SentenceTransformer model load. Caught mid-import
                             # on the loop by a 5.17s wedge dump.
    "_strip_html_to_text",   # BeautifulSoup tree-walk (crawler/fetcher.py)
    "_safe_encode",          # sentence-transformer encode (semantic_search.py)
    "_encode_edges",         # neural-memory JSON edge encode
    "_encode_all",           # knowledge snapshot JSON encode
    "_compute_matrix_sync",  # coverage-heatmap matrix build
}

_ROOT = pathlib.Path(__file__).resolve().parents[1]  # aria_service/


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str):
        self.path = path
        self.async_depth = 0
        self.violations: list[str] = []
        # R-F4143 (C-172) — Calls that are the operand of `await`. An awaited
        # call MUST be a coroutine (awaiting a sync function is a TypeError),
        # so it cannot be the blocking-sync-CPU-on-the-loop defect this gate
        # exists for. Without this, adding `_get_embedder` to the denylist
        # produced FOUR false positives — `await self._get_embedder()` against
        # genuinely `async def` methods — and a gate that cannot distinguish
        # forces either bogus edits or an exemption list. Both are worse than
        # no gate.
        self._awaited: set[int] = set()

    def visit_Await(self, node):
        if isinstance(node.value, ast.Call):
            self._awaited.add(id(node.value))
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self.async_depth += 1
        self.generic_visit(node)
        self.async_depth -= 1

    def visit_FunctionDef(self, node):
        # entering a plain `def` resets async context (a sync helper run via
        # to_thread is fine — its body is off the loop).
        prev = self.async_depth
        self.async_depth = 0
        self.generic_visit(node)
        self.async_depth = prev

    def visit_Call(self, node):
        """R-F4141 (C-171) — match `mod.fn(...)` as well as bare `fn(...)`.

        This matched ONLY `ast.Name`, i.e. a bare call. **Every real call site
        in this tree is module-qualified** — `knowledge.search_knowledge(...)`,
        `_kb.search_fact_records(...)`, `_pv.verify_premises(...)` — all of
        which are `ast.Attribute`. So the vaccine written to stop this exact
        failure class was structurally blind to the codebase's own style from
        the day it shipped, and two on-loop scans (C-170 premise_verifier,
        signal_correlator) passed straight through it.

        Worse, `test_guard_actually_detects_a_violation` proved the guard
        worked using the BARE form — certifying it against a case that does not
        occur in practice. A guard that cannot fire, plus a self-test that
        cannot catch that, is the register's most-repeated defect.

        Offloading is still not flagged: `to_thread(mod.fn, x)` passes the
        function as an ARGUMENT, so it is never the `func` of a Call node.
        """
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if id(node) in self._awaited:
            name = None          # awaited => a coroutine, not sync CPU
        if self.async_depth > 0 and name in DENYLIST:
            self.violations.append(
                f"{self.path}:{node.lineno} inline {name}() in async context")
        self.generic_visit(node)


def _scan(py: pathlib.Path) -> list[str]:
    try:
        tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    v = _Visitor(str(py.relative_to(_ROOT.parent)))
    v.visit(tree)
    return v.violations


def test_no_heavy_sync_call_on_event_loop():
    """Repo-wide: no denylisted heavy function is called inline in an async fn."""
    violations: list[str] = []
    for py in _ROOT.rglob("*.py"):
        if "/tests/" in py.as_posix() or "\\tests\\" in str(py):
            continue
        violations.extend(_scan(py))
    assert not violations, (
        "G4 regression — GIL-heavy sync call(s) on the event loop "
        "(offload via asyncio.to_thread / run_in_thread_cpu):\n  "
        + "\n  ".join(violations)
    )


def test_guard_actually_detects_a_violation():
    """Sanity: the guard flags a deliberately-bad async inline call (so a green
    repo-wide test isn't green merely because the visitor is broken)."""
    bad = ast.parse(
        "import asyncio\n"
        "async def h():\n"
        "    return search_knowledge('x')\n"
    )
    v = _Visitor("synthetic")
    v.visit(bad)
    assert v.violations, "guard failed to detect an inline heavy call"

    good = ast.parse(
        "import asyncio\n"
        "async def h():\n"
        "    return await asyncio.to_thread(search_knowledge, 'x')\n"
    )
    v2 = _Visitor("synthetic")
    v2.visit(good)
    assert not v2.violations, "guard false-flagged an offloaded call"


def test_guard_detects_the_MODULE_QUALIFIED_form():
    """R-F4141 (C-171) — the case that actually occurs, and the one the guard
    was blind to for its whole life.

    The test above uses a BARE `search_knowledge('x')`. Nothing in this tree is
    written that way: every call site is `knowledge.search_knowledge(...)` or
    `_kb.search_fact_records(...)`. So the original self-test certified the
    guard against a form that never appears, while two real on-loop scans
    (C-170 `premise_verifier`, and `signal_correlator`) sailed through.
    """
    bad = ast.parse(
        "async def h(knowledge):\n"
        "    return knowledge.search_knowledge('x')\n"
    )
    v = _Visitor("synthetic")
    v.visit(bad)
    assert v.violations, (
        "guard is blind to `mod.fn(...)` — the only form this codebase uses")


def test_guard_does_not_flag_an_offloaded_module_qualified_call():
    """The other half. `to_thread(mod.fn, x)` passes the function as an
    ARGUMENT, so it must not be flagged — otherwise the gate is unsatisfiable
    and the next person deletes it rather than fixing anything."""
    good = ast.parse(
        "import asyncio\n"
        "async def h(knowledge):\n"
        "    return await asyncio.to_thread(knowledge.search_knowledge, 'x')\n"
    )
    v = _Visitor("synthetic")
    v.visit(good)
    assert not v.violations, "offloaded module-qualified call wrongly flagged"


def test_guard_covers_every_denylisted_name_in_both_forms():
    """Each entry must be detectable both ways, so adding a name to DENYLIST
    cannot silently give half-coverage."""
    for fn in sorted(DENYLIST):
        for src in (f"async def h():\n    return {fn}('x')\n",
                    f"async def h(m):\n    return m.{fn}('x')\n"):
            v = _Visitor("synthetic")
            v.visit(ast.parse(src))
            assert v.violations, f"{fn} not detected in: {src.strip()}"
