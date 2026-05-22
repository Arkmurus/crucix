"""R-F806 (2026-05-22): student.lift_all_topics respects per-topic
HARD_FLOORS on negative bumps.

Pre-R-F806 used a hardcoded 0.10 floor — completely independent of
HARD_FLOORS. calibration_review applies -3pp via
lift_all_topics(-drop), so even after R-F796 clamped negative
update_mastery calls at the topic's hard floor, calibration's bulk
adjustment bypassed the protection. Live evidence 2026-05-22 15:59
UTC: `Calibration-driven mastery drop: -0.030 on 11 topics`
followed by repeated `MASTERY HARD FLOOR BREACH: legal (66% < 70%)`.

R-F806 mirrors R-F796's logic in lift_all_topics:
- If old_score >= topic_floor and proposed < topic_floor: clamp at floor
- If old_score < topic_floor (legacy): hold steady (no auto-heal, no drop)
- If proposed >= topic_floor: normal drop
- Positive bump (correct) unchanged

Uses asyncio.run() per repo convention.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import student


def _setup(monkeypatch, initial_scores: dict):
    cache: dict = {}
    for topic, score in initial_scores.items():
        cache[topic] = {
            "score": score, "samples": 5, "correct": 0, "wrong": 0,
            "last_practiced": 0,
        }
    # Ensure all TOPICS keys exist with INITIAL_MASTERY if not specified,
    # mirroring _load_mastery's behaviour.
    for t in student.TOPICS:
        if t not in cache:
            cache[t] = {
                "score": student.INITIAL_MASTERY, "samples": 0,
                "correct": 0, "wrong": 0, "last_practiced": 0,
            }

    async def _load():
        return cache

    async def _save():
        return None

    monkeypatch.setattr(student, "_load_mastery", _load)
    monkeypatch.setattr(student, "_save_mastery", _save)
    monkeypatch.setattr(student, "_mark_mastery_dirty", lambda: None)
    monkeypatch.setattr(student, "_mastery_cache", cache, raising=False)
    return cache


def test_rf806_negative_bump_clamps_at_topic_hard_floor(monkeypatch):
    """lift_all_topics(-0.03) on legal at 0.72 should clamp at 0.70,
    not drop to 0.69."""
    cache = _setup(monkeypatch, {"legal": 0.72})
    new_scores = asyncio.run(student.lift_all_topics(-0.03))
    assert new_scores["legal"] == 0.70, (
        f"R-F806: legal should clamp at 0.70, got {new_scores['legal']}"
    )


def test_rf806_negative_bump_above_floor_normal_drop(monkeypatch):
    """If the drop stays above floor, behaviour is unchanged."""
    cache = _setup(monkeypatch, {"legal": 0.90})
    new_scores = asyncio.run(student.lift_all_topics(-0.03))
    assert abs(new_scores["legal"] - 0.87) < 1e-9


def test_rf806_already_below_floor_holds_steady(monkeypatch):
    """A topic already below its hard floor (pre-R-F806 data) is held
    steady — not auto-healed up to floor, not dropped further."""
    cache = _setup(monkeypatch, {"legal": 0.65})  # below 0.70 floor
    new_scores = asyncio.run(student.lift_all_topics(-0.03))
    # Pre-R-F806: 0.65 - 0.03 = 0.62 (well below floor, clamped at 0.10).
    # R-F806: hold steady at 0.65.
    assert new_scores["legal"] == 0.65, (
        f"R-F806: already-below-floor should hold steady, got "
        f"{new_scores['legal']}"
    )


def test_rf806_positive_bump_unchanged(monkeypatch):
    """Positive lift behaviour unchanged — capped at MASTERY_CEILING only."""
    cache = _setup(monkeypatch, {"legal": 0.50})
    new_scores = asyncio.run(student.lift_all_topics(+0.10))
    assert abs(new_scores["legal"] - 0.60) < 1e-9


def test_rf806_accelerating_decline_stops_at_floor(monkeypatch):
    """Capability test for the live scenario: 10 consecutive
    calibration cycles of -3pp on legal (HARD_FLOORS=0.70). Pre-R-F806
    would land at 0.42 (0.72 - 10×0.03). R-F806: clamps at 0.70."""
    cache = _setup(monkeypatch, {"legal": 0.72})
    for _ in range(10):
        asyncio.run(student.lift_all_topics(-0.03))
    assert cache["legal"]["score"] == 0.70, (
        f"R-F806 capability test: legal mastery after 10 calibration "
        f"drops landed at {cache['legal']['score']:.3f}. Should clamp "
        f"at 0.70 hard floor. Pre-R-F806 this is the bypass that "
        f"reopened Gate #2 on 2026-05-22."
    )


def test_rf806_sanctions_via_explicit_floor(monkeypatch):
    """Now that sanctions is explicitly in HARD_FLOORS (R-F800),
    lift_all_topics respects its 0.50 floor too. (sanctions is not in
    TOPICS, so this is via a topic that IS in both — using compliance
    which has HARD_FLOORS=0.70.)"""
    cache = _setup(monkeypatch, {"compliance": 0.71})
    new_scores = asyncio.run(student.lift_all_topics(-0.03))
    # 0.71 - 0.03 = 0.68. Floor=0.70. Clamp at 0.70.
    assert new_scores["compliance"] == 0.70


def test_rf806_nan_inf_bump_still_rejected(monkeypatch):
    """Regression guard for R-F206: NaN/inf bump returns empty dict."""
    cache = _setup(monkeypatch, {"legal": 0.72})
    import math
    assert asyncio.run(student.lift_all_topics(math.nan)) == {}
    assert asyncio.run(student.lift_all_topics(math.inf)) == {}
    assert asyncio.run(student.lift_all_topics(0)) == {}
    # legal score unchanged
    assert cache["legal"]["score"] == 0.72
