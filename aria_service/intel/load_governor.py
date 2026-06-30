"""R-F2185 — Adaptive Load Governor (self-regulating autonomy).

WHY THIS EXISTS
═══════════════
ARIA is a single-process asyncio brain: one event loop, one state_store
connection. ~15 autonomous loops + heavy background work (brain absorbs,
knowledge/neural graph writes) share that one loop with user-facing serving
(WhatsApp/web chat, DD). When the background work surges, it saturates the
state_store write queue and/or blocks the event loop with CPU-bound work — and
user serving STARVES (chat submits time out, the doc review "never delivers").

Until now nothing made the background work YIELD when serving was suffering.
ARIA could detect a wedge (R-F703) and respawn dead loops (R-F1610), but she
could not *throttle herself* under load. That is the missing self-heal: a
control loop that backs autonomy off automatically when the brain is under
pressure, and resumes it when the brain is calm — no operator intervention.

THE SIGNAL (cheap, no I/O)
══════════════════════════
Two instantaneous/recent pressure indicators, whichever is worse:
  • state_store WRITE-QUEUE DEPTH — qsize()/_WRITE_QUEUE_MAX. A backing-up
    queue means writes (incl. the ones serving needs) are not draining.
  • RECENT EVENT-LOOP STALLS — the R-F703 stall detector reports each stall
    here; N stalls in the window means the loop is being blocked by CPU work.

HOW IT'S USED
═════════════
The autonomous engine consults `should_shed()` at the top of each tick (right
after the manual-pause gate). Under pressure it skips the tick — exactly like
a pause, but automatic and self-clearing. Heavy bulk-absorb paths can consult
it too. The governor is SELF-AWARE: it records shed events so ARIA (and the
operator) can see "I throttled myself because serving was under pressure".

SAFE BY DESIGN
══════════════
Gating only ever makes ARIA do LESS background work — it can never break
serving, only protect it. Enabled by default (ARIA_LOAD_GOVERNOR_ENABLED=1);
set to 0 for the old always-run behaviour. Fail-safe: if the pressure probe
errors, it reports no pressure (don't shed) so a probe bug can't stall autonomy.
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger("aria.load_governor")


# ── Tunables (env-overridable) ──────────────────────────────────────────────
def _enabled() -> bool:
    return (os.getenv("ARIA_LOAD_GOVERNOR_ENABLED", "1") or "1").strip().lower() in ("1", "true", "yes")


def _shed_threshold() -> float:
    try:
        return float(os.getenv("ARIA_LOAD_SHED_THRESHOLD", "0.6"))
    except (TypeError, ValueError):
        return 0.6


def _interactive_shed_window_s() -> float:
    """R-F2198 — how long after a user request autonomy keeps yielding. Long
    enough to cover a multi-minute chat/doc-review turn so autonomous research
    can't restart mid-turn and starve it. Autonomy resumes once users go idle."""
    try:
        return float(os.getenv("ARIA_LOAD_SHED_INTERACTIVE_WINDOW_S", "90"))
    except (TypeError, ValueError):
        return 90.0


_STALL_WINDOW_S = 120.0          # how long a stall counts toward pressure
_STALL_TRIP_COUNT = 3.0          # this many recent stalls = full stall-pressure
_QUEUE_PRESSURE_GAIN = 1.5       # write-queue ratio is scaled by this (0.67 full→1.0)


# ── Recent event-loop stall tracking (fed by the R-F703 detector) ───────────
_stall_events: list[float] = []   # monotonic timestamps of recent stalls
_shed_events: list[float] = []     # timestamps of recent shed decisions
_shed_total: int = 0               # lifetime shed count
_last_shed_log: float = 0.0


def record_loop_stall(duration_s: float) -> None:
    """Called by main._event_loop_stall_detector (R-F703) on each detected
    stall, so the governor knows the loop is being blocked by CPU work."""
    now = time.monotonic()
    _stall_events.append(now)
    _trim(_stall_events, now)


def _trim(lst: list[float], now: float) -> None:
    cutoff = now - _STALL_WINDOW_S
    while lst and lst[0] < cutoff:
        lst.pop(0)


def _recent_stalls() -> int:
    now = time.monotonic()
    _trim(_stall_events, now)
    return len(_stall_events)


def pressure() -> dict:
    """Composite serving-pressure snapshot — cheap, no awaits, no I/O.
    `score` is 0.0 (calm) … 1.0 (saturated)."""
    q_ratio = 0.0
    op_timeouts = 0
    try:
        from . import state_store as ss
        q = getattr(ss, "_QUEUED_WRITES", None)
        if q is not None:
            q_ratio = q.qsize() / max(1, getattr(ss, "_WRITE_QUEUE_MAX", 2000))
        op_timeouts = sum((getattr(ss, "_op_timeout_counts", {}) or {}).values())
    except Exception:
        pass
    stalls = _recent_stalls()
    # R-F2198 — interactive pressure: a recent user request (chat / doc review)
    # means autonomy must YIELD so its heavy research can't starve the user on
    # the single process (live 2026-06-30: a doc review took 295s/never while
    # autonomous web_search + multi-LLM ran concurrently; paused → 71s). Full
    # pressure inside the window, decaying after.
    secs_since_user = 1e9
    try:
        from . import brain_hook as _bh
        if hasattr(_bh, "seconds_since_interactive"):
            secs_since_user = _bh.seconds_since_interactive()
    except Exception:
        pass
    _win = _interactive_shed_window_s()
    interactive_pressure = 1.0 if (secs_since_user < _win) else 0.0
    queue_pressure = min(q_ratio * _QUEUE_PRESSURE_GAIN, 1.0)
    stall_pressure = min(stalls / _STALL_TRIP_COUNT, 1.0) if _STALL_TRIP_COUNT > 0 else 0.0
    score = max(queue_pressure, stall_pressure, interactive_pressure)
    return {
        "score": round(score, 3),
        "write_queue_ratio": round(q_ratio, 3),
        "recent_stalls": stalls,
        "secs_since_user": round(secs_since_user, 1) if secs_since_user < 1e8 else None,
        "interactive": interactive_pressure >= 1.0,
        "op_timeouts_cumulative": op_timeouts,
        "shedding": score >= _shed_threshold(),
        "enabled": _enabled(),
    }


def should_shed() -> bool:
    """True when heavy BACKGROUND work should back off this cycle. Fail-safe:
    governor disabled or probe error → False (don't shed)."""
    if not _enabled():
        return False
    try:
        p = pressure()
    except Exception as e:
        # §21a — wire the probe failure to the brain so a broken governor is
        # visible, not dark. Then fail-safe: never let a probe bug stall autonomy.
        try:
            from .engine_wiring import wire_failure
            wire_failure(
                module="load_governor",
                detail=f"pressure probe failed: {e}",
                gap_type="engine_failure",
                source="load_governor:should_shed",
            )
        except Exception:
            pass
        return False
    shed = p["score"] >= _shed_threshold()
    if shed:
        _note_shed(p)
    return shed


def _note_shed(p: dict) -> None:
    """Record a shed decision + rate-limited self-aware log/brain-wire."""
    global _shed_total, _last_shed_log
    now = time.monotonic()
    _shed_events.append(now)
    _trim(_shed_events, now)
    _shed_total += 1
    # Rate-limit the visible signal to once per 30s so a sustained-pressure
    # window doesn't itself spam the log (which would add loop pressure).
    if now - _last_shed_log >= 30.0:
        _last_shed_log = now
        logger.warning(
            "[R-F2185] load-shed: backing off background work — pressure=%.2f "
            "(write_queue=%.2f, recent_stalls=%d). Serving is protected; "
            "autonomy resumes automatically when pressure clears.",
            p["score"], p["write_queue_ratio"], p["recent_stalls"],
        )
        # §21a — wire the shed to the brain (canonical success signal: the
        # governor DID its job and protected serving). Rate-limited with the log.
        try:
            from .engine_wiring import wire_success
            wire_success(
                module="load_governor",
                summary=(f"load-shed: protected serving by backing off background "
                         f"work (pressure={p['score']:.2f})"),
                source_id="load_governor:shed",
            )
        except Exception:
            pass
        # Self-awareness: wire the shed to the brain so ARIA SEES she throttled.
        # Only schedule the coroutine when a loop is actually running (the engine
        # path) — checking first avoids creating an un-awaited coroutine in a
        # sync caller / test.
        try:
            import asyncio as _aio
            _aio.get_running_loop()  # raises RuntimeError if no running loop
            from . import brain_hook as _bh
            if hasattr(_bh, "observe_self_event"):
                _aio.ensure_future(_bh.observe_self_event(
                    "load_shed",
                    {"pressure": p["score"], "write_queue_ratio": p["write_queue_ratio"],
                     "recent_stalls": p["recent_stalls"]},
                    gap_type="self_runtime",
                ))
        except Exception:
            pass


def stats() -> dict:
    """Observability snapshot for /health and the dashboard."""
    now = time.monotonic()
    _trim(_shed_events, now)
    p = pressure()
    return {
        "enabled": _enabled(),
        "shed_threshold": _shed_threshold(),
        "pressure": p,
        "shed_total": _shed_total,
        "recent_sheds": len(_shed_events),
    }
