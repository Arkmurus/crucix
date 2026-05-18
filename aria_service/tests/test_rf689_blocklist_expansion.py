"""R-F689 (2026-05-18) — _GARBAGE_COMMON_NOUN_LABELS expansion.

Live fly logs 2026-05-18 10:35-10:50 (alphabetical crawl sweep)
revealed labels in the auto-registered registry that R-F687's
initial blocklist didn't catch:
  - Generic English nouns: establishment, executive, expedite, export,
    failed, financial, finnish, fire, fiscal, funding, further,
    general, generic, government, hypothesis, key, killing
  - Country/region single-words: india, indo, japan, korea (already
    had koreas; korea is the alphabetic-adjacent variant)
  - Acronyms / generic tech: ieee, informa, intelligence
  - Geopolitical adjectives: european, geopolitical
  - Misc compounds-that-act-as-singles: fujimoto, interpols

R-F689 adds them. R-F687's three-condition filter (tier=4 +
sector=discovered + label-in-blocklist) is unchanged — only the
data set grows.
"""
from __future__ import annotations


def test_rf689_new_live_evidence_labels_in_blocklist():
    from aria_service.crawler import on_demand
    new_labels = [
        # Generic English nouns
        "establishment", "executive", "expedite", "export",
        "failed", "financial", "finnish", "fire", "fiscal",
        "funding", "further", "general", "generic", "government",
        "hypothesis", "key", "killing",
        # Country / region singles
        "india", "indo", "japan", "korea",
        # Acronyms / generic tech
        "ieee", "informa", "intelligence",
        # Geopolitical adjectives
        "european", "geopolitical",
        # Misc
        "fujimoto", "interpols",
    ]
    for lbl in new_labels:
        assert lbl in on_demand._GARBAGE_COMMON_NOUN_LABELS, (
            f"R-F689: expected `{lbl}` in blocklist"
        )


def test_rf689_filter_blocks_new_live_evidence():
    """End-to-end: the new labels at tier-4 + discovered are filtered."""
    from aria_service.crawler import on_demand
    for label in [
        "japan", "korea", "india", "indo", "intelligence", "ieee",
        "establishment", "executive", "export", "failed", "financial",
        "fire", "funding", "general", "government", "key", "killing",
    ]:
        for tld in (".com", ".co", ".net", ".org", ".com.br", ".pt"):
            row = {
                "domain": f"{label}{tld}",
                "tier": 4,
                "sector": "discovered",
            }
            assert on_demand.is_auto_registered_garbage(row), (
                f"R-F689: `{label}{tld}` not filtered"
            )


def test_rf689_compound_labels_still_pass():
    """Long compound labels (legit defence / publication domains) must
    NOT be filtered, even when one of their stem-words is in the
    blocklist — only the FULL leftmost label is checked.

    Note: R-F692 added an algorithmic filter for short single-word
    labels (3-8 chars). Genuine compounds tend to be longer; the
    examples below are all ≥9 chars or contain hyphens.

    `iranuae.com` was removed from this test — log evidence showed it
    redirects to `ai.ad.bio/make-an-offer.html` (parked for sale), so
    R-F692 correctly flags it as garbage. `fegulf.com` was removed
    for the same reason (also parked / unverified).
    """
    from aria_service.crawler import on_demand
    for legit in (
        # These have blocklisted STEMS but long compound labels (≥9 chars):
        "executivegov.com",         # 12 chars, not just "executive"
        "internationalintelligence.com",  # 25 chars
        "indianmasterminds.com",    # 17 chars
        "indoasiadefense.com",      # 15 chars
        "koreajoongangdaily.joins.com",  # 18-char left-most label
        # Hyphenated compounds with at least one legit brand or long token:
        "koreaplus-lifes.com",      # left half 9 chars (>8) — passes regex
        "baykar-savunma.com",       # left = OEM brand
    ):
        row = {"domain": legit, "tier": 4, "sector": "discovered"}
        assert not on_demand.is_auto_registered_garbage(row), (
            f"R-F689: legitimate compound `{legit}` wrongly filtered"
        )


def test_rf689_original_labels_still_blocked():
    """Regression guard — R-F687's original blocklist entries still work."""
    from aria_service.crawler import on_demand
    for original in (
        "billion", "cisco", "defence", "drone", "email",
        "philippines", "indonesia", "koreas",
    ):
        row = {"domain": f"{original}.com", "tier": 4, "sector": "discovered"}
        assert on_demand.is_auto_registered_garbage(row), (
            f"R-F689 regression: original label `{original}` lost"
        )
