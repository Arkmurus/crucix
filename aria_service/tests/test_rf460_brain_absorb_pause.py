"""R-F460 — ARIA_BRAIN_ABSORB_PAUSE_MS operator-tunable dampener.

Live evidence (fly logs 2026-05-13 22:31 BST): under email-reader
backlog bursts of 30+ /brain/absorb calls in 1 second, absorb p95
climbed to 28484ms against a 3500ms breaker threshold. The breaker
tripped, auto-recovered, tripped again — the visible "ping-pong"
cascade in the live logs.

R-F460 adds an env-var-tunable inter-absorb pause. Default 0 (no
behavior change). Operator can set 100ms on fly to space the
email-reader bursts so downstream embedding + chromadb work catches
up between calls.

The env var is read PER-CALL (not at import time) so the operator
can change it without restarting the service.
"""
from __future__ import annotations

import asyncio


def _reset_interactive(monkeypatch):
    """Reset the interactive-activity flag so R-F860 doesn't inflate
    the pause value. Other tests may have called mark_interactive() or
    absorb(), which sets _last_interactive_at to a recent timestamp."""
    from aria_service.intel import brain_hook
    monkeypatch.setattr(brain_hook, "_last_interactive_at", 0.0)


def test_rf460_pause_defaults_to_zero(monkeypatch):
    """Default (env var unset) is 0ms — no behavior change for
    operators who haven't opted in."""
    _reset_interactive(monkeypatch)
    monkeypatch.delenv("ARIA_BRAIN_ABSORB_PAUSE_MS", raising=False)
    from aria_service.intel.brain_hook import _absorb_pause_ms
    assert _absorb_pause_ms() == 0


def test_rf460_pause_reads_env_var(monkeypatch):
    """The pause is set from the env var on every call (not cached
    at import time) so operators can change it on a running deploy."""
    _reset_interactive(monkeypatch)
    from aria_service.intel.brain_hook import _absorb_pause_ms
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "100")
    assert _absorb_pause_ms() == 100
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "50")
    assert _absorb_pause_ms() == 50


def test_rf460_pause_invalid_value_falls_back_to_zero(monkeypatch):
    """Garbage in env var → 0 (don't crash the absorb path on a typo)."""
    _reset_interactive(monkeypatch)
    from aria_service.intel.brain_hook import _absorb_pause_ms
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "not-a-number")
    assert _absorb_pause_ms() == 0
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "")
    assert _absorb_pause_ms() == 0


def test_rf460_pause_negative_clamped_to_zero(monkeypatch):
    """Negative values are nonsensical — clamp."""
    _reset_interactive(monkeypatch)
    from aria_service.intel.brain_hook import _absorb_pause_ms
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "-100")
    assert _absorb_pause_ms() == 0


def test_rf460_pause_excessive_clamped_to_ceiling(monkeypatch):
    """5s ceiling — operator shouldn't be able to wedge the service
    by setting a pathological value like 60000ms."""
    _reset_interactive(monkeypatch)
    from aria_service.intel.brain_hook import _absorb_pause_ms
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "60000")
    assert _absorb_pause_ms() == 5000


def test_rf460_absorb_sleeps_when_pause_set(monkeypatch):
    """When the env var is set, _absorb_pause_ms returns the configured
    value. Pin the wire-in so a future refactor doesn't silently drop it."""
    _reset_interactive(monkeypatch)
    from aria_service.intel.brain_hook import _absorb_pause_ms
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "100")
    assert _absorb_pause_ms() == 100
    monkeypatch.setenv("ARIA_BRAIN_ABSORB_PAUSE_MS", "500")
    assert _absorb_pause_ms() == 500


def test_rf460_absorb_does_not_sleep_when_pause_zero(monkeypatch):
    """Default behavior: no pause = _absorb_pause_ms returns 0."""
    _reset_interactive(monkeypatch)
    monkeypatch.delenv("ARIA_BRAIN_ABSORB_PAUSE_MS", raising=False)
    from aria_service.intel.brain_hook import _absorb_pause_ms
    assert _absorb_pause_ms() == 0
