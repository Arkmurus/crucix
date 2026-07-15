"""Capability tests for the 3 sources-page / engine root-kills.

R-F2020 — catch_up_overdue_tasks: refresh the runtime override FIRST and LOG
          every genuinely-overdue task it skips (was silent → a missed fire
          vanished without trace, the blind spot that hid the 187h fire=0 outage).
R-F2021 — brain_hook.get_stats: surface the real `_by_sector` aggregate that the
          blanket "_"-prefix module filter dropped (sources-page sector panel was
          empty despite real data).
R-F2022 — source_uptime_monitor: enumerate the REAL seeded defence-source
          catalogue (old code probed non-existent web_atlas fns → always [] →
          uptime panel permanently empty) AND surface a per-source array.

Each test drives the ACTUAL broken path (§3c), not a helper.
Run: python -m pytest aria_service/tests/test_rf2020_2022_root_kills.py -v
"""
from __future__ import annotations

import asyncio
import logging
import types

import pytest


# ─────────────────────────── R-F2022 — uptime monitor ───────────────────────

def test_rf2022_registered_sources_reads_real_catalogue():
    """The broken path returned [] (probed non-existent web_atlas fns). It must
    now return the real seeded defence sources, each with name + http url."""
    from aria_service.intel import source_uptime_monitor as sum_mod

    sources = asyncio.run(sum_mod._get_registered_sources())
    assert isinstance(sources, list)
    assert len(sources) > 10, "expected the real ~200-source catalogue, got %d" % len(sources)
    for s in sources[:20]:
        assert s.get("name"), "every source needs a name"
        assert str(s.get("url", "")).startswith(("http://", "https://")), \
            "every source needs an http(s) url for pinging"


def test_rf2022_health_exposes_sources_array(monkeypatch):
    """health() must surface a top-level `sources` array (the panel reads it).
    Before the fix it returned only aggregate counts → panel had no rows."""
    from aria_service.intel import source_uptime_monitor as sum_mod

    fake_last_run = {
        "ran_at": "2026-06-27T10:00:00+00:00",
        "sources_checked": 2, "up": 1, "down": 1,
        "sources": [
            {"name": "ofac_treasury", "url": "https://ofac.treasury.gov/", "status": "ok"},
            {"name": "deadsrc", "url": "https://dead.example/", "status": "error",
             "last_error": "HTTP 503"},
        ],
    }

    # Monkeypatch the real redis_store.get_json directly. (Swapping the whole
    # module via sys.modules is fragile — `from . import redis_store` resolves the
    # already-imported package attribute, bypassing the swap, when another test in
    # the suite imported redis_store first → flaky in-suite, green in isolation.)
    rs_mod = __import__("aria_service.intel.redis_store", fromlist=["x"])

    async def _fake_get_json(key):
        return fake_last_run if key == sum_mod._K_LAST_RUN else []

    monkeypatch.setattr(rs_mod, "get_json", _fake_get_json)

    h = asyncio.run(sum_mod.health())
    assert "sources" in h, "health() must expose a top-level sources array"
    assert isinstance(h["sources"], list) and len(h["sources"]) == 2
    assert {s["status"] for s in h["sources"]} == {"ok", "error"}


# ─────────────────────────── R-F2021 — _by_sector ───────────────────────────

def test_rf2021_get_stats_surfaces_by_sector(monkeypatch):
    """get_stats dropped `_by_sector` via the blanket "_"-key filter. It must now
    re-expose the real aggregate so the sources-page sector panel renders."""
    from aria_service.intel import brain_hook

    stats_blob = {
        "_global": {"total": 5, "started_at": 0},
        "_by_sector": {"defence": 3, "compliance": 2},  # the dropped aggregate
        "news_monitor": {"total": 5, "success": 5, "fail": 0, "skip": 0,
                         "last_signal_at": __import__("time").time()},
    }

    rs_mod = __import__("aria_service.intel.redis_store", fromlist=["x"])

    async def _fake_get_json(key):
        return stats_blob

    monkeypatch.setattr(rs_mod, "get_json", _fake_get_json)
    # bust the 5s stats cache so our blob is read fresh
    monkeypatch.setattr(brain_hook, "_stats_cache", None, raising=False)
    monkeypatch.setattr(brain_hook, "_stats_cache_at", 0.0, raising=False)

    result = asyncio.run(brain_hook.get_stats())
    assert "_by_sector" in result, "get_stats must surface the real _by_sector aggregate"
    assert result["_by_sector"] == {"defence": 3, "compliance": 2}


# ─────────────────────────── R-F2020 — catch-up ─────────────────────────────

def _overdue_task(cron="0 * * * *"):
    return types.SimpleNamespace(enabled=True, cron=cron)


def _wire_catch_up(monkeypatch, *, enabled=True, paused=False,
                   can_run=(True, "ok"), tasks=None):
    """Wire engine globals so catch_up_overdue_tasks runs deterministically."""
    from aria_service.autonomous import engine

    fired_ids = []

    async def _refresh():
        return None

    async def _is_paused():
        return paused

    # R-F2635: must accept the `slot` kwarg the engine now passes — without
    # **kwargs this raises TypeError, which catch_up's blanket except swallows
    # into safety_error:TypeError and fired=0.
    async def _can_task_run(a, b, **kwargs):
        return can_run

    async def _exec(task, llm, dry_run):
        fired_ids.append(getattr(task, "cron", "?"))

    async def _get_last_fire(tid):
        return None  # never ran since its slot → genuinely overdue

    async def _set_last_fire(tid, ts):
        return None

    monkeypatch.setattr(engine, "refresh_runtime_override", _refresh)
    monkeypatch.setattr(engine, "is_enabled", lambda: enabled)
    monkeypatch.setattr(engine, "is_dry_run", lambda: True)
    monkeypatch.setattr(engine.safety, "is_engine_paused", _is_paused)
    monkeypatch.setattr(engine.safety, "can_task_run", _can_task_run)
    monkeypatch.setattr(engine.tasks_mod, "get_loaded_tasks",
                        lambda: tasks if tasks is not None else {})
    monkeypatch.setattr(engine.tasks_mod, "execute_task", _exec)
    monkeypatch.setattr(engine, "_get_task_last_fire", _get_last_fire)
    monkeypatch.setattr(engine, "_set_task_last_fire", _set_last_fire)
    # recent slot → genuinely overdue
    monkeypatch.setattr(engine, "_most_recent_cron_match_epoch",
                        lambda cron, now, maxage: now - 120)
    # no operating-mode restriction
    from aria_service.intel import operating_modes as om

    async def _get_mode():
        return None
    monkeypatch.setattr(om, "get_mode", _get_mode)
    # capture the wire call instead of hitting the brain
    wired = {}
    monkeypatch.setattr(engine, "_wire_catchup",
                        lambda f, s, d: wired.update(fired=f, skipped=dict(s), deferred=d))
    return engine, fired_ids, wired


def test_rf2020_overdue_task_fires(monkeypatch):
    engine, fired_ids, wired = _wire_catch_up(
        monkeypatch, tasks={"HOURLY-NEWS-MONITOR": _overdue_task()})
    n = asyncio.run(engine.catch_up_overdue_tasks(llm=None))
    assert n == 1, "a genuinely-overdue, allowed task must be caught up"
    assert len(fired_ids) == 1
    assert wired.get("fired") == 1


def test_rf2020_skipped_overdue_is_logged_not_silent(monkeypatch, caplog):
    """The core fix: an overdue task blocked by a safety gate must be LOGGED
    with its reason (was a silent drop) and reflected in the wired outcome."""
    engine, fired_ids, wired = _wire_catch_up(
        monkeypatch, can_run=(False, "rate_limit"),
        tasks={"HOURLY-NEWS-MONITOR": _overdue_task()})
    with caplog.at_level(logging.WARNING, logger="aria.autonomous.engine"):
        n = asyncio.run(engine.catch_up_overdue_tasks(llm=None))
    assert n == 0 and len(fired_ids) == 0
    text = caplog.text
    assert "OVERDUE" in text and "rate_limit" in text, \
        "a skipped overdue task must be logged with its reason"
    assert "done:" in text, "catch-up must ALWAYS emit a summary line"
    assert wired.get("skipped", {}).get("safety_gate:rate_limit") == 1


def test_rf2020_master_disabled_wires_and_returns_zero(monkeypatch):
    """When the (freshly-refreshed) master switch is off, catch-up returns 0,
    logs why, and still wires an outcome — never silent."""
    engine, fired_ids, wired = _wire_catch_up(
        monkeypatch, enabled=False,
        tasks={"HOURLY-NEWS-MONITOR": _overdue_task()})
    n = asyncio.run(engine.catch_up_overdue_tasks(llm=None))
    assert n == 0 and len(fired_ids) == 0
    assert wired.get("skipped", {}).get("master_disabled") == 1
