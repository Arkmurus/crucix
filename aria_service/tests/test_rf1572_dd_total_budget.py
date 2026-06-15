"""R-F1572 — overall wall-clock budget on the DD orchestrator.

ROOT CAUSE (operator-visible, 2026-06-15 WhatsApp):
    Antonio: "Aria, do a full DD on https://www.deltaguard.org/en/"
    ... 15 min of "still researching" interim messages ...
    Aria: "⚠️ I hit a timeout on that one ... Please try again"

The DD orchestrator had PER-LAYER asyncio.wait_for caps but NO overall
budget. A sparse target (an NGO/security-firm website with little registry
footprint) gate-triggers INSUFFICIENT_EVIDENCE, which R-F409 (routes/aria.py)
escalates by re-running the ENTIRE DD in deep mode. identity(90)+network(90)+
compliance(90)+digital(180)+extensions(45)+… ×2 blew past the WhatsApp
async-push poll window (15 min, aria_wa_listener.mjs MAX_MS=900000). The poll
gave up → the user got a dead-end "try again" → resent → spawned a DUPLICATE
15-min job (the recurring WA pain CLAUDE.md §25 says to master at the root).

R-F1572 adds `total_budget_s` (env ARIA_DD_TOTAL_BUDGET_S, default 660s) and
clamps the heavy layers' timeouts to the remaining budget via _clamp(). When
the budget is exhausted the heavy layers fail fast and the walk cascades to
verification + synthesis, which build a real (partial) report from whatever
the completed layers collected. routes/aria.py passes the REMAINING budget to
the R-F409 deep re-run so the total still fits the push window.

These are CAPABILITY tests: they drive the real `orchestrate_dd` coroutine
(the broken path) and assert the user-visible guarantee — a DD that would run
unbounded now RETURNS, bounded, with a time-boxed flag — rather than only
asserting source strings.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from aria_service.intel import dd_orchestrator as ddo


def _patch_layers(monkeypatch, *, heavy_sleep_s: float):
    """Replace the layer entry points with controlled fakes.

    Heavy layers (network/compliance/digital) sleep `heavy_sleep_s` each so
    that, unbounded, the run would take ~3×heavy_sleep_s. The clamp must cut
    them once the budget is gone. Everything else is a fast no-op so the test
    is deterministic and never touches the network.
    """
    async def _identity(target, report):
        return False  # no hard-stop → full walk continues

    async def _heavy(target, report, *a, **k):
        await asyncio.sleep(heavy_sleep_s)

    async def _noop(*a, **k):
        return None

    async def _noop_dict(*a, **k):
        return {}

    async def _ext(*a, **k):
        return {"ran": False}

    monkeypatch.setattr(ddo, "_run_identity", _identity)
    monkeypatch.setattr(ddo, "_run_network", _heavy)
    monkeypatch.setattr(ddo, "_run_compliance", _heavy)
    monkeypatch.setattr(ddo, "_run_digital", _heavy)
    monkeypatch.setattr(ddo, "_run_sweep_intelligence", _noop)
    monkeypatch.setattr(ddo, "_run_verification", _noop)
    monkeypatch.setattr(ddo, "_run_synthesis", _noop)
    # _assemble_bluf + predictor + deception analyser pull in ML models /
    # heavy deps that are NOT part of the budget mechanism under test — stub
    # them so the test isolates orchestrate_dd's _clamp/time_boxed logic.
    monkeypatch.setattr(ddo, "_assemble_bluf", _noop)
    try:
        from aria_service.intel import predictor as _pred
        monkeypatch.setattr(_pred, "forecast", _noop_dict)
    except Exception:
        pass
    try:
        from aria_service.intel import deception_detection as _ddx

        class _StubAnalyser:
            def __init__(self, *a, **k):
                pass

        monkeypatch.setattr(_ddx, "ARIADeceptionAnalyser", _StubAnalyser)
    except Exception:
        pass
    # Skip the small data-driven layers that would otherwise call real modules.
    monkeypatch.setenv("ARIA_LAYER_5C_ENABLED", "0")
    try:
        from aria_service.intel import counter_intelligence as _ci
        monkeypatch.setattr(_ci, "scan_entity", _noop_dict)
    except Exception:
        pass
    try:
        from aria_service.intel import sanctions_divergence as _sd
        monkeypatch.setattr(_sd, "analyze_divergence", _noop_dict)
    except Exception:
        pass
    try:
        from aria_service.intel import dd_layer_extensions as _dlx
        monkeypatch.setattr(_dlx, "run_all_extensions", _ext)
    except Exception:
        pass
    # The post-BLUF adverse-media deep search hits the network — stub it so the
    # ample-budget test (where it is NOT budget-skipped) stays offline.
    try:
        from aria_service.intel import researcher as _res
        monkeypatch.setattr(_res, "run_adverse_media_deep_search", _noop_dict)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_rf1572_tiny_budget_bounds_an_otherwise_unbounded_run(monkeypatch):
    """The smoking gun: layers that would take ~60s total must be cut by a
    1s budget. The run RETURNS (no dead-end timeout) in a fraction of the
    unbounded time, and is flagged time_boxed."""
    _patch_layers(monkeypatch, heavy_sleep_s=20.0)  # 3×20 = 60s unbounded

    target = {"name": "Delta Guard Ltd", "type": "company"}
    t0 = time.time()
    report = await ddo.orchestrate_dd(target=target, llm=None, total_budget_s=1.0)
    elapsed = time.time() - t0

    assert report is not None, "DD returned nothing — the failure class is back"
    # Unbounded this is ~60s; bounded it must be far less. Generous ceiling for
    # preamble (predictor/cost) + the one un-clamped identity layer fake.
    assert elapsed < 15.0, (
        f"DD ran {elapsed:.1f}s with a 1s budget — heavy layers were NOT "
        f"clamped (root cause unfixed; unbounded ≈ 60s)."
    )
    assert getattr(report, "time_boxed", False) is True, (
        "Budget was exhausted but report.time_boxed not set — the footer "
        "would imply full coverage on a partial run."
    )


@pytest.mark.asyncio
async def test_rf1572_ample_budget_is_not_time_boxed(monkeypatch):
    """Guard against a false-positive: with fast layers and a large budget,
    the run completes normally and is NOT flagged time_boxed."""
    _patch_layers(monkeypatch, heavy_sleep_s=0.0)  # instant layers

    target = {"name": "Delta Guard Ltd", "type": "company"}
    report = await ddo.orchestrate_dd(target=target, llm=None, total_budget_s=600.0)

    assert report is not None
    assert getattr(report, "time_boxed", False) is False, (
        "A fast run inside budget was wrongly flagged time_boxed."
    )


def test_rf1572_orchestrate_dd_accepts_total_budget_s():
    """The signature must expose total_budget_s so routes/aria.py can pass
    the remaining budget to the R-F409 deep re-run."""
    import inspect

    sig = inspect.signature(ddo.orchestrate_dd)
    assert "total_budget_s" in sig.parameters, (
        "orchestrate_dd lost its total_budget_s parameter — R-F409 can no "
        "longer bound the chained deep run."
    )


def test_rf1572_rf409_escalation_is_budget_gated():
    """routes/aria.py must (a) pass total_budget_s into BOTH orchestrate_dd
    calls and (b) gate the deep escalation on remaining budget, so the
    initial+deep total can't exceed the WhatsApp push window."""
    import pathlib

    src = pathlib.Path(
        "C:/code/crucix/aria_service/routes/aria.py"
    ).read_text(encoding="utf-8", errors="ignore")
    idx = src.find("R-F409")
    assert idx > 0
    block = src[idx - 1500:idx + 3500]
    assert "total_budget_s=" in block, (
        "R-F1572: routes/aria.py doesn't pass total_budget_s to "
        "orchestrate_dd — the chained deep run is unbounded again."
    )
    assert "_dd_remaining_s" in block and ">= _rf409_min_deep_s" in block, (
        "R-F1572: the R-F409 escalation is no longer gated on remaining "
        "budget — a sparse target will double-run past the push window."
    )
