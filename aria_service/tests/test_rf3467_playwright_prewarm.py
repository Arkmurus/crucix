"""R-F3467 — a lazy `import playwright` was stalling the event loop.

This one is worth recording because of HOW it was found. R-F3464 fixed the stall
detector so it reports the LOOP THREAD's stack instead of a census of sleeping
threads. Minutes after that deployed, the live /health/perf named the culprit:

    "last_stall_loop_stack": [
      "<frozen importlib._bootstrap_external>:get_data:1214",   <- blocking disk read
      "<frozen importlib._bootstrap_external>:get_code:1115",
      "<frozen importlib._bootstrap_external>:exec_module:1019",
      ...
      "/usr/local/lib/python3.13/site-packages/playwright/_impl/_locator.py:<module>:43"
    ],
    "last_stall_threads": {"total": 23, "parked": 17, "aiosqlite_workers": 9, ...}

Two conclusions, both from that one payload:

  1. An application frame on the loop thread means something BLOCKED the loop
     (main.py:1771's own rule). `from playwright.async_api import async_playwright`
     is lazy in four call sites, so whichever fires first pays a multi-second
     synchronous module load ON the loop.
  2. The thread census clears the standing theory. R-F3252 found 56 aiosqlite
     connection workers (peak 140) against a design of ~6 and concluded GIL
     starvation. Live now: 9. R-F2754's leak fix is holding, and THIS stall is
     not that failure.

The fix is the existing R-F1845 pattern, not a new mechanism: pre-warm the module
in a thread at boot so the in-request import is a cache hit.
"""
from __future__ import annotations

import ast
import pathlib

import pytest


_MAIN = pathlib.Path(__file__).resolve().parents[1] / "main.py"


def test_playwright_is_prewarmed():
    """The load-bearing assertion: playwright must be in the pre-warm list."""
    from aria_service.main import _HEAVY_PREWARM_MODULES
    assert any(m.startswith("playwright") for m in _HEAVY_PREWARM_MODULES), (
        f"playwright is not pre-warmed; its first lazy import will block the "
        f"event loop. list={_HEAVY_PREWARM_MODULES}"
    )


def test_prewarm_list_keeps_the_original_entry():
    """R-F3467 must not drop what R-F1845 put there."""
    from aria_service.main import _HEAVY_PREWARM_MODULES
    assert "aria_service.writers.procurement_paper_writer" in _HEAVY_PREWARM_MODULES


def test_prewarm_runs_off_the_event_loop():
    """A pre-warm that imports ON the loop would cause the very stall it exists
    to prevent. Assert the call is dispatched through asyncio.to_thread.

    Parsed from source rather than executed: _prewarm_heavy_imports is a closure
    inside lifespan(), and booting lifespan here would load the whole brain.
    """
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_prewarm_heavy_imports":
            target = node
            break
    assert target is not None, "_prewarm_heavy_imports not found in main.py"

    src = ast.dump(target)
    assert "to_thread" in src, (
        "_prewarm_heavy_imports must dispatch the import via asyncio.to_thread; "
        "importing on the loop would create the stall it exists to prevent"
    )
    # And it must iterate the shared constant, not an inline literal that could
    # drift away from what the test asserts.
    assert any(
        isinstance(n, ast.Name) and n.id == "_HEAVY_PREWARM_MODULES"
        for n in ast.walk(target)
    ), "_prewarm_heavy_imports no longer iterates _HEAVY_PREWARM_MODULES"


def test_playwright_import_is_still_lazy_at_call_sites():
    """Guards the premise. If someone converts these to top-level imports, the
    boot path pays the cost instead and this pre-warm becomes pointless.
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for rel in ("intel/headless.py", "intel/scraper/playwright_engine.py",
                "intel/scraper/procurement_adapters.py"):
        path = root / rel
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # module level only
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                name = getattr(node, "module", "") or ""
                names = " ".join(a.name for a in node.names)
                if "playwright" in name or "playwright" in names:
                    offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        f"playwright imported at module level (no longer lazy): {offenders}"
    )
