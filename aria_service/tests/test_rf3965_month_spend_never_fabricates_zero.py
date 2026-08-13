"""R-F3965 / C-54 — a store read failure rendered month spend as $0.00 and let
the monthly cap through.

`_refresh_month_cache` reads the rollup and the index with the NON-STRICT
readers:

    cost_tracker.py:1462
        roll = await rs.get_json(f"{COST_MONTH_PREFIX}{month}") or {}
        total = float(roll.get("total_cost_usd") or 0.0)
        if total == 0.0:
            index_roll = await _breakdown_from_index(month)   # also non-strict
            total = float(index_roll.get("total_cost_usd") or 0.0)

`get_json`'s R-F1392 contract returns `None` on a store-layer failure — the same
value an absent key produces. So a dead connection became `{}` became `0.0`, and
that fabricated zero was written **unconditionally** into `_month_cache`,
poisoning it for the cache TTL.

The `except Exception` handler directly below already does the right thing
(preserve the last known total) and **could never run**, because the read layer
had converted the failure into a plausible number before it could raise. A guard
made unreachable by its own dependency — the same shape as the three Phase A
gates CLAUDE.md §1 records as "certified by an absence".

`assert_monthly_cap` then reads the same poisoned value (`spent =
await _refresh_month_cache()`), computes `spent + reserve` against the cap, and
**passes**. R-F2854 fixed exactly this on the WRITE path and its docstring names
the cap-safety consequence; the read path that feeds the cap decision was not
fixed.

Bounded, and that is the point: when the store is healthy the cap is correctly
enforced by the atomic reserve. This is a store-failure-only defect — which is
precisely when you most need a spend ceiling.

The fix reads STRICTLY so the failure reaches the handler that was written for
it, keeps the last known total instead of inventing a zero, and reports
readability so a caller can tell "measured zero" from "could not measure" —
the distinction §17 already records costing a session a fabricated P0.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import cost_tracker as CT
from aria_service.intel import ocr
from aria_service.intel import redis_store as rs
from aria_service.intel import wire

from ._source_probe import function_code


@pytest.fixture(autouse=True)
def _reset_cache():
    CT._month_cache.clear()
    CT._month_cache.update({"month": "", "total": 0.0, "loaded_at": 0.0})
    yield
    CT._month_cache.clear()
    CT._month_cache.update({"month": "", "total": 0.0, "loaded_at": 0.0})


def _prime(total: float):
    """Simulate a process that has successfully read a real month total."""
    CT._month_cache.update({
        "month": CT._current_month_key(),
        "total": total,
        "loaded_at": 0.0,           # expired, so the next call re-reads
        "loaded_ok": True,
    })


# ── the reader must not invent a zero ────────────────────────────────────────

def test_store_failure_keeps_the_last_known_total(monkeypatch):
    _prime(123.45)

    async def _boom(key):
        raise rs.StoreReadError("connection is dead")

    monkeypatch.setattr(rs, "get_json_strict", _boom)

    total = asyncio.run(CT._refresh_month_cache(force=True))
    assert total == 123.45, (
        f"a dead store overwrote $123.45 of recorded spend with {total}"
    )
    assert CT._month_cache.get("stale") is True


def test_a_genuine_zero_is_still_reported_as_zero(monkeypatch):
    """The guard must not turn every zero into a suspicion."""
    async def _empty(key):
        return None                      # key genuinely absent

    async def _no_index(month):
        return {}

    monkeypatch.setattr(rs, "get_json_strict", _empty)
    monkeypatch.setattr(CT, "_breakdown_from_index", _no_index)

    total = asyncio.run(CT._refresh_month_cache(force=True))
    assert total == 0.0
    assert CT._month_cache.get("stale") is False
    assert CT._month_cache.get("loaded_ok") is True


def test_a_real_total_loads_normally(monkeypatch):
    async def _roll(key):
        return {"total_cost_usd": 87.5}

    monkeypatch.setattr(rs, "get_json_strict", _roll)
    total = asyncio.run(CT._refresh_month_cache(force=True))
    assert total == 87.5
    assert CT._month_cache.get("loaded_ok") is True
    assert CT._month_cache.get("stale") is False


def test_absent_rollup_plus_unreadable_index_is_not_a_measured_zero(monkeypatch):
    """The strict rollup read must not be bypassed by a non-strict fallback."""
    async def _strict(key):
        if key.startswith(CT.COST_MONTH_PREFIX):
            return None
        raise rs.StoreReadError("index unreadable")

    monkeypatch.setattr(rs, "get_json_strict", _strict)

    total = asyncio.run(CT._refresh_month_cache(force=True))
    assert total == 0.0
    assert CT._month_cache.get("loaded_ok") is not True
    assert CT._month_cache.get("stale") is True


def test_month_rollover_does_not_relabel_last_month_as_current(monkeypatch):
    CT._month_cache.update({
        "month": "1999-12",
        "total": 321.0,
        "loaded_at": 0.0,
        "loaded_ok": True,
    })

    async def _boom(key):
        raise rs.StoreReadError("dead at rollover")

    monkeypatch.setattr(rs, "get_json_strict", _boom)

    total = asyncio.run(CT._refresh_month_cache(force=True))
    assert total == 0.0
    assert CT._month_cache.get("loaded_ok") is False
    assert CT._month_cache.get("stale") is True


# ── the gauge must say when it could not measure ─────────────────────────────

def test_get_month_spend_reports_unreadable_rather_than_zero(monkeypatch):
    async def _boom(key):
        raise rs.StoreReadError("dead")

    async def _noflush(force=False):
        return None

    monkeypatch.setattr(rs, "get_json_strict", _boom)
    monkeypatch.setattr(CT, "_flush_cost_pending", _noflush)

    out = asyncio.run(CT.get_month_spend())
    assert out.get("spent_readable") is False, (
        "the gauge presented an unmeasurable spend as a measurement"
    )
    # A cold process with a dead store has NO last-known value; it must not
    # render 0.0 as if it were one.
    assert out.get("spent_usd") is None


def test_get_month_spend_reports_readable_on_a_healthy_store(monkeypatch):
    async def _roll(key):
        return {"total_cost_usd": 42.0}

    async def _noflush(force=False):
        return None

    monkeypatch.setattr(rs, "get_json_strict", _roll)
    monkeypatch.setattr(CT, "_flush_cost_pending", _noflush)

    out = asyncio.run(CT.get_month_spend())
    assert out.get("spent_readable") is True
    assert out.get("spent_usd") == 42.0
    assert out.get("remaining_usd") is not None


# ── the cap must not pass on a phantom zero ──────────────────────────────────

def test_cap_does_not_pass_on_an_unreadable_spend(monkeypatch):
    """A cold process + dead store cannot enforce a ceiling. Say so."""
    async def _boom(key):
        raise rs.StoreReadError("dead")

    async def _incr(key, amt):
        return amt

    async def _expire(key, ttl):
        return True

    monkeypatch.setattr(rs, "get_json_strict", _boom)
    monkeypatch.setattr(rs, "incrbyfloat", _incr)
    monkeypatch.setattr(rs, "expire", _expire)
    monkeypatch.setattr(CT, "_monthly_cap_usd", lambda: 600.0)
    monkeypatch.setattr(CT, "_warn_only", lambda: False)

    with pytest.raises(CT.MonthlyCostCapUnverifiable):
        asyncio.run(CT.assert_monthly_cap(0.02))


def test_unverifiable_cap_remains_compatible_control_flow():
    """Callers and wiring that know the established parent keep working."""
    error = CT.MonthlyCostCapUnverifiable("2099-01", 600.0)
    assert isinstance(error, CT.MonthlyCostCapExceeded)
    assert isinstance(error, ocr._CapExceeded)
    assert wire._is_control_flow(error, ("MonthlyCostCapExceeded",)) is True


def test_cap_uses_the_last_known_total_when_the_store_blips(monkeypatch):
    """A transient blip must NOT block spend — that would be an outage.

    With a last-known total the ceiling is still enforceable, so the call
    proceeds. Only a process that has never read a total fails closed.
    """
    _prime(10.0)

    async def _boom(key):
        raise rs.StoreReadError("blip")

    async def _incr(key, amt):
        return amt

    async def _expire(key, ttl):
        return True

    monkeypatch.setattr(rs, "get_json_strict", _boom)
    monkeypatch.setattr(rs, "incrbyfloat", _incr)
    monkeypatch.setattr(rs, "expire", _expire)
    monkeypatch.setattr(CT, "_monthly_cap_usd", lambda: 600.0)
    monkeypatch.setattr(CT, "_warn_only", lambda: False)

    asyncio.run(CT.assert_monthly_cap(0.02))       # must not raise


def test_cap_still_blocks_when_genuinely_over(monkeypatch):
    _prime(700.0)

    async def _roll(key):
        return {"total_cost_usd": 700.0}

    async def _incr(key, amt):
        return amt

    async def _expire(key, ttl):
        return True

    monkeypatch.setattr(rs, "get_json_strict", _roll)
    monkeypatch.setattr(rs, "incrbyfloat", _incr)
    monkeypatch.setattr(rs, "expire", _expire)
    monkeypatch.setattr(CT, "_monthly_cap_usd", lambda: 600.0)
    monkeypatch.setattr(CT, "_warn_only", lambda: False)

    with pytest.raises(CT.MonthlyCostCapExceeded):
        asyncio.run(CT.assert_monthly_cap(0.02))


# ── the non-strict read must not come back ───────────────────────────────────

def test_the_month_read_is_strict():
    src = function_code(CT, "_refresh_month_cache")
    assert "get_json_strict" in src, (
        "the monthly rollup is read non-strictly again — a store failure will "
        "render as $0.00 and the cap will pass on it"
    )
