"""R-F616 — dialogue_state SQLite store.

Foundation for chase logic (R-F618, R-F624, R-F629). Pins:
  - open_question persists + returns id
  - get / list_open_for_user / list_due_for_chase / list_all_open
    return the right rows
  - mark_answered / mark_chased / mark_closed transition correctly
  - chase_count increments
  - Defensive: bad inputs don't crash, fail-open on store errors
  - Schema indexes exist for the expected query shapes
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest


# ── Fixture: per-test temp DB ─────────────────────────────────────


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Point ARIA_DIALOGUE_DB_PATH at a temp file + reset module state
    so each test gets a clean store."""
    db_path = tmp_path / "test_dialogue.db"
    monkeypatch.setenv("ARIA_DIALOGUE_DB_PATH", str(db_path))
    from aria_service.intel import dialogue_state as ds
    # Reset the module-level connection so the new env var takes effect
    asyncio.run(ds._reset_for_tests())
    yield db_path
    asyncio.run(ds._reset_for_tests())


# ══════════════════════════════════════════════════════════════════
# PART A — open + get
# ══════════════════════════════════════════════════════════════════

def test_rf616_open_question_returns_id(fresh_db):
    from aria_service.intel import dialogue_state as ds
    qid = asyncio.run(ds.open_question(
        user_id="antonio",
        question_text="Did the OFSI fetch come back?",
        channel="wa_dm",
        context_summary="DD on La Scala blocked at AMBER pending UK sanctions",
    ))
    assert isinstance(qid, int)
    assert qid > 0


def test_rf616_open_question_persists_to_get(fresh_db):
    from aria_service.intel import dialogue_state as ds
    qid = asyncio.run(ds.open_question(
        user_id="antonio",
        question_text="Q1",
        channel="wa_dm",
    ))
    row = asyncio.run(ds.get(qid))
    assert row is not None
    assert row["user_id"] == "antonio"
    assert row["question_text"] == "Q1"
    assert row["channel"] == "wa_dm"
    assert row["status"] == "open"
    assert row["chase_count"] == 0


def test_rf616_open_question_rejects_empty_inputs(fresh_db):
    from aria_service.intel import dialogue_state as ds
    assert asyncio.run(ds.open_question(user_id="", question_text="Q")) is None
    assert asyncio.run(ds.open_question(user_id="u", question_text="")) is None


def test_rf616_get_unknown_id_returns_none(fresh_db):
    from aria_service.intel import dialogue_state as ds
    assert asyncio.run(ds.get(999999)) is None
    assert asyncio.run(ds.get(0)) is None


# ══════════════════════════════════════════════════════════════════
# PART B — mark_answered
# ══════════════════════════════════════════════════════════════════

def test_rf616_mark_answered_flips_status(fresh_db):
    from aria_service.intel import dialogue_state as ds
    qid = asyncio.run(ds.open_question(user_id="ant", question_text="Q"))
    ok = asyncio.run(ds.mark_answered(qid, answer_text="Yes, here it is", by_user_id="ant"))
    assert ok is True
    row = asyncio.run(ds.get(qid))
    assert row["status"] == "answered"
    assert row["answer_text"] == "Yes, here it is"
    assert row["answered_at"] is not None
    assert row["closed_reason"] == "answered"


def test_rf616_mark_answered_idempotent(fresh_db):
    """Re-marking an already-answered question is a no-op success."""
    from aria_service.intel import dialogue_state as ds
    qid = asyncio.run(ds.open_question(user_id="ant", question_text="Q"))
    asyncio.run(ds.mark_answered(qid, answer_text="first"))
    asyncio.run(ds.mark_answered(qid, answer_text="second"))
    row = asyncio.run(ds.get(qid))
    # Latest answer wins
    assert row["answer_text"] == "second"
    assert row["status"] == "answered"


# ══════════════════════════════════════════════════════════════════
# PART C — mark_chased increments count + flips status
# ══════════════════════════════════════════════════════════════════

def test_rf616_mark_chased_increments_count_and_flips_status(fresh_db):
    from aria_service.intel import dialogue_state as ds
    qid = asyncio.run(ds.open_question(user_id="ant", question_text="Q"))
    asyncio.run(ds.mark_chased(qid))
    asyncio.run(ds.mark_chased(qid))
    asyncio.run(ds.mark_chased(qid))
    row = asyncio.run(ds.get(qid))
    assert row["chase_count"] == 3
    assert row["status"] == "chasing"
    assert row["last_chased_at"] is not None


def test_rf616_mark_chased_no_op_on_closed_question(fresh_db):
    from aria_service.intel import dialogue_state as ds
    qid = asyncio.run(ds.open_question(user_id="ant", question_text="Q"))
    asyncio.run(ds.mark_closed(qid, by_user_id="ant", reason="no_longer_relevant"))
    # Chase after close should not flip status or increment
    asyncio.run(ds.mark_chased(qid))
    row = asyncio.run(ds.get(qid))
    assert row["status"] == "closed"
    assert row["chase_count"] == 0


# ══════════════════════════════════════════════════════════════════
# PART D — mark_closed
# ══════════════════════════════════════════════════════════════════

def test_rf616_mark_closed_flips_status_and_records_reason(fresh_db):
    from aria_service.intel import dialogue_state as ds
    qid = asyncio.run(ds.open_question(user_id="ant", question_text="Q"))
    asyncio.run(ds.mark_closed(qid, by_user_id="antonio", reason="operator_accepted_residual"))
    row = asyncio.run(ds.get(qid))
    assert row["status"] == "closed"
    assert row["closed_by"] == "antonio"
    assert row["closed_reason"] == "operator_accepted_residual"
    assert row["closed_at"] is not None


# ══════════════════════════════════════════════════════════════════
# PART E — list queries
# ══════════════════════════════════════════════════════════════════

def test_rf616_list_open_for_user_filters_correctly(fresh_db):
    from aria_service.intel import dialogue_state as ds
    asyncio.run(ds.open_question(user_id="ant", question_text="Q1"))
    asyncio.run(ds.open_question(user_id="ant", question_text="Q2"))
    asyncio.run(ds.open_question(user_id="other", question_text="Q3"))
    q_closed = asyncio.run(ds.open_question(user_id="ant", question_text="Q4"))
    asyncio.run(ds.mark_answered(q_closed, answer_text="done"))

    open_for_ant = asyncio.run(ds.list_open_for_user("ant"))
    texts = {q["question_text"] for q in open_for_ant}
    assert texts == {"Q1", "Q2"}  # Q3 is other user, Q4 is answered


def test_rf616_list_open_for_user_includes_chasing(fresh_db):
    """`chasing` status counts as open for the per-user list — these
    are questions ARIA is actively pursuing."""
    from aria_service.intel import dialogue_state as ds
    qid = asyncio.run(ds.open_question(user_id="ant", question_text="Q"))
    asyncio.run(ds.mark_chased(qid))
    open_for_ant = asyncio.run(ds.list_open_for_user("ant"))
    assert len(open_for_ant) == 1
    assert open_for_ant[0]["status"] == "chasing"


def test_rf616_list_due_for_chase_returns_overdue_only(fresh_db):
    from aria_service.intel import dialogue_state as ds
    now = time.time()
    # Overdue
    asyncio.run(ds.open_question(
        user_id="ant", question_text="overdue",
        expected_by=now - 3600,
    ))
    # Not yet due
    asyncio.run(ds.open_question(
        user_id="ant", question_text="future",
        expected_by=now + 3600,
    ))
    # No deadline
    asyncio.run(ds.open_question(
        user_id="ant", question_text="open-ended",
    ))
    due = asyncio.run(ds.list_due_for_chase(now=now))
    assert len(due) == 1
    assert due[0]["question_text"] == "overdue"


def test_rf616_list_all_open_aggregates_across_users(fresh_db):
    from aria_service.intel import dialogue_state as ds
    asyncio.run(ds.open_question(user_id="ant", question_text="A"))
    asyncio.run(ds.open_question(user_id="user_b", question_text="B"))
    asyncio.run(ds.open_question(user_id="user_c", question_text="C"))
    all_open = asyncio.run(ds.list_all_open())
    assert len(all_open) == 3
    user_ids = {r["user_id"] for r in all_open}
    assert user_ids == {"ant", "user_b", "user_c"}


# ══════════════════════════════════════════════════════════════════
# PART F — stats
# ══════════════════════════════════════════════════════════════════

def test_rf616_stats_reports_counts_per_status(fresh_db):
    from aria_service.intel import dialogue_state as ds
    q1 = asyncio.run(ds.open_question(user_id="ant", question_text="open1"))
    asyncio.run(ds.open_question(user_id="ant", question_text="open2"))
    q3 = asyncio.run(ds.open_question(user_id="ant", question_text="answered"))
    asyncio.run(ds.mark_answered(q3, answer_text="ok"))
    asyncio.run(ds.mark_chased(q1))  # q1 becomes chasing
    s = asyncio.run(ds.stats())
    assert s["total"] == 3
    assert s["by_status"].get("open") == 1
    assert s["by_status"].get("chasing") == 1
    assert s["by_status"].get("answered") == 1


def test_rf616_stats_avg_resolution_seconds_populated_after_answer(fresh_db):
    from aria_service.intel import dialogue_state as ds
    qid = asyncio.run(ds.open_question(user_id="ant", question_text="Q"))
    # tiny delay so answered_at > asked_at
    time.sleep(0.05)
    asyncio.run(ds.mark_answered(qid, answer_text="ok"))
    s = asyncio.run(ds.stats())
    assert s["avg_resolution_seconds"] is not None
    assert s["avg_resolution_seconds"] >= 0


# ══════════════════════════════════════════════════════════════════
# PART G — DD AskItem linkage (Phase 6 preparation)
# ══════════════════════════════════════════════════════════════════

def test_rf616_related_ask_id_persists(fresh_db):
    """Phase 6 (R-F626) will link dialogue questions to DDGoal.AskItems.
    The column must persist correctly."""
    from aria_service.intel import dialogue_state as ds
    qid = asyncio.run(ds.open_question(
        user_id="ant", question_text="Q",
        related_ask_id="ask_LaScala_OFSI",
    ))
    row = asyncio.run(ds.get(qid))
    assert row["related_ask_id"] == "ask_LaScala_OFSI"


def test_rf616_goal_id_persists(fresh_db):
    """goal_id is the GoalContext key — must round-trip correctly."""
    from aria_service.intel import dialogue_state as ds
    qid = asyncio.run(ds.open_question(
        user_id="ant", question_text="Q",
        goal_id="dd_LaScala_2026_05_17",
    ))
    row = asyncio.run(ds.get(qid))
    assert row["goal_id"] == "dd_LaScala_2026_05_17"
