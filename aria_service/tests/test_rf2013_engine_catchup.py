"""R-F2013 — engine startup catch-up for MISSED cron slots.

A once-an-hour task (cron "0 * * * *", e.g. news_monitor) only fires at :00. If a
restart spans its slot, the slot is missed; repeated restarts on the hour left
news_monitor 189h stale. On startup the engine now fires any task whose recent
scheduled slot was missed — bounded + through the same gates as a normal tick.

These drive the REAL engine functions.
"""
import asyncio
import time

from aria_service.autonomous import engine, safety
import aria_service.autonomous.tasks as tasks_mod
import aria_service.intel.operating_modes as om


class FakeTask:
    def __init__(self, tid, cron, enabled=True):
        self.id = tid
        self.cron = cron
        self.enabled = enabled


def _run(coro):
    return asyncio.run(coro)


def _setup(monkeypatch, tasks, *, last_fire=None, enabled=True, paused=False,
           can_run=True, mode_ok=True):
    last_fire = dict(last_fire or {})
    monkeypatch.setattr(engine, "is_enabled", lambda: enabled)
    monkeypatch.setattr(engine, "is_dry_run", lambda: True)
    async def _paused(): return paused
    monkeypatch.setattr(safety, "is_engine_paused", _paused)
    async def _can(tid, ent): return (can_run, "" if can_run else "blocked")
    monkeypatch.setattr(safety, "can_task_run", _can)
    monkeypatch.setattr(tasks_mod, "get_loaded_tasks", lambda: tasks)
    fired = []
    async def _exec(*, task, llm, dry_run): fired.append(task.id)
    monkeypatch.setattr(tasks_mod, "execute_task", _exec)
    async def _get_lf(tid): return last_fire.get(tid)
    monkeypatch.setattr(engine, "_get_task_last_fire", _get_lf)
    async def _set_lf(tid, ep): last_fire[tid] = ep
    monkeypatch.setattr(engine, "_set_task_last_fire", _set_lf)
    async def _mode(): return "NORMAL"
    monkeypatch.setattr(om, "get_mode", _mode)
    monkeypatch.setattr(om, "should_task_run", lambda tid, m: mode_ok)
    return fired


# ── _most_recent_cron_match_epoch ─────────────────────────────────────────────
def test_most_recent_match_hourly_returns_last_top_of_hour():
    now = 1_000_000  # 1970-01-12 13:46:40 UTC
    m = engine._most_recent_cron_match_epoch("0 * * * *", now, 7200)
    assert m is not None
    assert (now - m) <= 3600
    assert time.gmtime(m).tm_min == 0


def test_most_recent_match_none_when_no_slot_in_window():
    # Jan-1-00:00-only cron won't match in a 2-minute lookback
    assert engine._most_recent_cron_match_epoch("0 0 1 1 *", time.time(), 120) is None


# ── catch_up_overdue_tasks ────────────────────────────────────────────────────
def test_fires_missed_hourly_task(monkeypatch):
    fired = _setup(monkeypatch, {"NEWS": FakeTask("NEWS", "0 * * * *")})
    assert _run(engine.catch_up_overdue_tasks(None)) == 1
    assert fired == ["NEWS"]


def test_skips_task_that_already_ran_since_its_slot(monkeypatch):
    fired = _setup(monkeypatch, {"NEWS": FakeTask("NEWS", "0 * * * *")},
                   last_fire={"NEWS": time.time()})  # ran just now, after the :00
    assert _run(engine.catch_up_overdue_tasks(None)) == 0
    assert fired == []


def test_skips_when_engine_disabled(monkeypatch):
    _setup(monkeypatch, {"NEWS": FakeTask("NEWS", "0 * * * *")}, enabled=False)
    assert _run(engine.catch_up_overdue_tasks(None)) == 0


def test_skips_when_paused(monkeypatch):
    _setup(monkeypatch, {"NEWS": FakeTask("NEWS", "0 * * * *")}, paused=True)
    assert _run(engine.catch_up_overdue_tasks(None)) == 0


def test_respects_safety_gate(monkeypatch):
    fired = _setup(monkeypatch, {"NEWS": FakeTask("NEWS", "0 * * * *")}, can_run=False)
    assert _run(engine.catch_up_overdue_tasks(None)) == 0
    assert fired == []


def test_respects_operating_mode_gate(monkeypatch):
    fired = _setup(monkeypatch, {"NEWS": FakeTask("NEWS", "0 * * * *")}, mode_ok=False)
    assert _run(engine.catch_up_overdue_tasks(None)) == 0


def test_burst_cap(monkeypatch):
    many = {f"T{i}": FakeTask(f"T{i}", "*/2 * * * *") for i in range(40)}
    _setup(monkeypatch, many)
    n = _run(engine.catch_up_overdue_tasks(None))
    assert n == engine._CATCH_UP_MAX_FIRES  # capped, doesn't fire all 40
