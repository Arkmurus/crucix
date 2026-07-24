"""R-F2977 — a slow DD sub-op must degrade to a data_gap, not error the whole layer.

Live DD 2026-07-24 (Silverbrook, dd_0b6c78446376): the COMPLIANCE layer showed
"Layer error: timeout after 90s" and DIGITAL "timeout after 180s". Each layer runs
several heavy external sub-ops SEQUENTIALLY with no per-op bound, so one slow/hung
call blew the layer budget → the whole layer was CANCELLED mid-op → status ERROR
(health: digital 29 err / 1 ok over 7d). R-F2977 wraps each heavy op in a per-op
timeout so a slow op records a data_gap and the LAYER COMPLETES (status OK).

These pin the contract of _bounded_dd_op AND prove a slow financial-health sub-op
does NOT error the real _run_compliance layer.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel.dd_schema import ARKDDReport


class _Layer:
    def __init__(self):
        self.data_gaps: list[str] = []


def test_bounded_op_slow_degrades_to_gap():
    async def _slow():
        await asyncio.sleep(2.0)
        return "should-not-return"

    layer = _Layer()

    async def _go():
        t0 = time.time()
        out = await ddo._bounded_dd_op(_slow(), 0.2, layer, "slow op", default="DEF")
        return out, time.time() - t0

    out, elapsed = asyncio.run(_go())
    assert out == "DEF", "a timed-out op must return the default, not hang"
    assert elapsed < 1.0, "the per-op timeout must fire fast, not wait for the slow op"
    assert any("slow op" in g for g in layer.data_gaps), "a timeout must record a data_gap"


def test_bounded_op_fast_passes_through():
    async def _fast():
        return {"ok": True}

    layer = _Layer()
    out = asyncio.run(ddo._bounded_dd_op(_fast(), 5.0, layer, "fast op", default=None))
    assert out == {"ok": True}
    assert layer.data_gaps == [], "a fast op must not record a data_gap"


def test_bounded_op_non_timeout_exception_propagates():
    async def _boom():
        raise ValueError("real error")

    layer = _Layer()
    with pytest.raises(ValueError):
        asyncio.run(ddo._bounded_dd_op(_boom(), 5.0, layer, "boom op"))
    assert layer.data_gaps == [], "a non-timeout error must NOT be swallowed as a gap"


def test_slow_financial_health_does_not_error_compliance_layer():
    """The user-visible contract: a slow financial-health sub-op degrades to a
    data_gap and _run_compliance COMPLETES, instead of the layer erroring."""
    report = ARKDDReport()
    report.identity.jurisdiction_iso2 = "GB"
    report.identity.entity_name = "Silverbrook Capital Management"
    target = {"name": "Silverbrook Capital Management", "type": "company",
              "jurisdiction_iso2": "GB"}

    async def _slow_assess(*a, **k):
        await asyncio.sleep(3.0)
        return {"data_available": True}

    async def _go():
        with patch("aria_service.intel.financial_health.assess",
                   AsyncMock(side_effect=_slow_assess)), \
             patch("aria_service.intel.sources.usaspending.lookup",
                   AsyncMock(return_value=None)), \
             patch("aria_service.intel.sources.worldbank_indicators.country_risk_overlay",
                   AsyncMock(return_value={})), \
             patch.object(ddo, "_OP_T_FINANCIAL", 0.3):
            t0 = time.time()
            # _run_compliance must return normally (not hang, not raise) well under
            # the 90s layer budget — the slow op is bounded at 0.3s.
            await asyncio.wait_for(ddo._run_compliance(target, report), timeout=30.0)
            return time.time() - t0

    elapsed = asyncio.run(_go())
    assert elapsed < 25.0, "compliance layer must complete (the slow op is bounded)"
    assert any("financial health" in g for g in report.compliance.data_gaps), (
        "the bounded slow op must leave an honest data_gap")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
