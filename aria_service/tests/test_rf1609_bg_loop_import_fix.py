"""R-F1609 — fix relative-import-beyond-top-level that killed 10 autonomous loops.

ROOT CAUSE (found via R-F1608's death-logging on the live box 2026-06-16):
10 background loops in aria_service/main.py had, as the FIRST line inside their
`while True` body:
    from ..autonomous.safety import is_engine_paused as _is_paused
main is a module of the top-level package `aria_service`, so `..autonomous`
escapes ABOVE the top-level package → ImportError "attempted relative import
beyond top-level package" on iteration 1 → the loop dies instantly. Live logs:
    ERROR [R-F1608] bg task research_loop died: attempted relative import beyond top-level package
    ERROR [R-F1608] bg task tender_monitor_loop died: ...
This silently killed ~70% of ARIA's autonomous loops (research, self_improve,
proactive, watchlist_rescreen, tender_monitor, weekly_report, student_*,
library_consolidate) — the master cause of "she doesn't self-heal / the same
issues recur." Fix: `..autonomous` → `.autonomous` (= aria_service.autonomous).

This is a CAPABILITY test: it drives the exact import the loops run, and guards
against the regression that crashed them.
"""
from __future__ import annotations

import pathlib
import inspect

import pytest


def test_rf1609_safety_import_resolves():
    """The import the loops execute on every iteration must resolve."""
    from aria_service.autonomous.safety import is_engine_paused
    assert callable(is_engine_paused)


def test_rf1609_no_beyond_top_level_import_in_main():
    """Regression guard: main.py must never reintroduce `from ..autonomous`
    (the exact bug that killed 10 loops). The correct form is `.autonomous`."""
    src = pathlib.Path("C:/code/crucix/aria_service/main.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "from ..autonomous" not in src, (
        "R-F1609 regression: a 'from ..autonomous' (relative-beyond-top-level) "
        "import is back in main.py — it crashes the bg loop on iteration 1."
    )
    # And the corrected form IS present (the loops still gate on engine-pause).
    assert "from .autonomous.safety import is_engine_paused" in src


@pytest.mark.asyncio
async def test_rf1609_is_engine_paused_callable_returns_bool():
    """The loops do `if await is_engine_paused():` on iteration 1 — prove that
    whole expression works (this is what ImportError-crashed before)."""
    from aria_service.autonomous.safety import is_engine_paused
    result = is_engine_paused()
    if inspect.isawaitable(result):
        result = await result
    assert isinstance(result, bool)
