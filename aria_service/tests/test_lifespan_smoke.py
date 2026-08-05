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
import pathlib
import subprocess
import tempfile
import sys

import pytest

# R-F3754/§16 — NOT inspect.getsource: it slices at the line numbers captured
# AT IMPORT, so an edit mid-run returns a DIFFERENT function's body, silently.
from ._source_probe import function_source

#: R-F3459 — the subprocess budget MUST stay strictly below the per-test budget below.
#: See the invariant explained on test_lifespan_starts_and_shuts_down_cleanly.
#:
#: MEASURED, not guessed. This boot takes ~43s when the test runs alone and exceeded 90s
#: when run in a 5-file batch on the same machine — real contention, not a hang. A 90s
#: inner bound was therefore flaky, so both numbers are set from the loaded case with
#: room to spare. A genuinely wedged boot now costs 4 minutes and then reports ONE named
#: failure, which is the whole point: the previous 600s bound cost the entire run.
_LIFESPAN_SUBPROCESS_TIMEOUT_S = 240
_LIFESPAN_TEST_TIMEOUT_S = 300


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


@pytest.mark.timeout(_LIFESPAN_TEST_TIMEOUT_S)
def test_lifespan_starts_and_shuts_down_cleanly():
    """Enter the lifespan, then exit. If any exception fires during
    the startup or shutdown phase, the test fails.

    R-F3459 — THE TIMEOUT INVARIANT: inner subprocess budget < per-test budget.

    This test used `timeout=600` on the subprocess while pytest.ini caps every test at
    `timeout = 120`. The inner guard was five times larger than the budget governing it,
    so it could NEVER fire first. A boot slower than 120s therefore did not fail this
    test — pytest-timeout killed the whole PYTEST PROCESS (thread method on Windows),
    which produces NO summary and no attribution. That is exactly what ended the
    2026-07-30 full-suite run at ~14%, and it is why the suite baseline could not be
    measured. The test had only ever passed because boot usually finishes inside 120s.

    The marker above raises this ONE test's budget to 150s and the subprocess is bounded
    at 90s, so the inner bound always trips first and a slow boot is reported as a
    single, named, diagnosable failure. `test_rf3459_*` enforces the ordering for every
    test in the suite so this class cannot come back.

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

    # R-F3347 — run the lifespan in a SUBPROCESS. It used to run in-process via
    # asyncio.run(), and that poisoned the whole suite.
    #
    # Measured: after `async with lifespan(app)` exits and asyncio.run() closes
    # the loop, FIVE threads survive in the parent —
    #     rf704-wedge-watchdog · Thread-8 (_patched_worker) · Thread-9 ·
    #     QueueFeederThread · continuous-profiler
    # plus an encode-offload CHILD PROCESS ("R-F1890 encode-offload worker warmed
    # (model loaded in child process)"). They are bound to a loop that no longer
    # exists.
    #
    # The next test that does asyncio.run() and reaches the embedder then waits
    # forever. Bisected to exactly that: test_rf1401_held_out_split_eval:209 ->
    # asyncio.run(run_eval(...)) -> eval_runner.py:588
    # `await asyncio.to_thread(_cosine_score, ...)` -> model.encode() ->
    # windows_events._poll -> GetQueuedCompletionStatus, in an executor thread
    # named asyncio_4, killed by pytest-timeout. THIS FILE plus that one is a
    # two-file reproduction; either alone passes. It killed the full-suite run at
    # ~31%, so no complete baseline could be measured at all.
    #
    # Cleaning up the known globals afterwards would be whack-a-mole: the next
    # subsystem the lifespan starts leaks again, silently, and the symptom
    # reappears somewhere unrelated. A subprocess contains ANY of them by
    # construction, and the contract this file exists for (CLAUDE.md §9: enter the
    # real lifespan, exit it, fail on anything that raises) is unchanged —
    # a boot-path UnboundLocalError still fails this test, which is the F28 case.
    driver = (
        "import asyncio, sys\n"
        "from aria_service.main import lifespan, app\n"
        "async def _run():\n"
        "    async with lifespan(app):\n"
        "        return True\n"
        "assert asyncio.run(_run()) is True\n"
        "print('LIFESPAN_OK')\n"
    )
    repo_root = str(pathlib.Path(__file__).resolve().parents[2])

    # Output goes to FILES, not pipes. capture_output=True was the first cut and
    # it hung IN-SUITE at this very line: subprocess.run waits for EOF on the
    # pipes, and the lifespan spawns its own encode-offload GRANDCHILD (R-F1890)
    # which inherits those handles and holds them open after the direct child has
    # exited. Running this file alone hid it. Files have no EOF dependency, so the
    # wait ends when the direct child ends, whatever it left running.
    with tempfile.TemporaryDirectory() as td:
        out_path = pathlib.Path(td) / "out.txt"
        err_path = pathlib.Path(td) / "err.txt"
        with open(out_path, "w", encoding="utf-8") as out_f, \
             open(err_path, "w", encoding="utf-8") as err_f:
            # R-F3459 — the inner timeout MUST be below the per-test budget. It was 600
            # under an ini-wide `timeout = 120`, so it could never fire first: a boot
            # slower than 120s did not fail this test, it KILLED THE PYTEST PROCESS with
            # no summary (pytest-timeout uses the thread method on Windows). That is what
            # ended the 2026-07-30 full-suite run at ~14%, and this test only ever passed
            # because boot usually finishes inside 120s.
            try:
                proc = subprocess.run(
                    [sys.executable, "-c", driver],
                    cwd=repo_root, stdout=out_f, stderr=err_f,
                    timeout=_LIFESPAN_SUBPROCESS_TIMEOUT_S,
                )
            except subprocess.TimeoutExpired:
                # Flag it and fail BELOW, once the handles are closed — reading these
                # paths while they are still open for writing is not reliable on Windows.
                proc = None
        out = out_path.read_text(encoding="utf-8", errors="replace")
        err = err_path.read_text(encoding="utf-8", errors="replace")

    if proc is None:
        pytest.fail(
            f"lifespan did not complete within {_LIFESPAN_SUBPROCESS_TIMEOUT_S}s.\n"
            "This is a REAL failure of the boot path, and it is reported as ONE test "
            "failing rather than as the whole suite dying with no summary.\n"
            f"--- stdout tail ---\n{out[-2000:]}\n--- stderr tail ---\n{err[-4000:]}"
        )

    assert "LIFESPAN_OK" in out, (
        "lifespan did not start and shut down cleanly.\n"
        f"exit={proc.returncode}\n--- stdout tail ---\n{out[-2000:]}\n"
        f"--- stderr tail ---\n{err[-4000:]}"
    )
    assert proc.returncode == 0, (
        f"lifespan subprocess exited {proc.returncode}\n{err[-4000:]}"
    )


def test_module_level_os_alias_not_shadowed_in_lifespan():
    """Static guard against the F28-style scoping bug.

    If anyone adds a lifespan-local ``import os as _os``, Python treats
    ``_os`` as local from the first line and any earlier module-alias use
    raises ``UnboundLocalError``. No local import is the safest state.

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
    src = function_source(main, "lifespan")
    tree = ast.parse(src)
    local_os_import_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "os" and alias.asname == "_os"
    ]
    if local_os_import_lines:
        local_os_import_line = min(local_os_import_lines)
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
        # (not a function-scope `_os` that a future local import could shadow).
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
