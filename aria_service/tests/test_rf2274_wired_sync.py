"""R-F2274 — @wired handles sync functions (fixes the DD 'coroutine has no attribute' bug).

get_country_risk + financial_findings are sync `def`s decorated @wired. The old async-only
wrapper turned them into un-awaitable coroutines, so the DD's sync call sites got
"'coroutine' object has no attribute 'as_dict'" and EVERY DD lost all country-risk substance.
"""
from __future__ import annotations
import asyncio
from aria_service.intel import risk_indices, financial_dd
from aria_service.intel.engine_wiring import wired


def test_get_country_risk_returns_profile_not_coroutine():
    # the EXACT broken path: dd_orchestrator calls this WITHOUT await, then .as_dict()
    r = risk_indices.get_country_risk("BR", name="Brazil")
    assert not asyncio.iscoroutine(r), "must NOT be a coroutine"
    d = r.as_dict()  # raised 'coroutine object has no attribute as_dict' before the fix
    assert isinstance(d, dict) and "headline_risk" in d


def test_financial_findings_sync_works():
    out = financial_dd.financial_findings({"revenue": 1000000})
    assert not asyncio.iscoroutine(out)
    assert isinstance(out, list)


def test_wired_still_wraps_async_functions():
    @wired(module="t", summary="s")
    async def _af(x):
        return x + 1
    assert asyncio.iscoroutinefunction(_af)
    assert asyncio.run(_af(1)) == 2


def test_wired_wraps_sync_function_passthrough():
    @wired(module="t", summary="s")
    def _sf(x):
        return x * 2
    assert not asyncio.iscoroutinefunction(_sf)
    assert _sf(3) == 6
