"""R-F2904 — detected gaps reach the OPERATOR review queue, not a dead key.

`GapDetector.publish_latest` writes `crucix:aria:gaps:latest` with a 30-minute
TTL, documented as being "for the ARIACoder to consume". The coder lane was
paused on 2026-07-23 (ARIA_CODER_ENABLED=0) after five weeks produced 1 gold fix
in 52 attempts — so detection kept running and every finding expired unread.

These tests drive `publish_latest` (the REAL path the 15-minute scan loop calls),
not the helper in isolation, so a regression that stops routing fails here.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from aria_service.autonomous import gap_detector as gd
from aria_service.autonomous.gap_detector import Gap, GapDetector, GapSeverity, GapType


def _run(coro):
    return asyncio.run(coro)


class _FakeRedis:
    """Mirrors the surface GapDetector uses: get / setex."""

    def __init__(self, fail_get: bool = False):
        self.store: dict[str, str] = {}
        self.fail_get = fail_get

    async def get(self, key):
        if self.fail_get:
            raise RuntimeError("store unreachable")
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value
        return True


def _gap(gap_id: str, severity: GapSeverity, gap_type: str = GapType.MODULE_BUG) -> Gap:
    return Gap(
        gap_id=gap_id,
        gap_type=gap_type,
        severity=severity,
        title=f"title-{gap_id}",
        description=f"description of {gap_id}",
        module="aria_service.intel.example",
    )


@pytest.fixture
def captured(monkeypatch):
    """Capture pending_actions.record — the operator-visible queue."""
    calls: list[dict] = []

    async def _fake_record(**kwargs):
        calls.append(kwargs)
        return {}

    from aria_service.intel import pending_actions as _pa
    monkeypatch.setattr(_pa, "record", _fake_record)
    monkeypatch.delenv("ARIA_GAP_REVIEW_QUEUE_ENABLED", raising=False)
    return calls


class TestRoutingThroughTheRealPath:
    def test_publish_latest_routes_high_severity_to_the_queue(self, captured):
        """THE capability: the 15-min loop calls publish_latest, and a HIGH gap
        now reaches a human instead of expiring in LATEST_KEY."""
        d = GapDetector(_FakeRedis())
        _run(d.publish_latest([_gap("g1", GapSeverity.HIGH)]))

        assert captured, "HIGH gap never reached the operator queue"
        assert captured[0]["severity"] == "HIGH"
        assert captured[0]["source"] == "gap_detector"
        assert captured[0]["metadata"]["gap_id"] == "g1"

    def test_latest_key_is_still_written(self, captured):
        """Additive change: the coder's key must keep working for when the
        lane is re-enabled."""
        r = _FakeRedis()
        d = GapDetector(r)
        _run(d.publish_latest([_gap("g1", GapSeverity.HIGH)]))
        assert d.LATEST_KEY in r.store

    def test_critical_maps_to_critical(self, captured):
        d = GapDetector(_FakeRedis())
        _run(d.publish_latest([_gap("g1", GapSeverity.CRITICAL)]))
        assert captured[0]["severity"] == "CRITICAL"


class TestNoiseControl:
    """A review queue that pages on everything gets ignored — same end state as
    not having one. These are the properties that keep it credible."""

    def test_low_and_medium_are_not_queued(self, captured):
        d = GapDetector(_FakeRedis())
        _run(d.publish_latest([
            _gap("low", GapSeverity.LOW),
            _gap("med", GapSeverity.MEDIUM),
        ]))
        assert captured == []

    def test_same_gap_is_raised_once_across_scans(self, captured):
        """A persistent gap is seen every 15 min — 96x/day. It must page ONCE."""
        r = _FakeRedis()
        d = GapDetector(r)
        g = _gap("persistent", GapSeverity.HIGH)
        for _ in range(5):
            _run(d.publish_latest([g]))
        assert len(captured) == 1, f"raised {len(captured)} times, expected 1"

    def test_burst_is_capped_per_scan(self, captured):
        d = GapDetector(_FakeRedis())
        _run(d.publish_latest(
            [_gap(f"g{i}", GapSeverity.HIGH) for i in range(20)]
        ))
        assert len(captured) == GapDetector.REVIEW_MAX_PER_SCAN

    def test_highest_severity_wins_the_cap(self, captured):
        """When capped, CRITICAL must not be crowded out by HIGH."""
        d = GapDetector(_FakeRedis())
        gaps = [_gap(f"h{i}", GapSeverity.HIGH) for i in range(10)]
        gaps.append(_gap("crit", GapSeverity.CRITICAL))
        _run(d.publish_latest(gaps))
        ids = [c["metadata"]["gap_id"] for c in captured]
        assert "crit" in ids, f"CRITICAL was crowded out: {ids}"

    def test_dedupe_read_failure_fails_CLOSED(self, captured):
        """If the marker cannot be read we cannot know whether this was already
        raised. Publishing anyway would spam every 15 minutes forever, so the
        gap is skipped instead."""
        d = GapDetector(_FakeRedis(fail_get=True))
        _run(d.publish_latest([_gap("g1", GapSeverity.HIGH)]))
        assert captured == [], "spammed the queue when dedupe was unreadable"

    def test_marker_is_only_set_after_a_successful_publish(self, monkeypatch):
        """If the queue write fails, the gap must remain eligible next scan —
        marking first would silently swallow it."""
        async def _boom(**kwargs):
            raise RuntimeError("queue down")

        from aria_service.intel import pending_actions as _pa
        monkeypatch.setattr(_pa, "record", _boom)
        monkeypatch.delenv("ARIA_GAP_REVIEW_QUEUE_ENABLED", raising=False)

        r = _FakeRedis()
        d = GapDetector(r)
        _run(d.publish_latest([_gap("g1", GapSeverity.HIGH)]))
        assert not any(k.startswith(d.REVIEW_MARKER_PREFIX) for k in r.store), \
            "gap was marked as reviewed despite the publish failing"


class TestKillSwitch:
    def test_disabled_publishes_nothing(self, captured, monkeypatch):
        monkeypatch.setenv("ARIA_GAP_REVIEW_QUEUE_ENABLED", "0")
        d = GapDetector(_FakeRedis())
        _run(d.publish_latest([_gap("g1", GapSeverity.CRITICAL)]))
        assert captured == []

    def test_default_is_on(self, monkeypatch):
        monkeypatch.delenv("ARIA_GAP_REVIEW_QUEUE_ENABLED", raising=False)
        assert gd._review_queue_enabled() is True


class TestNeverBreaksTheScanLoop:
    def test_a_broken_queue_does_not_raise(self, monkeypatch):
        """publish_latest runs inside the 15-min loop; raising would kill it."""
        async def _boom(**kwargs):
            raise RuntimeError("queue down")

        from aria_service.intel import pending_actions as _pa
        monkeypatch.setattr(_pa, "record", _boom)
        d = GapDetector(_FakeRedis())
        _run(d.publish_latest([_gap("g1", GapSeverity.HIGH)]))  # must not raise
