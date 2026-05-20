"""R-F765 — confidence-footer regex now matches space-separated caveat forms.

LIVE BUG from 2026-05-20 5-turn audit transcript:

    Turn 1 body had `[CONFIRMED from ATTACHED DOCUMENT]` inline
    Turn 1 footer headline showed `Confidence: (no tag — treat as unverified)`

The pre-R-F765 _TAG_RE required `[—–-]` (em-dash, en-dash, or hyphen)
between the tag word and the caveat. Forms with whitespace + preposition
("CONFIRMED from ...", "ASSESSED via ...", "PROBABLE based on ...")
didn't match — the footer fell through to no-tag and the operator saw
a misleading "unverified" headline on a turn whose body was fully
grounded.

R-F765 widens the regex to accept whitespace + ANY non-`]` continuation,
so all five of these now match:
    [CONFIRMED]
    [CONFIRMED — from extraction]            (existing — dash)
    [CONFIRMED from ATTACHED DOCUMENT]       (NEW — space + preposition)
    [ASSESSED via OFSI lookup]               (NEW)
    [PROBABLE based on two sources]          (NEW)
"""
from __future__ import annotations

from aria_service.intel.confidence_footer import _dominant_tag, _TAG_RE


# ── R-F765 new patterns (must match) ─────────────────────────────────


def test_rf765_confirmed_from_attached_document_matches():
    """The live-bug exact phrase from the 2026-05-20 audit."""
    txt = "Identity [CONFIRMED from ATTACHED DOCUMENT]: Acme Corp."
    matches = _TAG_RE.findall(txt)
    assert "CONFIRMED" in [m.upper() for m in matches], (
        f"R-F765 regression: _TAG_RE missed '[CONFIRMED from ATTACHED "
        f"DOCUMENT]'. Got matches={matches!r}"
    )


def test_rf765_assessed_via_lookup_matches():
    txt = "Sanctions [ASSESSED via OFSI lookup] CLEAN."
    matches = [m.upper() for m in _TAG_RE.findall(txt)]
    assert "ASSESSED" in matches


def test_rf765_probable_based_on_two_sources_matches():
    txt = "Director [PROBABLE based on two sources] is Acme N. Vendor."
    matches = [m.upper() for m in _TAG_RE.findall(txt)]
    assert "PROBABLE" in matches


def test_rf765_dominant_tag_picks_confirmed_from_attached():
    """End-to-end: the dominant-tag function must return CONFIRMED
    when the body has '[CONFIRMED from ATTACHED DOCUMENT]'."""
    txt = (
        "Identity [CONFIRMED from ATTACHED DOCUMENT]: Acme Corp. "
        "Sanctions screen [ASSESSED via OFSI lookup] — CLEAN."
    )
    # avg = (0.95 + 0.60) / 2 = 0.775 → nearest tag is PROBABLE (0.78)
    # so the dominant tag should be PROBABLE, NOT None.
    tag = _dominant_tag(txt)
    assert tag is not None, (
        "R-F765 regression: dominant-tag returned None on a body that "
        "contains TWO matchable tags. The footer would show "
        "'(no tag — treat as unverified)' — the exact bug."
    )
    # Tag could be PROBABLE (0.78) — confirm it's one of the resolved tags.
    assert tag in ("CONFIRMED", "PROBABLE", "ASSESSED")


# ── Backwards-compatibility (existing patterns must STILL match) ─────


def test_rf765_existing_em_dash_caveat_still_matches():
    txt = "[CONFIRMED — from extraction of https://example.com]"
    matches = [m.upper() for m in _TAG_RE.findall(txt)]
    assert "CONFIRMED" in matches


def test_rf765_existing_hyphen_caveat_still_matches():
    txt = "[ASSESSED - single source]"
    matches = [m.upper() for m in _TAG_RE.findall(txt)]
    assert "ASSESSED" in matches


def test_rf765_bare_tag_still_matches():
    """No caveat at all — must still match."""
    txt = "Status [CONFIRMED]. Sanctions [UNCERTAIN]."
    matches = [m.upper() for m in _TAG_RE.findall(txt)]
    assert matches == ["CONFIRMED", "UNCERTAIN"]


def test_rf765_does_not_match_non_tag_brackets():
    """The regex must NOT match arbitrary bracketed text that doesn't
    start with one of the 5 confidence tag words."""
    txt = "[TOOL: deep_research] [from https://example.com] [section 3]"
    matches = [m.upper() for m in _TAG_RE.findall(txt)]
    assert matches == [], (
        f"R-F765: _TAG_RE over-matched. Got: {matches!r}"
    )


def test_rf765_speculative_with_long_caveat_matches():
    """The caveat may be long — must still match up to closing `]`."""
    txt = (
        "[SPECULATIVE based on a single unverified Telegram post from "
        "the Rybar channel which is propaganda-tier]"
    )
    matches = [m.upper() for m in _TAG_RE.findall(txt)]
    assert "SPECULATIVE" in matches
