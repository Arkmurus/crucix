"""R-F2279 — forensic thread-stack dump before the state_store wedge os._exit.

The 2026-07-02 3.5h outage could not be root-caused because the op that wedged
the aiosqlite connection thread was never logged. R-F2277's watchdog now restarts
on a wedge; R-F2279 makes that restart CAPTURE the cause first — dumping all
thread stacks (the aiosqlite worker thread's frame = the stuck SQL) so the NEXT
wedge is diagnosable.

These capability tests drive the REAL dump function and assert it (a) writes a
readable dump containing thread-stack content, (b) never raises, and (c) is
invoked by the watchdog BEFORE the process exit.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from aria_service.intel import state_store as _ss


class _ForcedExit(BaseException):
    """BaseException so it escapes the watchdog loop's `except Exception`, exactly
    as a real os._exit terminates the process."""


class TestForensicDump:
    def test_dump_writes_readable_stack_file(self, tmp_path):
        _ss._dump_wedge_forensics(200.0, base_dir=str(tmp_path))
        files = list(tmp_path.glob("ss_wedge_*.log"))
        assert len(files) == 1, "a durable wedge dump file must be written"
        body = files[0].read_text(encoding="utf-8", errors="replace")
        assert "[R-F2279]" in body, "dump must be tagged"
        assert "unavailable 200s" in body, "dump must record how long the store was down"
        # faulthandler output always includes a Thread header + a Python frame ('File ')
        assert "Thread" in body and "File " in body, "dump must contain real thread stacks"
        assert "end [R-F2279] dump" in body

    def test_dump_never_raises_on_bad_dir(self):
        # unwritable/nonsense base dir must be swallowed, not raised
        _ss._dump_wedge_forensics(10.0, base_dir="/nonexistent\x00/definitely/not/writable")
        # (no assertion needed — the test passes iff no exception propagated)

    def test_dump_never_raises_default_path(self):
        # exercise the default-path branch (no base_dir) — must not raise
        _ss._dump_wedge_forensics(1.0)


class TestWatchdogCapturesBeforeExit:
    @pytest.fixture(autouse=True)
    def _fast_and_stubbed(self, monkeypatch):
        monkeypatch.setenv("ARIA_SS_WATCHDOG_SETTLE_S", "0.0")
        monkeypatch.setenv("ARIA_SS_WATCHDOG_INTERVAL_S", "0.02")
        monkeypatch.setenv("ARIA_SS_WATCHDOG_RECONNECT_S", "0.04")
        monkeypatch.setenv("ARIA_SS_WATCHDOG_CEILING_S", "0.10")
        async def _noop():
            return None
        monkeypatch.setattr(_ss, "_reconnect", _noop)
        monkeypatch.setattr(_ss, "_ensure_read_conn", _noop)
        async def _always_fail(*a, **k):
            return False
        monkeypatch.setattr(_ss, "probe_liveness", _always_fail)
        self.order = []
        def _fake_dump(unhealthy_for_s, base_dir=None):
            self.order.append("dump")
        monkeypatch.setattr(_ss, "_dump_wedge_forensics", _fake_dump)
        def _fake_exit(code):
            self.order.append("exit")
            raise _ForcedExit(code)
        monkeypatch.setattr(os, "_exit", _fake_exit)
        _ss._ss_wd_unhealthy_since = None
        _ss._ss_wd_reconnect_fired = False

    @pytest.mark.asyncio
    async def test_forensic_dump_runs_before_exit(self):
        with pytest.raises(_ForcedExit):
            await asyncio.wait_for(_ss.liveness_watchdog_loop(), timeout=5.0)
        assert self.order == ["dump", "exit"], (
            "the watchdog must capture forensics BEFORE forcing the process exit; "
            f"got order={self.order}"
        )
