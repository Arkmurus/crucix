"""R-F698 (2026-05-18) — hyphenated common-noun compound garbage filter.

Live fly logs 2026-05-18 17:59:40 showed `competitive-intelligence.com`
slipping past both:
  - R-F692 algorithmic check (regex ^[a-z]{3,8}$ rejected hyphens)
  - R-F687/689/691 explicit blocklist (had `counter-intelligence` but
    not `competitive-intelligence`)

The breaker tripped on absorb(web_atlas) at p95=5533ms, triggering a
PR04 cascade at 18:00:43 — same wedge pattern R-F687 was meant to
prevent.

R-F698 adds a second regex for hyphenated compounds of two 3-8-char
lowercase tokens. Either-half-is-legitimate-brand exception lets
`baykar-savunma`, `aselsan-roketsan` etc. pass.
"""
from __future__ import annotations


def test_rf698_hyphenated_all_blocklisted_tokens_is_garbage():
    """When EVERY token in a hyphenated label is on the explicit
    blocklist, the compound is garbage. Conservative rule — avoids
    false-positives on legit thinktanks where only some tokens are
    common nouns."""
    from aria_service.crawler import on_demand
    # All tokens below must be in _GARBAGE_COMMON_NOUN_LABELS:
    # competitive, intelligence (R-F687/689)
    # general, dynamics (R-F687)
    # defence, news (R-F687)
    # killing (R-F689)
    # military (R-F691)
    for label in (
        "competitive-intelligence",  # live evidence 17:59:40
        "general-dynamics",          # both blocklisted
        "defence-news",              # both blocklisted
        "killing-news",              # both blocklisted
        "military-defence",          # both blocklisted
        "drone-news",                # both blocklisted
        "intelligence-news",         # both blocklisted
    ):
        row = {"domain": f"{label}.com", "tier": 4, "sector": "discovered"}
        assert on_demand.is_auto_registered_garbage(row), (
            f"R-F698: hyphenated garbage `{label}` not filtered"
        )


def test_rf698_hyphenated_with_legit_brand_half_passes():
    """Hyphenated compound where ONE half is a known legitimate brand
    must NOT be filtered — these are real OEM joint-venture or
    sub-brand domains."""
    from aria_service.crawler import on_demand
    for legit in (
        "baykar-savunma.com",      # baykar = OEM
        "aselsan-defence.com",     # aselsan = OEM
        "modirum-systems.com",     # modirum = operator example
        "thales-uk.com",           # thales = OEM
        "embraer-defence.com",     # embraer = OEM
        "leonardo-helicopters.com",  # leonardo = OEM
    ):
        row = {"domain": legit, "tier": 4, "sector": "discovered"}
        assert not on_demand.is_auto_registered_garbage(row), (
            f"R-F698: legit hyphenated `{legit}` wrongly filtered"
        )


def test_rf698_hyphenated_three_or_more_parts_pass():
    """Three-part-or-more hyphenated labels don't match the strict
    2-token regex — pass through. (These tend to be more specific
    and less likely to be hallucination shape.)"""
    from aria_service.crawler import on_demand
    for legit in (
        "east-african-policy.org",   # 3 parts
        "anti-money-laundering.eu",  # 3 parts
        "south-china-monitor.com",   # 3 parts
    ):
        row = {"domain": legit, "tier": 4, "sector": "discovered"}
        assert not on_demand.is_auto_registered_garbage(row), (
            f"R-F698: 3-part compound `{legit}` wrongly filtered"
        )


def test_rf698_hyphen_with_long_half_passes():
    """If either half is >8 chars, the regex doesn't match — pass
    through. (Long compounds are usually intentional naming, not
    hallucination shape.)"""
    from aria_service.crawler import on_demand
    for legit in (
        "international-defence.org",  # 13-char left half
        "competitive-intelligenceanalysis.com",  # 25-char right
        "defenseweb-corp.za",         # 10-char left half
    ):
        row = {"domain": legit, "tier": 4, "sector": "discovered"}
        assert not on_demand.is_auto_registered_garbage(row), (
            f"R-F698: long-half compound `{legit}` wrongly filtered"
        )


def test_rf698_single_word_filter_still_works():
    """Regression guard — R-F692's single-word filter still flags
    short common-noun labels."""
    from aria_service.crawler import on_demand
    for label in ("naval", "rocket", "missile", "tank"):
        row = {"domain": f"{label}.com", "tier": 4, "sector": "discovered"}
        assert on_demand.is_auto_registered_garbage(row), (
            f"R-F698 regression: single-word `{label}` lost"
        )


def test_rf698_explicit_blocklist_still_wins():
    """Explicit blocklist still takes precedence over algorithmic check."""
    from aria_service.crawler import on_demand
    for label in ("cisco", "drone", "intelligence"):
        row = {"domain": f"{label}.com", "tier": 4, "sector": "discovered"}
        assert on_demand.is_auto_registered_garbage(row), (
            f"R-F698 regression: explicit blocklist `{label}` lost"
        )


def test_rf698_tier_3_compound_passes_regardless():
    """Tier-3 (operator-curated) hyphenated compound is never filtered."""
    from aria_service.crawler import on_demand
    row = {
        "domain": "competitive-intelligence.com",
        "tier": 1,
        "sector": "publication",
    }
    assert not on_demand.is_auto_registered_garbage(row), (
        "R-F698: tier-1 row wrongly filtered"
    )
