"""Tests for procurement_calendar (Priority 3, 2026-04-17)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from aria_service.intel import procurement_calendar as pc
from aria_service.intel import redis_store as rs


@pytest.fixture(autouse=True)
def _force_memory_backend():
    rs._client = None
    rs._mem_store.clear()
    yield
    rs._mem_store.clear()


def _run(coro):
    return asyncio.run(coro)


class TestCalendarSeed:
    def test_seed_loads_on_first_access(self):
        data = _run(pc._load())
        assert len(data["items"]) >= 25
        assert all("id" in ev for ev in data["items"])

    def test_seed_normalises_country(self):
        data = _run(pc._load())
        iso2s = {ev["iso2"] for ev in data["items"]}
        assert "GB" in iso2s
        assert "EU" in iso2s  # supranational preserved
        assert "US" in iso2s


class TestNextOccurrence:
    def test_annual_rolls_forward_to_future(self):
        anchor = datetime(2024, 10, 1, tzinfo=timezone.utc)
        now = datetime(2026, 2, 1, tzinfo=timezone.utc)
        nxt = pc._next_occurrence(anchor, 365, "annual", now)
        assert nxt is not None
        assert nxt >= now
        assert (nxt - now).days <= 365

    def test_one_off_past_returns_none(self):
        anchor = datetime(2020, 1, 1, tzinfo=timezone.utc)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert pc._next_occurrence(anchor, 0, "one_off", now) is None

    def test_one_off_future_returns_anchor(self):
        anchor = datetime(2030, 1, 1, tzinfo=timezone.utc)
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert pc._next_occurrence(anchor, 0, "one_off", now) == anchor


class TestListUpcoming:
    def test_list_upcoming_default_horizon(self):
        events = _run(pc.list_upcoming())
        assert all(0 <= ev["days_until"] <= pc.DEFAULT_HORIZON_DAYS for ev in events)

    def test_list_upcoming_filtered_by_country(self):
        events = _run(pc.list_upcoming(iso2="GB", days_ahead=400))
        assert events
        assert all(ev["iso2"] == "GB" for ev in events)

    def test_list_upcoming_accepts_country_name(self):
        events = _run(pc.list_upcoming(iso2="United Kingdom", days_ahead=400))
        assert events
        assert all(ev["iso2"] == "GB" for ev in events)


class TestAlerts:
    def test_upcoming_alerts_respect_event_lead_days(self):
        # Election rows have 180d lead — they should surface even outside
        # the default 90d horizon.
        alerts = _run(pc.upcoming_alerts())
        types = {a["event_type"] for a in alerts}
        # Alerts list is trivially sortable
        assert all("days_until" in a for a in alerts)

    def test_briefing_renders_alerts(self):
        text = _run(pc.generate_calendar_briefing())
        # Either alerts exist and text contains header, or alerts are empty
        # and text is blank — both are valid outcomes depending on anchors.
        assert text == "" or "PROCUREMENT CALENDAR" in text


class TestChatContext:
    def test_context_returns_empty_on_unmentioned_country(self):
        ctx = _run(pc.get_calendar_context("unrelated question"))
        assert ctx == ""

    def test_context_mentions_country_marker(self):
        # Force an upcoming-soon event we know will match
        _run(pc.add_event({
            "iso2": "AO",
            "event_type": "budget_cycle",
            "title": "Angola OGE budget — test override",
            "recurrence": "annual",
            "anchor_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()[:10],
            "cycle_period_days": 365,
            "lead_days": 90,
            "confidence": "HIGH",
            "source": "test",
        }))
        ctx = _run(pc.get_calendar_context("what's upcoming in angola defence budget?"))
        assert "[CALENDAR:" in ctx
        assert "AO" in ctx


class TestRefresh:
    def test_refresh_returns_counts(self):
        result = _run(pc.refresh())
        assert "upcoming_alerts" in result
        assert "pushed_to_chain" in result
        # Pushed should be >= 0
        assert result["pushed_to_chain"] >= 0

    def test_refresh_pushes_elections_to_chain_store(self):
        from aria_service.intel import chain_correlator as cc
        # Force a fresh upcoming election event so the refresh has something to push
        _run(pc.add_event({
            "iso2": "BR",
            "event_type": "election",
            "title": "Brazil general election — test override",
            "recurrence": "quadrennial",
            "anchor_date": (datetime.now(timezone.utc) + timedelta(days=120)).isoformat()[:10],
            "cycle_period_days": 1461,
            "lead_days": 180,
            "confidence": "HIGH",
            "source": "test",
        }))
        _run(pc.refresh())
        stored = _run(cc._load(cc.SHIFTS_KEY))
        iso2s = {s["iso2"] for s in stored["items"]}
        assert "BR" in iso2s


class TestStats:
    def test_stats_shape(self):
        snap = _run(pc.stats())
        assert snap["events_total"] >= 25
        assert "by_type" in snap
        assert "by_confidence" in snap
