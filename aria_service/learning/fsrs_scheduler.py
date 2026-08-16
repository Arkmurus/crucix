"""fsrs_scheduler — R-F664 FSRS (Free Spaced Repetition Scheduler) wiring.

R-F664 (2026-05-17). Slice 3 of the OSS-only learning-framework buildout.

Why this exists
═══════════════
Pre-R-F664 student.py used a hand-rolled EWMA decay model: scores moved
toward 1.0 on `correct` and toward 0.0 on `wrong`, but there was no
TIME-aware forgetting curve. A topic answered correctly once would stay
at high mastery indefinitely until the next interaction — even if that
interaction was six months later.

FSRS (Free Spaced Repetition Scheduler) is the SOTA forgetting-curve
model that Anki / RemNote / Mochi all use. v6 has 21 calibrated
parameters. It answers two questions ARIA's learning loop needs:

  1. When should ARIA next review topic X?       → card.due (datetime)
  2. What is the predicted retention right now?  → get_card_retrievability(card)

This module wraps the FSRS library and exposes a topic-keyed API on top.
Per-topic Card state is serialised via Card.to_dict() and stored alongside
the existing mastery dict under the new `fsrs_card` field.

OSS-only learning framework
═══════════════════════════
fsrs is MIT-licensed, ~22 KB, pure Python, zero network calls, zero LLM
calls. This module fits the "no Anthropic / DeepSeek in the learning
loop" constraint set by the operator 2026-05-17:

    "aria should be independent by now, not really on anthropic neither
     deepseek for her learning… we want her independence and the code
     is able to do that"

Public surface
══════════════
    Rating  (re-export)
    get_scheduler() -> Scheduler
    review_topic(topic, correct, *, now=None) -> dict
        Update FSRS state for `topic`. correct=True → Rating.Good,
        correct=False → Rating.Again. Returns the serialised new card.
    is_due(card_dict, *, now=None) -> bool
    retention_now(card_dict, *, now=None) -> float
    parse_card(card_dict) -> Card | None
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fsrs import Card, Rating, Scheduler

logger = logging.getLogger("aria.learning.fsrs")

# R-F4052 (C-108) — §21a wiring for this module's WORK, not its import.
#
# The old R-F1319 block fired wire_success at IMPORT time under
# "learning.fsrs_scheduler" — a name brain_hook._MODULE_TOPICS does not contain, so it
# was invisible to `never_seen` (C-104) and proved only that the file had been
# imported. Outcomes are now reported under the REGISTERED name, on BOTH
# branches. Success is throttled because this module's work runs per item.


def _wire_fsrs_scheduler(ok: bool, summary: str) -> None:
    """Report a real outcome to the brain. Never raises."""
    try:
        from aria_service.intel.engine_wiring import (
            wire_failure, wire_success_throttled,
        )
        if ok:
            wire_success_throttled(
                "fsrs_scheduler", summary, source_id="fsrs_scheduler:work",
            )
        else:
            wire_failure(
                module="fsrs_scheduler", detail=summary,
                gap_type="engine_failure", source="fsrs_scheduler:work",
            )
    except Exception as _exc:   # pragma: no cover - telemetry never blocks
        # Swallowed deliberately: brain wiring must never break this
        # module's work. Logged rather than `pass`ed so the wiring
        # failure is not itself dark (bandit B110) — wiring the wiring
        # failure would be circular.
        logger.debug("[R-F4052] fsrs_scheduler outcome wiring failed: %s", _exc)


# Singleton scheduler — parameters are FSRS v6 defaults. The operator can
# tune these later (per-domain) via a calibration pass; for now defaults
# match the SOTA recommendation.
_SCHEDULER: Optional[Scheduler] = None


def get_scheduler() -> Scheduler:
    global _SCHEDULER
    if _SCHEDULER is None:
        # enable_fuzzing=False so review schedules are deterministic in
        # tests + production. Fuzzing is for human-facing Anki-style
        # spacing where you want intervals to feel "natural"; for an
        # autonomous learning loop deterministic is better.
        _SCHEDULER = Scheduler(enable_fuzzing=False)
    return _SCHEDULER


def parse_card(card_dict: dict[str, Any] | None) -> Card | None:
    """Round-trip a serialised card back to a Card object. Returns None
    when the input is missing or unparseable — caller treats None as
    'no card yet, create a fresh one'."""
    if not card_dict or not isinstance(card_dict, dict):
        return None
    try:
        return Card.from_dict(card_dict)
    except Exception as e:
        logger.debug("fsrs_scheduler: parse_card failed: %s", e)
        return None


def review_topic(
    topic: str,
    correct: bool,
    *,
    prior_card: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Update FSRS state for a topic after a review event.

    Mapping:
      correct=True  → Rating.Good   (the standard 'I knew it' response)
      correct=False → Rating.Again  ('I forgot, schedule a relearning')

    For finer-grained ratings (Hard / Easy) call the scheduler directly;
    this helper covers the binary correct/wrong path that student.py's
    existing API exposes.

    Returns the new card serialised as dict, ready to store alongside
    the existing mastery entry."""
    if not topic:
        raise ValueError("topic required")
    scheduler = get_scheduler()
    card = parse_card(prior_card) or Card()
    rating = Rating.Good if correct else Rating.Again
    try:
        new_card, _log = scheduler.review_card(card, rating, review_datetime=now)
    except Exception as e:
        # R-F4052 (C-108) — report, then RE-RAISE. §21a asks for visibility,
        # never for swallowing: a scheduling failure must still reach the
        # caller, which decides what a missing card means.
        _wire_fsrs_scheduler(
            False, f"review_card failed for {topic!r}: {type(e).__name__}: {e}"
        )
        raise
    _wire_fsrs_scheduler(True, f"scheduled review for topic {topic!r}")
    return new_card.to_dict()


def is_due(card_dict: dict[str, Any] | None, *, now: datetime | None = None) -> bool:
    """True iff the card's due timestamp has elapsed. Missing/invalid
    cards count as due (so first-time topics are picked up immediately)."""
    card = parse_card(card_dict)
    if card is None:
        return True
    if card.due is None:
        return True
    when = now or datetime.now(timezone.utc)
    # FSRS stores `due` as a timezone-aware datetime; compare directly.
    try:
        return card.due <= when
    except TypeError:
        # Naive vs aware mismatch — coerce both to UTC before comparing.
        due = card.due.replace(tzinfo=timezone.utc) if card.due.tzinfo is None else card.due
        cmp = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when
        return due <= cmp


def retention_now(
    card_dict: dict[str, Any] | None, *, now: datetime | None = None,
) -> float:
    """Predicted probability the topic is still retained RIGHT NOW.

    Returns 0.0 when the card is missing/unparseable (we have no signal
    that anything has been learned about this topic yet). Otherwise
    delegates to scheduler.get_card_retrievability."""
    card = parse_card(card_dict)
    if card is None:
        return 0.0
    try:
        return float(get_scheduler().get_card_retrievability(card, current_datetime=now))
    except Exception as e:
        logger.debug("fsrs_scheduler: retention_now failed for topic card: %s", e)
        return 0.0
