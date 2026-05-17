"""R-F651 — direct-tool dispatch enforces timeout_seconds.

Live incident 2026-05-17 06:00 UTC:
  RUN-EVAL-DAILY cron fired with timeout_seconds: 600 + cost_cap_usd: 0.30.
  The job ran 2 hours past its 600s timeout, burning $12.76 against
  claude-sonnet-4-6 — 40x the cost cap, 12x the timeout.

Root cause: in autonomous/tasks.py, the chat-pipeline branch wraps
aria_engine.aria_chat in `asyncio.wait_for(..., timeout=task.timeout_seconds)`
but the direct-tool branch (which dispatches run_eval, adversarial_weekly,
constitution_test, etc.) did NOT. So tool:run_eval ignored the configured
timeout completely.

Fix: wrap `_execute_direct_tool` in `asyncio.wait_for(...)` mirroring the
chat-pipeline pattern. On timeout, charge the task cap to the safety
counter so the daily-cap circuit breaker still sees runaway runs.
"""
from __future__ import annotations

import re
from pathlib import Path


_SRC = (
    Path(__file__).resolve().parent.parent / "autonomous" / "tasks.py"
).read_text(encoding="utf-8")


def test_rf651_direct_tool_wrapped_in_wait_for():
    """The direct-tool dispatch must wrap `_execute_direct_tool` in
    `asyncio.wait_for(..., timeout=task.timeout_seconds)`. Without this,
    timeout_seconds is silently ignored for every direct-call tool."""
    # Find the direct-call dispatch block (right after the `elif tool_kind in
    # (...)` tuple — anchored on the R-F651 marker so we don't drift to
    # the chat-pipeline branch).
    assert "R-F651" in _SRC, (
        "R-F651 marker comment missing — has the fix been reverted?"
    )
    # Confirm asyncio.wait_for wraps _execute_direct_tool, with the task
    # timeout argument.
    pattern = re.compile(
        r"asyncio\.wait_for\(\s*\n?\s*_execute_direct_tool\([^)]*\)\s*,\s*\n?"
        r"\s*timeout\s*=\s*task\.timeout_seconds",
        re.DOTALL,
    )
    assert pattern.search(_SRC), (
        "R-F651: _execute_direct_tool is not wrapped in "
        "asyncio.wait_for(timeout=task.timeout_seconds). Direct-tool "
        "dispatch will silently ignore the configured timeout."
    )


def test_rf651_timeout_records_status_and_charges_safety():
    """On TimeoutError, the direct-tool branch must set status=timeout
    and charge the task cap to the safety counter, matching the chat-
    pipeline branch's behaviour."""
    # Find a TimeoutError handler that references 'tool_kind' (the
    # direct-tool branch's identifier, distinct from the chat-pipeline
    # variable `task.id`).
    pattern = re.compile(
        r"except asyncio\.TimeoutError:.*?"
        r'record\["status"\]\s*=\s*"timeout".*?'
        r"safety.*?record_task_cost",
        re.DOTALL,
    )
    assert pattern.search(_SRC), (
        "R-F651: direct-tool timeout handler missing — must set "
        "record['status']='timeout' and call safety.record_task_cost so "
        "the daily-cap circuit breaker sees runaway direct-tool runs."
    )


def test_rf651_run_eval_uses_direct_tool_path():
    """Sanity: run_eval is in the direct-call dispatch tuple, so it
    benefits from R-F651. If someone moves it out, this test catches it
    so the regression is obvious."""
    # The dispatch tuple lives a few hundred lines down — anchor on the
    # R-F470 comment for run_eval inclusion.
    marker = '"run_eval"'
    block_start = _SRC.find("elif tool_kind in (")
    block_end = _SRC.find("):", block_start)
    assert block_start > 0 and block_end > block_start
    block = _SRC[block_start:block_end]
    assert marker in block, (
        "R-F651: 'run_eval' is no longer in the direct-tool dispatch "
        "tuple — R-F651's timeout wrap won't apply. Either re-add it or "
        "ensure the new path also has timeout enforcement."
    )
