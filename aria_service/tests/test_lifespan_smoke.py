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
    from aria_service import main
    src = inspect.getsource(main.lifespan)
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
