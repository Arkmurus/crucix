"""R-F2198 — the load governor yields autonomy to active users.

Proven live 2026-06-30: a document review took 295s / never delivered while
autonomous research (web_search + multi-LLM) ran concurrently on the single
process; with autonomy PAUSED it delivered in 71s. The doc-lane (R-F2196) was
correct — the remaining cause was autonomous LLM-heavy work starving the user.

Fix: the load governor now sheds the autonomous tick when a user request
arrived within the interactive window (brain_hook.mark_interactive), so autonomy
backs off while someone is chatting and resumes when users go idle. These tests
drive the real governor + the real interactive signal.
"""
from __future__ import annotations

import pytest

from aria_service.intel import load_governor as lg
from aria_service.intel import brain_hook as bh


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    lg._stall_events.clear()
    lg._shed_events.clear()
    lg._shed_total = 0
    lg._last_shed_log = 0.0
    bh._last_interactive_at = 0.0          # no user activity by default
    monkeypatch.setenv("ARIA_LOAD_GOVERNOR_ENABLED", "1")
    monkeypatch.setenv("ARIA_LOAD_SHED_THRESHOLD", "0.6")
    monkeypatch.setenv("ARIA_LOAD_SHED_INTERACTIVE_WINDOW_S", "90")
    # neutralise state_store/stall pressure so we isolate the interactive signal
    import aria_service.intel.state_store as ss

    class _Q:
        def qsize(self): return 0
    monkeypatch.setattr(ss, "_QUEUED_WRITES", _Q(), raising=False)
    monkeypatch.setattr(ss, "_WRITE_QUEUE_MAX", 2000, raising=False)
    monkeypatch.setattr(ss, "_op_timeout_counts", {}, raising=False)
    yield
    bh._last_interactive_at = 0.0


def test_rf2198_idle_does_not_shed():
    """No recent user request → autonomy runs (no shed)."""
    assert lg.pressure()["interactive"] is False
    assert lg.should_shed() is False


def test_rf2198_active_user_makes_autonomy_shed():
    """A user request just arrived → governor sheds so autonomy yields the
    single process to the user (the live doc-review-starvation fix)."""
    bh.mark_interactive()                  # simulate a chat / doc review arriving
    p = lg.pressure()
    assert p["interactive"] is True, p
    assert p["score"] >= 0.6, p
    assert lg.should_shed() is True


def test_rf2198_autonomy_resumes_when_user_idle():
    """Once the interactive window has elapsed, autonomy resumes."""
    import time
    bh.mark_interactive()
    assert lg.should_shed() is True
    # Push the last-interactive timestamp well past the 90s window.
    bh._last_interactive_at = time.monotonic() - 200.0
    assert lg.pressure()["interactive"] is False
    assert lg.should_shed() is False


def test_rf2198_seconds_since_interactive_accessor():
    """The brain_hook accessor the governor depends on behaves correctly."""
    bh._last_interactive_at = 0.0
    assert bh.seconds_since_interactive() > 1e8   # never seen → huge
    bh.mark_interactive()
    assert bh.seconds_since_interactive() < 5.0    # just now → small
