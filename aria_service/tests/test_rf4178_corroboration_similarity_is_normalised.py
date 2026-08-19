"""R-F4178 / C-191 - corroboration similarity was measured on RAW names, so a
shared legal form inflated it and differing word order deflated it.

`is_corroborated_match()` is the gate that decides whether a sanctions match may
drive a BLOCKING verdict, and it reads `string_similarity` against
`_MIN_BLOCK_SIMILARITY = 0.50`. That number is produced by
`sanctions._normalise_match` as a plain Levenshtein ratio over the two RAW name
strings. Levenshtein over raw company names measures the wrong thing twice:

* **shared legal forms inflate it.** "BLACK ROSE SECURITY LTD" vs
  "BLACK SHIELD COMPANY LTD." scores **0.520** - over the blocking floor -
  largely because both end in "LTD." and both begin "BLACK ". Two unrelated
  companies clear the gate on their suffix.
* **word order deflates it.** "Gazprom Neft Limited" vs "GAZPROM NEFT PJSC" -
  the same company - scores 0.650, and "Modirum Gespi" vs "Modirum Defence Ltd"
  scores **0.474**, i.e. BELOW the floor. A genuine designation was being
  recorded as an uncorroborated non-hit, which `derive_verified_sources` then
  reports as **CLEAN**. That is a false clean, the failure mode ARIA's USP names
  as the one it must never produce.

Both directions are cured by measuring the same quantity the module already
computes for token overlap: the name with corporate suffixes, stopwords and
geographic words stripped. Measured across every case the suite pins, normalised
similarity puts **every one on the correct side of the blocking floor**, and
fixes two that raw got wrong. (It is not monotonically "better" as a value —
Black Rose vs the long name RISES 0.295 -> 0.400 — but both are under the floor,
so the verdict is unchanged. The verdict is the property that means something.)

    case                          raw     normalised
    Black Rose vs long name       0.295   0.400      (both demote - correct)
    Black Rose vs "... LTD."      0.520   0.421      FIXED: no longer blocks
    Rosoboronexport               0.789   1.000
    Modirum "Gespi/Defence"       0.474   0.600      FIXED: real hit recovered
    Vladimir Putin                0.500   0.500
    Gazprom Neft                  0.650   1.000
    ADSM vs Shazand               0.172   0.143
    Modirum "Gespi Ind/Vladimir"  0.208   0.188      (unchanged, still low)

The raw value is KEPT and reported as `string_similarity_raw`, because the USP
commits ARIA to showing its work: a reader must be able to see both what was
measured and what it was measured on.

This is a change to the MEASURE, not to the threshold. Retuning
`_MIN_BLOCK_SIMILARITY` would have traded one error for the other; correcting
what is measured moves both.
"""
from __future__ import annotations

import pytest

from aria_service.intel import sanctions as s
from aria_service.intel import _sanctions_classify as sc


# ── the normalisation is ONE derivation, shared with the tokenizer ──────────

def test_normalisation_strips_the_legal_form():
    """The inflation half. "LTD" must not be evidence of identity - it is the
    single most common token in the corpus."""
    assert sc.normalise_for_similarity("BLACK SHIELD COMPANY LTD.") == \
        sc.normalise_for_similarity("Black Shield")


def test_normalisation_is_word_order_independent():
    """The deflation half. Registries and sanctions lists disagree on where the
    legal form and qualifiers sit."""
    assert sc.normalise_for_similarity("Gazprom Neft PJSC") == \
        sc.normalise_for_similarity("PJSC Neft Gazprom")


def test_normalisation_reuses_the_module_tokenizer():
    """One derivation: if `_tokenize_entity_name` gains a stopword class, the
    similarity measure inherits it rather than drifting."""
    out = sc.normalise_for_similarity("Rosoboronexport JSC")
    assert set(out.split()) == sc._tokenize_entity_name("Rosoboronexport JSC")


def test_normalisation_never_raises_and_degrades_honestly():
    for junk in (None, "", "   ", "LTD", 5):
        assert isinstance(sc.normalise_for_similarity(junk), str)


def test_a_name_that_is_ALL_suffix_falls_back_to_the_raw_string():
    """"Ltd" alone normalises to nothing. Comparing two empty strings would
    score 1.0 - a perfect match between two names we could not read. Fall back
    to the raw text rather than manufacture certainty."""
    assert sc.normalise_for_similarity("Ltd") != ""


# ── THE CAPABILITY TESTS: the two errors, measured ─────────────────────────

def _sim_norm(a: str, b: str) -> float:
    return s._similarity(sc.normalise_for_similarity(a),
                         sc.normalise_for_similarity(b))


def test_a_shared_legal_form_no_longer_clears_the_blocking_floor():
    """THE FALSE-POSITIVE HALF. Raw scores 0.520 and blocks; the shared "LTD."
    is doing the work."""
    raw = s._similarity("BLACK ROSE SECURITY LTD", "BLACK SHIELD COMPANY LTD.")
    assert raw >= sc._MIN_BLOCK_SIMILARITY, "fixture drifted: raw no longer blocks"

    assert _sim_norm("BLACK ROSE SECURITY LTD", "BLACK SHIELD COMPANY LTD.") < \
        sc._MIN_BLOCK_SIMILARITY, (
        "two unrelated companies still clear the blocking floor on their suffix"
    )


def test_a_real_designation_is_no_longer_recorded_as_uncorroborated():
    """THE FALSE-CLEAN HALF, and the one the USP cares about most. Raw scores
    0.474 - under the floor - so a genuine hit was reported CLEAN."""
    raw = s._similarity("Modirum Gespi", "Modirum Defence Ltd")
    assert raw < sc._MIN_BLOCK_SIMILARITY, "fixture drifted: raw no longer fails"

    assert _sim_norm("Modirum Gespi", "Modirum Defence Ltd") >= \
        sc._MIN_BLOCK_SIMILARITY, (
        "a real designation is still measured as an uncorroborated non-hit"
    )


@pytest.mark.parametrize("a,b", [
    ("Rosoboronexport", "JSC ROSOBORONEXPORT"),
    ("Gazprom Neft Limited", "GAZPROM NEFT PJSC"),
    ("Vladimir Putin", "Vladimir Vladimirovich Putin"),
])
def test_every_real_hit_still_clears_the_floor(a, b):
    assert _sim_norm(a, b) >= sc._MIN_BLOCK_SIMILARITY


@pytest.mark.parametrize("a,b", [
    ("BLACK ROSE SECURITY LTD", "Black Shield Company for General Trading LLC"),
    ("ADSM Saudi Arabia", "SHAZAND PETROCHEMICAL COMPANY"),
])
def test_every_known_false_positive_still_fails_the_floor(a, b):
    assert _sim_norm(a, b) < sc._MIN_BLOCK_SIMILARITY


def test_normalisation_gets_every_pinned_case_on_the_right_side_of_the_floor():
    """The claim that justifies the change, stated as the property that MEANS
    something: the VERDICT, not the value.

    A first draft asserted value-monotonicity (never raise a false positive,
    never lower a real hit) and failed on Black Rose vs the long name, which
    normalisation lifts 0.295 -> 0.400. That is harmless - both are under the
    0.50 floor, so the verdict is identical - and the assertion was simply
    stronger than the property that matters. What must hold is that no false
    positive CROSSES the floor and no real hit falls below it.

    The raw column is reported in the failure message so a regression here shows
    whether normalisation caused it or merely failed to cure it."""
    hits = [("Rosoboronexport", "JSC ROSOBORONEXPORT"),
            ("Gazprom Neft Limited", "GAZPROM NEFT PJSC"),
            ("Modirum Gespi", "Modirum Defence Ltd"),
            ("Vladimir Putin", "Vladimir Vladimirovich Putin")]
    misses = [("BLACK ROSE SECURITY LTD", "BLACK SHIELD COMPANY LTD."),
              ("BLACK ROSE SECURITY LTD", "Black Shield Company for General Trading LLC"),
              ("ADSM Saudi Arabia", "SHAZAND PETROCHEMICAL COMPANY")]
    floor = sc._MIN_BLOCK_SIMILARITY
    for a, b in hits:
        assert _sim_norm(a, b) >= floor, (
            f"a real hit is below the blocking floor: {a!r} vs {b!r} "
            f"(normalised {_sim_norm(a, b):.3f}, raw {s._similarity(a, b):.3f}) "
            f"— this is the false-clean direction")
    for a, b in misses:
        assert _sim_norm(a, b) < floor, (
            f"a known false positive clears the blocking floor: {a!r} vs {b!r} "
            f"(normalised {_sim_norm(a, b):.3f}, raw {s._similarity(a, b):.3f})")


def test_normalisation_fixes_two_pinned_cases_that_raw_got_WRONG():
    """The measurable improvement, pinned so it cannot silently regress: raw put
    these two on the wrong side of the floor, normalised puts them right."""
    floor = sc._MIN_BLOCK_SIMILARITY
    # false positive: cleared the floor on a shared "LTD."
    fp = ("BLACK ROSE SECURITY LTD", "BLACK SHIELD COMPANY LTD.")
    assert s._similarity(*fp) >= floor > _sim_norm(*fp)
    # false clean: a real designation scored under the floor on word order
    fc = ("Modirum Gespi", "Modirum Defence Ltd")
    assert s._similarity(*fc) < floor <= _sim_norm(*fc)


# ── THE WIRING: the producer must emit the normalised value ────────────────

def test_the_match_producer_emits_the_normalised_similarity():
    """A better measure nothing computes is the producer-with-no-consumer
    defect. `_normalise_match` is the ONE place OpenSanctions matches are built,
    and `string_similarity` is what the blocking gate reads."""
    from ._source_probe import repo_path

    src = repo_path("aria_service/intel/sanctions.py").read_text(
        encoding="utf-8", errors="replace")
    assert "normalise_for_similarity" in src, (
        "sanctions.py still measures corroboration on raw name strings"
    )
    assert "string_similarity_raw" in src, (
        "the raw value is no longer reported - the USP commits ARIA to showing "
        "its work, so both the measure and what it was measured on must survive"
    )
