"""R-F1877 — deep_researcher must never call the synchronous search_knowledge
inline on the event loop; every call goes through asyncio.to_thread.

Wedge DD 2026-06-24: after R-F1871 killed the scan_keys full-table-scan, the
residual R-F703 event-loop stalls traced to knowledge.search_knowledge — a sync
O(~87K-fact) linear scan — being awaited INLINE on the event loop from the
research loop (deep_researcher). R-F939 added a GIL-yield, but that only lets the
loop run when search_knowledge executes in a WORKER thread, not when it's called
directly on the loop. Fix: offload every deep_researcher call via
asyncio.to_thread(search_knowledge, ...).

Capability test (AST, not a string grep): parse deep_researcher.py and assert
there are ZERO direct `search_knowledge(...)` calls — when correctly offloaded,
search_knowledge is passed as a NAME argument to to_thread, never invoked as a
Call. This guards the exact contract the fix establishes (no inline blocking
call) against regression.
"""
from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path("aria_service/intel/deep_researcher.py")


def _tree() -> ast.AST:
    return ast.parse(_SRC.read_text(encoding="utf-8"))


def test_no_inline_search_knowledge_calls():
    """Every search_knowledge call must be offloaded — zero direct invocations."""
    tree = _tree()
    direct_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "search_knowledge"
    ]
    assert not direct_calls, (
        f"{len(direct_calls)} inline search_knowledge call(s) on the event loop "
        f"(lines {[n.lineno for n in direct_calls]}) — must use "
        f"asyncio.to_thread(search_knowledge, ...)"
    )


def test_offload_is_actually_used():
    """Sanity: the offloaded form (to_thread with search_knowledge as an arg)
    is present — so the test above isn't passing simply because the calls were
    deleted."""
    tree = _tree()
    offloaded = 0
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "to_thread"):
            for a in n.args:
                if isinstance(a, ast.Name) and a.id == "search_knowledge":
                    offloaded += 1
    assert offloaded >= 10, f"expected the search_knowledge calls to be offloaded; found {offloaded}"
