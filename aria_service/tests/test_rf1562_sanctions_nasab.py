"""R-F1562 capability test — sanctions false-negative on Arabic nasab +
non-Latin script.

Before R-F1562, sanctions.py `_generate_variants` only handled Cyrillic→Latin
transliteration + acronym/suffix variants. It did NOT:
  - use the nasab/particle parser (person_resolver.parse_components), so a
    reordered Arabic patronymic name (Latin script) produced no matching
    variant; and
  - handle Arabic-SCRIPT input at all, so a name written in Arabic produced
    zero Latin variants and could never match OpenSanctions' Latin primary
    names — a legal-liability false-negative class.

These tests drive the REAL `_generate_variants` (the function `fuzzy_screen`
calls at sanctions.py to build its query set). They do NOT hit the live
OpenSanctions network — they assert variant GENERATION only.
"""
from __future__ import annotations

import re

from aria_service.intel.sanctions import (
    _generate_variants,
    _transliterate_arabic,
)


def _norm(s: str) -> str:
    """Normalise for comparison: lowercase, strip hyphens/punctuation,
    collapse whitespace. Lets 'Al-Saud' == 'Al Saud' == 'al saud'."""
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _has_variant(variants, target: str) -> bool:
    t = _norm(target)
    return any(_norm(v) == t for v in variants)


# ── 1. Latin-script reordered nasab name ─────────────────────────────────────

def test_nasab_latin_reordering_generates_dropped_particle_form():
    """'Ahmed bin Mohammed Al-Saud' must generate a particle-dropped form
    matching 'Ahmed Mohammed Al Saud'."""
    variants = _generate_variants("Ahmed bin Mohammed Al-Saud")
    assert _has_variant(variants, "Ahmed Mohammed Al Saud"), (
        f"expected 'Ahmed Mohammed Al Saud' among variants, got {variants}"
    )


def test_nasab_latin_generates_short_given_plus_family():
    """The short patronymic-free 'Ahmed Al-Saud' form must be generated —
    this is how many sanctions lists store the bare given+family name."""
    variants = _generate_variants("Ahmed bin Mohammed Al-Saud")
    assert _has_variant(variants, "Ahmed Al-Saud") or _has_variant(
        variants, "Ahmed Al Saud"
    ), f"expected 'Ahmed Al-Saud' / 'Ahmed Al Saud' among variants, got {variants}"


def test_nasab_latin_drops_all_particles():
    """The fully particle-stripped 'Ahmed Mohammed Saud' must be generated."""
    variants = _generate_variants("Ahmed bin Mohammed Al-Saud")
    assert _has_variant(variants, "Ahmed Mohammed Saud"), (
        f"expected 'Ahmed Mohammed Saud' among variants, got {variants}"
    )


# ── 2. Arabic-SCRIPT input ───────────────────────────────────────────────────

def test_arabic_script_generates_a_latin_variant():
    """An Arabic-script name must yield at least one Latin-script variant,
    otherwise it can never match an OpenSanctions Latin primary name."""
    arabic = "محمد بن سلمان"  # Mohammed bin Salman
    variants = _generate_variants(arabic)
    latin = [v for v in variants if re.search(r"[a-zA-Z]", v)]
    assert latin, f"expected ≥1 Latin variant for Arabic input, got {variants}"
    # The transliteration should at least contain the salman/slman family form.
    joined = " ".join(_norm(v) for v in latin)
    assert "slman" in joined or "salman" in joined, (
        f"expected a salman-like Latin token, got {latin}"
    )


def test_transliterate_arabic_helper_strips_diacritics_and_maps_letters():
    """Direct unit check on the Arabic→Latin helper used by the screen path."""
    # With harakat (diacritics) present, they must be stripped, not mapped.
    out = _transliterate_arabic("مُحَمَّد")  # 'muhammad' with harakat
    assert out, "transliteration produced empty output"
    assert re.fullmatch(r"[a-z ]+", out), f"non-latin leaked: {out!r}"


def test_arabic_input_feeds_nasab_reorderer():
    """Arabic 'محمد بن سلمان' → Latin → nasab-dropped short form
    (given+family, patronymic dropped) must appear."""
    variants = _generate_variants("محمد بن سلمان")
    norm = [_norm(v) for v in variants]
    # 'bn' (bin) is the dropped patronymic → expect 'mhmd slman' present.
    assert "mhmd slman" in norm, (
        f"expected patronymic-dropped 'mhmd slman' among {norm}"
    )


# ── 3. Regression: existing behaviour preserved + variant cap ────────────────

def test_existing_acronym_and_suffix_variants_still_work():
    """The pre-R-F1562 corporate logic must be untouched."""
    variants = _generate_variants("Rostec Corporation")
    norm = [_norm(v) for v in variants]
    assert "rostec" in norm, f"suffix-strip variant lost, got {variants}"


def test_input_name_always_first_variant():
    """The original input must always be present (and first) so an exact
    match is never lost."""
    variants = _generate_variants("Bank Rossiya")
    assert variants[0] == "Bank Rossiya"


def test_variant_set_is_capped_and_deduped():
    """Variant explosion must be capped and case-insensitively deduped."""
    variants = _generate_variants("Ahmed bin Mohammed Al-Saud bin Abdulaziz")
    assert len(variants) <= 24, f"variant cap exceeded: {len(variants)}"
    lowered = [v.lower() for v in variants]
    assert len(lowered) == len(set(lowered)), f"duplicate variants: {variants}"
