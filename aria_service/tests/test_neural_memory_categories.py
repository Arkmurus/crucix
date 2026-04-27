"""Regression guard for F25 — neural_memory valid categories.

Live noise 2026-04-27 19:40:21:
  [neural_memory] LLM returned unknown category 'concept' for concept
  'Affordable Mass'; remapping to 'general'

DeepSeek (and other LLMs) emit doctrinal / abstract category labels
that aren't in the curated set. Each unknown emission produces a noisy
INFO line and silently downgrades the entity to 'general', losing the
LLM's intended classification.

Fix: extend VALID_CATEGORIES to cover the categories LLMs actually
emit. None of these trigger conflict detection (which is gated to
person/organisation/oem) so adding them is structurally safe.
"""
from __future__ import annotations

from aria_service.intel.neural_memory import VALID_CATEGORIES


def test_concept_now_valid():
    """The exact 2026-04-27 19:40:21 LLM emission must be accepted."""
    assert "concept" in VALID_CATEGORIES


def test_other_llm_emitted_categories_valid():
    """Defensive coverage for similar LLM-emitted categories that
    routinely show up in defence-procurement extraction."""
    for cat in ("doctrine", "platform", "technology", "program"):
        assert cat in VALID_CATEGORIES, f"category {cat!r} should be valid"


def test_existing_categories_unchanged():
    """Original 8 categories from the curated set must still be valid."""
    for cat in ("market", "oem", "capability", "regulation",
                "event", "person", "organisation", "general"):
        assert cat in VALID_CATEGORIES


def test_truly_garbage_categories_still_rejected():
    """The validation gate is still useful — random LLM hallucinations
    that aren't in the broadened set should still be remapped."""
    for cat in ("foo", "bar", "xyz", "lorem ipsum", "random_label"):
        assert cat not in VALID_CATEGORIES
