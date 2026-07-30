"""R-F3468 — the agent registry ran blocking sqlite on the event loop.

Found by R-F3464's stall attribution. Live /health/perf, 2026-07-30:

    "last_stall_loop_stack": [
      "/app/aria_service/intel/agent_registry.py:_db_tick_heartbeat:194",
      "/app/aria_service/intel/agent_registry.py:tick_heartbeat:407",
      "/app/aria_service/intel/autonomous_scheduler.py:_tick_heartbeat:79",
      "/app/aria_service/intel/autonomous_scheduler.py:_run_interval:100",
      ...uvicorn/server.py:run:77 ]
    "last_stall_threads": {"total": 24, "parked": 21, "aiosqlite_workers": 9}

An application frame on the loop thread means something BLOCKED the loop
(main.py:1771). ``tick_heartbeat`` is ``async`` but called ``_db_tick_heartbeat``
straight through: a synchronous ``sqlite3`` execute + ``commit()``. A commit is an
fsync, and under WAL/disk pressure that is exactly a multi-second loop freeze.

R-F1446 introduced this with the comment "writes to the dedicated DB first (fast,
no lock contention)". The dedicated DB did remove lock contention — that part
worked. But "fast" is not "non-blocking", and on the event loop only the second
property matters.

This is a CLASS, not one call: an AST sweep found **12** blocking ``_db_*`` calls
made from async functions (register x2, unregister, tick_heartbeat,
list_active_agents, get_agent_status, claim_gap x2, release_gap, is_gap_claimed,
send_message, read_messages). The stall named one of them; all twelve block.

Two properties must hold together, which is why the fix needs a lock as well as a
thread: the connection is opened ``check_same_thread=False``
(agent_registry.py:116) so off-thread use is permitted, but sqlite3 connections
are NOT safe for concurrent use — dispatching to threads without serialising
would trade a stall for a race.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import time

import pytest

from aria_service.intel.agent_registry import AgentRegistry


_SRC = pathlib.Path(__file__).resolve().parents[1] / "intel" / "agent_registry.py"


class _NoopRedis:
    async def set(self, *_a, **_kw):
        return True

    async def get(self, *_a, **_kw):
        return None

    async def expire(self, *_a, **_kw):
        return True


async def _loop_latency_while(coro, samples: list[float]) -> None:
    """Run `coro` while sampling event-loop responsiveness."""
    state = {"stop": False}

    async def _ticker() -> None:
        last = time.perf_counter()
        while not state["stop"]:
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            samples.append(now - last)
            last = now

    t = asyncio.create_task(_ticker())
    try:
        await coro
    finally:
        state["stop"] = True
        await asyncio.sleep(0.02)
        t.cancel()


class TestHeartbeatDoesNotBlockTheLoop:

    @pytest.mark.asyncio
    async def test_tick_heartbeat_keeps_the_loop_responsive(self, monkeypatch):
        """The capability test: drive the real async method with a SLOW db write.

        Pre-fix the synchronous call parks the loop for the whole sleep, so the
        ticker records a gap >= 0.4s. Post-fix the write runs off-thread and the
        loop keeps ticking.
        """
        reg = AgentRegistry()

        def _slow_db_write(*_a, **_kw):
            time.sleep(0.4)          # stands in for a commit()/fsync under pressure

        monkeypatch.setattr(reg, "_db_tick_heartbeat", _slow_db_write)
        monkeypatch.setattr(reg, "_get_redis", lambda: _NoopRedis())

        samples: list[float] = []
        await _loop_latency_while(reg.tick_heartbeat("test_agent", "unit test"), samples)

        assert samples, "loop-latency ticker never ran"
        worst = max(samples)
        assert worst < 0.25, (
            f"the event loop was blocked for {worst:.2f}s during tick_heartbeat — "
            f"a synchronous DB write is still running on the loop"
        )

    @pytest.mark.asyncio
    async def test_heartbeat_still_actually_writes(self, monkeypatch):
        """Moving work off-thread must not lose the write."""
        reg = AgentRegistry()
        seen: dict = {}

        def _capture(agent_id, now, current_task=None):
            seen["agent_id"] = agent_id
            seen["task"] = current_task

        monkeypatch.setattr(reg, "_db_tick_heartbeat", _capture)
        monkeypatch.setattr(reg, "_get_redis", lambda: _NoopRedis())
        await reg.tick_heartbeat("agent_x", "doing a thing")
        assert seen.get("agent_id") == "agent_x"
        assert seen.get("task") == "doing a thing"

    @pytest.mark.asyncio
    async def test_concurrent_db_calls_are_serialised(self, monkeypatch):
        """check_same_thread=False permits off-thread use; it does NOT make the
        connection safe for CONCURRENT use. Overlap would be a race.

        NB this passes pre-fix too — single-threaded calls are inherently
        serialised. It is here to stay green AFTER threads are introduced.
        """
        reg = AgentRegistry()
        active = {"now": 0, "max": 0}

        def _tracked(*_a, **_kw):
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            time.sleep(0.05)
            active["now"] -= 1

        monkeypatch.setattr(reg, "_db_tick_heartbeat", _tracked)
        monkeypatch.setattr(reg, "_get_redis", lambda: _NoopRedis())
        await asyncio.gather(*(reg.tick_heartbeat(f"a{i}") for i in range(6)))
        assert active["max"] == 1, (
            f"{active['max']} DB calls overlapped — sqlite3 connection used "
            f"concurrently across threads"
        )


class TestTheClassCannotGrow:
    """R-F3449 pattern: stop the failure CLASS recurring, not just this instance."""

    def test_no_async_function_calls_a_db_helper_directly(self):
        tree = ast.parse(_SRC.read_text(encoding="utf-8"))
        offenders: list[str] = []

        class _V(ast.NodeVisitor):
            def __init__(self) -> None:
                self.fn: str | None = None

            def visit_AsyncFunctionDef(self, node):
                prev, self.fn = self.fn, node.name
                self.generic_visit(node)
                self.fn = prev

            def visit_FunctionDef(self, node):
                prev, self.fn = self.fn, None   # sync fn: direct calls are fine
                self.generic_visit(node)
                self.fn = prev

            def visit_Call(self, node):
                if (self.fn
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr.startswith("_db_")):
                    offenders.append(f"{self.fn}() -> {node.func.attr}() "
                                     f"at line {node.lineno}")
                self.generic_visit(node)

        _V().visit(tree)
        assert not offenders, (
            "blocking sqlite called directly from an async function — dispatch it "
            "through the off-thread helper instead:\n  " + "\n  ".join(offenders)
        )

    def test_no_db_helper_calls_another_db_helper(self):
        """A plain (non-reentrant) Lock guards _adb, so nesting would deadlock."""
        tree = ast.parse(_SRC.read_text(encoding="utf-8"))
        nested: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name.startswith("_db_"):
                for call in ast.walk(node):
                    if isinstance(call, ast.Call) \
                            and isinstance(call.func, ast.Attribute) \
                            and call.func.attr.startswith("_db_"):
                        nested.append(f"{node.name} -> {call.func.attr}")
        assert not nested, f"nested _db_* call would deadlock the lock: {nested}"
