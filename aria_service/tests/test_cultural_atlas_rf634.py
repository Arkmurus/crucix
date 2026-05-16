"""R-F634 — Cultural atlas (country × dimension knowledge layer).

Foundation pins for the cultural-intelligence cluster (R-F634-F637).
Tests verify:
  - Seeded countries present with non-trivial profiles
  - Hofstede dimensions in 0-100 range when set
  - Context-block renderer produces honest PROBABLE tag
  - Style + negotiation suggestions surface key cultural signals
  - pair_compare highlights large gaps for cross-cultural deals
  - All public-API functions handle bad inputs defensively
"""
from __future__ import annotations


# ══════════════════════════════════════════════════════════════════
# PART A — seeded coverage
# ══════════════════════════════════════════════════════════════════

def test_rf634_cplp_priority_countries_seeded():
    """CPLP / Lusophone Africa is ARIA's INCUMBENT tier — must be in
    the seed."""
    from aria_service.intel.cultural_atlas import get
    for iso2 in ("AO", "MZ", "PT", "BR", "CV", "GW", "ST"):
        p = get(iso2)
        assert p is not None, f"R-F634: CPLP country {iso2} missing from seed"
        assert p.name


def test_rf634_gcc_countries_seeded():
    """GCC + Levant — Cold Entry tier where cultural intel matters most."""
    from aria_service.intel.cultural_atlas import get
    for iso2 in ("SA", "AE", "QA", "KW", "BH", "OM", "JO", "EG"):
        assert get(iso2) is not None, f"R-F634: GCC/Levant {iso2} missing"


def test_rf634_major_nato_seeded():
    from aria_service.intel.cultural_atlas import get
    for iso2 in ("GB", "US", "FR", "DE", "IT", "ES", "PL", "TR", "GR", "NL"):
        assert get(iso2) is not None, f"R-F634: NATO partner {iso2} missing"


def test_rf634_other_priority_seeded():
    from aria_service.intel.cultural_atlas import get
    for iso2 in ("NG", "KE", "ZA", "MA", "IN", "ID", "RU", "CN", "SG", "IL"):
        assert get(iso2) is not None, f"R-F634: {iso2} missing"


def test_rf634_list_countries_returns_at_least_25():
    """Sanity check: seed coverage is large enough to be useful."""
    from aria_service.intel.cultural_atlas import list_countries
    assert len(list_countries()) >= 25


def test_rf634_hofstede_scores_in_valid_range():
    """All Hofstede dimensions must be 0-100 when set."""
    from aria_service.intel.cultural_atlas import _COUNTRIES
    for iso2, p in _COUNTRIES.items():
        for dim in ("pdi", "idv", "mas", "uai", "lto", "ivr"):
            val = getattr(p, dim)
            if val is None:
                continue
            assert 0 <= val <= 100, (
                f"R-F634: {iso2}.{dim} = {val} outside 0-100"
            )


# ══════════════════════════════════════════════════════════════════
# PART B — context block renderer
# ══════════════════════════════════════════════════════════════════

def test_rf634_render_context_block_brief_includes_country_name():
    from aria_service.intel.cultural_atlas import render_context_block
    block = render_context_block("SA", depth="brief")
    assert "Saudi Arabia" in block
    assert "[CULTURAL CONTEXT" in block


def test_rf634_render_context_block_tags_probable():
    """Clause 1 (epistemic honesty): cultural summaries are population-
    level, not deterministic about individuals. The block MUST flag this."""
    from aria_service.intel.cultural_atlas import render_context_block
    block = render_context_block("SA", depth="brief")
    assert "PROBABLE" in block
    assert "population-level" in block.lower() or "not deterministic" in block.lower()


def test_rf634_render_context_block_full_includes_hofstede():
    from aria_service.intel.cultural_atlas import render_context_block
    block = render_context_block("SA", depth="full")
    assert "PDI=" in block or "Hofstede" in block
    assert "Notes" in block or "notes" in block.lower()


def test_rf634_render_context_block_unknown_iso2_returns_empty():
    """Unknown ISO2 returns empty string — caller can simply NOT
    inject the block. No error, no fabrication."""
    from aria_service.intel.cultural_atlas import render_context_block
    assert render_context_block("ZZ") == ""
    assert render_context_block("") == ""


# ══════════════════════════════════════════════════════════════════
# PART C — communication + negotiation suggestions
# ══════════════════════════════════════════════════════════════════

def test_rf634_suggest_communication_style_high_context_country():
    """Saudi Arabia is high-context — suggestion should flag implicit
    communication."""
    from aria_service.intel.cultural_atlas import suggest_communication_style
    s = suggest_communication_style("SA")
    s_lc = s.lower()
    assert "imply" in s_lc or "high" in s_lc or "indirect" in s_lc


def test_rf634_suggest_communication_style_low_context_country():
    """Germany is low-context — suggestion should flag direct."""
    from aria_service.intel.cultural_atlas import suggest_communication_style
    s = suggest_communication_style("DE")
    s_lc = s.lower()
    assert "direct" in s_lc or "explicit" in s_lc


def test_rf634_suggest_negotiation_relationship_first():
    """Angola is relationship-first — negotiation guidance should say so."""
    from aria_service.intel.cultural_atlas import suggest_negotiation_approach
    s = suggest_negotiation_approach("AO")
    assert "relationship" in s.lower() or "rapport" in s.lower()


def test_rf634_suggest_negotiation_unknown_country_returns_empty():
    from aria_service.intel.cultural_atlas import (
        suggest_communication_style, suggest_negotiation_approach,
    )
    assert suggest_communication_style("ZZ") == ""
    assert suggest_negotiation_approach("ZZ") == ""


# ══════════════════════════════════════════════════════════════════
# PART D — pair_compare cross-cultural helper
# ══════════════════════════════════════════════════════════════════

def test_rf634_pair_compare_saudi_germany_shows_pdi_gap():
    """Saudi PDI=95, Germany PDI=35 → >30pp gap should surface."""
    from aria_service.intel.cultural_atlas import pair_compare
    result = pair_compare("SA", "DE")
    assert result["available"] is True
    gap_dims = [g["dimension"] for g in result["hofstede_gaps"]]
    assert any("PDI" in d for d in gap_dims), (
        "PDI gap between SA (95) and DE (35) should be flagged"
    )


def test_rf634_pair_compare_small_gap_no_amber():
    """Two similar cultures should produce few/no Hofstede flags."""
    from aria_service.intel.cultural_atlas import pair_compare
    # UK + Netherlands are both low-context, individualist, egalitarian
    result = pair_compare("GB", "NL")
    assert result["available"] is True
    # Mostly small gaps — at most one or two
    assert len(result["hofstede_gaps"]) <= 2


def test_rf634_pair_compare_unknown_country_returns_unavailable():
    from aria_service.intel.cultural_atlas import pair_compare
    result = pair_compare("ZZ", "DE")
    assert result["available"] is False


def test_rf634_pair_compare_style_gaps_surface_differences():
    """SA (hierarchical, top-down) vs NL (egalitarian, consensual)
    should surface style gaps."""
    from aria_service.intel.cultural_atlas import pair_compare
    result = pair_compare("SA", "NL")
    style_dims = [g["dimension"] for g in result["style_gaps"]]
    assert "leading" in style_dims
    assert "deciding" in style_dims


# ══════════════════════════════════════════════════════════════════
# PART E — defensive defaults
# ══════════════════════════════════════════════════════════════════

def test_rf634_get_handles_lowercase_iso2():
    """Caller-friendly: lowercase ISO2 should work via .upper() normalisation."""
    from aria_service.intel.cultural_atlas import get
    assert get("sa") is not None
    assert get("SA") is not None
    assert get(" sa ") is not None


def test_rf634_get_handles_bad_input():
    from aria_service.intel.cultural_atlas import get
    assert get(None) is None  # type: ignore[arg-type]
    assert get("") is None
    assert get(123) is None  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════
# PART F — GCC-specific weekend pattern
# ══════════════════════════════════════════════════════════════════

def test_rf634_saudi_kuwait_qatar_weekend_is_fri_sat():
    """GCC business norm — operator must know this when scheduling."""
    from aria_service.intel.cultural_atlas import get
    for iso in ("SA", "KW", "QA", "BH", "OM"):
        p = get(iso)
        assert p is not None
        assert p.weekend == ("fri", "sat"), (
            f"R-F634: {iso} weekend should be Fri/Sat (GCC norm)"
        )


def test_rf634_uae_weekend_is_sat_sun_post_2022():
    """UAE changed to Sat/Sun in 2022 — atlas must reflect this."""
    from aria_service.intel.cultural_atlas import get
    p = get("AE")
    assert p.weekend == ("sat", "sun")
