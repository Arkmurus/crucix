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
    "search_knowledge",      # O(~87k-fact) linear scan (knowledge.py)
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
        func = node.func
        if (self.async_depth > 0
                and isinstance(func, ast.Name)
                and func.id in DENYLIST):
            self.violations.append(f"{self.path}:{node.lineno} inline {func.id}() in async context")
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
