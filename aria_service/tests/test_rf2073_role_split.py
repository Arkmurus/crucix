"""R-F2073 (Tier 1) — ARIA_ROLE web|engine role-split capability test.

The keystone for multi-worker scaling: singleton background loops (autonomous
engine, research/self-improve/coder, schedulers, monitors, etc.) must start ONLY
on the engine process, so N request-serving 'web' processes don't each run them
(which would mean N× LLM cost, N× external calls, N× git deploys, gap-queue
races). The default (unset / 'all') must preserve today's single-process
behavior: one process runs BOTH web and engine.

These tests drive the real `_aria_role`, `_runs_singletons`, and `_singleton_task`
from aria_service.main.
"""
import asyncio

import pytest

from aria_service.main import _aria_role, _runs_singletons, _singleton_task


@pytest.mark.parametrize(
    "env_val,expected_runs",
    [
        (None, True),      # unset → all-in-one (BACKWARD-COMPAT: runs singletons)
        ("", True),        # blank → all-in-one
        ("all", True),     # explicit all-in-one
        ("engine", True),  # engine process runs singletons
        ("ENGINE", True),  # case-insensitive
        ("web", False),    # web process must NOT run singletons
        ("WEB", False),    # case-insensitive
    ],
)
def test_rf2073_runs_singletons_by_role(monkeypatch, env_val, expected_runs):
    if env_val is None:
        monkeypatch.delenv("ARIA_ROLE", raising=False)
    else:
        monkeypatch.setenv("ARIA_ROLE", env_val)
    assert _runs_singletons() is expected_runs


def test_rf2073_role_normalizes_unset_to_all(monkeypatch):
    monkeypatch.delenv("ARIA_ROLE", raising=False)
    assert _aria_role() == "all"
    monkeypatch.setenv("ARIA_ROLE", "  Engine ")
    assert _aria_role() == "engine"


def test_rf2073_singleton_task_skipped_on_web_role(monkeypatch):
    """On a 'web' process, _singleton_task must NOT create the task and must NOT
    invoke the loop factory — this is what prevents N× cost on extra workers."""
    monkeypatch.setenv("ARIA_ROLE", "web")
    started = {"n": 0}

    async def _loop():
        started["n"] += 1
        await asyncio.sleep(0)

    async def run():
        t = _singleton_task(_loop, "rf2073_test_loop")
        assert t is None, "web role must skip the singleton (return None)"
        await asyncio.sleep(0.01)
        assert started["n"] == 0, "loop factory must never be invoked on web role"

    asyncio.run(run())


def test_rf2073_singleton_task_runs_on_engine_role(monkeypatch):
    """On the engine process (and unset/all), the loop IS started."""
    monkeypatch.setenv("ARIA_ROLE", "engine")
    started = {"n": 0}

    async def _loop():
        started["n"] += 1
        await asyncio.sleep(0)

    async def run():
        t = _singleton_task(_loop, "rf2073_test_loop_engine")
        assert t is not None, "engine role must start the singleton"
        await t                       # let it run to completion
        assert started["n"] == 1, "loop factory must be invoked on engine role"

    asyncio.run(run())


def test_rf2073_singleton_task_runs_when_unset(monkeypatch):
    """Default (unset) is the all-in-one single process — must run singletons so
    today's 1-worker deploy is unchanged."""
    monkeypatch.delenv("ARIA_ROLE", raising=False)
    started = {"n": 0}

    async def _loop():
        started["n"] += 1

    async def run():
        t = _singleton_task(_loop, "rf2073_test_loop_default")
        assert t is not None
        await t
        assert started["n"] == 1

    asyncio.run(run())
