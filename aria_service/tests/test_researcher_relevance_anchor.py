"""Regression guard for the defence-anchor gate in article scoring.

Live incident 2026-04-27: a hotel-management paper containing
'billion-dollar deal in Angola' was selected for deep reading by the
autonomous research cycle. Cause: procurement and Lusophone boosts in
researcher.py fired on generic words (billion, deal, angola) without
requiring any defence-relevant anchor term.

Fix: gate the boosts on _DEFENCE_ANCHOR_TERMS membership.
"""
from __future__ import annotations

from aria_service.intel import researcher


def _score(title: str, description: str = "") -> int:
    """Replicate the inline scoring loop in research_and_learn() so we can
    test it without spinning up the full async cycle."""
    text = f"{title} {description}".lower()
    if not any(k in text for k in researcher._DEFENCE_ANCHOR_TERMS):
        return 0
    score = 0
    for interest in researcher.RESEARCH_INTERESTS:
        words = interest.lower().split()
        matches = sum(1 for w in words if w in text)
        if matches >= 2:
            score += matches * 2
    if any(k in text for k in ["tender", "contract", "procure", "award", "billion", "million", "deal"]):
        score += 5
    if any(c in text for c in ["angola", "mozambique", "guinea-bissau", "cape verde", "lusophone"]):
        score += 8
    if any(c in text for c in ["nigeria", "kenya", "saudi", "uae", "indonesia", "philippines", "poland"]):
        score += 3
    return score


def test_hotel_paper_in_angola_scores_zero():
    """The exact 2026-04-27 false positive: hospitality paper with no
    defence anchor should not pick up procurement/regional boosts."""
    title = "Hotel management excellence: a billion-dollar deal in Angola"
    description = "We award case studies on hospitality contract growth"
    assert _score(title, description) == 0


def test_defence_article_in_angola_still_scores_high():
    """Real defence article in Angola should still get the boosts."""
    title = "Angola signs $5bn defence procurement deal for fighter aircraft"
    description = "Military modernisation programme contract awarded to OEM"
    score = _score(title, description)
    assert score > 10, f"defence article should score high, got {score}"


def test_tourism_award_in_nigeria_scores_zero():
    title = "Nigeria wins tourism award for billion-naira hospitality contract"
    description = "Hotel sector deal celebrated"
    assert _score(title, description) == 0


def test_celebrity_news_with_million_scores_zero():
    title = "Celebrity signs million-dollar streaming deal in Saudi Arabia"
    description = "Award show contract announced"
    assert _score(title, description) == 0


def test_defence_minister_announcement_passes_anchor():
    """Anchored on 'defence' / 'military' / 'army' etc — boosts apply."""
    title = "UAE army procures new armoured vehicles in $2bn contract"
    description = "Award includes maintenance package"
    score = _score(title, description)
    assert score >= 8  # defence anchor + procurement + UAE


def test_anchor_terms_are_lowercase():
    """Sanity: scoring lowercases text before matching, so anchor terms must
    be lowercase. Catching this early avoids a silent always-False bug."""
    for term in researcher._DEFENCE_ANCHOR_TERMS:
        assert term == term.lower(), f"anchor term not lowercase: {term!r}"
