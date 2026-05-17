"""R-F664 — FSRS spaced-repetition scheduler wired into student.py mastery.

R-F664 (2026-05-17). Slice 3 of the OSS-only learning framework (Phase A
classification). Adds the SOTA forgetting-curve scheduler ON TOP of the
existing EWMA mastery — doesn't replace it.

Before R-F664: a topic answered correctly stayed at high mastery
indefinitely until the next interaction. There was no TIME-aware decay,
so "what should ARIA review next?" had no good answer.

After R-F664: every update_mastery() call also runs through the FSRS
scheduler. Each topic carries a serialised Card. The controller
(R-F662) can call student.get_due_topics() to pick what to study now.

Verifies:
  - fsrs_scheduler helpers (parse_card, review_topic, is_due, retention_now)
  - student.update_mastery integrates FSRS without breaking the EWMA path
  - student.get_due_topics / get_topic_retention return the expected shape
  - never-reviewed topics surface as due
  - Zero LLM calls anywhere in the loop
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from aria_service.learning import fsrs_scheduler as fsrs_mod
from aria_service.intel import student


# ── fsrs_scheduler helpers ────────────────────────────────────────────────

def test_rf664_get_scheduler_is_singleton():
    s1 = fsrs_mod.get_scheduler()
    s2 = fsrs_mod.get_scheduler()
    assert s1 is s2, "R-F664: get_scheduler must return the same singleton"


def test_rf664_review_topic_creates_card_on_first_review():
    """First call (prior_card=None) must produce a brand-new card."""
    card = fsrs_mod.review_topic("sanctions", correct=True)
    assert isinstance(card, dict)
    assert "due" in card
    assert "stability" in card
    assert "difficulty" in card
    assert "state" in card


def test_rf664_review_topic_round_trips():
    """A serialised card must be re-parseable + further reviewable."""
    card1 = fsrs_mod.review_topic("sanctions", correct=True)
    card2 = fsrs_mod.review_topic("sanctions", correct=True, prior_card=card1)
    assert isinstance(card2, dict)
    # Both cards have due timestamps; second-review schedule changes vs first
    assert card1.get("due") != card2.get("due") or card1.get("stability") != card2.get("stability")


def test_rf664_review_topic_correct_vs_wrong_diverge():
    """Rating.Good (correct=True) and Rating.Again (correct=False) must
    produce different next-due schedules — wrong should be sooner."""
    card_good = fsrs_mod.review_topic("sanctions", correct=True)
    card_again = fsrs_mod.review_topic("sanctions", correct=False)
    # Both cards exist; their due dates differ
    assert card_good != card_again


def test_rf664_review_topic_empty_topic_raises():
    with pytest.raises(ValueError):
        fsrs_mod.review_topic("", correct=True)


def test_rf664_is_due_treats_missing_card_as_due():
    """A topic that has never been reviewed counts as due — that's what
    surfaces fresh topics into the controller's queue."""
    assert fsrs_mod.is_due(None) is True
    assert fsrs_mod.is_due({}) is True


def test_rf664_is_due_respects_future_due():
    """A fresh review schedules due in the future → is_due now → False."""
    card = fsrs_mod.review_topic("sanctions", correct=True)
    # A Good review has due in the future
    assert fsrs_mod.is_due(card) is False


def test_rf664_is_due_respects_past_due():
    """If `now` is far in the future, the card is due."""
    card = fsrs_mod.review_topic("sanctions", correct=True)
    far_future = datetime.now(timezone.utc) + timedelta(days=3650)
    assert fsrs_mod.is_due(card, now=far_future) is True


def test_rf664_retention_now_for_fresh_review():
    """Right after a Good review, retention should be high (close to
    the FSRS desired_retention=0.9 target)."""
    card = fsrs_mod.review_topic("sanctions", correct=True)
    r = fsrs_mod.retention_now(card)
    assert 0.0 <= r <= 1.0
    # Right after the review the retention should be near 1.0
    assert r > 0.8, f"expected high retention right after Good review, got {r}"


def test_rf664_retention_now_missing_card_is_zero():
    assert fsrs_mod.retention_now(None) == 0.0
    assert fsrs_mod.retention_now({}) == 0.0


# ── student.update_mastery integration ────────────────────────────────────

def test_rf664_update_mastery_stores_fsrs_card():
    """A real update_mastery call must populate m['fsrs_card']."""
    # Use an isolated mastery cache for this test
    isolated: dict = {}

    async def runner():
        with patch.object(student, "_mastery_cache", None), \
             patch.object(student, "_load_mastery", lambda: _load_mock()), \
             patch.object(student, "_save_mastery", _save_noop):
            await student.update_mastery(["sanctions"], correct=True)
            return isolated.get("sanctions")

    async def _load_mock():
        return isolated

    async def _save_noop():
        return None

    result = asyncio.run(runner())
    assert result is not None
    assert "fsrs_card" in result
    assert isinstance(result["fsrs_card"], dict)
    assert "due" in result["fsrs_card"]


def test_rf664_update_mastery_preserves_ewma():
    """The FSRS hook must NOT disturb the existing EWMA score update."""
    isolated: dict = {}

    async def _load_mock():
        return isolated

    async def _save_noop():
        return None

    async def runner():
        with patch.object(student, "_mastery_cache", None), \
             patch.object(student, "_load_mastery", lambda: _load_mock()), \
             patch.object(student, "_save_mastery", _save_noop):
            await student.update_mastery(["sanctions"], correct=True)
            score1 = isolated["sanctions"]["score"]
            await student.update_mastery(["sanctions"], correct=True)
            score2 = isolated["sanctions"]["score"]
            return score1, score2

    s1, s2 = asyncio.run(runner())
    # EWMA score must increase on consecutive 'correct' reviews
    assert s2 > s1, f"EWMA should increase: {s1} -> {s2}"


def test_rf664_update_mastery_fsrs_failure_non_fatal():
    """If FSRS raises, update_mastery must still complete and persist
    the EWMA delta — failure here is by design non-fatal."""
    isolated: dict = {}

    async def _load_mock():
        return isolated

    async def _save_noop():
        return None

    def _bomb(*a, **kw):
        raise RuntimeError("simulated FSRS failure")

    async def runner():
        with patch.object(student, "_mastery_cache", None), \
             patch.object(student, "_load_mastery", lambda: _load_mock()), \
             patch.object(student, "_save_mastery", _save_noop), \
             patch.object(fsrs_mod, "review_topic", _bomb):
            await student.update_mastery(["sanctions"], correct=True)
            return isolated.get("sanctions")

    result = asyncio.run(runner())
    assert result is not None
    # EWMA still updated — score moved above the initial baseline
    assert result["score"] > student.INITIAL_MASTERY
    # FSRS card NOT set because of the bomb
    assert "fsrs_card" not in result


# ── get_due_topics / get_topic_retention ──────────────────────────────────

def test_rf664_get_due_topics_returns_never_reviewed():
    """Topics with no fsrs_card must be returned as due."""
    isolated = {
        "sanctions": {"score": 0.5, "samples": 0, "correct": 0, "wrong": 0},
        "aviation": {"score": 0.5, "samples": 0, "correct": 0, "wrong": 0},
    }

    async def _load_mock():
        return isolated

    async def runner():
        with patch.object(student, "_load_mastery", lambda: _load_mock()):
            return await student.get_due_topics()

    result = asyncio.run(runner())
    topics = {r["topic"] for r in result}
    assert "sanctions" in topics
    assert "aviation" in topics


def test_rf664_get_due_topics_skips_future_due():
    """A topic reviewed correctly just now should NOT be in the due list."""
    fresh_card = fsrs_mod.review_topic("sanctions", correct=True)
    isolated = {
        "sanctions": {"score": 0.9, "samples": 1, "correct": 1, "wrong": 0,
                       "fsrs_card": fresh_card},
        "aviation":  {"score": 0.5, "samples": 0, "correct": 0, "wrong": 0},
    }

    async def _load_mock():
        return isolated

    async def runner():
        with patch.object(student, "_load_mastery", lambda: _load_mock()):
            return await student.get_due_topics()

    result = asyncio.run(runner())
    topics = {r["topic"] for r in result}
    assert "aviation" in topics       # never reviewed → due
    assert "sanctions" not in topics  # just reviewed → not yet due


def test_rf664_get_topic_retention_shape():
    fresh_card = fsrs_mod.review_topic("sanctions", correct=True)
    isolated = {
        "sanctions": {"score": 0.85, "samples": 1, "correct": 1, "wrong": 0,
                       "fsrs_card": fresh_card},
    }

    async def _load_mock():
        return isolated

    async def runner():
        with patch.object(student, "_load_mastery", lambda: _load_mock()):
            return await student.get_topic_retention("sanctions")

    result = asyncio.run(runner())
    assert result["topic"] == "sanctions"
    assert result["has_card"] is True
    assert 0.0 <= result["retention_now"] <= 1.0
    assert result["retention_now"] > 0.8


def test_rf664_get_topic_retention_missing_topic():
    isolated: dict = {}

    async def _load_mock():
        return isolated

    async def runner():
        with patch.object(student, "_load_mastery", lambda: _load_mock()):
            return await student.get_topic_retention("never_seen")

    result = asyncio.run(runner())
    assert result["topic"] == "never_seen"
    assert result["has_card"] is False
    assert result["retention_now"] == 0.0


# ── No LLM in the loop ────────────────────────────────────────────────────

def test_rf664_no_llm_calls_in_fsrs_scheduler_module():
    """Static guard: fsrs_scheduler.py must not IMPORT cloud LLM SDKs —
    the whole point of R-F664 is OSS-only learning. Mentions in
    docstrings are fine; actual import statements are not."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "learning" / "fsrs_scheduler.py").read_text(encoding="utf-8")
    # Strip docstrings + comments before scanning for imports.
    code_only = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code_only = re.sub(r"#.*$", "", code_only, flags=re.MULTILINE)
    forbidden_patterns = [
        r"\bimport\s+anthropic\b",
        r"\bfrom\s+anthropic\b",
        r"\bimport\s+openai\b",
        r"\bfrom\s+openai\b",
        r"\bdeepseek[._]",
        r"\bllm\.complete\b",
        r"\bgroq[._]",
    ]
    for pat in forbidden_patterns:
        m = re.search(pat, code_only, re.IGNORECASE)
        assert not m, (
            f"R-F664: fsrs_scheduler.py contains forbidden pattern "
            f"{pat!r} — the learning loop must be OSS-only."
        )
