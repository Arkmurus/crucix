"""R-F766 — entity-name variant fanout for deep_research.

LIVE BUG from 2026-05-20 5-turn audit transcript: deep_research on
'Efdal Colpan' ran 4 queries -> 0 results. ARIA's gap analysis
listed better variants (Çolpan, Cholpan, Djolpan) but never auto-ran
them. R-F766 generates plausible romanisation / transliteration
variants for non-English-shaped names and adds one search angle per
variant before declaring zero results.

Two parts under test:
  1. The name_variants module: variant generation contracts.
  2. The deep_research wiring: variants are added to the angles list.
"""
from __future__ import annotations

from pathlib import Path

from aria_service.intel.name_variants import (
    generate_variants,
    likely_needs_variants,
)


# ── name_variants module — generation contracts ─────────────────────


def test_rf766_original_name_always_first():
    out = generate_variants("Efdal Colpan", limit=6)
    assert out[0] == "Efdal Colpan", (
        "R-F766: the original spelling must always be the first variant "
        "so existing query templates that depend on `angles[0] == entity` "
        "keep working."
    )


def test_rf766_efdal_colpan_emits_turkish_diacritic_variant():
    """Live bug coverage: 'Efdal Colpan' must produce 'Efdal Çolpan'
    as one of the variants — that's the canonical Turkish spelling."""
    out = generate_variants("Efdal Colpan", limit=6)
    assert any("Çolpan" in v for v in out), (
        f"R-F766 regression: 'Efdal Colpan' did NOT produce a 'Çolpan' "
        f"diacritic variant. Got: {out}"
    )


def test_rf766_efdal_colpan_emits_phonetic_initial_swap():
    """Live bug coverage: 'Efdal Colpan' must produce 'Efdal Cholpan'
    (Ch- transliteration) and/or 'Efdal Djolpan' (Dj-)."""
    out = generate_variants("Efdal Colpan", limit=8)
    has_ch = any("Cholpan" in v for v in out)
    has_dj = any("Djolpan" in v for v in out)
    assert has_ch or has_dj, (
        f"R-F766 regression: 'Efdal Colpan' did NOT produce any "
        f"phonetic initial-consonant swap (Ch-/Dj-). Got: {out}"
    )


def test_rf766_cyrillic_transliteration_alternates():
    out = generate_variants("Sergey Mikhailov", limit=6)
    assert "Sergey Mikhailov" in out
    # Either Sergei or one of the suffix-swapped surnames should appear.
    has_sergei = any("Sergei" in v for v in out)
    has_suffix = any(v.endswith("Mikhailoff") or v.endswith("Mikhailow") for v in out)
    assert has_sergei or has_suffix, (
        f"R-F766: 'Sergey Mikhailov' produced no recognisable Cyrillic "
        f"alternate (Sergei / -off / -ow). Got: {out}"
    )


def test_rf766_arabic_transliteration_alternates():
    out = generate_variants("Mohammed Ahmed", limit=6)
    assert "Mohammed Ahmed" in out
    has_alt = any(
        v in out for v in ("Muhammad Ahmed", "Mohamed Ahmed", "Mohammed Ahmad")
    )
    assert has_alt, (
        f"R-F766: 'Mohammed Ahmed' produced no Arabic transliteration "
        f"alternate. Got: {out}"
    )


def test_rf766_limit_is_respected():
    out = generate_variants("Efdal Colpan", limit=3)
    assert len(out) <= 3, f"R-F766: limit=3 not respected, got {len(out)}"


def test_rf766_deduplicates_case_insensitively():
    """If two transliterations collapse to the same lowercase form,
    we shouldn't ship duplicates."""
    out = generate_variants("Mohammed Ahmed", limit=10)
    lowered = [v.lower() for v in out]
    assert len(lowered) == len(set(lowered)), (
        f"R-F766: variants contain case-insensitive duplicates: {out}"
    )


def test_rf766_empty_input_returns_empty():
    assert generate_variants("") == []
    assert generate_variants("   ") == []
    # Non-strings are graceful
    assert generate_variants(None) == []  # type: ignore[arg-type]


# ── likely_needs_variants — pre-check contracts ──────────────────────


def test_rf766_likely_needs_variants_yes_on_turkish_name():
    assert likely_needs_variants("Efdal Colpan") is True


def test_rf766_likely_needs_variants_yes_on_cyrillic_name():
    assert likely_needs_variants("Sergey Mikhailov") is True
    assert likely_needs_variants("Dmitri Petrov") is True


def test_rf766_likely_needs_variants_yes_on_arabic_name():
    assert likely_needs_variants("Mohammed Ahmed") is True


def test_rf766_likely_needs_variants_yes_on_slavic_suffix():
    assert likely_needs_variants("Kowalski") is True
    assert likely_needs_variants("Anton Petrov") is True


def test_rf766_likely_needs_variants_no_on_plain_western_name():
    """Cheap-skip gate: plain Western names don't need fanout."""
    assert likely_needs_variants("John Smith") is False
    assert likely_needs_variants("Sarah Brown") is False


def test_rf766_likely_needs_variants_yes_on_non_ascii():
    """Any input that already has diacritics qualifies."""
    assert likely_needs_variants("Çağlar Çolpan") is True
    assert likely_needs_variants("Müller") is True


# ── deep_research wiring — angles list contains variants ─────────────


def test_rf766_deep_research_imports_name_variants():
    """Verify researcher.py wires in the variant fanout. Source-level
    check (the actual deep_research execution requires the full HTTP
    stack)."""
    p = Path(__file__).resolve().parents[1] / "intel" / "researcher.py"
    src = p.read_text(encoding="utf-8")
    assert "from .name_variants import generate_variants, likely_needs_variants" in src, (
        "R-F766 regression: deep_research no longer imports the "
        "name_variants module. Fanout will not trigger."
    )
    assert "if likely_needs_variants(entity):" in src, (
        "R-F766 regression: deep_research no longer gates the fanout "
        "on likely_needs_variants. Either fanout runs on every entity "
        "(wastes budget on John Smith) or never runs at all."
    )
    assert "_variant_angles" in src, (
        "R-F766 regression: deep_research no longer assembles "
        "_variant_angles. The variant list isn't reaching the search."
    )


def test_rf766_deep_research_merges_variant_angles_into_angles():
    p = Path(__file__).resolve().parents[1] / "intel" / "researcher.py"
    src = p.read_text(encoding="utf-8")
    assert "_base_angles + _variant_angles + _memory_expansions" in src, (
        "R-F766 regression: variant angles must be merged INTO the "
        "final `angles` list (not just logged). Expected ordering: "
        "base + variants + memory expansions."
    )
