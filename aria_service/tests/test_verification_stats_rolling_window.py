"""Tests for the 2026-04-26 verification_gate rolling-window fix.

Background: get_stats() was reading from a single Redis key with
`ex=86400` — that's a key-EVICTION TTL, not a rolling 24h window
counter. After the last verification, the counter snapped to 0
exactly 24h later, even when `recent` plainly showed an entry at
T-24.5h. Live verification 2026-04-26 returned 0/0/0/24h while
`recent` listed one CRITICAL_UNVERIFIED at 04:30 the previous day.

Fix: derive verified_24h / unverified_24h / blocking_24h / warn_24h
from the timestamped `recent` list (no TTL, capped at _MAX_RECENT).
The Redis stats key is kept for the skipped-counters approximation.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta


def _run(coro):
    return asyncio.run(coro)


def _setup_fake_redis(monkeypatch, recent_blob, stats_blob=None):
    from aria_service.intel import redis_store as rs
    from aria_service.learning import verification_gate as vg

    async def fake_get_json(key):
        if key == vg._REDIS_RECENT_KEY:
            return recent_blob
        if key == vg._REDIS_STATS_KEY:
            return stats_blob
        return None

    async def fake_set_json(key, value, ex=None):
        return None

    monkeypatch.setattr(rs, "get_json", fake_get_json)
    monkeypatch.setattr(rs, "set_json", fake_set_json)


def test_get_stats_counts_recent_within_24h(monkeypatch):
    """A verification 12 hours ago must count toward verified_24h /
    unverified_24h regardless of whether the legacy stats Redis key
    has TTL'd out."""
    now = datetime.now(timezone.utc)
    recent = [
        {
            "verdict": "CRITICAL_UNVERIFIED",
            "severity": "BLOCKING",
            "at": (now - timedelta(hours=12)).isoformat(),
            "hash": "abc1",
        },
        {
            "verdict": "CRITICAL_VERIFIED",
            "severity": "NONE",
            "at": (now - timedelta(hours=2)).isoformat(),
            "hash": "abc2",
        },
    ]
    # stats key is None — simulating a TTL'd-out legacy counter
    _setup_fake_redis(monkeypatch, recent, stats_blob=None)
    from aria_service.learning import verification_gate as vg

    out = _run(vg.get_stats())
    assert out["verified_24h"] == 1
    assert out["unverified_24h"] == 1
    assert out["blocking_disagreements_24h"] == 1
    assert out["warn_disagreements_24h"] == 0
    # rate = 1 / 2 = 0.5
    assert out["verification_rate"] == 0.5


def test_get_stats_excludes_entries_older_than_24h(monkeypatch):
    """An entry at T-24.5h must NOT count, even if it's the only one
    in `recent`. This was the live-observed bug."""
    now = datetime.now(timezone.utc)
    recent = [
        {
            "verdict": "CRITICAL_UNVERIFIED",
            "severity": "BLOCKING",
            "at": (now - timedelta(hours=24, minutes=30)).isoformat(),
            "hash": "old",
        },
    ]
    _setup_fake_redis(monkeypatch, recent, stats_blob=None)
    from aria_service.learning import verification_gate as vg

    out = _run(vg.get_stats())
    assert out["verified_24h"] == 0
    assert out["unverified_24h"] == 0
    # Rate is None when no completions in window — dashboard shows "—"
    assert out["verification_rate"] is None


def test_get_stats_returns_zero_when_no_recent(monkeypatch):
    """Cold start — no verifications ever — must return clean zeros
    without crashing, and rate must be None (not 0.0)."""
    _setup_fake_redis(monkeypatch, [], stats_blob=None)
    from aria_service.learning import verification_gate as vg

    out = _run(vg.get_stats())
    assert out["verified_24h"] == 0
    assert out["unverified_24h"] == 0
    assert out["blocking_disagreements_24h"] == 0
    assert out["warn_disagreements_24h"] == 0
    assert out["verification_rate"] is None
    assert out["recent"] == []


def test_get_stats_handles_malformed_timestamps(monkeypatch):
    """A bad/missing timestamp must skip the entry, not raise."""
    now = datetime.now(timezone.utc)
    recent = [
        {"verdict": "CRITICAL_VERIFIED", "severity": "NONE", "at": "not-a-date"},
        {"verdict": "CRITICAL_VERIFIED", "severity": "NONE"},  # missing 'at'
        {
            "verdict": "CRITICAL_VERIFIED",
            "severity": "NONE",
            "at": (now - timedelta(hours=3)).isoformat(),
        },
    ]
    _setup_fake_redis(monkeypatch, recent, stats_blob=None)
    from aria_service.learning import verification_gate as vg

    out = _run(vg.get_stats())
    # Only the well-formed recent entry counts
    assert out["verified_24h"] == 1


def test_get_stats_preserves_skipped_from_legacy_stats_key(monkeypatch):
    """skipped_24h is still computed from the legacy stats key — that
    field is approximate-by-design (TTL window). The rolling-window
    fix shouldn't break this path."""
    now = datetime.now(timezone.utc)
    recent = [
        {
            "verdict": "CRITICAL_VERIFIED",
            "severity": "NONE",
            "at": (now - timedelta(hours=1)).isoformat(),
        },
    ]
    stats = {
        "skipped_total": 7,
        "skipped_by_reason": {"no_secondary_provider": 5, "llm_unavailable": 2},
    }
    _setup_fake_redis(monkeypatch, recent, stats_blob=stats)
    from aria_service.learning import verification_gate as vg

    out = _run(vg.get_stats())
    assert out["verified_24h"] == 1
    assert out["skipped_24h"] == 7
    assert out["skipped_by_reason_24h"] == {
        "no_secondary_provider": 5,
        "llm_unavailable": 2,
    }
