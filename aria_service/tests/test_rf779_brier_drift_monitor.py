"""R-F779 — mastery-drift monitor + RunPod fine-tune trigger scaffold.

Tests the drift-detection logic in aria_service/intel/brier_drift_monitor.py
without hitting the real Redis snapshot ledger or the real student module.
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest import mock

import pytest


def test_rf779_snapshot_persists_to_redis_list():
    """snapshot_mastery() must call rs.lpush + rs.ltrim with the
    crucix:brier_drift:snapshots key. The structured snapshot must
    carry ts, overall_mastery, headline_mastery, and topics."""
    from aria_service.intel import brier_drift_monitor as bdm

    fake_report = {
        "overall_mastery": 0.85,
        "core_mastery": 0.82,
        "headline_mastery": 0.82,
        "topics": {
            "compliance": {"score": 0.90, "samples": 10},
            "lang:es":     {"score": 0.65, "samples": 8},
        },
    }

    pushes: list = []
    trims: list = []

    async def fake_lpush(key, value):
        pushes.append((key, value))

    async def fake_ltrim(key, start, stop):
        trims.append((key, start, stop))

    async def fake_get_report():
        return fake_report

    async def run():
        with mock.patch("aria_service.intel.student.get_mastery_report",
                        side_effect=fake_get_report), \
             mock.patch.object(bdm.rs, "lpush", side_effect=fake_lpush), \
             mock.patch.object(bdm.rs, "ltrim", side_effect=fake_ltrim):
            return await bdm.snapshot_mastery()

    snap = asyncio.run(run())
    assert snap["overall_mastery"] == 0.85
    assert snap["topics"]["compliance"] == 0.90
    assert snap["topics"]["lang:es"] == 0.65
    assert len(pushes) == 1
    assert pushes[0][0] == bdm._SNAPSHOT_KEY
    assert len(trims) == 1
    assert trims[0][2] == bdm._MAX_SNAPSHOTS - 1


def test_rf779_compute_drift_returns_per_topic_deltas():
    """With a clear baseline + latest, compute_drift must return the
    per-topic delta. Negative delta = mastery dropped."""
    from aria_service.intel import brier_drift_monitor as bdm

    now = time.time()
    # sanctions drops -0.20 (the worst); compliance drops -0.15; lang:es flat.
    # Tied deltas would depend on dict insertion order — using distinct magnitudes
    # so worst_drops[0] is unambiguous.
    fake_snapshots = [
        {  # latest
            "ts": now,
            "overall_mastery": 0.68,
            "headline_mastery": 0.62,
            "topics": {"compliance": 0.70, "lang:es": 0.60, "sanctions": 0.55},
        },
        {  # 24h ago
            "ts": now - 86400,
            "overall_mastery": 0.80,
            "headline_mastery": 0.75,
            "topics": {"compliance": 0.85, "lang:es": 0.60, "sanctions": 0.75},
        },
    ]

    async def fake_read():
        return fake_snapshots

    async def run():
        with mock.patch.object(bdm, "_read_snapshots", side_effect=fake_read):
            return await bdm.compute_drift(window_hours=24)

    drift = asyncio.run(run())
    assert drift["ok"] is True
    assert drift["composite_delta"] == round(0.68 - 0.80, 4)
    assert drift["topic_deltas"]["compliance"] == round(0.70 - 0.85, 4)
    assert drift["topic_deltas"]["lang:es"] == 0.0
    assert drift["topic_deltas"]["sanctions"] == round(0.55 - 0.75, 4)
    # Worst drops sorted ascending (most-negative first)
    assert drift["worst_drops"][0][0] == "sanctions"


def test_rf779_compute_drift_needs_two_snapshots():
    """Without baseline, compute_drift must degrade to {ok: False}."""
    from aria_service.intel import brier_drift_monitor as bdm

    async def fake_read():
        return [{"ts": time.time(), "topics": {}}]

    async def run():
        with mock.patch.object(bdm, "_read_snapshots", side_effect=fake_read):
            return await bdm.compute_drift(window_hours=24)

    drift = asyncio.run(run())
    assert drift["ok"] is False
    assert "need at least 2" in drift["reason"]


def test_rf779_threshold_alert_fires_on_breach():
    """When a topic drops >= threshold, check_thresholds_and_alert must
    record a CRITICAL pending_action with the operator-runnable training
    command in the body."""
    from aria_service.intel import brier_drift_monitor as bdm

    now = time.time()
    fake_snapshots = [
        {
            "ts": now,
            "overall_mastery": 0.70,
            "headline_mastery": 0.65,
            "topics": {"sanctions": 0.55, "compliance": 0.85},
        },
        {
            "ts": now - 86400,
            "overall_mastery": 0.80,
            "headline_mastery": 0.75,
            "topics": {"sanctions": 0.70, "compliance": 0.85},  # -0.15 on sanctions
        },
    ]

    recorded: list = []

    async def fake_pa_record(**kw):
        recorded.append(kw)

    async def fake_read():
        return fake_snapshots

    async def run():
        with mock.patch.object(bdm, "_read_snapshots", side_effect=fake_read), \
             mock.patch("aria_service.intel.pending_actions.record",
                        side_effect=fake_pa_record):
            return await bdm.check_thresholds_and_alert(threshold=0.10, window_hours=24)

    out = asyncio.run(run())
    assert out["alerted"] is True
    assert out["worst_topic"] == "sanctions"
    assert out["worst_delta"] == round(0.55 - 0.70, 4)
    assert len(recorded) == 1
    pa = recorded[0]
    assert pa["severity"] == "CRITICAL"
    assert pa["resolver_kind"] == "operator_action"
    assert pa["resolver_ref"] == "RUNPOD_FINETUNE"
    assert "scripts/train/" in pa["operator_prompt"]
    assert "sanctions" in pa["operator_prompt"]


def test_rf779_threshold_alert_no_breach_no_action():
    """When no topic drops >= threshold, no pending_action is recorded."""
    from aria_service.intel import brier_drift_monitor as bdm

    now = time.time()
    fake_snapshots = [
        {"ts": now, "overall_mastery": 0.82, "headline_mastery": 0.78,
         "topics": {"compliance": 0.85, "sanctions": 0.78}},
        {"ts": now - 86400, "overall_mastery": 0.80, "headline_mastery": 0.75,
         "topics": {"compliance": 0.85, "sanctions": 0.80}},  # -0.02, under 10pp threshold
    ]

    pa_calls: list = []

    async def fake_pa_record(**kw):
        pa_calls.append(kw)

    async def fake_read():
        return fake_snapshots

    async def run():
        with mock.patch.object(bdm, "_read_snapshots", side_effect=fake_read), \
             mock.patch("aria_service.intel.pending_actions.record",
                        side_effect=fake_pa_record):
            return await bdm.check_thresholds_and_alert(threshold=0.10, window_hours=24)

    out = asyncio.run(run())
    assert out["alerted"] is False
    assert out["reason"] == "no_breach"
    assert pa_calls == [], "pending_action must not fire when below threshold"


def test_rf779_topic_present_only_in_one_snapshot_is_skipped():
    """If a topic exists in latest but not baseline (new mastery cell),
    we can't compute a delta — it must be skipped, not crash."""
    from aria_service.intel import brier_drift_monitor as bdm

    now = time.time()
    fake_snapshots = [
        {"ts": now, "topics": {"a": 0.5, "b_new": 0.3}, "overall_mastery": 0.4, "headline_mastery": 0.4},
        {"ts": now - 86400, "topics": {"a": 0.6}, "overall_mastery": 0.6, "headline_mastery": 0.6},
    ]

    async def fake_read():
        return fake_snapshots

    async def run():
        with mock.patch.object(bdm, "_read_snapshots", side_effect=fake_read):
            return await bdm.compute_drift(window_hours=24)

    drift = asyncio.run(run())
    assert drift["ok"] is True
    assert "a" in drift["topic_deltas"]
    assert "b_new" not in drift["topic_deltas"], (
        "topics not present in baseline must be silently skipped"
    )
