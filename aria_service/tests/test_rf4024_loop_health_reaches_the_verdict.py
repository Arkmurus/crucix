"""R-F4024 (C-96) — `/health` must not publish `starved` and verdict `operational`.

THE DEFECT. `/health` returns `loop` (the R-F2849 event-loop lag gauge) and
`status`/`degraded_reasons` in the SAME payload, and the verdict never read the
gauge. Live on aria-intel 2026-08-14, one response:

    "loop":   {"status": "starved", "p50_ms": 0.7,
               "p95_ms": 3264.1, "max_ms": 9726.2}
    "status": "operational"
    "degraded_reasons": []
    "diagnostic": {"overall": "GREEN", "fail": 0}

The event loop was blocking for up to 9.7 s and every verdict said fine. The
comment above `_degraded_reasons` says the block exists so this surface cannot
become "a status page divorced from reality" (R-F3667) — it was, about the one
number it already had in hand.

This is why C-95 ran unnoticed: `knowledge.py` records the loop reading
`starved` at p95 2058ms on 2026-08-13, and nothing escalated, because no health
verdict in the tree could express it.

WHAT IS PINNED, and what deliberately is NOT.
  - `starved` degrades. That is the band the gauge defines as "I/O callbacks are
    waiting behind CPU work".
  - `busy` does NOT. Elevated-but-turning is normal under load, and a verdict
    that cries wolf gets ignored — which is how a real `starved` would be
    missed again.
  - A STALE feed degrades. The monitor samples ON the loop, so if the loop
    wedges the samples stop; a frozen gauge showing its last healthy p95 is
    exactly the "guard that goes blind rather than fails" shape §16 records.
  - `unknown` with no samples yet does NOT degrade. The detector arms 120 s
    after boot by design, and flagging that would make every deploy flap.
"""
import pytest

from aria_service import main as main_mod


def _reasons(loop_health):
    return main_mod._loop_degraded_reasons(loop_health)


# ── the defect ─────────────────────────────────────────────────────────────

def test_starved_loop_degrades_the_verdict():
    live = {"status": "starved", "samples": 600, "p50_ms": 0.7,
            "p95_ms": 3264.1, "max_ms": 9726.2, "last_sample_age_s": 0.6}
    assert "event_loop_starved" in _reasons(live), (
        "R-F4024: /health reported `operational` while publishing "
        "`loop.status: starved` in the same payload."
    )


# ── the false-alarm guards ─────────────────────────────────────────────────

def test_busy_loop_does_not_degrade():
    busy = {"status": "busy", "samples": 600, "p95_ms": 240.0,
            "last_sample_age_s": 0.4}
    assert _reasons(busy) == [], (
        "`busy` is elevated-but-turning. A verdict that fires on it gets "
        "ignored, which is how a real `starved` gets missed."
    )


def test_healthy_loop_does_not_degrade():
    ok = {"status": "healthy", "samples": 600, "p95_ms": 1.1,
          "last_sample_age_s": 0.4}
    assert _reasons(ok) == []


def test_freshly_booted_unknown_does_not_degrade():
    """The detector arms 120s after boot — flagging that flaps every deploy."""
    booting = {"status": "unknown", "samples": 0}
    assert _reasons(booting) == []


# ── the blind-guard case: a frozen gauge is not a healthy one ──────────────

def test_stale_feed_degrades_even_though_the_last_reading_was_healthy():
    """A wedged loop stops feeding the monitor that runs ON it.

    The gauge then keeps serving its last good numbers forever. Reading that as
    health is the same failure as a guard whose universe went empty.
    """
    frozen = {"status": "healthy", "samples": 600, "p95_ms": 1.1,
              "max_ms": 12.0, "last_sample_age_s": 240.0}
    got = _reasons(frozen)
    assert "event_loop_monitor_stale" in got, (
        "R-F4024: the lag gauge stopped updating and the verdict still read it "
        "as healthy — a frozen instrument certifying the thing it stopped "
        f"measuring. got={got}"
    )


def test_stale_feed_and_starved_both_reported():
    both = {"status": "starved", "samples": 600, "p95_ms": 4000.0,
            "last_sample_age_s": 300.0}
    got = _reasons(both)
    assert "event_loop_starved" in got and "event_loop_monitor_stale" in got


# ── never raise on a malformed gauge ───────────────────────────────────────

@pytest.mark.parametrize("bad", [None, {}, {"status": None}, "nonsense", 42,
                                 {"status": "starved", "last_sample_age_s": "x"}])
def test_malformed_gauge_never_raises(bad):
    """A health endpoint that 500s because its own gauge is odd is worse than
    one that reports nothing."""
    out = main_mod._loop_degraded_reasons(bad)
    assert isinstance(out, list)


def test_starved_still_detected_when_age_is_unparseable():
    """Degrade on what IS readable rather than discarding the whole reading."""
    got = main_mod._loop_degraded_reasons(
        {"status": "starved", "last_sample_age_s": "x"}
    )
    assert "event_loop_starved" in got


# ── §3c capability test: drive the REAL endpoint, not just the helper ──────

@pytest.fixture
def _app_state():
    """`/health` reads `app.state.llm_provider`, which lifespan populates.

    Set it to None (the handler's own "no provider" path) so the endpoint can
    be driven without booting the app — the subject here is the verdict, not
    the LLM chain.
    """
    state = main_mod.app.state
    had = hasattr(state, "llm_provider")
    prev = state.llm_provider if had else None
    state.llm_provider = None
    yield
    if had:
        state.llm_provider = prev
    else:
        try:
            del state.llm_provider
        except (AttributeError, KeyError):
            pass

@pytest.mark.asyncio
async def test_real_health_endpoint_degrades_when_the_loop_is_starved(monkeypatch, _app_state):
    """The exact live payload that exposed C-96, through `/health` itself.

    A helper-level test would have passed against the broken code too — the
    helper is new. What was broken is that the ENDPOINT never called it.
    """
    from aria_service.intel import loop_monitor

    starved = {"status": "starved", "samples": 600, "p50_ms": 0.7,
               "p95_ms": 3264.1, "max_ms": 9726.2, "interval_s": 1.0,
               "last_sample_age_s": 0.6}
    monkeypatch.setattr(loop_monitor, "snapshot", lambda: starved)

    out = await main_mod.health()

    assert out["loop"]["status"] == "starved", "precondition: gauge is starved"
    reasons = out.get("degraded_reasons") or []
    assert "event_loop_starved" in reasons, (
        f"/health published a starved loop and did not degrade; reasons={reasons}"
    )
    assert out["status"] == "degraded", (
        "R-F4024: `status: operational` beside `loop.status: starved` is the "
        "exact contradiction this closes."
    )


@pytest.mark.asyncio
async def test_real_health_endpoint_stays_operational_when_loop_is_healthy(monkeypatch, _app_state):
    """The guard must be able to NOT fire, or it is not a guard (R-F3858)."""
    from aria_service.intel import loop_monitor

    ok = {"status": "healthy", "samples": 600, "p50_ms": 0.3, "p95_ms": 1.1,
          "max_ms": 12.0, "interval_s": 1.0, "last_sample_age_s": 0.4}
    monkeypatch.setattr(loop_monitor, "snapshot", lambda: ok)

    out = await main_mod.health()
    assert "event_loop_starved" not in (out.get("degraded_reasons") or [])
    assert "event_loop_monitor_stale" not in (out.get("degraded_reasons") or [])
