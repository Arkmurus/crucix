"""Tests for brain_hook circuit breaker close-after-cooldown logic.

Regression guard for the live 2026-04-27 incident: breaker tripped at
15:41:35 due to first-time sentence-transformer cold-load (6s), then
stayed OPEN for 13+ minutes despite the cooldown being only 60s. Cause:
OPEN-state absorbs short-circuit before _record_latency, so the
latency window stays frozen with the slow samples that tripped it,
p95 stays high forever, _maybe_close_breaker never finds it recovered.

Fix: clear the stale window on cooldown elapse and unconditionally
close. _maybe_trip_breaker will re-trip naturally if the underlying
issue is still present.
"""
from __future__ import annotations

import time

from aria_service.intel import brain_hook


def _reset_breaker():
    """Restore breaker to a known-fresh state. brain_hook keeps state in
    module-level mutables, so tests must reset between cases."""
    brain_hook._breaker_state["open"] = False
    brain_hook._breaker_state["tripped_at"] = 0.0
    brain_hook._breaker_state["consecutive_high"] = 0
    # R-F790: per-episode ticket flag, reset between cases.
    brain_hook._breaker_state["ticket_filed_this_episode"] = False
    # R-F858: cross-episode ticket cooldown timestamp, reset between cases
    # (module-level state leaks across tests otherwise).
    brain_hook._breaker_state["last_ticket_at"] = 0.0
    brain_hook._recent_latencies_ms.clear()


def test_close_clears_stale_window_after_cooldown():
    _reset_breaker()
    # Simulate the live failure: window full of slow samples that tripped
    # the breaker, breaker open, cooldown elapsed.
    brain_hook._recent_latencies_ms.extend([5000.0, 6000.0, 7000.0, 8000.0])
    brain_hook._breaker_state["open"] = True
    brain_hook._breaker_state["tripped_at"] = time.time() - (brain_hook._COOLDOWN_S + 5)

    brain_hook._maybe_close_breaker()

    assert brain_hook._breaker_state["open"] is False, (
        "breaker must close after cooldown even when stale samples "
        "are still in the window"
    )
    assert len(brain_hook._recent_latencies_ms) == 0, (
        "stale latency window must be cleared so the next call is judged "
        "fresh, not against the samples that caused the trip"
    )
    assert brain_hook._breaker_state["consecutive_high"] == 0


def test_close_is_noop_before_cooldown_elapses():
    _reset_breaker()
    brain_hook._recent_latencies_ms.extend([5000.0, 6000.0])
    brain_hook._breaker_state["open"] = True
    # Tripped 10s ago — well within the 60s cooldown
    brain_hook._breaker_state["tripped_at"] = time.time() - 10

    brain_hook._maybe_close_breaker()

    assert brain_hook._breaker_state["open"] is True, (
        "breaker must stay open while cooldown is still elapsing"
    )
    # Window stays untouched while cooldown is active
    assert len(brain_hook._recent_latencies_ms) == 2


def test_close_is_noop_when_already_closed():
    _reset_breaker()
    brain_hook._recent_latencies_ms.extend([100.0, 200.0])
    # Already closed — no-op
    brain_hook._maybe_close_breaker()
    # Window untouched
    assert len(brain_hook._recent_latencies_ms) == 2
    assert brain_hook._breaker_state["open"] is False


def test_breaker_can_re_trip_after_close_if_still_slow():
    """After cooldown closes the breaker, the next slow calls should
    re-trip it — proving the close+clear pattern works as a real
    half-open probe rather than just hiding the issue."""
    _reset_breaker()
    # Trip once
    brain_hook._recent_latencies_ms.extend(
        [brain_hook._LATENCY_TRIP_MS + 1000] * 50
    )
    brain_hook._breaker_state["open"] = True
    brain_hook._breaker_state["tripped_at"] = time.time() - (brain_hook._COOLDOWN_S + 5)

    # Cooldown elapses → close + clear
    brain_hook._maybe_close_breaker()
    assert brain_hook._breaker_state["open"] is False

    # Simulate downstream still slow — feed _TRIP_CONSECUTIVE bad samples
    for _ in range(brain_hook._TRIP_CONSECUTIVE + 1):
        brain_hook._record_latency(brain_hook._LATENCY_TRIP_MS + 500)
        brain_hook._maybe_trip_breaker(reason="re-trip test")

    assert brain_hook._breaker_state["open"] is True, (
        "breaker must be able to re-trip after a close+clear cycle if "
        "downstream is still slow — otherwise we just hide the problem"
    )

    # Cleanup so other tests start fresh
    _reset_breaker()


# ─── R-F790 — one HIGH ticket per wedge episode, no auto-resolve ───────────

def test_rf790_should_file_ticket_true_when_flag_clear():
    """Fresh episode (flag False) → file a ticket."""
    _reset_breaker()
    assert brain_hook._should_file_ticket() is True


def test_rf790_should_file_ticket_false_when_flag_set():
    """Re-trip mid-episode (flag True) → DO NOT file a duplicate ticket.

    Live evidence 2026-05-21 16:13–16:25: 4 trips filed 4 HIGH tickets;
    operator saw alert churn instead of one persistent wedge signal.
    """
    _reset_breaker()
    brain_hook._breaker_state["ticket_filed_this_episode"] = True
    assert brain_hook._should_file_ticket() is False


def test_rf790_close_resets_ticket_flag_for_next_episode():
    """After cooldown closes the breaker, the per-episode flag resets so
    the NEXT genuine trip (a fresh wedge) does file a ticket."""
    _reset_breaker()
    brain_hook._recent_latencies_ms.extend([5000.0, 6000.0, 7000.0, 8000.0])
    brain_hook._breaker_state["open"] = True
    brain_hook._breaker_state["tripped_at"] = time.time() - (brain_hook._COOLDOWN_S + 5)
    brain_hook._breaker_state["ticket_filed_this_episode"] = True

    brain_hook._maybe_close_breaker()

    assert brain_hook._breaker_state["ticket_filed_this_episode"] is False
    # And as a consequence, _should_file_ticket reports True again (no recent
    # cross-episode ticket — last_ticket_at reset to 0).
    assert brain_hook._should_file_ticket() is True


# ─── R-F858 — cross-episode ticket cooldown (stop flapping-wedge alert spam) ─

def test_rf858_suppresses_ticket_within_cooldown():
    """A flapping wedge re-trips every 60-120s; each re-trip is a fresh episode
    (flag clear) but should NOT file a new HIGH ticket within the cross-episode
    cooldown — the flaps coalesce into one 'wedge recurring' signal."""
    _reset_breaker()
    brain_hook._breaker_state["last_ticket_at"] = time.time()  # ticket filed just now
    assert brain_hook._should_file_ticket() is False


def test_rf858_allows_ticket_after_cooldown_elapses():
    """Once the cooldown has elapsed, a genuinely new wedge files again."""
    _reset_breaker()
    brain_hook._breaker_state["last_ticket_at"] = (
        time.time() - (brain_hook._TICKET_COOLDOWN_S + 5)
    )
    assert brain_hook._should_file_ticket() is True


def test_rf858_trip_stamps_last_ticket_at():
    """A trip that files a ticket stamps last_ticket_at so subsequent flaps
    within the cooldown are suppressed."""
    _reset_breaker()
    brain_hook._warmup_complete = True
    # R-F1956: derive test latency from actual threshold so the test
    # automatically adapts to threshold changes (structural fix for
    # test-data coupling to a config constant).
    _test_latency = float(brain_hook._LATENCY_TRIP_MS + 5000)
    brain_hook._recent_latencies_ms.extend([_test_latency] * 12)
    for _ in range(brain_hook._TRIP_CONSECUTIVE):
        brain_hook._maybe_trip_breaker("test-flap")
    assert brain_hook._breaker_state["open"] is True
    assert brain_hook._breaker_state["last_ticket_at"] > 0.0
    _reset_breaker()


# ─── R-F968 — trip threshold must clear the per-tier wall-time ceiling ──────

def test_rf968_steady_state_tier_ceiling_does_not_trip():
    """R-F968 — a single slow-but-healthy tier hitting its configured budget
    must NOT trip the breaker. Live 2026-05-28 16:59-17:00: with the R-F932
    tier timeout at 3500ms, one slow neural absorb pushed p95 to ~4200ms and
    the OLD 3500ms trip threshold flapped the breaker TRIPPED↔CLOSED every
    ~60s with zero real loop starvation. The trip threshold (6000) now clears
    the configured tier ceiling + observed steady p95."""
    _reset_breaker()
    brain_hook._warmup_complete = True
    # p95 ~4200ms — the observed steady-state one-slow-tier latency, well
    # under the 6000ms threshold but over the OLD 3500ms one.
    brain_hook._recent_latencies_ms.extend([4200.0] * 12)
    for _ in range(brain_hook._TRIP_CONSECUTIVE + 2):
        brain_hook._maybe_trip_breaker(reason="steady-state tier ceiling")
    assert brain_hook._breaker_state["open"] is False, (
        "p95 at the configured tier ceiling (~4200ms) must NOT trip the "
        "6000ms breaker — that was the R-F968 flap"
    )
    _reset_breaker()
    brain_hook._warmup_complete = False


def test_rf968_genuine_wedge_still_trips():
    """A genuine wedge / loop starvation (the 11.6s R-F703 stalls) pushes p95
    well above 6000ms and must still trip — raising the threshold tames the
    flap without disarming the breaker."""
    _reset_breaker()
    brain_hook._warmup_complete = True
    # R-F1956: derive test latency from actual threshold so the test
    # automatically adapts to threshold changes.
    _test_latency = float(brain_hook._LATENCY_TRIP_MS + 5000)
    brain_hook._recent_latencies_ms.extend([_test_latency] * 12)
    for _ in range(brain_hook._TRIP_CONSECUTIVE):
        brain_hook._maybe_trip_breaker(reason="genuine wedge")
    assert brain_hook._breaker_state["open"] is True, (
        "a real >10000ms wedge must still trip the breaker"
    )
    _reset_breaker()
    brain_hook._warmup_complete = False


def test_rf968_trip_threshold_clears_tier_ceiling_invariant():
    """Invariant guard: the breaker trip threshold MUST stay above the per-tier
    wall-time ceiling (_TIER_TIMEOUT_S). If it drops to/below the ceiling, a
    healthy slow tier guarantees a false trip (the R-F968 regression)."""
    assert brain_hook._LATENCY_TRIP_MS > brain_hook._TIER_TIMEOUT_S * 1000, (
        f"trip threshold {brain_hook._LATENCY_TRIP_MS}ms must exceed the "
        f"per-tier ceiling {brain_hook._TIER_TIMEOUT_S * 1000}ms"
    )


def test_rf790_close_does_not_reference_pending_actions():
    """Source-level regression guard. R-F790 removed the premature
    auto-resolve from _maybe_close_breaker because cooldown elapsing is
    not proof the root cause cleared — the OLD code filed 8 churning
    HIGH tickets in 21 minutes on 2026-05-21, each auto-resolved
    seconds before the next re-trip. The closed-breaker code path must
    have zero references to pending_actions / mark_satisfied / the old
    _auto_resolve helper.
    """
    import inspect
    src = inspect.getsource(brain_hook._maybe_close_breaker)
    assert "pending_actions" not in src, (
        "R-F790 regression: _maybe_close_breaker references pending_actions. "
        "The premature auto-resolve was removed because cooldown ≠ cleared."
    )
    assert "mark_satisfied" not in src
    assert "_auto_resolve" not in src
