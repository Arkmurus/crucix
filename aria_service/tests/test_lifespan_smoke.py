"""Lifespan startup smoke test — catches the F28-class outage.

2026-04-27 outage: commit `6c26e17` (F28 NODE_OPTIONS fix) referenced
module-level `_os.environ` at the top of `lifespan`. A pre-existing
`import os as _os` later in the same function made `_os` LOCAL for the
whole function scope, so the early reference raised `UnboundLocalError`
on every restart. fly.io looped → service down 5 min 16 sec.

All 1109 unit tests passed. None exercised lifespan end-to-end.

This file is the cheapest possible safety net: enter the lifespan
context manager and exit it. Any UnboundLocalError, missing import,
or unhandled exception in the startup path will surface here BEFORE
the commit can ship to production.

Memory note: `lifespan_smoke_test_required.md`.
"""
from __future__ import annotations

import asyncio


def test_lifespan_pre_yield_top_level_awaits_are_bounded():
    """R-F2378 guard: startup must not add unbounded top-level pre-yield awaits."""
    import ast
    from pathlib import Path

    src = Path("aria_service/main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    lifespan_fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
    )

    class TopLevelAwaitVisitor(ast.NodeVisitor):
        def __init__(self):
            self.await_nodes = []
            self.yield_lines = []

        def visit_AsyncFunctionDef(self, node):
            if node is lifespan_fn:
                for stmt in node.body:
                    self.visit(stmt)

        def visit_FunctionDef(self, node):
            return

        def visit_ClassDef(self, node):
            return

        def visit_Lambda(self, node):
            return

        def visit_Await(self, node):
            self.await_nodes.append(node)
            self.generic_visit(node)

        def visit_Yield(self, node):
            self.yield_lines.append(node.lineno)

        def visit_YieldFrom(self, node):
            self.yield_lines.append(node.lineno)

    visitor = TopLevelAwaitVisitor()
    visitor.visit(lifespan_fn)
    first_yield = min(visitor.yield_lines)
    pre_yield_awaits = [
        node for node in visitor.await_nodes
        if node.lineno < first_yield
    ]

    def _is_allowed(node):
        value = node.value
        if not isinstance(value, ast.Call):
            return False
        func = value.func
        if isinstance(func, ast.Name) and func.id == "_run_boot_inits":
            return True
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "wait_for"
            and isinstance(func.value, ast.Name)
            and func.value.id == "asyncio"
        ):
            return True
        return False

    offenders = [
        f"{node.lineno}: {ast.get_source_segment(src, node)}"
        for node in pre_yield_awaits
        if not _is_allowed(node)
    ]
    assert offenders == [], (
        "R-F2378 regression: top-level pre-yield awaits in lifespan must be "
        f"bounded with asyncio.wait_for or routed through _run_boot_inits: {offenders}"
    )


def test_lifespan_starts_and_shuts_down_cleanly():
    """Enter the lifespan, then exit. If any exception fires during
    the startup or shutdown phase, the test fails.

    NOTE: this exercises real init paths — Redis connect, knowledge load,
    LLM provider build, error-ledger handler install, autonomous engine
    start, etc. — using the local environment. It tolerates "missing dep"
    warnings (chromadb, sentence-transformers) because those degrade
    gracefully; what it does NOT tolerate is anything that raises out of
    the `async with lifespan(app)` block.

    Runtime budget: ~30 seconds locally. Skip on CI with explicit
    SKIP_LIFESPAN_SMOKE=1 if it ever becomes a flake source.
    """
    import os
    if os.getenv("SKIP_LIFESPAN_SMOKE", "").strip() in ("1", "true", "yes"):
        import pytest
        pytest.skip("SKIP_LIFESPAN_SMOKE=1")

    from aria_service.main import lifespan, app

    async def _run():
        async with lifespan(app):
            # If we get here, startup succeeded. We don't need to test
            # anything else inside the context — the act of entering and
            # exiting cleanly is the contract.
            return True

    result = asyncio.run(_run())
    assert result is True, "lifespan context exited without yielding True"


def test_module_level_os_alias_not_shadowed_in_lifespan():
    """Static guard against the F28-style scoping bug.

    If anyone later adds another reference to `_os` at the top of
    `lifespan` (BEFORE the local `import os as _os` in the rag_init_bg
    block), Python will treat `_os` as a function-scope local from the
    very first line and the early reference will UnboundLocalError.

    Two safer patterns:
      1. Use a fresh local alias (the `_f28_os` pattern from the hotfix)
      2. Move the early reference AFTER the existing `import os as _os`

    This test asserts the F28 fix is using the safe local-alias pattern,
    so a regression that re-introduces the unsafe `_os.environ` reference
    near the top of the function will fail the test BEFORE it can ship.
    """
    import inspect
    import ast
    from aria_service import main
    src = inspect.getsource(main.lifespan)
    tree = ast.parse(src)
    local_os_import_line = min(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "os" and alias.asname == "_os"
    )
    early_os_refs = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id == "_os"
        and node.lineno < local_os_import_line
    ]
    assert early_os_refs == [], (
        "F28/R-F2378 regression: lifespan references `_os` before its local "
        f"`import os as _os` on line {local_os_import_line}. Use a fresh local "
        f"alias such as `_f28_os` instead. Early refs: {early_os_refs}"
    )

    # The F28 block must use a uniquely-scoped local alias, not _os.
    # If you change the variable name, update this assertion to match.
    if "NODE_OPTIONS" in src:
        # Find the NODE_OPTIONS line — must reference a fresh local alias
        # (not the function-scope `_os` which gets shadowed by the later
        # `import os as _os` in the rag_init_bg block).
        import re as _re
        # Match \b_os\b — word-boundary so `_f28_os.environ` doesn't match
        bare_os_pattern = _re.compile(r"\b_os\b")
        lines_with_node_options = [
            ln for ln in src.split("\n") if "NODE_OPTIONS" in ln and "environ" in ln
        ]
        for ln in lines_with_node_options:
            assert not bare_os_pattern.search(ln), (
                f"F28 regression: line {ln.strip()!r} references the bare "
                "function-scope alias `_os`, but `lifespan` has a later "
                "`import os as _os` that makes `_os` a local. This will "
                "UnboundLocalError on production startup. Use a fresh "
                "alias like `import os as _f28_os` instead."
            )
