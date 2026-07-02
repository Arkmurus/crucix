"""R-F2284 — grounded_rate_unknown must not flag health "degraded" when the
metric is null simply because NO recent answer made a citable claim.

Live 2026-07-02: /health/perf sat permanently `degraded` with
degraded_reasons=["grounded_rate_unknown"] because 363/500 recent answers were
no_claims/no_citations → source_verifier.get_verification_stats() returned
avg_grounded_rate=None with data_source="no_data". That is a benign "nothing to
ground" state, NOT a grounding failure. Only a genuine stats error (data_source
absent) should still flag it.

Capability test drives the REAL health_check_ep() and asserts degraded_reasons.
"""
from __future__ import annotations

import pytest

from aria_service.routes import aria as routes
from aria_service.intel import source_verifier
from aria_service.intel import endpoint_cache


def _bust_health_cache():
    """health_check_ep is @cached_endpoint(name='health') — reset the entry so
    each test gets a fresh cold compute instead of a cached verdict."""
    entry = endpoint_cache._REGISTRY.get("health")
    if entry is not None:
        entry.value = endpoint_cache._MISS
        entry.ts = 0.0


@pytest.mark.asyncio
async def test_rf2284_no_data_grounding_is_not_degraded(monkeypatch):
    async def _stats():
        return {"avg_grounded_rate": None, "data_source": "no_data",
                "by_verdict": {"no_claims": 363}}
    monkeypatch.setattr(source_verifier, "get_verification_stats", _stats)
    _bust_health_cache()

    out = await routes.health_check_ep()
    reasons = out.get("degraded_reasons") or []
    assert "grounded_rate_unknown" not in reasons, (
        f"no-citable-claims must NOT flag health degraded; reasons={reasons}"
    )


@pytest.mark.asyncio
async def test_rf2284_stats_error_still_flags_unknown(monkeypatch):
    # data_source absent (stats error path) → grounding genuinely unmeasurable → still flagged.
    async def _stats():
        return {"error": "boom"}
    monkeypatch.setattr(source_verifier, "get_verification_stats", _stats)
    _bust_health_cache()

    out = await routes.health_check_ep()
    reasons = out.get("degraded_reasons") or []
    assert "grounded_rate_unknown" in reasons, (
        f"a genuine stats error must still flag grounded_rate_unknown; reasons={reasons}"
    )


@pytest.mark.asyncio
async def test_rf2284_real_low_rate_is_reported_not_masked(monkeypatch):
    # A real measured grounding rate (data_source present, not None) → not flagged
    # as unknown (it's known); the fix must not hide a genuine value.
    async def _stats():
        return {"avg_grounded_rate": 0.42, "data_source": "lifetime_fallback"}
    monkeypatch.setattr(source_verifier, "get_verification_stats", _stats)
    _bust_health_cache()

    out = await routes.health_check_ep()
    reasons = out.get("degraded_reasons") or []
    assert "grounded_rate_unknown" not in reasons, reasons
