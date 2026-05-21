"""R-F776 — self_diagnostic.tasks.yaml load moved off the asyncio main loop.

Wedge stack /data/wedge_stacks/wedge_673_1779352642.log captured the main
asyncio thread blocked inside yaml.safe_load called from
_check_autonomous_task on every diagnostic sweep × every module
registered (~30+ calls per sweep), producing 5-16s loop stalls (R-F703
events 08:45-08:51 UTC on 2026-05-21).

Fix:
  1. _sync_load_tasks_yaml runs the actual file open + yaml.safe_load.
  2. _load_tasks_yaml_cached wraps it in asyncio.to_thread + a 60s cache
     so the parse happens once per sweep, off the event loop.
  3. _check_autonomous_task becomes async and awaits the cache.

This regression test pins three contracts:
  - yaml.safe_load is reached via the cached-async path (off the loop).
  - The cache returns the same parsed dict on repeated calls within TTL.
  - The PASS/WARN/FAIL return shape on three task states still works.
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest


def _reset_cache():
    """Clear the module-level cache between tests so they don't interfere."""
    from aria_service.intel import self_diagnostic
    self_diagnostic._TASKS_YAML_CACHE = None


def test_rf776_yaml_load_runs_via_to_thread():
    """_sync_load_tasks_yaml must be invoked through asyncio.to_thread
    so the main asyncio event loop is never blocked by yaml.safe_load."""
    from aria_service.intel import self_diagnostic
    _reset_cache()

    real_to_thread = asyncio.to_thread
    calls: list = []

    async def tracking_to_thread(fn, *args, **kw):
        calls.append(fn.__name__)
        return await real_to_thread(fn, *args, **kw)

    async def run():
        with mock.patch("asyncio.to_thread", side_effect=tracking_to_thread):
            return await self_diagnostic._load_tasks_yaml_cached()

    data = asyncio.run(run())
    assert isinstance(data, dict)
    assert "_sync_load_tasks_yaml" in calls, (
        f"R-F776 regression: yaml.safe_load did not run via asyncio.to_thread. "
        f"to_thread call list: {calls}"
    )


def test_rf776_cache_avoids_repeated_loads_within_ttl():
    """N rapid calls must trigger ONE _sync_load_tasks_yaml, not N.
    This is the actual wedge fix — one yaml.safe_load per sweep, not
    one per module checked."""
    from aria_service.intel import self_diagnostic
    _reset_cache()

    load_count = {"n": 0}
    real_loader = self_diagnostic._sync_load_tasks_yaml

    def counting_loader():
        load_count["n"] += 1
        return real_loader()

    async def run():
        with mock.patch.object(
            self_diagnostic, "_sync_load_tasks_yaml", side_effect=counting_loader
        ):
            results = []
            for _ in range(20):
                results.append(await self_diagnostic._load_tasks_yaml_cached())
            return results

    results = asyncio.run(run())
    assert len(results) == 20
    assert load_count["n"] == 1, (
        f"R-F776 regression: tasks.yaml was loaded {load_count['n']} times "
        f"across 20 rapid calls — should be exactly 1 (cache miss only on "
        f"first call within the 60s TTL)."
    )


def test_rf776_check_autonomous_task_is_now_async():
    """The function must be async (was sync, blocked the loop)."""
    from aria_service.intel import self_diagnostic
    import inspect
    assert inspect.iscoroutinefunction(self_diagnostic._check_autonomous_task), (
        "R-F776 regression: _check_autonomous_task must be async so the "
        "yaml.safe_load it depends on can be awaited off the event loop."
    )


def test_rf776_returns_pass_for_enabled_task():
    """Sanity: the new async path still returns the expected verdict shape."""
    from aria_service.intel import self_diagnostic
    _reset_cache()

    fake_yaml = {
        "tasks": [
            {"id": "T-ENABLED", "enabled": True, "cron": "0 6 * * *"},
            {"id": "T-DISABLED", "enabled": False, "cron": "0 7 * * *"},
        ]
    }

    async def run():
        with mock.patch.object(
            self_diagnostic,
            "_sync_load_tasks_yaml",
            return_value=fake_yaml,
        ):
            return (
                await self_diagnostic._check_autonomous_task("T-ENABLED"),
                await self_diagnostic._check_autonomous_task("T-DISABLED"),
                await self_diagnostic._check_autonomous_task("T-MISSING"),
            )

    enabled, disabled, missing = asyncio.run(run())
    assert enabled[0] == "PASS"
    assert "cron" in enabled[1]
    assert disabled[0] == "WARN"
    assert "enabled=false" in disabled[1]
    assert missing[0] == "FAIL"
    assert "NOT FOUND" in missing[1]


def test_rf776_yaml_parse_error_degrades_to_warn():
    """A broken tasks.yaml must not crash the diagnostic — degrade to WARN."""
    from aria_service.intel import self_diagnostic
    _reset_cache()

    async def run():
        with mock.patch.object(
            self_diagnostic,
            "_sync_load_tasks_yaml",
            side_effect=RuntimeError("YAML scanner error: tab found"),
        ):
            return await self_diagnostic._check_autonomous_task("any-id")

    status, note = asyncio.run(run())
    assert status == "WARN"
    assert "tasks.yaml check failed" in note
