"""R-F392 — deep_research search-anchor extraction.

Live evidence 2026-05-13 ~06:53 / 06:59 UTC: ARIA self-reported that
deep_research's "single most damaging operational failure" was
searching the entire user sentence as a raw query. Four investigations
(ADS-Saudi network, Arnaldo La Scala, Swisscraft network, Luke Oil
network) returned zero relevant results because the search query was
the full instruction string ("we need a full DD on …", "investigate
the network of …") rather than the entity name.

Fix: `_extract_search_anchor(topic)` runs before the two fallback
paths in `investigate()` (LLM-returned-empty and LLM-threw). It
reuses `query_decomposer._extract_entities()` first, then falls back
to a regex strip of ARIA-voice imperatives + question words + 8-word
truncation.

These tests pin the anchor extraction. The function MUST:
  - Extract proper-noun entities from imperative voice commands
  - Strip greeting prefixes ("Aria, …", "please …")
  - Strip imperative prefixes ("run a full DD on", "give me a report on")
  - Strip leading question words
  - Cap output at 8 words even when no entity is found
  - Preserve direct entity inputs (no over-stripping)
  - Never return the full original sentence when the topic is long
"""
from __future__ import annotations

from aria_service.intel.deep_researcher import _extract_search_anchor


# ── Named-entity extraction (the four ARIA-flagged failure cases) ─────

def test_rf392_extracts_lukoil_from_imperative():
    """The 2026-05-12 Lukoil DD failure case."""
    result = _extract_search_anchor(
        "Aria, run a full DD on Lukoil Neftochim in Burgas. "
        "A contact there, a client, with tank allocations"
    )
    # query_decomposer matches "Lukoil Neftochim" via _CAPS_ENTITY
    assert "Lukoil" in result, (
        f"R-F392 regression: anchor must contain 'Lukoil', got {result!r}"
    )
    # And must NOT contain the imperative
    assert "run a full DD" not in result.lower(), (
        f"R-F392 regression: anchor still contains imperative: {result!r}"
    )


def test_rf392_extracts_arnaldo_la_scala():
    """The Arnaldo La Scala investigation that returned 0 results."""
    result = _extract_search_anchor(
        "deep investigation of Arnaldo Renato Matteo La Scala please"
    )
    assert "Arnaldo" in result, (
        f"R-F392 regression: anchor must contain 'Arnaldo', got {result!r}"
    )
    assert "deep investigation" not in result.lower()


def test_rf392_extracts_swisscraft():
    """The Swisscraft network investigation."""
    result = _extract_search_anchor(
        "we need a full investigation on the Swisscraft Zagaria network"
    )
    assert "Swisscraft" in result, (
        f"R-F392 regression: anchor must contain 'Swisscraft', got {result!r}"
    )


# ── Country extraction when no named entity present ──────────────────

def test_rf392_extracts_country_when_no_named_entity():
    """When no proper-noun company/person exists but a country does,
    use the country as the anchor."""
    result = _extract_search_anchor("what defence imports has Saudi Arabia made last year")
    assert "Saudi Arabia" in result, (
        f"R-F392: anchor should be 'Saudi Arabia' (country match), got {result!r}"
    )


# ── Direct entity passed in — must not be over-stripped ──────────────

def test_rf392_preserves_direct_entity():
    """If the user passes the entity name directly, return it unchanged."""
    assert _extract_search_anchor("Lukoil") == "Lukoil"
    assert _extract_search_anchor("Rheinmetall") == "Rheinmetall"


def test_rf392_preserves_multi_word_direct_entity():
    """'Arnaldo La Scala' passed directly must not be stripped."""
    result = _extract_search_anchor("Arnaldo La Scala")
    assert "Arnaldo" in result
    assert "La Scala" in result


# ── Question-word stripping (the brave_answer / Saudi case) ─────────

def test_rf392_strips_leading_question_words():
    """'What has improved' / 'how is X' patterns must be reduced."""
    result = _extract_search_anchor(
        "What has improved in the system over this conversation"
    )
    # Either the anchor strips "what has", or it found a proper noun.
    # Either way, the full sentence must not survive intact.
    assert result != (
        "What has improved in the system over this conversation"
    ), "R-F392 regression: question still passed verbatim"
    assert "what has" not in result.lower()


# ── 8-word truncation when no entity found ─────────────────────────

def test_rf392_truncates_long_no_entity_sentence():
    """A long sentence with no proper noun or country must be capped
    at 8 words — not passed in full to the search backend."""
    long_topic = (
        "tell me everything about the situation and the various "
        "factors influencing the outcome and the implications for "
        "the team going forward"
    )
    result = _extract_search_anchor(long_topic)
    assert len(result.split()) <= 8, (
        f"R-F392: anchor must be ≤ 8 words, got {len(result.split())}: {result!r}"
    )


# ── Empty / whitespace inputs handled safely ────────────────────────

def test_rf392_empty_input_returns_empty():
    assert _extract_search_anchor("") == ""
    assert _extract_search_anchor("   ") == "   " or _extract_search_anchor("   ").strip() == ""


def test_rf392_never_returns_none():
    """Defensive: function must never return None on any input."""
    for inp in ["", "a", "Aria,", "the the the", "What", "Lukoil"]:
        assert _extract_search_anchor(inp) is not None


# ── Regression guard: the four ARIA-flagged failures never pass through ─

def test_rf392_none_of_arias_four_failures_pass_through_full_sentence():
    """The single most important guarantee: none of the four investigations
    ARIA flagged as having failed must result in the full user sentence
    being used as the search query."""
    failures = [
        "Aria, do a deep investigation on the ADS-Saudi network",
        "we need a full DD on Arnaldo Renato Matteo La Scala please",
        "investigate the Swisscraft Zagaria network and its links",
        "find the network of people linked to Luke Oil Panama",
    ]
    for full_sentence in failures:
        anchor = _extract_search_anchor(full_sentence)
        assert anchor != full_sentence, (
            f"R-F392 regression: full sentence passed through unchanged: "
            f"{full_sentence!r}"
        )
        # Anchor must be meaningfully shorter than the full sentence.
        assert len(anchor) < len(full_sentence) * 0.9, (
            f"R-F392: anchor barely shorter than full sentence — "
            f"check stripping: {anchor!r} vs {full_sentence!r}"
        )
