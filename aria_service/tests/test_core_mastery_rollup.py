"""Tests for the core-mastery rollup introduced 2026-04-22.

The bug this guards against: the dashboard was reporting 96% overall
mastery while all 9 load-bearing core-mastery tags were flagged weak.
The sample-weighted mean hid the problem because those tags had few
or zero samples — their contribution to the weighted mean was ~0.

The fix is a parallel unweighted rollup (`core_mastery`) over the 9
tags in `student.CORE_MASTERY_TAGS`, plus a `headline_mastery` that
is the minimum of the two. The dashboard and autonomy gate read the
headline so neither can be pulled up by easy high-sample topics.
"""
from __future__ import annotations

import asyncio

import pytest


def _run(coro):
    return asyncio.run(coro)


def _with_mastery(monkeypatch, mastery_state: dict):
    from aria_service.intel import student

    monkeypatch.setattr(student, "_mastery_cache", dict(mastery_state))

    async def _fake_save():
        pass

    monkeypatch.setattr(student, "_save_mastery", _fake_save)
    return student


def test_core_mastery_tags_constant_has_nine_tags():
    from aria_service.intel import student

    assert len(student.CORE_MASTERY_TAGS) == 9
    # The operator-confirmed set — any rename is a capability regression
    # and must be done with explicit operator approval.
    expected = {
        "lang:pt", "lang:ar", "lang:fr",
        "angola_procurement", "uk_compliance", "uk_export_control",
        "sanctions", "nato_standards", "strategic_geography",
    }
    assert set(student.CORE_MASTERY_TAGS) == expected


def test_load_mastery_scaffolds_core_tags(monkeypatch):
    from aria_service.intel import student

    monkeypatch.setattr(student, "_mastery_cache", None)

    async def _fake_get_json(key):
        return None

    async def _fake_save():
        pass

    monkeypatch.setattr(student.rs, "get_json", _fake_get_json)
    monkeypatch.setattr(student, "_save_mastery", _fake_save)

    mastery = _run(student._load_mastery())
    for tag in student.CORE_MASTERY_TAGS:
        assert tag in mastery, f"core tag {tag!r} must be scaffolded"
        assert mastery[tag]["score"] == student.INITIAL_MASTERY
        assert mastery[tag]["samples"] == 0


def test_headline_mastery_is_min_of_overall_and_core(monkeypatch):
    from aria_service.intel import student

    # Simulate the exact bug state: 11 domain topics strong + lots of
    # samples, 9 core tags at the 0.50 initial floor with zero samples.
    # Weighted overall should read ~0.96; core rollup should read 0.50.
    state: dict = {}
    for topic in student.TOPICS:
        state[topic] = {"score": 0.96, "samples": 200, "correct": 190, "wrong": 10}
    for tag in student.CORE_MASTERY_TAGS:
        state[tag] = {"score": 0.50, "samples": 0, "correct": 0, "wrong": 0}

    _with_mastery(monkeypatch, state)
    report = _run(student.get_mastery_report())

    assert report["overall_mastery"] == pytest.approx(0.96, abs=0.01)
    assert report["core_mastery"] == pytest.approx(0.50, abs=0.001)
    # Headline must track the weaker of the two — this is the whole point
    # of the fix; the dashboard cannot read higher than the core floor.
    assert report["headline_mastery"] == pytest.approx(0.50, abs=0.001)
    assert set(report["core_weak_topics"]) == set(student.CORE_MASTERY_TAGS)


def test_headline_tracks_weaker_side_directional(monkeypatch):
    from aria_service.intel import student

    # Regression guard: min() is directional. When the domain topics are
    # weak (and drive the sample-weighted overall) while the core tags
    # are strong, headline must report the weaker domain side — not the
    # stronger core. Core tags carry no samples here so they do not
    # contaminate the weighted mean.
    state: dict = {}
    for topic in student.TOPICS:
        state[topic] = {"score": 0.40, "samples": 100}
    for tag in student.CORE_MASTERY_TAGS:
        state[tag] = {"score": 0.90, "samples": 0}

    _with_mastery(monkeypatch, state)
    report = _run(student.get_mastery_report())

    assert report["overall_mastery"] == pytest.approx(0.40, abs=0.01)
    assert report["core_mastery"] == pytest.approx(0.90, abs=0.001)
    assert report["headline_mastery"] == pytest.approx(0.40, abs=0.01)


def test_core_mastery_breakdown_exposes_every_tag(monkeypatch):
    from aria_service.intel import student

    state: dict = {
        tag: {"score": round(0.30 + 0.05 * i, 3), "samples": 1}
        for i, tag in enumerate(student.CORE_MASTERY_TAGS)
    }
    _with_mastery(monkeypatch, state)
    report = _run(student.get_mastery_report())

    breakdown = report["core_mastery_breakdown"]
    assert set(breakdown) == set(student.CORE_MASTERY_TAGS)
    for tag in student.CORE_MASTERY_TAGS:
        assert isinstance(breakdown[tag], float)


def test_empty_mastery_does_not_crash(monkeypatch):
    from aria_service.intel import student

    _with_mastery(monkeypatch, {})
    report = _run(student.get_mastery_report())

    # With no data at all, headline must fall back to INITIAL_MASTERY —
    # not None and not a divide-by-zero.
    assert report["headline_mastery"] == pytest.approx(student.INITIAL_MASTERY, abs=0.01)
    assert report["core_mastery"] == pytest.approx(student.INITIAL_MASTERY, abs=0.01)


def test_source_verifier_exposes_both_grounded_rate_keys(monkeypatch):
    # Guards the /health + autonomy_scorer + operating_modes readers that
    # look up `avg_grounded_rate`. Before the alias fix they all saw None
    # and the dashboard rendered `--` for grounded rate forever.
    from aria_service.intel import source_verifier

    async def _fake_get_json(key):
        return [
            {"verdict": "grounded", "grounded_rate": 1.0, "ts": 1_700_000_000},
            {"verdict": "partial",  "grounded_rate": 0.5, "ts": 1_700_000_000},
        ]

    monkeypatch.setattr(source_verifier.rs, "get_json", _fake_get_json)
    stats = _run(source_verifier.get_verification_stats())

    assert stats["rolling_grounded_rate"] == pytest.approx(0.75, abs=0.001)
    assert stats["avg_grounded_rate"] == stats["rolling_grounded_rate"]
