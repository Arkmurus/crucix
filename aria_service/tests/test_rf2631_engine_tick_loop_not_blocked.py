"""R-F2631 (2026-07-15) — the tick loop must not wait for startup maintenance.

DEEP RCA of "ENGINE DARK" (live aria-intel, 2026-07-15). The dedupe NULL-TTL
bug (R-F2626/R-F2629) was real but was NOT the main cause. The main cause:

    558: await asyncio.sleep(STARTUP_DELAY_SECONDS)   # 180s
    601: await safety.repair_nulled_dedupe_markers()  # minutes
    618: await catch_up_overdue_tasks(llm)            # fires up to 15 tasks INLINE
    624: while True:                                  # <- tick loop starts ONLY here

`catch_up_overdue_tasks` executes real tasks serially (`await
tasks_mod.execute_task(...)`, engine.py:506) up to _CATCH_UP_MAX_FIRES=15; one
observed task ran >10 min despite timeout_seconds=180.

Measured live:
  - time-to-first-tick = 19.2 min
  - tick_count = 0 at 28.7 min after _started_at (loop never ran)
  - a process crashed exit_code=1 after 6.6 min — before it could ever tick

So for long stretches the polling loop DID NOT EXIST and no cron was ever
evaluated. tasks.yaml's crons imply ~964 fires/24h; observed fires_24h = 7.

FIX: dispatch the startup maintenance as a background task so the loop begins
within seconds. Safe by construction: both catch-up and the tick go through
safety.can_task_run, whose dedupe marker is exactly the guard against firing
the same task twice — that is what dedupe is FOR.
"""
from __future__ import annotations

import inspect


def test_tick_loop_is_not_behind_the_startup_maintenance():
    """CAPABILITY (static, on the real source): the `while True:` tick loop
    must NOT sit behind an awaited repair/catch_up. Those must be dispatched
    to a background task first.

    A runtime test would have to actually run _engine_loop (180s sleep +
    network + LLM), so we assert on the real structure of the shipped
    function — which is what the bug WAS: an ordering defect.
    """
    from aria_service.autonomous import engine

    src = inspect.getsource(engine._engine_loop)
    i_loop = src.index("while True:")
    i_dispatch = src.index("_startup_maintenance_task = asyncio.create_task")

    assert i_dispatch < i_loop, (
        "startup maintenance must be dispatched BEFORE the tick loop starts"
    )

    # Only the OUTER pre-loop path matters. The nested _startup_maintenance
    # body sits textually before the loop but does not execute there, so
    # excise it before checking — a naive substring test cannot tell
    # "awaited at loop level" from "defined for later".
    i_def = src.index("async def _startup_maintenance")
    outer_pre_loop = src[:i_def] + src[i_dispatch:i_loop]

    assert "await safety.repair_nulled_dedupe_markers()" not in outer_pre_loop, (
        "repair is awaited on the pre-loop path — it delays the first tick "
        "(measured 19.2 min time-to-first-tick; processes died at 6.6 min)"
    )
    assert "await catch_up_overdue_tasks(llm)" not in outer_pre_loop, (
        "catch_up is awaited on the pre-loop path — it fires up to 15 tasks "
        "INLINE and the tick loop cannot start until they all finish"
    )
    # And the awaits MUST still exist inside the background maintenance —
    # moving them off the path must not mean dropping them.
    maint = src[i_def:i_dispatch]
    assert "await safety.repair_nulled_dedupe_markers()" in maint
    assert "await catch_up_overdue_tasks(llm)" in maint


def test_maintenance_preserves_repair_before_catchup_order():
    """catch_up is a dedupe CONSUMER: it must not be gated by the very
    NULL-TTL markers the repair is clearing."""
    from aria_service.autonomous import engine

    src = inspect.getsource(engine._engine_loop)
    i_repair = src.index("repair_nulled_dedupe_markers")
    i_catchup = src.index("catch_up_overdue_tasks(llm)")
    assert i_repair < i_catchup


def test_maintenance_task_is_strongly_referenced():
    """asyncio holds only a WEAK ref to a bare create_task() result — without
    a strong ref the maintenance could be garbage-collected mid-flight and
    the repair/catch-up would silently vanish."""
    from aria_service.autonomous import engine

    assert hasattr(engine, "_startup_maintenance_task")
    src = inspect.getsource(engine._engine_loop)
    assert "global _startup_maintenance_task" in src


def test_maintenance_failure_cannot_stop_the_engine():
    """Both halves are individually try/except'd — a failing repair or a
    failing catch-up must never take the engine down with it."""
    from aria_service.autonomous import engine

    src = inspect.getsource(engine._engine_loop)
    start = src.index("async def _startup_maintenance")
    end = src.index("_startup_maintenance_task = asyncio.create_task")
    body = src[start:end]
    assert body.count("except Exception") >= 2, (
        "repair and catch_up must each be independently guarded"
    )
