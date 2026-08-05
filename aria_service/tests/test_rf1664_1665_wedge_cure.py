"""R-F1664 + R-F1665 — wedge-cure capability tests.

Root cause (live probe 2026-06-18): absorb p95 22-44s tripped the brain_hook
circuit, wedging the event loop. Two drivers:
  - Cure 1 (R-F1664): engine_wiring.wire_success used the HEAVY absorb path for
    pure per-tick/per-module telemetry (258 heavy absorb vs 21 metric calls).
  - Cure 2 (R-F1665): absorb's neural tier (slow, GIL-bound encode) ran INSIDE
    the 8-slot absorb semaphore AND counted toward the breaker latency — so it
    both occupied slots (queue) and inflated p95.

These tests drive the REAL paths: the wire_success dispatch and absorb_tiers_bg.
"""
import inspect

import pytest

# R-F3754/§16 — NOT inspect.getsource: it slices at the line numbers captured
# AT IMPORT, so an edit mid-run returns a DIFFERENT function's body, silently.
from ._source_probe import function_source


# ── Cure 1: wire_success must use the lightweight metric path ────────────────

def test_wire_success_uses_lightweight_record_signal_not_heavy_absorb():
    from aria_service.intel import engine_wiring
    src = function_source(engine_wiring, "wire_success")
    # Strip the docstring so explanatory prose ("heavy absorb_silent") doesn't
    # false-trip — check the actual call code only.
    body = src.split('"""')[-1] if '"""' in src else src
    assert "_bh.record_signal(" in body, (
        "R-F1664: wire_success must route to brain_hook.record_signal (lightweight)."
    )
    assert "_bh.absorb" not in body, (
        "R-F1664: wire_success must NOT call the heavy absorb path — it is "
        "per-tick telemetry, not new knowledge."
    )


# ── Cure 2: neural tier on its own lane + measured AFTER the breaker latency ──

def test_neural_concurrency_sem_is_separate_lane():
    from aria_service.intel import brain_hook
    assert hasattr(brain_hook, "_get_neural_concurrency_sem"), (
        "R-F1665: a separate neural-concurrency semaphore accessor must exist."
    )
    assert brain_hook._NEURAL_CONCURRENCY >= 1
    # It must be a DIFFERENT semaphore object than the main absorb sem.
    main = brain_hook._get_absorb_concurrency_sem()
    neural = brain_hook._get_neural_concurrency_sem()
    if main is not None and neural is not None:
        assert main is not neural, (
            "R-F1665: neural tier must use its OWN semaphore, not the main one."
        )


def test_neural_tier_runs_after_breaker_latency_in_source():
    """The neural encode must execute AFTER _record_latency/_maybe_trip_breaker,
    so the breaker measures durable-core latency, not the slow neural encode."""
    from aria_service.intel import brain_hook_bg
    src = function_source(brain_hook_bg, "absorb_tiers_bg")
    lat_pos = src.find("_record_latency(")
    breaker_pos = src.find("_maybe_trip_breaker(")
    neural_pos = src.rfind("learn_from_text(")
    assert lat_pos != -1 and breaker_pos != -1 and neural_pos != -1
    assert neural_pos > lat_pos and neural_pos > breaker_pos, (
        "R-F1665: the neural tier (learn_from_text) must run AFTER the breaker "
        "latency is recorded, so neural no longer inflates absorb p95."
    )


@pytest.mark.asyncio
async def test_record_signal_actually_awaits_and_records():
    """R-F1664: the lightweight record_signal (which Cure 1 routes wire_success
    through) must actually AWAIT _record_signal — previously it was called
    without await, so the signal was silently dropped (and the module went dark)."""
    from aria_service.intel import brain_hook

    if not brain_hook.BRAIN_HOOK_ENABLED:
        pytest.skip("brain hook disabled in this env")

    calls = []

    async def fake_record_signal(module, success=True, sector="", skipped=False):
        calls.append((module, success))

    import unittest.mock as mock
    with mock.patch.object(brain_hook, "_record_signal", fake_record_signal):
        out = await brain_hook.record_signal(module="unit_test_metric", success=True)
    assert out.get("recorded") is True
    assert calls == [("unit_test_metric", True)], (
        f"record_signal must await _record_signal exactly once; got {calls}"
    )


@pytest.mark.asyncio
async def test_absorb_tiers_bg_records_latency_before_neural_runs():
    """Behavioural: drive the REAL absorb_tiers_bg and assert the ordering —
    durable core + latency record happen BEFORE the neural encode, and neural
    still runs on its own lane."""
    from aria_service.intel import brain_hook, brain_hook_bg

    order: list[str] = []

    async def fake_run_tier(coro, label):
        # consume/await the passed coroutine so it doesn't warn, then record
        try:
            coro.close()
        except Exception:
            pass
        order.append(f"tier:{label}")
        return True, None

    def fake_record_latency(ms):
        order.append("record_latency")

    def fake_trip(reason="", module=""):
        order.append("trip_breaker")

    async def fake_record_signal(module, success=True, sector="", skipped=False):
        order.append("record_signal")

    await brain_hook_bg.absorb_tiers_bg(
        module="unit_test",
        summary="a durable fact summary long enough to persist",
        text_for_neural="some neural text that is well over the fifty character minimum threshold",
        source="unit_test",
        topics=["test"],
        success=True,
        weight=1.0,
        confidence="ASSESSED",
        entity_name="",
        gap_type=None,
        gap_detail=None,
        sector="",
        user_id="",
        result={"errors": []},
        _get_absorb_concurrency_sem=brain_hook._get_absorb_concurrency_sem,
        _get_neural_concurrency_sem=brain_hook._get_neural_concurrency_sem,
        _ABSORB_CONCURRENCY=brain_hook._ABSORB_CONCURRENCY,
        _ABSORB_SEM_ACQUIRE_TIMEOUT_S=brain_hook._ABSORB_SEM_ACQUIRE_TIMEOUT_S,
        _run_tier=fake_run_tier,
        _record_signal=fake_record_signal,
        _record_latency=fake_record_latency,
        _maybe_trip_breaker=fake_trip,
        _start_ms=0.0,
    )

    assert "record_latency" in order, "latency must be recorded"
    assert "tier:neural" in order, "neural tier must still run (best-effort)"
    # The KEY invariant of cure 2: latency recorded BEFORE neural runs.
    assert order.index("record_latency") < order.index("tier:neural"), (
        f"R-F1665: latency must be recorded before neural. Order: {order}"
    )
    # And the durable core ran before latency.
    assert order.index("tier:mastery") < order.index("record_latency")
    assert order.index("tier:knowledge") < order.index("record_latency")
