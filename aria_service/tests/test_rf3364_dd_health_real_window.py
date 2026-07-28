"""R-F3364 — /dd/health promised "the last 7 days" and served all-time counters.

HOW THIS WAS FOUND, and why it matters more than the number that led to it.

Live 2026-07-28 the digital layer read 49 error / 45 ok — a 52% failure rate,
by far the worst layer, and it looked like an open P1. It was not. R-F3059 +
R-F3066 had already diagnosed and fixed that exact failure on 2026-07-25: the
digital layer's per-op bounds summed to ~175s while the no-website path only
granted it `DEFAULT_LAYER_TIMEOUT_S` (90s), so the layer "could NOT fit its own
ops and was guaranteed to be cancelled". Three days old, already repaired.

So why did the surface still show 52%? Because the counter is not a window.
`_finalize_dd_run` does, per layer per run:

    hincrby(f'crucix:dd:layer_stats:{layer}', status, 1)
    expire(f'crucix:dd:layer_stats:{layer}', 86400 * 7)

The TTL is REFRESHED on every write. While DDs keep running, the key is
continually renewed and never expires, so the "7-day" hash accumulates FOREVER.
The endpoint's own docstring says "over the last 7 days"; it was serving
all-time totals.

TWO CONSEQUENCES, and the second is the dangerous one:
  1. a fixed defect looks permanently broken — the pre-fix failures never age
     out, so no repair can ever show up on the surface that is supposed to prove
     repairs; and
  2. a NEW regression is invisible, diluted into a growing historical
     denominator. The larger the history, the blinder the alarm.

Same family as the error ledger reporting `window_hours: 168` while physically
retaining 6.4h, and as `operational-gaps` serving two-month-old signals as
"real-time" (R-F3362): a surface asserting a recency it never measured.

THE FIX. Per-day buckets (`…:{layer}:{YYYY-MM-DD}`) with a TTL that is a real
expiry, and a reader that sums the last N buckets. Old flat keys stop being
written, so they expire on their own 7 days later — the migration cleans itself.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import dd_orchestrator as ddo


def _run(coro):
    return asyncio.run(coro)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── the key must carry the day ──────────────────────────────────────────────

def test_layer_stat_key_is_day_bucketed():
    k = ddo._layer_stats_key("digital")
    assert k.endswith(_today()), k
    assert "crucix:dd:layer_stats:digital:" in k


def test_two_days_do_not_share_a_bucket():
    a = ddo._layer_stats_key("digital", day=datetime(2026, 7, 25, tzinfo=timezone.utc))
    b = ddo._layer_stats_key("digital", day=datetime(2026, 7, 28, tzinfo=timezone.utc))
    assert a != b, "a fix on the 25th can never show up if both days share a key"


def test_recorder_writes_into_todays_bucket():
    from aria_service.intel.dd_schema import ARKDDReport
    written: list[str] = []

    async def _hincrby(key, field_, amount):
        written.append(key)

    r = ARKDDReport()
    r.layers_run = ["digital"]
    with patch("aria_service.intel.redis_store.hincrby", new=AsyncMock(side_effect=_hincrby)), \
         patch("aria_service.intel.redis_store.expire", new=AsyncMock()):
        _run(ddo._finalize_dd_run(r))
    assert written, "nothing recorded"
    assert all(k.endswith(_today()) for k in written), written


def test_ttl_outlives_the_reported_window():
    """The bucket must survive long enough to be READ for the whole window, but
    still actually expire — that is the difference from the old refreshed TTL."""
    assert ddo.DD_LAYER_STATS_TTL_S > ddo.DD_LAYER_STATS_WINDOW_DAYS * 86400


# ── the reader must sum a REAL window ───────────────────────────────────────

def test_reader_sums_only_the_window():
    """Days inside the window count; a day outside it does not."""
    now = datetime.now(timezone.utc)
    inside = {ddo._layer_stats_key("digital", day=now - timedelta(days=d)): {"ok": "1"}
              for d in range(ddo.DD_LAYER_STATS_WINDOW_DAYS)}
    outside = {ddo._layer_stats_key("digital", day=now - timedelta(days=90)): {"error": "99"}}
    store = {**inside, **outside}

    async def _hgetall(key):
        return store.get(key, {})

    with patch("aria_service.intel.redis_store.hgetall", new=AsyncMock(side_effect=_hgetall)):
        got = _run(ddo.get_layer_stats_window("digital"))
    assert got.get("ok") == ddo.DD_LAYER_STATS_WINDOW_DAYS, got
    assert got.get("error", 0) == 0, (
        f"a 90-day-old failure leaked into the '7 day' window: {got}"
    )


def test_reader_merges_counts_across_days():
    now = datetime.now(timezone.utc)
    store = {
        ddo._layer_stats_key("digital", day=now): {"ok": "3", "error": "1"},
        ddo._layer_stats_key("digital", day=now - timedelta(days=1)): {"ok": "2"},
    }

    async def _hgetall(key):
        return store.get(key, {})

    with patch("aria_service.intel.redis_store.hgetall", new=AsyncMock(side_effect=_hgetall)):
        got = _run(ddo.get_layer_stats_window("digital"))
    assert got == {"ok": 5, "error": 1}, got


def test_reader_is_empty_for_a_layer_with_no_history():
    async def _hgetall(key):
        return {}

    with patch("aria_service.intel.redis_store.hgetall", new=AsyncMock(side_effect=_hgetall)):
        got = _run(ddo.get_layer_stats_window("counter_intelligence"))
    assert got == {}


def test_reader_tolerates_junk_values():
    now = datetime.now(timezone.utc)
    store = {ddo._layer_stats_key("digital", day=now): {"ok": "not-a-number", "error": "2"}}

    async def _hgetall(key):
        return store.get(key, {})

    with patch("aria_service.intel.redis_store.hgetall", new=AsyncMock(side_effect=_hgetall)):
        got = _run(ddo.get_layer_stats_window("digital"))
    assert got.get("error") == 2, got


# ── the endpoint must use the windowed reader, not the flat key ────────────

def test_endpoint_reads_the_window_not_the_flat_key():
    from pathlib import Path
    src = (Path(ddo.__file__).parent.parent / "routes" / "aria.py").read_text(encoding="utf-8")
    assert "get_layer_stats_window" in src, "the route still reads the all-time flat key"
    assert 'f"crucix:dd:layer_stats:{layer_name}"' not in src


def test_window_length_is_declared_and_matches_the_claim():
    assert ddo.DD_LAYER_STATS_WINDOW_DAYS == 7
