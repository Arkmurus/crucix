"""Regression guard for Batch C researcher pipeline correctness.

F9 — short anchors substring-matched in unrelated words. The 2026-04-27
train-crash article passed the defence-anchor gate because its
description contained 'disruption' (substring 'isr' matched).

F10 — validate_hypothesis sent 200+ char descriptions verbatim as
search queries. F22 fix already short-circuits paywalled URLs; this
fix prevents the long-query waste in the first place.

F23 — _process_analysis upserted facts one at a time, each triggering
its own sentence-transformer batch. New rag_store.add_facts_batch
collapses N encodes into 1.
"""
from __future__ import annotations

from aria_service.intel import researcher


# ── F9: anchor false-positives in unrelated words ─────────────────────────

def test_train_crash_disruption_does_NOT_pass_anchor():
    """The exact 2026-04-27 18:30:21 false positive — 'disruption' in
    a train-crash article must not trip the defence anchor."""
    text = "4 killed and dozens injured in train crash outside Jakarta. " \
           "Service disruption affects commuters across the network."
    assert not researcher._has_defence_anchor(text), (
        "anchor false-positive: 'disruption' contains 'isr' substring "
        "but the article has zero defence content"
    )


def test_modern_NOT_anchor_match():
    """Original list had ' mod' which substring-matched 'modern' / 'module'."""
    text = "The modern transportation system in Jakarta is undergoing modernisation."
    assert not researcher._has_defence_anchor(text)


def test_disregard_NOT_anchor_match():
    text = "The court ruling on liability disregards the witness testimony."
    assert not researcher._has_defence_anchor(text)


def test_misrepresentation_NOT_anchor_match():
    text = "Customer misrepresentation in financial disclosures is rising."
    assert not researcher._has_defence_anchor(text)


def test_thank_NOT_anchor_match():
    """'tank' was on the substring list — would match 'thank'."""
    text = "Thank you for your service, said the mayor."
    assert not researcher._has_defence_anchor(text)


# ── F9: real defence terms still match ─────────────────────────────────────

def test_real_defence_articles_still_pass_anchor():
    cases = [
        "Angola signs $5bn defence procurement deal for fighter aircraft",
        "Saudi Arabia received new tanks from Turkey under FMS agreement",
        "NATO STANAG-4569 armoured vehicle protection levels reviewed",
        "Indonesia army deploys howitzers along eastern border",
        "Russian military deploys Su-35 fighters to Syria",
        "ISR drone capability added to UK MOD inventory",
        "Combat-proven UCAV ordered by Saudi air force",
        "Tactical deployment of NATO troops to Eastern Europe",
        "C4ISR upgrades to Naval frigate fleet",
    ]
    for text in cases:
        assert researcher._has_defence_anchor(text), f"missed: {text!r}"


def test_isr_word_bounded_only():
    """Standalone 'ISR' (the abbreviation) must match. 'disruption' must not."""
    assert researcher._has_defence_anchor("New ISR satellite deployed")
    assert not researcher._has_defence_anchor("Service disruption widespread")


def test_mod_word_bounded_only():
    assert researcher._has_defence_anchor("UK MOD signs new contract")
    assert not researcher._has_defence_anchor("Modern apartment block in Jakarta")


def test_arms_word_bounded_only():
    assert researcher._has_defence_anchor("New arms embargo on Russia announced")
    # 'farmstand' contains 'arms' as substring — must not match
    assert not researcher._has_defence_anchor("Local farmstand offers organic produce")


# ── F10: hypothesis validation query truncation ────────────────────────────

def test_shorten_for_search_keeps_short_text():
    s = "Boeing 737 MAX certification issues"
    assert researcher._shorten_for_search(s, max_chars=60) == s


def test_shorten_for_search_cuts_on_word_boundary():
    long = ("The Measure Group Europe LinkedIn page shows negligible engagement "
            "and no defence content, suggesting it is either a non-defence entity, "
            "a shell page, or a very small inactive company.")
    out = researcher._shorten_for_search(long, max_chars=60)
    assert len(out) <= 60
    assert " " not in out[-1:]   # last char isn't a space
    assert not out.endswith(",")
    assert not out.endswith(".")


def test_shorten_for_search_strips_punctuation():
    """Quotes and sentence punctuation confuse search APIs."""
    s = '"ALADA\'s full legal identity, country of incorporation, or man"'
    out = researcher._shorten_for_search(s, max_chars=80)
    assert "'" not in out
    assert '"' not in out
    assert "," not in out


def test_shorten_for_search_handles_empty():
    assert researcher._shorten_for_search("") == ""
    assert researcher._shorten_for_search("   ") == ""


# ── F23: rag_store.add_facts_batch ─────────────────────────────────────────

def test_add_facts_batch_signature_exists():
    """Validate the batch helper exists and accepts the documented shape.
    Exercising the chromadb upsert path requires the persistent volume
    so this is a contract check only."""
    from aria_service.intel import rag_store
    assert hasattr(rag_store, "add_facts_batch")
    import inspect
    sig = inspect.signature(rag_store.add_facts_batch)
    assert "facts" in sig.parameters


def test_add_facts_batch_empty_returns_zero():
    """Empty input must not error or hit chromadb."""
    import asyncio
    from aria_service.intel import rag_store
    result = asyncio.run(rag_store.add_facts_batch([]))
    assert result == 0
