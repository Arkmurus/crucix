"""R-F796 (2026-05-22): student.update_mastery clamps at topic hard floor.

Live evidence 2026-05-22 15:59-16:04 UTC: adversarial-overconfidence
detected 26pp, calibration_review applied -3pp on 11 topics per
cycle. Some topics (legal, technical, sanctions) plummeted below
their HARD_FLOORS — Gate #2 reopened.

R-F796 stops the bleeding: a negative update can't push a topic
score below its HARD_FLOORS entry. The remediation signal still
fires (via the `_rf796_proposed_breach` tracking field) so operator
visibility is preserved.

Uses asyncio.run() per repo convention.
"""
from __future__ import annotations

import asyncio

from aria_service.intel import student


def _setup(monkeypatch, initial_scores: dict):
    """Stub the storage so update_mastery is self-contained."""
    cache: dict = {}
    for topic, score in initial_scores.items():
        cache[topic] = {
            "score": score, "samples": 10, "correct": 5, "wrong": 5,
            "last_practiced": 0,
        }

    async def _load():
        return cache

    async def _save():
        return None

    monkeypatch.setattr(student, "_load_mastery", _load)
    monkeypatch.setattr(student, "_save_mastery", _save)
    monkeypatch.setattr(student, "_mark_mastery_dirty", lambda: None)
    return cache


def test_rf796_negative_update_clamps_at_topic_hard_floor(monkeypatch):
    """A negative update that would push 'legal' below 70% (its
    HARD_FLOORS value) gets clamped at 70%. 'legal' is explicitly in
    HARD_FLOORS so we can rely on its floor value."""
    assert student.HARD_FLOORS.get("legal") == 0.70, (
        "HARD_FLOORS['legal'] expected 0.70 — adjust test if changed"
    )
    cache = _setup(monkeypatch, {"legal": 0.72})

    asyncio.run(student.update_mastery(
        topics=["legal"], correct=False, weight=10.0,
    ))

    # Unclamped: 0.72 - min(0.12 × 10 × 0.72, 0.15) = 0.72 - 0.15 = 0.57.
    # Pre-R-F796 the score would land at 0.57 (below 0.70 floor).
    # R-F796: clamps at 0.70.
    assert cache["legal"]["score"] >= 0.70, (
        f"R-F796 regression: legal dropped to {cache['legal']['score']:.3f} "
        f"(below 0.70 floor)"
    )
    # Floor breach flag must STILL fire — operator needs the
    # remediation signal even though the drop was clamped.
    assert cache["legal"].get("below_floor") is True, (
        "R-F796: clamping must not hide the remediation signal — "
        "below_floor=True expected because the unclamped proposed "
        "would have breached"
    )


def test_rf796_negative_update_above_floor_normal(monkeypatch):
    """If the drop stays above floor, behaviour is unchanged."""
    cache = _setup(monkeypatch, {"sanctions": 0.90})

    asyncio.run(student.update_mastery(
        topics=["sanctions"], correct=False, weight=1.0,
    ))

    # Score should drop but remain well above 0.50.
    assert cache["sanctions"]["score"] < 0.90
    assert cache["sanctions"]["score"] > 0.50
    # No floor breach.
    assert cache["sanctions"].get("below_floor") is not True


def test_rf796_already_below_floor_holds_steady(monkeypatch):
    """If a topic is ALREADY below floor (legacy data), a negative
    update neither auto-heals up to floor nor drops further. Score
    stays put."""
    cache = _setup(monkeypatch, {"sanctions": 0.41})  # already below 0.50
    initial = cache["sanctions"]["score"]

    asyncio.run(student.update_mastery(
        topics=["sanctions"], correct=False, weight=1.0,
    ))

    # Should hold steady (within float tolerance).
    assert cache["sanctions"]["score"] == initial, (
        f"R-F796: already-below-floor score should hold steady "
        f"(was {initial}, now {cache['sanctions']['score']})"
    )
    # below_floor flag fires (proposed drop AND current score < floor).
    assert cache["sanctions"].get("below_floor") is True


def test_rf796_positive_update_unchanged(monkeypatch):
    """Positive updates (correct=True) are unaffected by R-F796 —
    they raise the score subject to MASTERY_CEILING. Regression guard."""
    cache = _setup(monkeypatch, {"sanctions": 0.55})
    initial = cache["sanctions"]["score"]

    asyncio.run(student.update_mastery(
        topics=["sanctions"], correct=True, weight=1.0,
    ))

    # Score should rise.
    assert cache["sanctions"]["score"] > initial
    assert cache["sanctions"]["score"] <= student.MASTERY_CEILING


def test_rf796_calibration_accelerating_decline_stops(monkeypatch):
    """Capability test for the live scenario: starting just above the
    legal floor (0.72), simulate calibration applying repeated negative
    updates. Pre-R-F796 the score would drop through 0.70 and continue
    falling (live evidence: legal hit 63%). Post-R-F796 it clamps at
    0.70 and stays there."""
    assert student.HARD_FLOORS.get("legal") == 0.70
    cache = _setup(monkeypatch, {"legal": 0.72})

    # Apply 10 negative cycles. Pre-R-F796 this drives legal far below
    # the 0.70 floor. Post-R-F796 it clamps at 0.70.
    for _ in range(10):
        asyncio.run(student.update_mastery(
            topics=["legal"], correct=False, weight=0.5,
        ))

    assert cache["legal"]["score"] >= 0.70, (
        f"R-F796 capability test failed: legal mastery dropped to "
        f"{cache['legal']['score']:.3f} after 10 negative cycles. "
        f"Pre-R-F796 this is exactly the regression that reopened "
        f"Gate #2 on 2026-05-22."
    )
    # Remediation flag still fires (operator visibility preserved).
    assert cache["legal"].get("below_floor") is True
