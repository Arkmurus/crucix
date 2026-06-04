"""R-F1309 + R-F1310 — capability tests for ARIA's self-healing.

R-F1309: the Enter-key handler survives any exception (no more frozen REPL).
R-F1310: the aria-forever supervisor — stall detection (busy + stale heartbeat),
crash restart with ARIA_RECOVERED reason, clean-exit stop, restart-storm brake.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from aria_cli import supervisor as sup


# ── liveness primitives ──────────────────────────────────────────────────────

def test_heartbeat_age_missing_is_inf(tmp_path: Path) -> None:
    assert sup.heartbeat_age(tmp_path) == float("inf")


def test_heartbeat_age_fresh(tmp_path: Path) -> None:
    (tmp_path / "heartbeat").write_text(str(time.time()), encoding="utf-8")
    assert sup.heartbeat_age(tmp_path) < 5


def test_stall_requires_busy_AND_stale(tmp_path: Path) -> None:
    # not busy + no heartbeat → idle at prompt, never a stall
    assert sup.is_stalled(tmp_path, stall_s=1) is False
    # busy + fresh heartbeat → working fine
    (tmp_path / "busy").write_text("1", encoding="utf-8")
    (tmp_path / "heartbeat").write_text("1", encoding="utf-8")
    assert sup.is_stalled(tmp_path, stall_s=300) is False
    # busy + stale heartbeat → STALL
    import os
    old = time.time() - 1000
    os.utime(tmp_path / "heartbeat", (old, old))
    assert sup.is_stalled(tmp_path, stall_s=300) is True


def test_reset_liveness_clears_stale_busy(tmp_path: Path) -> None:
    """A crash that died mid-turn leaves `busy` behind; reset must clear it so
    the NEXT child isn't insta-killed as a stall."""
    (tmp_path / "busy").write_text("1", encoding="utf-8")
    sup.reset_liveness(tmp_path)
    assert not (tmp_path / "busy").exists()
    assert sup.heartbeat_age(tmp_path) < 5


# ── supervisor loop (fake children, no real processes) ───────────────────────

class _FakeChild:
    def __init__(self, rc: int) -> None:
        self._rc = rc
        self.returncode: int | None = None
        self.pid = 4242

    def wait(self, timeout=None):
        self.returncode = self._rc
        return self._rc

    def kill(self):  # pragma: no cover - not reached in these tests
        self.returncode = -9


def _patch_run_env(monkeypatch, tmp_path: Path, children: list[_FakeChild],
                   envs: list[dict]) -> None:
    monkeypatch.setattr(sup, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(sup.time, "sleep", lambda s: None)
    it = iter(children)

    def _popen(cmd, env=None, **kwargs):
        envs.append(dict(env or {}))
        return next(it)

    monkeypatch.setattr(sup.subprocess, "Popen", _popen)


def test_clean_exit_stops_supervision(monkeypatch, tmp_path: Path) -> None:
    envs: list[dict] = []
    _patch_run_env(monkeypatch, tmp_path, [_FakeChild(0)], envs)
    assert sup.run([]) == 0
    assert len(envs) == 1
    assert envs[0].get("ARIA_SUPERVISED") == "1"
    assert "ARIA_RECOVERED" not in envs[0]


def test_crash_restarts_with_recovery_reason(monkeypatch, tmp_path: Path) -> None:
    """Crash (exit 3) → relaunch with ARIA_RECOVERED naming the crash; second
    child exits clean → supervision ends 0."""
    envs: list[dict] = []
    _patch_run_env(monkeypatch, tmp_path, [_FakeChild(3), _FakeChild(0)], envs)
    assert sup.run([]) == 0
    assert len(envs) == 2
    assert "crash" in envs[1].get("ARIA_RECOVERED", "")
    assert "3" in envs[1]["ARIA_RECOVERED"]


def test_restart_storm_brake(monkeypatch, tmp_path: Path) -> None:
    """More than MAX restarts in the rolling hour → give up with exit 1, not an
    infinite crash loop."""
    envs: list[dict] = []
    crashers = [_FakeChild(1) for _ in range(sup.MAX_RESTARTS_PER_HOUR + 2)]
    _patch_run_env(monkeypatch, tmp_path, crashers, envs)
    assert sup.run([]) == 1
    # It launched at most MAX+1 children before braking.
    assert len(envs) <= sup.MAX_RESTARTS_PER_HOUR + 1


# ── R-F1309: the Enter handler survives a poisoned buffer ────────────────────

def test_enter_handler_survives_exception() -> None:
    """Drive the real keybinding handler with a buffer whose
    validate_and_handle raises (the frozen-REPL class). The handler must
    swallow, sanitize and retry — never propagate into the event loop."""
    from aria_cli.cli import PROMPT_TOOLKIT_AVAILABLE, _build_key_bindings
    if not PROMPT_TOOLKIT_AVAILABLE:
        pytest.skip("prompt_toolkit not installed")
    kb = _build_key_bindings()
    enter = next(b for b in kb.bindings
                 if getattr(b.keys[0], "value", str(b.keys[0])) in ("enter", "c-m")
                 or str(b.keys[0]).endswith("ControlM"))

    class _Buffer:
        def __init__(self) -> None:
            self.text = "task with surrogate \ud83d here"
            self.calls = 0
            self.reset_called = False

        def validate_and_handle(self):
            self.calls += 1
            if self.calls == 1:
                raise UnicodeEncodeError("utf-8", "x", 0, 1, "surrogates not allowed")

        def reset(self):
            self.reset_called = True

        def insert_text(self, t):
            self.text = t

    class _Event:
        current_buffer = _Buffer()

    enter.handler(_Event)  # must NOT raise
    assert _Event.current_buffer.reset_called
    assert _Event.current_buffer.calls == 2  # sanitized retry happened
