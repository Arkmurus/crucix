"""R-F2663 — the per-boot heavy-graph warmup must NOT reset the Phase A gate-#3 streak.

Before R-F2663, main.py::_warmup_heavy_graphs ran knowledge.init + neural_memory.init
through _run_boot_inits' 5s pre-yield timeout — but those loads take ~10 min (R-F2122:
~223k facts + ~1.2M neural edges). So they ALWAYS timed out (the init was CANCELLED,
graphs never loaded), and the umbrella "[R-F2122] heavy graph warmup failed" line logged
at ERROR on EVERY boot → error_log_handler mirrored it to record_error as `log:error` →
is_reset_type=True → RESET the gate-#3 7-day error streak. At ~10 deploys/day the streak
could never accrue: gate #3 was structurally un-closeable (the DD finding).

R-F2663: the background warmup gets a generous timeout (ARIA_HEAVY_WARMUP_TIMEOUT_S,
default 1200s) so the normal slow load COMPLETES, and any residual is logged at WARNING
(`log:warning` → is_reset_type=False → does NOT reset the streak).

The warmup is a nested closure inside lifespan() (not directly invokable), so these
capability tests drive the REAL main.py source via AST (the exact structure of the
broken path) + the reset-type invariant the fix relies on. Verified to FAIL against the
pre-R-F2663 tree.
"""
from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

import aria_service.main as _main
from aria_service.intel import error_streak


def _warmup_node() -> ast.AST:
    src = pathlib.Path(_main.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_warmup_heavy_graphs":
            return node
    raise AssertionError("_warmup_heavy_graphs closure not found in main.py")


def _dotted_calls(node: ast.AST) -> list[str]:
    """Dotted names of every Call inside node (e.g. 'logger.error', '_run_boot_inits').
    AST-based, so it counts CALLS only — comments/strings mentioning a name don't match."""
    out: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                base = f.value.id if isinstance(f.value, ast.Name) else "?"
                out.append(f"{base}.{f.attr}")
            elif isinstance(f, ast.Name):
                out.append(f.id)
    return out


def test_warmup_never_logs_error():
    """The warmup closure must NOT call logger.error — that mirrored as log:error and
    reset the gate-#3 streak on every boot."""
    calls = _dotted_calls(_warmup_node())
    assert "logger.error" not in calls, (
        "warmup logs ERROR → resets the gate-#3 streak every boot (R-F2663 regression)")
    assert "logger.warning" in calls, "warmup must log its degradation at WARNING instead"


def test_warmup_does_not_use_5s_boot_init_timeout():
    """The background warmup must NOT route the heavy graphs through _run_boot_inits
    (the 5s pre-yield timeout that cancelled the ~10-min load). It uses its own
    generous ARIA_HEAVY_WARMUP_TIMEOUT_S cap."""
    node = _warmup_node()
    calls = _dotted_calls(node)
    assert "_run_boot_inits" not in calls, (
        "warmup routes heavy graphs through _run_boot_inits' 5s timeout — cancels the "
        "~10-min load (R-F2663 regression)")
    # R-F4213 moved the env read OUT of this closure into the module-level
    # _heavy_warmup_timeout_s(), because a malformed value raised ValueError here
    # — ABOVE the only heavy_graph_ready.set() — and parked every gated boot
    # workload forever. The SURVIVING INTENT is "a generous cap, not the 5s
    # boot-init default", so assert that property through the one source of
    # truth. Do NOT green a failure here by deleting the assertion: that is how
    # the 5s cap silently comes back.
    assert "_heavy_warmup_timeout_s" in calls, (
        "warmup no longer takes its cap from _heavy_warmup_timeout_s() — either "
        "the 5s boot-init default is back, or a second cap has been forked")
    src = inspect.getsource(_main._heavy_warmup_timeout_s)
    assert "ARIA_HEAVY_WARMUP_TIMEOUT_S" in src, (
        "the cap helper no longer reads the operator's env var")
    assert _main._HEAVY_WARMUP_TIMEOUT_DEFAULT_S >= 600.0, (
        "the background warmup default is no longer generous — a short cap "
        "cancels the ~10-min graph load, the R-F2663 regression")


def test_warmup_warning_type_cannot_reset_gate3():
    """The INVARIANT the fix relies on: a WARNING mirrors as log:warning, which
    is_reset_type=False → cannot reset the streak; ERROR/CRITICAL (log:error/critical)
    would. Write-path and read-path share is_reset_type so they cannot drift."""
    assert error_streak.is_reset_type("log:error") is True
    assert error_streak.is_reset_type("log:critical") is True
    assert error_streak.is_reset_type("log:warning") is False


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
