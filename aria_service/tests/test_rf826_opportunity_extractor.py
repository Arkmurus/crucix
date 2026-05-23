"""R-F826 — OpportunityExtractor regression suite.

Covers:
  - GapType.OPPORTUNITY is in the type enum + autonomy routing
  - OpportunityExtractor returns a Gap when a topic recurs ≥3x in low-grounded chats
  - OpportunityExtractor returns NO gap when topic count is below the threshold
  - OpportunityExtractor returns NO gap when grounded_rate is above the threshold
  - OpportunityExtractor ignores entries outside the lookback window
  - self_coder.gap_type_to_change_type maps OPPORTUNITY → "enhancement"
    so it NEVER auto-deploys regardless of ARIA_SELF_IMPROVE_AUTO_DEPLOY (R-F462).

Tests use asyncio.run(...) per the project convention (no pytest-asyncio).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit_entry(*, topic: str, grounded: float, ts: datetime) -> str:
    return json.dumps({
        "timestamp": _iso(ts),
        "mastery_weak_topics": [topic],
        "grounded_rate": grounded,
        "session_id": "sess-1",
        "verification_status": "unverified",
    })


class _FakeRedis:
    """Minimal fake — only the methods OpportunityExtractor calls."""

    def __init__(self, entries: list[str]) -> None:
        self._entries = entries

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        assert key == "crucix:chat_audit:log"
        return self._entries[start : stop + 1]


def test_opportunity_gaptype_registered():
    from aria_service.autonomous import gap_detector as gd

    assert gd.GapType.OPPORTUNITY == "opportunity"
    auto, wa, hard = gd.AUTONOMY_LEVEL[gd.GapType.OPPORTUNITY]
    assert auto is False, "OPPORTUNITY must NOT be auto-fixable"
    assert wa is True, "OPPORTUNITY must require operator approval"
    assert hard is False


def test_opportunity_change_type_is_enhancement():
    """R-F462 gate: OPPORTUNITY must route to 'enhancement', which is
    the change-type that is ALWAYS staged and NEVER auto-deploys."""
    from aria_service.autonomous import gap_detector as gd
    from aria_service.autonomous.self_coder import (
        GAP_TYPE_TO_CHANGE_TYPE, gap_type_to_change_type,
    )

    assert GAP_TYPE_TO_CHANGE_TYPE[gd.GapType.OPPORTUNITY] == "enhancement"
    assert gap_type_to_change_type(gd.GapType.OPPORTUNITY) == "enhancement"


def test_opportunity_extractor_emits_gap_when_topic_recurs():
    """3 chats on the same topic with grounded_rate < 0.6 → 1 OPPORTUNITY gap."""
    from aria_service.autonomous.gap_detector import (
        GapSeverity, GapType, OpportunityExtractor,
    )

    now = datetime.now(timezone.utc)
    entries = [
        _audit_entry(topic="export_control", grounded=0.30, ts=now - timedelta(minutes=10)),
        _audit_entry(topic="export_control", grounded=0.45, ts=now - timedelta(minutes=20)),
        _audit_entry(topic="export_control", grounded=0.20, ts=now - timedelta(minutes=30)),
    ]
    redis = _FakeRedis(entries)
    extractor = OpportunityExtractor(redis)

    since = now - timedelta(hours=2)
    gaps = asyncio.run(extractor.extract(since))

    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.gap_type == GapType.OPPORTUNITY
    assert gap.severity == GapSeverity.MEDIUM
    assert "export_control" in gap.title.lower()
    assert gap.evidence["occurrences"] == 3
    assert gap.evidence["topic"] == "export_control"
    assert gap.requires_wa_approval is True
    assert gap.auto_fixable is False


def test_opportunity_extractor_skips_below_threshold_count():
    """2 chats on the same low-grounded topic is below MIN_OCCURRENCES (3)."""
    from aria_service.autonomous.gap_detector import OpportunityExtractor

    now = datetime.now(timezone.utc)
    entries = [
        _audit_entry(topic="cuba_sanctions", grounded=0.30, ts=now - timedelta(minutes=10)),
        _audit_entry(topic="cuba_sanctions", grounded=0.45, ts=now - timedelta(minutes=20)),
    ]
    redis = _FakeRedis(entries)
    extractor = OpportunityExtractor(redis)

    gaps = asyncio.run(extractor.extract(now - timedelta(hours=2)))
    assert gaps == []


def test_opportunity_extractor_skips_when_grounded_above_threshold():
    """Topic recurs 3x but grounded_rate is high — no opportunity."""
    from aria_service.autonomous.gap_detector import OpportunityExtractor

    now = datetime.now(timezone.utc)
    entries = [
        _audit_entry(topic="nato_basics", grounded=0.95, ts=now - timedelta(minutes=10)),
        _audit_entry(topic="nato_basics", grounded=0.85, ts=now - timedelta(minutes=20)),
        _audit_entry(topic="nato_basics", grounded=0.75, ts=now - timedelta(minutes=30)),
    ]
    redis = _FakeRedis(entries)
    extractor = OpportunityExtractor(redis)

    gaps = asyncio.run(extractor.extract(now - timedelta(hours=2)))
    assert gaps == []


def test_opportunity_extractor_ignores_entries_outside_window():
    """Entries older than `since` must be skipped."""
    from aria_service.autonomous.gap_detector import OpportunityExtractor

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=30)
    entries = [
        _audit_entry(topic="old_topic", grounded=0.20, ts=old - timedelta(minutes=10)),
        _audit_entry(topic="old_topic", grounded=0.30, ts=old - timedelta(minutes=20)),
        _audit_entry(topic="old_topic", grounded=0.10, ts=old - timedelta(minutes=30)),
    ]
    redis = _FakeRedis(entries)
    extractor = OpportunityExtractor(redis)

    gaps = asyncio.run(extractor.extract(now - timedelta(hours=2)))
    assert gaps == []


def test_opportunity_extractor_normalises_topic_case():
    """'Export_Control' + 'export_control' + 'EXPORT_CONTROL' = one topic."""
    from aria_service.autonomous.gap_detector import OpportunityExtractor

    now = datetime.now(timezone.utc)
    entries = [
        _audit_entry(topic="Export_Control", grounded=0.30, ts=now - timedelta(minutes=10)),
        _audit_entry(topic="export_control", grounded=0.45, ts=now - timedelta(minutes=20)),
        _audit_entry(topic="EXPORT_CONTROL", grounded=0.20, ts=now - timedelta(minutes=30)),
    ]
    redis = _FakeRedis(entries)
    extractor = OpportunityExtractor(redis)

    gaps = asyncio.run(extractor.extract(now - timedelta(hours=2)))
    assert len(gaps) == 1
    assert gaps[0].evidence["occurrences"] == 3
    assert gaps[0].evidence["topic"] == "export_control"


def test_opportunity_extractor_wired_into_gap_detector():
    """GapDetector.__init__ must include OpportunityExtractor in its list."""
    from aria_service.autonomous.gap_detector import (
        GapDetector, OpportunityExtractor,
    )

    redis = _FakeRedis([])
    detector = GapDetector(redis)

    types = {type(e).__name__ for e in detector.extractors}
    assert "OpportunityExtractor" in types, (
        "OpportunityExtractor must be wired into GapDetector.extractors "
        "or the proactive-scan capability is dormant"
    )
