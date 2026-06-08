"""R-F1426 — CLI prompt self-checks on timeout (replaces cross-thread wake).

R-F1407b/R-F1423 tried to wake the blocking prompt() via app.exit() from a
daemon thread — fundamentally fragile because prompt_toolkit's Application is
not thread-safe. R-F1426 replaces the cross-thread wake with a concurrent
in-loop task that polls _OPERATOR_QUEUE and calls app.exit() on the SAME
event loop (thread-safe by construction). The box renders ONCE — no re-render
spam. The daemon just queues messages; the in-loop poller picks them up.

These tests verify the new contract: _wake_prompt_threadsafe is a no-op.
"""
from __future__ import annotations

import aria_cli.cli as cli


def test_wake_is_noop():
    """R-F1426: _wake_prompt_threadsafe is a no-op — the prompt self-checks."""
    # Must not raise regardless of _PT_SESSION state
    cli._wake_prompt_threadsafe()


def test_wake_noop_with_session(monkeypatch):
    """Even with a session set, wake is a no-op (no app.exit called)."""
    class _FakeApp:
        def __init__(self):
            self.exited_with = None
        def exit(self, result=None):
            self.exited_with = result

    class _FakeSession:
        def __init__(self):
            self.app = _FakeApp()

    monkeypatch.setattr(cli, "_PT_SESSION", _FakeSession())
    cli._wake_prompt_threadsafe()
    # No app.exit() was called — the prompt self-checks via in-loop poller


def test_wake_noop_with_none_session(monkeypatch):
    """No session is fine — wake is a no-op."""
    monkeypatch.setattr(cli, "_PT_SESSION", None)
    cli._wake_prompt_threadsafe()  # must not raise


def test_poll_interval_shortened():
    """Bridge poll interval is still short for fast message pickup."""
    assert cli._BRIDGE_POLL_INTERVAL_S <= 5.0


def test_prompt_poll_interval_constant():
    """The in-loop poller interval is set to 2s for responsive wake."""
    from aria_cli.cli import _PROMPT_POLL_INTERVAL_S
    assert _PROMPT_POLL_INTERVAL_S == 2.0, (
        "In-loop poller interval must be 2s for responsive Claude note pickup"
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
