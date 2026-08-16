"""R-F4064 (C-118) — the external-services panel showed an error COUNT and no
rate, so a 42% failure rate on the paid DD engine read as a small number.

Measured live 2026-08-16:

    /api/aria/cost/external
        brave: {calls: 168, cost_usd: 0.84, errors: 71}
    /api/aria/search/health.brave_usage.monthly
        {total: 234, ok: 135, empty: 99}

**71 of 168 is 42%.** Nearly half of every call to Brave — the paid, DD-exclusive
search engine, the one RULE ONE reserves for customer-facing due diligence — came
back unusable, and the page printed "71" in an Errors column beside "168" in a
Calls column, where it reads as small.

The second meter agrees on the ratio (99 empty of 234 = 42.3%) and differs on the
absolute only because it started counting earlier: `brave_usage` shipped in
R-F3868 and `cost_tracker.record_brave_call` was wired hours later the same day
in R-F3884, both on 2026-08-11. `_record_spend` is called from the same function
that increments the usage counter, so from that point they move together. **That
gap is a start offset, not a counting defect, and is not "fixed" here** — the
audit's first reading suggested cost might be understated by a third, and dating
the two commits disproved it.

`error_rate` is None, not 0.0, when a service has no calls: a service nobody
called has no failure rate, and rendering 0% would read as perfect health — the
same absence-as-measurement shape as the rest of this batch.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


async def _summary(agg: dict) -> dict:
    from aria_service.intel import cost_tracker as ct
    with patch.object(ct, "_flush_external_pending", AsyncMock()), \
         patch.object(ct.rs, "get_json", AsyncMock(return_value=agg)):
        return await ct.get_external_summary()


@pytest.mark.asyncio
async def test_the_live_shape_reports_a_42_percent_failure_rate():
    out = await _summary({
        "brave": {"calls": 168, "cost_usd": 0.84, "errors": 71},
    })
    rate = out["by_service"]["brave"]["error_rate"]
    assert rate == pytest.approx(0.4226, abs=0.001), out
    # the counts are untouched — the rate is additive, not a replacement
    assert out["by_service"]["brave"]["calls"] == 168
    assert out["total_calls"] == 168


@pytest.mark.asyncio
async def test_a_service_with_no_calls_has_no_rate():
    """0% would read as perfect health for a service nobody called."""
    out = await _summary({"opencorporates": {"calls": 0, "cost_usd": 0.0,
                                             "errors": 0}})
    assert out["by_service"]["opencorporates"]["error_rate"] is None, out


@pytest.mark.asyncio
async def test_a_clean_service_reports_zero_not_none():
    """The rate must be able to say "measured, and fine" — otherwise None
    means two different things."""
    out = await _summary({"brave": {"calls": 50, "cost_usd": 0.25, "errors": 0}})
    assert out["by_service"]["brave"]["error_rate"] == 0.0


@pytest.mark.asyncio
async def test_a_malformed_entry_does_not_sink_the_summary():
    out = await _summary({"brave": {"calls": 10, "errors": 2}, "junk": "nope"})
    assert out["by_service"]["brave"]["error_rate"] == pytest.approx(0.2)
    assert out["by_service"]["junk"] == "nope"


@pytest.mark.asyncio
async def test_empty_aggregate_is_still_empty():
    out = await _summary({})
    assert out["by_service"] == {}
    assert out["total_calls"] == 0
