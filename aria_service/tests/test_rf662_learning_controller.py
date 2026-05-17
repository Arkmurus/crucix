"""R-F662 — OSS-only learning controller (Phase B core).

Explicit operator override granted 2026-05-17. The conductor named in
the design analysis — picks topics, sequences them, judges stop-
criteria, drives the local-only learning loop. ZERO cloud LLM calls.

Verifies:
  - _agreement_score helper
  - is_enabled / kill-switch
  - _collect_candidate_topics merges FSRS-due + reading-queue with dedup
  - study_topic runs without cloud LLM
  - run_cycle returns a structured report, respects time budget
  - run_cycle no-ops when ARIA_LEARNING_CONTROLLER_ENABLED is off
  - Static guard: learning_controller.py has no cloud-LLM imports
  - yaml + dispatch wiring for the autonomous task
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

import pytest

from aria_service.learning import learning_controller as lc


# ── Pure helper: _agreement_score ─────────────────────────────────────────

def test_rf662_agreement_score_empty_returns_zero():
    assert lc._agreement_score([]) == 0.0
    assert lc._agreement_score([{}]) == 0.0


def test_rf662_agreement_score_means_scores():
    chunks = [{"score": 0.9}, {"score": 0.7}, {"score": 0.5}]
    assert lc._agreement_score(chunks) == pytest.approx(0.7)


def test_rf662_agreement_score_skips_chunks_without_score():
    chunks = [{"score": 0.8}, {"text": "no score field"}, {"score": 0.6}]
    assert lc._agreement_score(chunks) == pytest.approx(0.7)


# ── Kill switch ───────────────────────────────────────────────────────────

def test_rf662_is_enabled_defaults_off(monkeypatch):
    monkeypatch.delenv(lc._ENABLE_ENV, raising=False)
    assert lc.is_enabled() is False


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("True", True), ("YES", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("", False), ("anything", False),
])
def test_rf662_is_enabled_truthy_values(monkeypatch, val, expected):
    monkeypatch.setenv(lc._ENABLE_ENV, val)
    assert lc.is_enabled() is expected


# ── _collect_candidate_topics: merge + dedup ──────────────────────────────

def test_rf662_collect_merges_fsrs_and_queue(monkeypatch):
    """FSRS-due first, then queue items, with topic-level dedup."""
    fsrs_topics = [
        {"topic": "sanctions", "due_at": "2026-05-17T00:00:00Z", "retention_now": 0.5},
        {"topic": "aviation",  "due_at": "2026-05-17T01:00:00Z", "retention_now": 0.7},
    ]
    queue_items = [
        {"id": 7,  "topic": "aviation",   "reason": "failed_quiz"},   # dedup
        {"id": 11, "topic": "compliance", "reason": "failed_quiz"},
    ]

    async def _fake_get_due(limit=10):
        return fsrs_topics

    async def _fake_pop(limit=10):
        return queue_items

    from aria_service.intel import student
    from aria_service.learning import reading_queue

    async def runner():
        with patch.object(student, "get_due_topics", _fake_get_due), \
             patch.object(reading_queue, "pop_pending", _fake_pop):
            return await lc._collect_candidate_topics(max_topics=10)

    out = asyncio.run(runner())
    topics = [c["topic"] for c in out]
    assert topics == ["sanctions", "aviation", "compliance"]
    # Sources tagged correctly
    sources = {c["topic"]: c["source"] for c in out}
    assert sources["sanctions"] == "fsrs_due"
    assert sources["aviation"] == "fsrs_due"  # dedup keeps the FSRS-source entry
    assert sources["compliance"] == "reading_queue"


def test_rf662_collect_respects_max_topics(monkeypatch):
    fsrs_topics = [{"topic": f"t{i}"} for i in range(20)]

    async def _fake_get_due(limit=10):
        return fsrs_topics[:limit]

    async def _fake_pop(limit=10):
        return []

    from aria_service.intel import student
    from aria_service.learning import reading_queue

    async def runner():
        with patch.object(student, "get_due_topics", _fake_get_due), \
             patch.object(reading_queue, "pop_pending", _fake_pop):
            return await lc._collect_candidate_topics(max_topics=3)

    out = asyncio.run(runner())
    assert len(out) == 3


# ── study_topic: local-only, no LLM ───────────────────────────────────────

def test_rf662_study_topic_high_agreement_marks_correct(monkeypatch, tmp_path):
    """When RAG chunks agree (high mean score), update_mastery is called
    with correct=True."""
    # R-F663 debounce isolation: use a fresh bookmarks DB per test so
    # cross-test pollution can't trigger the 5-min debounce.
    monkeypatch.setenv("ARIA_BOOKMARKS_DB_PATH", str(tmp_path / "bm_high.db"))
    from aria_service.learning import bookmarks as _bm
    asyncio.run(_bm._close_conn())

    captured: dict = {}

    async def _fake_rag(query, top_k=20, min_similarity=0.4):
        return [{"score": 0.85, "text": "x"}, {"score": 0.8, "text": "y"}]

    async def _fake_update(topics, *, correct, weight):
        captured["topics"] = topics
        captured["correct"] = correct
        captured["weight"] = weight

    async def _fake_completion(topic, **kw):
        return {"complete": False, "blockers": ["verified_facts < 5"]}

    from aria_service.intel import rag_store, student, topic_completion

    async def runner():
        with patch.object(rag_store, "search", _fake_rag), \
             patch.object(student, "update_mastery", _fake_update), \
             patch.object(topic_completion, "topic_completion", _fake_completion):
            return await lc.study_topic("rf662_high_topic_unique")

    result = asyncio.run(runner())
    assert result["chunks_retrieved"] == 2
    assert result["agreement"] == pytest.approx(0.825, abs=0.01)
    assert result["mastery_updated"] is True
    assert result["passed"] is True
    assert captured["correct"] is True
    assert captured["weight"] == 0.4


def test_rf662_study_topic_low_agreement_marks_wrong(monkeypatch, tmp_path):
    monkeypatch.setenv("ARIA_BOOKMARKS_DB_PATH", str(tmp_path / "bm_low.db"))
    from aria_service.learning import bookmarks as _bm
    asyncio.run(_bm._close_conn())

    captured: dict = {}

    async def _fake_rag(query, top_k=20, min_similarity=0.4):
        return [{"score": 0.4, "text": "x"}, {"score": 0.45, "text": "y"}]

    async def _fake_update(topics, *, correct, weight):
        captured["correct"] = correct

    async def _fake_completion(topic, **kw):
        return {"complete": False, "blockers": []}

    from aria_service.intel import rag_store, student, topic_completion

    async def runner():
        with patch.object(rag_store, "search", _fake_rag), \
             patch.object(student, "update_mastery", _fake_update), \
             patch.object(topic_completion, "topic_completion", _fake_completion):
            return await lc.study_topic("rf662_low_topic_unique")

    result = asyncio.run(runner())
    assert result["passed"] is False
    assert captured["correct"] is False


def test_rf662_study_topic_empty_topic():
    async def runner():
        return await lc.study_topic("")
    out = asyncio.run(runner())
    assert out.get("error") == "empty_topic"


# ── run_cycle: end-to-end ─────────────────────────────────────────────────

def test_rf662_run_cycle_disabled_short_circuits(monkeypatch):
    monkeypatch.delenv(lc._ENABLE_ENV, raising=False)

    async def runner():
        return await lc.run_cycle()

    out = asyncio.run(runner())
    assert out["ok"] is False
    assert "disabled" in (out.get("error") or "").lower()
    assert out["studied_n"] == 0


def test_rf662_run_cycle_no_candidates(monkeypatch):
    monkeypatch.setenv(lc._ENABLE_ENV, "1")

    async def _empty(*a, **kw):
        return []

    async def runner():
        with patch.object(lc, "_collect_candidate_topics", _empty):
            return await lc.run_cycle()

    out = asyncio.run(runner())
    assert out["ok"] is True
    assert out["candidates_n"] == 0
    assert out["studied_n"] == 0


def test_rf662_run_cycle_studies_candidates_and_marks_done(monkeypatch):
    monkeypatch.setenv(lc._ENABLE_ENV, "1")

    candidates = [
        {"topic": "sanctions", "source": "fsrs_due"},
        {"topic": "aviation",  "source": "reading_queue", "queue_id": 42},
    ]

    async def _fake_collect(max_topics):
        return candidates

    async def _fake_study(topic):
        return {
            "topic": topic,
            "chunks_retrieved": 5,
            "agreement": 0.8,
            "mastery_updated": True,
            "complete": True if topic == "aviation" else False,
            "duration_ms": 100,
        }

    marked: list = []

    async def _fake_mark(item_id, *, status, notes):
        marked.append((item_id, status))
        return True

    from aria_service.learning import reading_queue

    async def runner():
        with patch.object(lc, "_collect_candidate_topics", _fake_collect), \
             patch.object(lc, "study_topic", _fake_study), \
             patch.object(reading_queue, "mark_processed", _fake_mark):
            return await lc.run_cycle(max_topics=2)

    out = asyncio.run(runner())
    assert out["ok"] is True
    assert out["candidates_n"] == 2
    assert out["studied_n"] == 2
    assert out["completed_topics"] == ["aviation"]
    assert marked == [(42, "done")]


def test_rf662_run_cycle_respects_time_budget(monkeypatch):
    """If the cycle runs over budget, it must bail out cleanly."""
    monkeypatch.setenv(lc._ENABLE_ENV, "1")
    candidates = [{"topic": f"t{i}", "source": "fsrs_due"} for i in range(5)]

    async def _fake_collect(max_topics):
        return candidates

    async def _slow_study(topic):
        await asyncio.sleep(0.05)
        return {"topic": topic, "complete": False, "duration_ms": 50}

    async def runner():
        with patch.object(lc, "_collect_candidate_topics", _fake_collect), \
             patch.object(lc, "study_topic", _slow_study):
            return await lc.run_cycle(max_topics=5, time_budget_s=0.1)

    out = asyncio.run(runner())
    # Cycle should have studied at most ~2 topics before budget exhaustion
    assert out["studied_n"] < 5
    assert any("time_budget_exhausted" in e for e in out["errors"])


# ── Static guards: no cloud LLM, no Anthropic SDK ─────────────────────────

def test_rf662_no_llm_imports_in_module():
    """OSS-only constraint: learning_controller.py must not import
    cloud LLM SDKs or call llm.complete inside the cycle."""
    import re
    from pathlib import Path
    src = (
        Path(__file__).resolve().parent.parent
        / "learning" / "learning_controller.py"
    ).read_text(encoding="utf-8")
    code_only = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code_only = re.sub(r"#.*$", "", code_only, flags=re.MULTILINE)
    for pat in (
        r"\bimport\s+anthropic\b",
        r"\bfrom\s+anthropic\b",
        r"\bimport\s+openai\b",
        r"\bdeepseek[._]",
        r"\bllm\.complete\b",
    ):
        m = re.search(pat, code_only, re.IGNORECASE)
        assert not m, (
            f"R-F662: learning_controller must not contain {pat!r} — "
            f"the loop is OSS-only by design."
        )


# ── Autonomous dispatch wiring ────────────────────────────────────────────

def test_rf662_yaml_has_learning_cycle_task():
    """The LEARNING-CYCLE task must be present in tasks.yaml with the
    R-F650/F651-lesson cost discipline."""
    from pathlib import Path
    yaml_text = (
        Path(__file__).resolve().parent.parent
        / "autonomous" / "tasks.yaml"
    ).read_text(encoding="utf-8")
    assert "id: LEARNING-CYCLE" in yaml_text
    assert "tool: learning_cycle" in yaml_text
    # Cost discipline: zero LLM cap, short timeout
    assert "cost_cap_usd: 0.00" in yaml_text or "cost_cap_usd: 0.0" in yaml_text
    assert "timeout_seconds: 120" in yaml_text


def test_rf662_dispatch_handler_present():
    """tool_kind == 'learning_cycle' branch must exist in
    autonomous/tasks.py's _execute_direct_tool."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parent.parent
        / "autonomous" / "tasks.py"
    ).read_text(encoding="utf-8")
    assert 'tool_kind == "learning_cycle"' in src
    assert "learning_controller" in src


def test_rf662_dispatch_tuple_includes_learning_cycle():
    """The 'unsupported tool kind' guard must whitelist 'learning_cycle'."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parent.parent
        / "autonomous" / "tasks.py"
    ).read_text(encoding="utf-8")
    # Anchor on the tuple of direct-call tool kinds
    block_start = src.find("elif tool_kind in (")
    assert block_start > 0
    block_end = src.find("):", block_start)
    block = src[block_start:block_end]
    assert '"learning_cycle"' in block
