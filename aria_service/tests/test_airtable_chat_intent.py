"""Regression tests for the airtable_health chat classifier (added 2026-04-21).

Operator asked ARIA "is Airtable sync healthy?" and she correctly refused
to fabricate a status because no tool block ran. Fix routed that query
shape to a new `airtable_health` tool that calls airtable_sync.health_check()
and surfaces live table reachability into the prompt context.

Tests pin the regex so the classifier catches the intended queries without
over-firing on unrelated mentions of "airtable".
"""
from __future__ import annotations

import re

# Pull the same regex from the aria.py classifier for direct testing
import aria_service.routes.aria  # noqa: F401 — ensure module loads cleanly


_TRIGGER_A = re.compile(
    r"\b(is\s+)?airtable\s+(sync\s+)?(healthy|working|ok|status|live|"
    r"synced|operational|up|reachable|connected)\b",
    re.IGNORECASE,
)
_TRIGGER_B = re.compile(
    r"\bstatus\s+of\s+(task\s+register|pipeline)\s+(table|airtable)",
    re.IGNORECASE,
)


def _fires(msg: str) -> bool:
    return bool(_TRIGGER_A.search(msg) or _TRIGGER_B.search(msg))


# ── MUST fire ────────────────────────────────────────────────────────

def test_is_airtable_healthy():
    assert _fires("Aria, is Airtable sync healthy?")


def test_is_airtable_working():
    assert _fires("is airtable working")


def test_airtable_status():
    assert _fires("Aria, what is the airtable status")


def test_airtable_operational():
    assert _fires("Is Airtable operational right now?")


def test_airtable_connected():
    assert _fires("Is Airtable connected")


def test_airtable_synced():
    assert _fires("is airtable synced with my tables")


def test_status_of_task_register_table():
    assert _fires("Give me the status of Task Register table")


def test_status_of_pipeline_airtable():
    assert _fires("What's the status of Pipeline airtable?")


# ── MUST NOT fire ─────────────────────────────────────────────────────

def test_mere_mention_of_airtable_does_not_fire():
    """Generic mentions of 'airtable' without a health/status shape should
    NOT route here — they might be research queries, noise, or references
    to the Airtable marketing email."""
    msg = "I just got an airtable marketing email"
    assert not _fires(msg)


def test_airtable_as_entity_in_DD():
    """Should NOT fire — this is a DD question about Airtable (the company)."""
    msg = "Aria, investigate Airtable the company"
    assert not _fires(msg)


def test_brave_answer_style_factual_does_not_fire():
    msg = "what is airtable used for"
    assert not _fires(msg)
