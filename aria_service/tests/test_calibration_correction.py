"""Tests for the self-calibration correction loop.

When ground-truth accuracy is consistently higher than self-assessed
mastery by more than 10pp, the calibration review pulls stored mastery
toward reality. Previously this only emitted a warning; now it
actually lifts the scores.
"""
from __future__ import annotations

import asyncio

import pytest


def test_lift_all_topics_adds_bump_and_caps_at_ceiling():
    from aria_service.intel import student

    seeded_topics = student.TOPICS[:3]

    async def run():
        # Force a known state — only seed the first 3 topics at 0.80.
        # Other topics will be initialised to INITIAL_MASTERY (0.5) by
        # lift_all_topics and receive the bump on top.
        student._mastery_cache = {t: {"score": 0.80} for t in seeded_topics}

        async def _fake_save():
            pass
        orig_save = student._save_mastery
        student._save_mastery = _fake_save
        try:
            return await student.lift_all_topics(0.05)
        finally:
            student._save_mastery = orig_save

    new = asyncio.run(run())
    assert new, "lift_all_topics must return the new score map"
    # Seeded topics must have moved from 0.80 → 0.85 (with ceiling cap)
    from aria_service.intel import student as _s
    for topic in seeded_topics:
        assert topic in new
        assert new[topic] <= _s.MASTERY_CEILING
        assert new[topic] >= 0.80 + 0.04  # 0.05 minus float tolerance


def test_lift_all_topics_zero_bump_is_noop():
    from aria_service.intel import student

    async def run():
        return await student.lift_all_topics(0.0)

    result = asyncio.run(run())
    assert result == {}


def test_lift_all_topics_respects_ceiling():
    from aria_service.intel import student

    async def run():
        # State where everything is already near ceiling
        student._mastery_cache = {
            t: {"score": 0.97} for t in student.TOPICS[:2]
        }

        async def _fake_save():
            pass
        orig_save = student._save_mastery
        student._save_mastery = _fake_save
        try:
            return await student.lift_all_topics(0.10)
        finally:
            student._save_mastery = orig_save

    new = asyncio.run(run())
    from aria_service.intel import student as _s
    for t, score in new.items():
        # Can't exceed ceiling
        assert score <= _s.MASTERY_CEILING


def test_correction_constants_sane():
    from aria_service.intel import calibration_review as cr
    # Threshold > 0; fraction in (0, 1]; cap reasonable; cooldown > 0
    assert 0 < cr._CORRECT_THRESHOLD < 0.5
    assert 0 < cr._CORRECT_FRACTION <= 1.0
    assert 0 < cr._CORRECT_CAP <= 0.20  # Never more than 20pp per run
    assert cr._CORRECT_COOLDOWN >= 600  # At least 10 min between runs


def test_mastery_lr_positive_bumped():
    """Ensure the learning rate was raised to help future events converge."""
    from aria_service.intel import student
    assert student.MASTERY_LR_POSITIVE >= 0.15, (
        "MASTERY_LR_POSITIVE should be >= 0.15 after 2026-04-17 late PM "
        "calibration-review feedback loop was added"
    )
