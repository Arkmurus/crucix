"""Tests for the 2026-04-22 counter + silent-skip visibility fixes.

Background: the ARIA-brain dashboard was showing structurally-always-0
counters (autonomous task fires, chat-audit entries_24h, training
examples, style exemplars, verification verified/unverified/blocking)
even though the underlying loops were demonstrably alive. Root causes:

  - Redis key never written (task_fires, chat entries_24h)
  - Silent-skip in collectors (training_export, style_learner)
  - Silent-skip when secondary provider unavailable (verification_gate)

These tests guard the counter/diag wiring so future edits don't
regress back to "structurally always 0".
"""
from __future__ import annotations

import asyncio
import pytest


def _run(coro):
    return asyncio.run(coro)


def test_chat_audit_get_stats_exposes_entries_24h():
    # /autonomy/surface reads stats["entries_24h"] as `chat_turns_served`.
    # Before 2026-04-22 this key didn't exist on the returned dict, so
    # the dashboard always read 0.
    from aria_service.intel import chat_audit_log as cal

    stats = _run(cal.get_stats())
    assert "entries_24h" in stats, "get_stats must expose entries_24h for /autonomy/surface"
    assert isinstance(stats["entries_24h"], int)


def test_training_export_summary_exposes_last_run_diagnostics():
    # Summary is what capability_manifest + /learning/stats read. Before
    # the fix, {written: 0} gave no signal WHY each source was empty.
    from aria_service.learning import training_export as te

    s = te.summary()
    assert "last_run_diagnostics" in s
    assert isinstance(s["last_run_diagnostics"], dict)


def test_training_export_set_diag_tracks_error_and_reason():
    from aria_service.learning import training_export as te

    te._last_collection_diag.clear()
    te._set_diag("writers", 0, reason="audit_path_missing:/data/aria_writer_audit.jsonl")
    assert te._last_collection_diag["writers"]["count"] == 0
    assert "audit_path_missing" in te._last_collection_diag["writers"]["reason"]

    te._set_diag("chat_turns", 0, error="KeyError: 'response'")
    assert te._last_collection_diag["chat_turns"]["error"] == "KeyError: 'response'"

    te._set_diag("adversarial", 5)
    assert te._last_collection_diag["adversarial"] == {
        "count": 5, "error": None, "reason": None,
    }


def test_style_learner_collect_gold_replies_accepts_verification_status(monkeypatch):
    # Before the fix, the collector filtered on "honesty_verdict" but
    # chat_audit_log.record_chat writes "verification_status" — every
    # entry was silently rejected, so style_exemplars stayed at 0.
    from aria_service.learning import style_learner as sl
    from aria_service.intel import chat_audit_log as cal

    long_reply = "This is a deliberately long reply to clear the 200-char floor. " * 5

    async def _fake_get_recent(limit: int = 200):
        return [
            {
                "response": long_reply,
                "verification_status": "grounded",
                "user_message": "Test question",
                "timestamp": "2099-01-01T00:00:00+00:00",
                "trace_id": "trace-abc",
            },
        ]

    monkeypatch.setattr(cal, "get_recent", _fake_get_recent)
    gold = _run(sl._collect_gold_replies(lookback_hours=24))
    assert len(gold) == 1, "verification_status=grounded must be accepted"


def test_style_learner_collect_gold_replies_records_filter_reasons(monkeypatch):
    from aria_service.learning import style_learner as sl
    from aria_service.intel import chat_audit_log as cal

    async def _fake_get_recent(limit: int = 200):
        # All entries too short — must be reflected in filter counts,
        # not returned as silently-empty.
        return [{"response": "tiny", "verification_status": "grounded"} for _ in range(3)]

    monkeypatch.setattr(cal, "get_recent", _fake_get_recent)
    _ = _run(sl._collect_gold_replies(lookback_hours=24))
    diag = sl._last_collection_diag
    assert diag.get("error") is None
    assert diag["filtered"]["total_entries"] == 3
    assert diag["filtered"]["too_short"] == 3
    assert diag["filtered"]["kept"] == 0


def test_verification_gate_record_skipped_increments_counter(monkeypatch):
    # Proves the /verification/stats `skipped_24h` counter lights up
    # when a CRITICAL output can't be verified because secondary provider
    # is missing. Before this, three zeros across verified/unverified/
    # blocking gave no signal whether the gate was idle or broken.
    from aria_service.learning import verification_gate as vg
    from aria_service.intel import redis_store as rs

    fake_store: dict = {}

    async def _fake_get_json(key):
        return fake_store.get(key)

    async def _fake_set_json(key, value, ex=None):
        fake_store[key] = value

    monkeypatch.setattr(rs, "get_json", _fake_get_json)
    monkeypatch.setattr(rs, "set_json", _fake_set_json)

    _run(vg.record_skipped("no_secondary_provider"))
    _run(vg.record_skipped("no_secondary_provider"))
    _run(vg.record_skipped("secondary_empty"))

    stats = _run(vg.get_stats())
    assert stats["skipped_24h"] == 3
    assert stats["skipped_by_reason_24h"]["no_secondary_provider"] == 2
    assert stats["skipped_by_reason_24h"]["secondary_empty"] == 1
