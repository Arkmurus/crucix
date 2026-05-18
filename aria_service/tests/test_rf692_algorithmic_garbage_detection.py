"""R-F692 (2026-05-18) — algorithmic garbage detection + allow-list.

Replaces the whack-a-mole blocklist expansion (R-F687/689/691 added
60+ labels in 30 min of evidence and counting). Any tier-4 +
sector=discovered label matching ^[a-z]{3,10}$ (lowercase ASCII,
no hyphen/digit, 3-10 chars) is treated as garbage unless on the
legitimate-entity allow-list.

Real defence-industry compound labels are either ≥11 chars
(rheinmetall, lockheedmartin, defencenews, modirumgespi) or contain
hyphens / digits / subdomains — so they don't match the pattern.

The allow-list catches the short legitimate single-word brands that
the pattern WOULD match but should never be filtered: baykar,
embraer, thales, saab, drdo, iai, kai, etc.
"""
from __future__ import annotations


def test_rf692_unknown_short_lowercase_label_is_blocked():
    """The point of the algorithmic check: previously-unseen labels
    that match the garbage shape are now blocked WITHOUT needing
    explicit blocklist additions."""
    from aria_service.crawler import on_demand
    # None of these are in the explicit blocklist as of R-F691 —
    # the algorithmic check is what catches them.
    for new_label in ("naval", "tank", "rocket", "missile", "ship",
                      "frigate", "stealth", "radar", "sonar", "trade"):
        row = {"domain": f"{new_label}.com", "tier": 4, "sector": "discovered"}
        assert on_demand.is_auto_registered_garbage(row), (
            f"R-F692: algorithmic catch missed `{new_label}`"
        )


def test_rf692_legitimate_short_brand_passes_via_allow_list():
    """Single-word defence-industry brand names that match the regex
    must pass thanks to the allow-list."""
    from aria_service.crawler import on_demand
    for brand in ("baykar", "embraer", "thales", "saab", "drdo",
                  "iai", "kai", "modirum", "qinetiq", "hanwha"):
        row = {"domain": f"{brand}.com", "tier": 4, "sector": "discovered"}
        assert not on_demand.is_auto_registered_garbage(row), (
            f"R-F692: legitimate brand `{brand}` wrongly filtered"
        )


def test_rf692_long_single_word_compound_labels_pass():
    """Labels >8 chars without hyphens don't match the single-word
    regex → not flagged. (R-F698 separately handles hyphenated
    compounds where ALL tokens are blocklisted — see
    test_rf698_hyphenated_all_blocklisted_tokens_is_garbage.)"""
    from aria_service.crawler import on_demand
    for legit in ("rheinmetall", "lockheedmartin", "defencenews",
                  "modirumgespi", "defenseindustrialbase"):
        row = {"domain": f"{legit}.com", "tier": 4, "sector": "discovered"}
        assert not on_demand.is_auto_registered_garbage(row), (
            f"R-F692: long compound `{legit}` wrongly filtered"
        )


def test_rf692_hyphenated_labels_with_legit_brand_pass():
    """Hyphenated compounds where AT LEAST ONE token is on the
    legitimate-brand allow-list (R-F698 rescue) must pass through.
    Bare common-noun-only compounds are caught by R-F698; this test
    just confirms the rescue path for hyphenated brand pairs."""
    from aria_service.crawler import on_demand
    for legit in (
        "baykar-savunma.com",      # baykar = OEM
        "thales-uk.com",           # thales = OEM
        "embraer-defence.com",     # embraer = OEM
        "modirum-systems.com",     # modirum = operator example
    ):
        row = {"domain": legit, "tier": 4, "sector": "discovered"}
        assert not on_demand.is_auto_registered_garbage(row), (
            f"R-F692: hyphenated brand `{legit}` wrongly filtered"
        )


def test_rf692_labels_with_digits_pass():
    """Labels with digits don't match the strict regex → not flagged."""
    from aria_service.crawler import on_demand
    for legit in ("dsm2024.com", "tank42.org", "tb2drone.net"):
        row = {"domain": legit, "tier": 4, "sector": "discovered"}
        assert not on_demand.is_auto_registered_garbage(row), (
            f"R-F692: digit-containing `{legit}` wrongly filtered"
        )


def test_rf692_explicit_blocklist_still_takes_precedence():
    """R-F687/689/691 entries in the explicit blocklist still hard-block
    even if the label would otherwise pass via the algorithmic gate."""
    from aria_service.crawler import on_demand
    for explicit in ("cisco", "billion", "drone", "defence",
                     "intelligence", "korea", "japan", "military"):
        row = {"domain": f"{explicit}.com", "tier": 4, "sector": "discovered"}
        assert on_demand.is_auto_registered_garbage(row), (
            f"R-F692 regression: explicit blocklist label `{explicit}` lost"
        )


def test_rf692_tier_and_sector_gate_still_first_line_of_defence():
    """The tier-4 + sector=discovered conditions are still required —
    operator-curated rows (any tier ≤ 3) are NEVER filtered, even if
    they match the algorithmic garbage pattern."""
    from aria_service.crawler import on_demand
    # A short common-noun label that would otherwise be blocked.
    for tier in (1, 2, 3):
        row = {"domain": "naval.com", "tier": tier, "sector": "discovered"}
        assert not on_demand.is_auto_registered_garbage(row), (
            f"R-F692: tier-{tier} row wrongly filtered"
        )
    # Same label at tier-4 with non-discovered sector also passes
    # (operator-set sector implies operator intent).
    row = {"domain": "naval.com", "tier": 4, "sector": "publication"}
    assert not on_demand.is_auto_registered_garbage(row)


def test_rf692_short_labels_under_3_chars_pass():
    """The regex requires 3-10 chars — 2-char labels don't match.
    (Two-letter labels are usually country-code TLD shorts, almost
    never the hallucination pattern.)"""
    from aria_service.crawler import on_demand
    for short in ("ai", "io", "co", "uk"):
        row = {"domain": f"{short}.example.com", "tier": 4, "sector": "discovered"}
        # Label here is "ai", "io", etc. — not enough chars to match
        assert not on_demand.is_auto_registered_garbage(row), (
            f"R-F692: 2-char label `{short}` wrongly filtered"
        )


def test_rf692_uppercase_labels_normalised_to_lowercase():
    """Domains are lowercased before matching — `INTELLIGENCE.com`
    gets blocked just like `intelligence.com`."""
    from aria_service.crawler import on_demand
    row = {"domain": "INTELLIGENCE.com", "tier": 4, "sector": "discovered"}
    assert on_demand.is_auto_registered_garbage(row)
