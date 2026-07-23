"""R-F2950 — a dead/broken encode-offload pool must SELF-HEAL, not freeze the loop.

`sentence_transformers.encode()` holds the GIL, so the offload runs it in a
SUBPROCESS pool (R-F1890). When that pool is unavailable, the caller falls back
to an in-process encode ON the event loop — which freezes it (live 2026-07-23: a
77.8s R-F703 stall + state_store 5s read timeouts, from `OffloadUnavailable` on a
RAG embed). Before R-F2950 a genuine worker crash latched `_pool_broken=True`
PERMANENTLY with no recovery, so every subsequent embed stalled until a full
process restart.

These tests prove `_ensure_pool()` rebuilds a dead/broken pool (cooldown-bounded)
so embeds return to the off-loop subprocess.
"""
from __future__ import annotations

import pytest

from aria_service.intel import encode_offload as eo


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(eo, "_ENABLED", True)
    monkeypatch.setattr(eo, "_pool", None)
    monkeypatch.setattr(eo, "_pool_broken", False)
    monkeypatch.setattr(eo, "_last_restart_attempt", 0.0)
    yield


def _install_fake_start(monkeypatch):
    """Make start() 'rebuild' by setting a sentinel pool, without a real subprocess."""
    calls = {"n": 0}
    def _fake_start(*, warmup=True):
        calls["n"] += 1
        eo._pool = object()          # a live pool sentinel
        eo._pool_broken = False
    monkeypatch.setattr(eo, "start", _fake_start)
    return calls


def test_broken_pool_is_rebuilt_after_cooldown(monkeypatch):
    calls = _install_fake_start(monkeypatch)
    eo._pool = object()
    eo._pool_broken = True            # crashed + latched
    eo._last_restart_attempt = 0.0    # cooldown long elapsed

    eo._ensure_pool()

    assert calls["n"] == 1, "a broken pool past cooldown must be rebuilt"
    assert eo.is_enabled() is True, "offload must be available again after self-heal"
    assert eo._pool_broken is False


def test_never_started_pool_is_left_to_boot_start(monkeypatch):
    """The never-started case (no crash) is boot start()'s job — _ensure_pool must
    NOT silently start it, preserving the documented unstarted→fall-back contract
    (test_rf1890)."""
    calls = _install_fake_start(monkeypatch)
    eo._pool = None                   # never came up, but NOT crash-latched
    eo._pool_broken = False
    eo._last_restart_attempt = 0.0

    eo._ensure_pool()
    assert calls["n"] == 0, "an unstarted (non-broken) pool must be left to boot start()"


def test_no_thrash_within_cooldown(monkeypatch):
    calls = _install_fake_start(monkeypatch)
    eo._pool = object()
    eo._pool_broken = True
    eo._last_restart_attempt = eo.time.time()  # JUST attempted → still cooling

    eo._ensure_pool()
    assert calls["n"] == 0, "must NOT rebuild within the cooldown (anti-thrash)"


def test_healthy_pool_is_a_noop(monkeypatch):
    calls = _install_fake_start(monkeypatch)
    eo._pool = object()
    eo._pool_broken = False           # healthy

    eo._ensure_pool()
    assert calls["n"] == 0, "a healthy pool must never be rebuilt"


def test_disabled_offload_never_rebuilds(monkeypatch):
    calls = _install_fake_start(monkeypatch)
    monkeypatch.setattr(eo, "_ENABLED", False)
    eo._pool = None
    eo._last_restart_attempt = 0.0

    eo._ensure_pool()
    assert calls["n"] == 0, "offload disabled by env must stay off"


def test_encode_calls_ensure_pool_before_falling_back(monkeypatch):
    """The user-facing entry point encode() must attempt self-heal before it
    raises OffloadUnavailable (which triggers the loop-freezing in-process path)."""
    seen = {"ensure": 0}
    monkeypatch.setattr(eo, "_ensure_pool", lambda: seen.__setitem__("ensure", seen["ensure"] + 1))
    eo._pool = None
    eo._pool_broken = False
    with pytest.raises(eo.OffloadUnavailable):
        eo.encode("hi")
    assert seen["ensure"] == 1, "encode() must call _ensure_pool() before giving up"
