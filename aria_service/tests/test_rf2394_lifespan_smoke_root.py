"""R-F2394 — local lifespan smoke must not stall on coder file scans."""
from __future__ import annotations

import asyncio
import inspect
import time

# R-F3755/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


def test_modifiable_file_scan_yields_event_loop(monkeypatch):
    """The coder startup scan must not run recursive filesystem work on-loop."""
    from aria_service.intel import self_improve as si

    si.MODIFIABLE_FILES.clear()
    si._MODIFIABLE_INITIALIZED = False

    def _slow_scan(root):
        time.sleep(0.2)
        return {"aria_service/main.py"}

    async def _run():
        monkeypatch.setattr(si, "_collect_modifiable_files_sync", _slow_scan)
        task = asyncio.create_task(si._ensure_modifiable_files())
        await asyncio.sleep(0.05)
        assert not task.done(), "scan should still be running in a worker thread"
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(_run())
    assert si._MODIFIABLE_INITIALIZED is True
    assert "aria_service/main.py" in si.MODIFIABLE_FILES


def test_modifiable_file_scan_failure_can_retry(monkeypatch):
    """A failed scan must not latch _MODIFIABLE_INITIALIZED permanently true."""
    from aria_service.intel import self_improve as si

    si.MODIFIABLE_FILES.clear()
    si._MODIFIABLE_INITIALIZED = False
    attempts = {"count": 0}

    def _flaky_scan(root):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("scan failed")
        return {"aria_service/intel/self_improve.py"}

    async def _run():
        monkeypatch.setattr(si, "_collect_modifiable_files_sync", _flaky_scan)
        try:
            await si._ensure_modifiable_files()
        except RuntimeError:
            pass
        else:
            raise AssertionError("first scan should raise")
        assert si._MODIFIABLE_INITIALIZED is False
        await si._ensure_modifiable_files()

    asyncio.run(_run())
    assert attempts["count"] == 2
    assert si._MODIFIABLE_INITIALIZED is True
    assert "aria_service/intel/self_improve.py" in si.MODIFIABLE_FILES


def test_security_scan_yields_event_loop(monkeypatch):
    """Scheduler security scans must not monopolize the event loop."""
    from aria_service.intel import ecosystem_dashboard as ed

    def _slow_scan():
        time.sleep(0.2)
        return {
            "timestamp": time.time(),
            "total_findings": 0,
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "findings": [],
        }

    async def _run():
        monkeypatch.setattr(ed, "_run_security_scan_sync", _slow_scan)
        task = asyncio.create_task(ed.run_security_scan())
        await asyncio.sleep(0.05)
        assert not task.done(), "security scan should still be running in a worker thread"
        result = await asyncio.wait_for(task, timeout=1.0)
        assert result["total_findings"] == 0

    asyncio.run(_run())


def test_project_context_analysis_yields_event_loop(monkeypatch, tmp_path):
    """Coder startup context logging must not run repo scans on-loop."""
    from aria_service.autonomous import coder_entrypoint as ce

    ce._REPO_ROOT = tmp_path

    def _slow_context(root):
        time.sleep(0.2)
        return {
            "python_files": 1,
            "js_files": 0,
            "test_files": 0,
            "total_lines": 10,
            "endpoints": 0,
            "aria_modules": 1,
        }

    async def _run():
        monkeypatch.setattr(ce, "_analyse_project_context_sync", _slow_context)
        task = asyncio.create_task(ce._analyse_project_context())
        await asyncio.sleep(0.05)
        assert not task.done(), "project context scan should still be running in a worker thread"
        result = await asyncio.wait_for(task, timeout=1.0)
        assert result["python_files"] == 1

    try:
        asyncio.run(_run())
    finally:
        ce._REPO_ROOT = None


def test_lifespan_shutdown_closes_state_store():
    """Lifespan teardown must close aiosqlite workers before process exit."""
    from aria_service import main

    source = function_source(main, "lifespan")
    assert "state_store" in source
    assert "_state_store_shut.close()" in source


def test_lifespan_startup_tasks_are_supervised():
    """Startup-owned work must be in _BG_TASKS so smoke teardown can cancel it."""
    from aria_service import main

    source = function_source(main, "lifespan")
    assert "asyncio.create_task(_prewarm_heavy_imports())" not in source
    assert "_bg_task(asyncio.create_task(_prewarm_heavy_imports()" in source
    assert "asyncio.create_task(_register_all_contracts())" not in source
    assert "_bg_task(asyncio.create_task(_register_all_contracts()" in source
    assert "asyncio.create_task(_delayed_auto_register(_auto_reg))" not in source
    assert "_bg_task(asyncio.create_task(_delayed_auto_register(_auto_reg)" in source


def test_state_store_close_bounds_stuck_aiosqlite_connections(monkeypatch):
    """A stuck aiosqlite close must not wedge lifespan teardown."""
    from aria_service.intel import state_store as ss

    class _StuckConn:
        def __init__(self):
            self.stopped = False

        async def close(self):
            await asyncio.sleep(5)

        def stop(self):
            self.stopped = True
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            fut.set_result(None)
            return fut

    stuck = _StuckConn()
    monkeypatch.setenv("ARIA_STATE_CLOSE_TIMEOUT_S", "0.25")
    monkeypatch.setattr(ss, "_flush_write_queue", lambda: asyncio.sleep(0))
    monkeypatch.setattr(ss, "_stop_write_worker", lambda: asyncio.sleep(0))
    monkeypatch.setattr(ss, "_flush_cold_queue", lambda: asyncio.sleep(0))
    monkeypatch.setattr(ss, "_stop_cold_write_worker", lambda: asyncio.sleep(0))
    ss._conn = stuck
    ss._read_conn = None
    ss._read_pool = []
    ss._cold_conn = None
    ss._cold_read_conn = None
    ss._cold_queue = None

    asyncio.run(ss.close())

    assert stuck.stopped is True
    assert ss._conn is None


def test_aiosqlite_workers_are_daemonized():
    """A stuck aiosqlite worker must not keep Python alive after teardown."""
    from aria_service.intel import redis_store  # noqa: F401 - applies patch
    import aiosqlite.core as core

    conn = core.Connection(lambda: None, 64)
    assert conn._thread.daemon is True
