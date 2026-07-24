"""R-F2959 (B1) — symmetric feed-liveness watchdog.

check_engine_liveness watches the ENGINE; check_feed_liveness watches the LEARNING
feeds (research + student loops) that the engine watchdog was blind to — the
silent-throttle footgun. Tests the classification without real Redis/registry.
"""
from __future__ import annotations

import asyncio
from unittest import mock


class _FakeReg:
    def __init__(self, ages: dict):
        self._ages = ages  # agent_id -> heartbeat_age_s (or None to mean 'not registered')

    async def get_agent_status(self, agent_id):
        if agent_id not in self._ages:
            return None
        age = self._ages[agent_id]
        return {"heartbeat_age_s": age}


def _run(env, ages, *, paused=False, shedding=False):
    from aria_service.autonomous import engine as eng

    async def fake_paused():
        return paused

    with mock.patch.dict("os.environ", env, clear=False), \
         mock.patch("aria_service.autonomous.safety.is_engine_paused", side_effect=fake_paused), \
         mock.patch("aria_service.intel.load_governor.pressure", return_value={"shedding": shedding}), \
         mock.patch("aria_service.intel.agent_registry.AgentRegistry", return_value=_FakeReg(ages)):
        return asyncio.run(eng.check_feed_liveness())


def test_rf2959_research_disabled_is_flagged():
    """ARIA_AUTONOMOUS_RESEARCH_ENABLED=0 must produce a problem — the exact
    silent-disable the engine watchdog missed."""
    problems = _run(
        {"ARIA_AUTONOMOUS_RESEARCH_ENABLED": "0"},
        {"student_reading": 10.0, "regional_snapshot": 10.0},
    )
    assert any("research feed DISABLED" in p for p in problems), problems


def test_rf2959_all_fresh_no_problems():
    """Research on + fresh heartbeats → healthy (empty list)."""
    problems = _run(
        {"ARIA_AUTONOMOUS_RESEARCH_ENABLED": "1", "ARIA_READING_INTERVAL_S": "9000"},
        {"student_reading": 100.0, "regional_snapshot": 100.0, "research_engine": 100.0},
    )
    assert problems == [], problems


def test_rf2959_stale_reading_loop_flagged():
    """A student_reading heartbeat older than 2x its interval → STALE problem."""
    problems = _run(
        {"ARIA_AUTONOMOUS_RESEARCH_ENABLED": "1", "ARIA_READING_INTERVAL_S": "9000"},
        {"student_reading": 9000 * 2 + 500.0, "regional_snapshot": 10.0, "research_engine": 10.0},
    )
    assert any("student_reading" in p and "STALE" in p for p in problems), problems


def test_rf2959_paused_suppresses_stale_alarm():
    """When the engine is paused, a stale feed is legitimately idle — no stale
    alarm (but research-disabled, a config fault, still surfaces if set)."""
    problems = _run(
        {"ARIA_AUTONOMOUS_RESEARCH_ENABLED": "1"},
        {"student_reading": 999999.0, "regional_snapshot": 999999.0, "research_engine": 999999.0},
        paused=True,
    )
    assert problems == [], problems


def test_rf2959_shedding_suppresses_stale_alarm():
    """A load-shed cycle explains an idle feed — treat as fresh, don't alarm."""
    problems = _run(
        {"ARIA_AUTONOMOUS_RESEARCH_ENABLED": "1"},
        {"student_reading": 999999.0, "regional_snapshot": 999999.0, "research_engine": 999999.0},
        shedding=True,
    )
    assert problems == [], problems


def test_rf2959_unregistered_agent_not_flagged():
    """An agent not yet registered (early boot) must be skipped, not flagged."""
    problems = _run(
        {"ARIA_AUTONOMOUS_RESEARCH_ENABLED": "1"},
        {},  # nothing registered yet
    )
    assert problems == [], problems
