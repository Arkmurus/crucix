"""Regression guard for F51/F52/F53/F54 — the 2026-04-28 autonomous-fire
log triage.

Production fly logs at 08:15:21 / 08:15:37 showed:
  * 22 `Redis GET ... failed: got Future ... attached to a different loop`
  * 17 `Redis GET ... failed: Event loop is closed`
  * `no_symbolic_rule` capability gap recorded twice for the same query

Root cause: `_sync_correlation_context` ran `asyncio.run(_get_both())` in
a worker thread, but the aioredis client was loop-bound to the main app
loop. Every redis call from that fresh worker-loop raised "got Future
attached to a different loop". The error_log_handler turned each warning
into a `record_error()` task on the same broken loop — which itself
called redis and failed the same way → 21-deep recursion until the
worker loop closed (F52).

The duplicate `no_symbolic_rule` was independent: `compare_local_silently`
re-runs the same query post-LLM purely to score divergence, and the
second `symbolic_reasoner.reason()` call emitted a fresh capability gap.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch


# ── F51/F52: redis_store.run_on_main_loop ───────────────────────────────────


def test_run_on_main_loop_uses_captured_loop():
    """When connect() captured a main loop, run_on_main_loop must execute
    the coroutine on THAT loop, not a fresh asyncio.run() loop. The loop
    identity check is what proves we avoid the cross-loop hazard."""
    from aria_service.intel import redis_store as rs

    captured_loops: list[int] = []

    async def _record_loop():
        captured_loops.append(id(asyncio.get_running_loop()))
        return "ok"

    async def _runner():
        # Inside a real running loop — capture its id so we can compare.
        rs._main_loop = asyncio.get_running_loop()
        main_id = id(rs._main_loop)

        # Schedule from a worker thread so run_on_main_loop is exercised
        # the same way it is in production (called from the context-build
        # ThreadPoolExecutor, with no running loop in the thread).
        result = await asyncio.to_thread(
            rs.run_on_main_loop, _record_loop(), 5.0,
        )
        return main_id, result

    main_id, result = asyncio.run(_runner())
    rs._main_loop = None

    assert result == "ok"
    assert len(captured_loops) == 1
    assert captured_loops[0] == main_id, (
        "coroutine ran on a different loop than the captured main loop — "
        "cross-loop hazard NOT mitigated"
    )


def test_run_on_main_loop_falls_back_when_no_loop_captured():
    """If connect() never ran (tests, startup, post-shutdown), the helper
    must still complete the coroutine via asyncio.run()."""
    from aria_service.intel import redis_store as rs

    rs._main_loop = None

    async def _trivial():
        return 42

    result = rs.run_on_main_loop(_trivial(), timeout=2.0)
    assert result == 42


# ── F54: error_log_handler cascade-killer ────────────────────────────────────


def test_cross_loop_warnings_filtered():
    """Cross-loop async failures must NOT cascade into record_error.

    Pre-fix, a single 'got Future attached to a different loop' warning
    cascaded into 21 record_error attempts in 10ms because each attempt
    failed the same way and re-triggered the handler."""
    from aria_service.intel import error_log_handler

    recorded: list = []

    async def fake_record_error(**kwargs):
        recorded.append(kwargs)

    async def run():
        error_log_handler.uninstall()
        with patch("aria_service.intel.self_improve.record_error",
                   side_effect=fake_record_error):
            error_log_handler.install()
            try:
                lg = logging.getLogger("aria.test")
                # Real production messages from 2026-04-28 fly logs:
                lg.warning(
                    "Redis GET crucix:aria:deal_pipeline:leads failed: Task "
                    "<Task pending name='Task-9217'> got Future <Future pending> "
                    "attached to a different loop"
                )
                lg.warning(
                    "Redis GET crucix:aria:error_log failed: Event loop is closed"
                )
                # And a real, recordable failure to prove we did not over-filter:
                lg.warning("Real failure that needs fixing")
                await asyncio.sleep(0.01)
            finally:
                error_log_handler.uninstall()

    asyncio.run(run())

    assert len(recorded) == 1, (
        f"cross-loop warnings leaked into ledger: "
        f"{[r['message'] for r in recorded]}"
    )
    assert "Real failure" in recorded[0]["message"]


# ── F53: silent path through symbolic_reasoner ─────────────────────────────


def test_symbolic_reasoner_silent_skips_brain_hook():
    """compare_local_silently re-runs the same query post-LLM purely for
    divergence scoring. The brain_hook absorb (and the no_symbolic_rule
    capability gap inside it) must NOT fire on the silent re-run."""
    from aria_service.intel import symbolic_reasoner

    absorbed: list = []

    async def fake_absorb(**kwargs):
        absorbed.append(kwargs)

    async def run():
        with patch("aria_service.intel.brain_hook.absorb",
                   side_effect=fake_absorb):
            # Long enough to skip the trivial-input guard, generic enough
            # that no rule will match → would normally emit no_symbolic_rule.
            q = "Aria, investigate the latest on: SAM.gov defence procurement global"
            symbolic_reasoner.reason(q, silent_brain_hook=True)
            # Give any (incorrectly-scheduled) task a tick to fire.
            await asyncio.sleep(0.01)

    asyncio.run(run())
    assert absorbed == [], (
        "silent_brain_hook=True must suppress brain_hook.absorb — "
        f"got {len(absorbed)} call(s)"
    )


def test_symbolic_reasoner_default_still_emits():
    """Sanity check: default reason() (silent_brain_hook=False) still fires
    brain_hook.absorb so the gap ledger keeps working for the real call.

    The relative import resolves the package module at call time, so patching the
    canonical module attribute drives the real emission path without changing
    production code."""
    from aria_service.intel import symbolic_reasoner

    absorbed: list[dict] = []

    async def fake_absorb(**kwargs):
        absorbed.append(kwargs)

    async def run():
        with patch("aria_service.intel.brain_hook.absorb", side_effect=fake_absorb):
            question = "Explain quantum gardening policy in an unknown lunar market"
            result = symbolic_reasoner.reason(question)
            assert result["confident"] is False
            await asyncio.sleep(0.01)

    asyncio.run(run())
    assert len(absorbed) == 1
    assert absorbed[0]["module"] == "symbolic_reasoner"
    assert absorbed[0]["success"] is False
    assert absorbed[0]["gap_type"] == "no_symbolic_rule"
