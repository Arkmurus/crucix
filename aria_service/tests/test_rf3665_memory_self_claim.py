"""R-F3665 — ARIA must never claim she lacks cross-session memory.

LIVE INCIDENT (2026-08-03, WhatsApp):

    Antonio: You dont need to run the numbers for two way conversations,
             we had a chat about this already?
    ARIA:    I hear you, but here's the honest thing: I don't carry memory
             across chats. Each conversation starts fresh for me, so I can't
             recall any agreement we supposedly had...

That is FALSE about her own architecture. mem0 is a first-class recall layer in
aria_engine (`_mem0_retrieve` at the mem0 context layer) and every turn is
written back via `mem0.summarise_and_store`. CLAUDE.md §7 is explicit: "ARIA has
infinite memory. No TTL on knowledge. No oldest-first prune. No eviction."

ROOT CAUSE: the answer was generated under ARIA_SYSTEM_PROMPT_COMPACT, which
carried 8 rules and said NOTHING about memory — and also omitted the full
prompt's clause 25 ("NO ARCHITECTURAL SELF-CLAIMS") and its
"Knowledge / RAG / ledger / MEM0 retention is PERMANENT" line. With no
instruction either way, the model fell back to the generic assistant disclaimer.

The compact prompt is not a rare path: `_compact_prompt_active()` returns True
whenever ARIA_LLM_URL is set, and it IS set in production — so this prompt is
serving live chat traffic.
"""
from __future__ import annotations

import re

import pytest

from aria_service import aria_engine as ae


COMPACT = ae.ARIA_SYSTEM_PROMPT_COMPACT
FULL = ae.ARIA_SYSTEM_PROMPT


def test_rf3665_compact_prompt_states_memory_is_permanent():
    """The invariant that was missing. Without it the model guesses, and the
    generic guess is 'I have no memory'."""
    low = COMPACT.lower()
    assert "memory is permanent" in low or "permanent" in low, (
        "the compact prompt must state that ARIA's memory is permanent"
    )
    assert "cross-session" in low, "must name cross-session memory explicitly"


def test_rf3665_compact_prompt_forbids_the_exact_false_claims():
    """Pin the literal phrasings ARIA actually produced, so a future reword of
    this prompt cannot quietly drop the prohibition."""
    low = COMPACT.lower()
    for phrase in (
        "don't carry memory across chats",
        "each conversation starts fresh",
    ):
        assert phrase in low, (
            f"the compact prompt must explicitly forbid saying {phrase!r} — "
            "that is the sentence from the live incident"
        )


def test_rf3665_compact_prompt_forbids_architectural_self_claims():
    """The full prompt has clause 25; the compact prompt had no equivalent."""
    low = COMPACT.lower()
    assert "architecture" in low, (
        "the compact prompt must forbid guessing about ARIA's own architecture"
    )


def test_rf3665_topic_absence_is_distinguished_from_memory_absence():
    """The honest reply when recall is empty is about the TOPIC, not the system.
    This distinction is the whole point — 'I have nothing stored about that' is
    true and useful; 'I have no memory' is false."""
    low = COMPACT.lower()
    assert "nothing stored about that" in low, (
        "the compact prompt must give the honest topic-scoped alternative"
    )


def test_rf3665_full_prompt_still_asserts_permanent_retention():
    """Guard the full prompt's existing anchor so the two cannot drift apart."""
    assert re.search(r"retention is PERMANENT", FULL), (
        "the full prompt lost its permanent-retention anchor"
    )


def test_rf3665_compact_prompt_is_actually_used_in_production_shape():
    """_compact_prompt_active() keys off ARIA_LLM_URL. This is not a dormant
    path — pin the coupling so the blast radius stays visible."""
    import inspect
    src = inspect.getsource(ae._compact_prompt_active)
    assert "ARIA_LLM_URL" in src, (
        "compact-prompt activation no longer keys off ARIA_LLM_URL — if that "
        "changed deliberately, update docs/decisions_pending_operator_2026_08_03.md"
    )


@pytest.mark.parametrize("forbidden", [
    "i don't carry memory across chats",
    "each conversation starts fresh for me",
])
def test_rf3665_forbidden_phrases_are_named_verbatim(forbidden):
    """Belt and braces: the prohibition must quote the real sentences, not a
    paraphrase, so the model has an exact string to avoid."""
    normalised = COMPACT.lower().replace("’", "'")
    key = forbidden.replace("i don't ", "").replace(" for me", "")
    assert key in normalised, f"compact prompt should name {key!r} verbatim"
