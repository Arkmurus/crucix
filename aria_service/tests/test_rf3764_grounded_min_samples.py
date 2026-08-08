"""R-F3764 — CAPABILITY: one sample must not take customer delivery offline.

`evaluate_auto_transition` degraded the whole platform whenever
`avg_grounded_rate < 0.30`, with NO check on how many observations produced that
average. DEGRADED suppresses ALL external delivery (`should_deliver_external`
returns `mode == NORMAL`), so a single eval answer scoring 0 took customer-facing
output offline.

Not hypothetical — live history:
    NORMAL -> DEGRADED  "grounded rate 0% < 30%"  2026-08-05T18:00:52Z
    NORMAL -> DEGRADED  "grounded rate 0% < 30%"  2026-08-07T00:00:48Z
with get_verification_stats reporting lifetime_sample_size=0 hours later. The
signal is thin and intermittent, and every dip took delivery with it.

The stats layer had already solved the hard half: R-F3696 added
`effective_sample_size` — the count MATCHING `effective_rate`, since the rate can
fall back from the 24h window to the lifetime average — explicitly "so a consumer
applying a minimum-sample guard" would not judge a value from window A by a count
from window B. The most consequential consumer never applied one.

Run: python -m pytest aria_service/tests/test_rf3764_grounded_min_samples.py -v
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import operating_modes as om

# R-F3785/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def _stats(rate, n):
    return {"avg_grounded_rate": rate, "effective_sample_size": n,
            "rate_sample_size": n}


@pytest.fixture(autouse=True)
def _normal(monkeypatch):
    """Start from NORMAL with the other two signals healthy."""
    async def _get_mode():
        return om.Mode.NORMAL
    monkeypatch.setattr(om, "get_mode", _get_mode)

    calls = {}

    async def _set_mode(mode, reason="manual"):
        calls["mode"], calls["reason"] = mode, reason
        return {"mode": mode.name, "changed": True}
    monkeypatch.setattr(om, "set_mode", _set_mode)
    return calls


def _run(monkeypatch, rate, n):
    from aria_service.intel import source_verifier, redis_store

    async def _vs():
        return _stats(rate, n)

    async def _get(key):
        return None                      # no predictor blocks

    monkeypatch.setattr(source_verifier, "get_verification_stats", _vs)
    monkeypatch.setattr(redis_store, "get", _get)
    return asyncio.run(om.evaluate_auto_transition())


def test_one_bad_sample_does_not_degrade_the_platform(_normal, monkeypatch):
    """THE HEADLINE: a single 0% observation must not suppress all delivery."""
    _run(monkeypatch, 0.0, 1)
    assert "mode" not in _normal, (
        f"the platform degraded on ONE sample (set_mode called with "
        f"{_normal.get('reason')!r}). DEGRADED suppresses every external "
        f"delivery — that bar cannot be one observation."
    )


@pytest.mark.parametrize("n", [0, 1, 2, 4])
def test_any_count_below_the_floor_is_treated_as_no_signal(_normal, monkeypatch, n):
    _run(monkeypatch, 0.0, n)
    assert "mode" not in _normal, f"degraded on {n} sample(s), below the floor"


def test_a_genuine_collapse_with_enough_samples_STILL_degrades(_normal, monkeypatch):
    """The control must survive the fix — this is not a way to disable it."""
    _run(monkeypatch, 0.0, om.GROUNDED_MIN_SAMPLES)
    assert _normal.get("mode") is om.Mode.DEGRADED, (
        "a real collapse across enough samples no longer degrades — the guard "
        "has been widened into a disabled control"
    )
    assert "grounded rate" in (_normal.get("reason") or "")


def test_a_healthy_rate_with_many_samples_does_not_degrade(_normal, monkeypatch):
    _run(monkeypatch, 0.95, 50)
    assert "mode" not in _normal


def test_a_missing_rate_is_still_treated_as_healthy(_normal, monkeypatch):
    """Pre-existing behaviour that must not regress: None = unknown = healthy."""
    _run(monkeypatch, None, 0)
    assert "mode" not in _normal


def test_the_floor_is_not_env_tunable():
    """A safety floor that can be set to 0 restores the defect exactly."""
    import inspect
    src = module_source(om)
    i = src.find("GROUNDED_MIN_SAMPLES =")
    assert i > 0
    line = src[i:src.find("\n", i)]
    assert "getenv" not in line and "environ" not in line, (
        f"GROUNDED_MIN_SAMPLES became env-tunable ({line!r}); setting it to 0 "
        f"reintroduces one-sample platform degradation"
    )
    assert om.GROUNDED_MIN_SAMPLES >= 5
