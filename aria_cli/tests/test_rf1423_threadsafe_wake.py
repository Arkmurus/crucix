"""R-F1423 — thread-safe CLI prompt wake (fixes submit-then-resume).

R-F1407b woke the blocking prompt() by calling _PT_SESSION.app.exit() DIRECTLY
from the poller daemon thread. prompt_toolkit's Application is not thread-safe,
so it worked once (single-shot test) but NOT in the repeated submit-then-idle
flow (operator: "after she submits she doesn't go back live without my prompt").
R-F1423 schedules the exit ON the app's running event loop via
call_soon_threadsafe, falling back to a direct call only if no running loop.

These tests drive the REAL _wake_prompt_threadsafe against mock apps.
"""
from __future__ import annotations

import aria_cli.cli as cli


class _FakeLoop:
    def __init__(self, running=True):
        self._running = running
        self.scheduled = []

    def is_running(self):
        return self._running

    def call_soon_threadsafe(self, fn, *a):
        self.scheduled.append(fn)
        fn(*a)  # execute so we can observe the effect


class _FakeApp:
    def __init__(self, loop):
        self.loop = loop
        self.exited_with = None

    def exit(self, result=None):
        self.exited_with = result


class _FakeSession:
    def __init__(self, app):
        self.app = app


def test_uses_call_soon_threadsafe_when_loop_running(monkeypatch):
    loop = _FakeLoop(running=True)
    app = _FakeApp(loop)
    monkeypatch.setattr(cli, "_PT_SESSION", _FakeSession(app))

    cli._wake_prompt_threadsafe()

    assert loop.scheduled, "must schedule the exit on the app loop (thread-safe)"
    assert app.exited_with == ""  # exit(result="") fired via the loop


def test_falls_back_to_direct_exit_when_no_running_loop(monkeypatch):
    loop = _FakeLoop(running=False)
    app = _FakeApp(loop)
    monkeypatch.setattr(cli, "_PT_SESSION", _FakeSession(app))

    cli._wake_prompt_threadsafe()

    assert not loop.scheduled              # did NOT use the (non-running) loop
    assert app.exited_with == ""           # fell back to a direct exit


def test_no_session_is_noop(monkeypatch):
    monkeypatch.setattr(cli, "_PT_SESSION", None)
    cli._wake_prompt_threadsafe()  # must not raise


def test_app_without_loop_attr_falls_back(monkeypatch):
    class _AppNoLoop:
        loop = None
        def __init__(self): self.exited_with = None
        def exit(self, result=None): self.exited_with = result
    app = _AppNoLoop()
    monkeypatch.setattr(cli, "_PT_SESSION", _FakeSession(app))
    cli._wake_prompt_threadsafe()
    assert app.exited_with == ""


def test_exit_raising_does_not_propagate(monkeypatch):
    class _BoomApp:
        loop = None
        def exit(self, result=None): raise RuntimeError("ptk boom")
    monkeypatch.setattr(cli, "_PT_SESSION", _FakeSession(_BoomApp()))
    cli._wake_prompt_threadsafe()  # daemon must survive — no raise


def test_poll_interval_shortened():
    # idle resume must be near-instant, not up to 20s
    assert cli._BRIDGE_POLL_INTERVAL_S <= 5.0


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
