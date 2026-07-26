"""R-F3100 — the scanner could not read half the ship records it was auditing.

R-F3095 held 39 reservations back as "mentioned only in a commit BODY", for a human
to judge. Reading them showed the premise was wrong for most of them: they were named
in the SUBJECT, in a shorthand that `\\bR-F(\\d+)\\b` cannot see —

    R-F1559/61/62/63/66/67/68/71: aria-intel brain hardening batch
    R-F2912/2913/2914/2916/2917 — stop the Claude overspend
    feat: R-F1099-R-F1107 — Phase 1 reading + Phase 2 registration

The plain pattern matched only the FIRST number in each, so seven of eight
subject-line ship records in one commit were misfiled as body references. 46 commits
in this repo use the slash form and 4 use a range — the "needs human judgement" pile
was mostly a parsing gap, and hand-marking those rows would have left the gap in
place for the next sweep. Fixing the reader took the pile from 39 to 25.

SUFFIX SEMANTICS: a suffix replaces the LAST k digits of the base, and every suffix
is relative to the ORIGINAL base — so `R-F1559/61` is R-F1561, not R-F61, and not
R-F1561→R-F1562 by chaining.
"""
import pytest

from aria_service.intel.r_number_registry import expand_r_numbers


def _nums(text):
    return sorted(expand_r_numbers(text), key=lambda s: int(s[3:]))


# ── the three real subject forms, verbatim from git history ────────────────
def test_rf3100_slash_suffix_abbreviation():
    assert _nums("R-F1559/61/62/63/66/67/68/71: aria-intel brain hardening batch") == [
        "R-F1559", "R-F1561", "R-F1562", "R-F1563",
        "R-F1566", "R-F1567", "R-F1568", "R-F1571"]


def test_rf3100_slash_full_number_abbreviation():
    assert _nums("R-F2912/2913/2914/2916/2917 — stop the Claude overspend") == [
        "R-F2912", "R-F2913", "R-F2914", "R-F2916", "R-F2917"]


def test_rf3100_inclusive_range():
    assert _nums("feat: R-F1099-R-F1107 — Phase 1 reading") == [
        f"R-F{n}" for n in range(1099, 1108)]


def test_rf3100_suffix_is_relative_to_the_base_not_to_the_previous():
    """R-F1559/61 is R-F1561. Chaining would give R-F1561/62 -> R-F1562 by accident
    even where the author meant otherwise; anchoring on the base is unambiguous."""
    assert _nums("R-F1559/61") == ["R-F1559", "R-F1561"]
    assert _nums("R-F2000/10/20") == ["R-F2000", "R-F2010", "R-F2020"]


# ── the forms that must keep working exactly as before ─────────────────────
@pytest.mark.parametrize("text,expected", [
    ("fix: R-F3089 — single", ["R-F3089"]),
    ("fix: R-F3091/R-F3092 — both fully written out", ["R-F3091", "R-F3092"]),
    ("R-F3056 + R-F3057 — the two REDs", ["R-F3056", "R-F3057"]),
    ("chore: ship-mark R-F3095/R-F3096/R-F3097 at adf54c7f",
     ["R-F3095", "R-F3096", "R-F3097"]),
])
def test_rf3100_existing_forms_are_unchanged(text, expected):
    assert _nums(text) == expected


def test_rf3100_no_r_numbers_yields_nothing():
    assert expand_r_numbers("docs: tidy the readme") == set()
    assert expand_r_numbers("") == set()
    assert expand_r_numbers(None) == set()


# ── the guards against over-matching ───────────────────────────────────────
def test_rf3100_an_absurd_range_is_refused():
    """A range wider than the cap is a typo or a plan document, not a batch — and
    silently expanding it would ship-mark hundreds of rows off one commit."""
    out = expand_r_numbers("R-F1-R-F9999 everything ever")
    assert out == {"R-F1", "R-F9999"}, "only the two endpoints, no expansion"


def test_rf3100_a_backwards_range_is_refused():
    assert expand_r_numbers("R-F2000-R-F1000") == {"R-F1000", "R-F2000"}


def test_rf3100_a_suffix_longer_than_its_base_is_ignored():
    """Nonsense input must not fabricate an R-number."""
    assert _nums("R-F12/345678") == ["R-F12"]


def test_rf3100_the_em_dash_separator_is_not_a_range():
    """Subjects read 'R-F3089 — title'. Treating that dash as a range operator would
    be catastrophic; it is only a range when a second R-F number follows."""
    assert _nums("fix: R-F3089 — DD report coherence") == ["R-F3089"]
    assert _nums("R-F3089 - some title - R-F3090 mentioned later") == ["R-F3089", "R-F3090"]


# ── the property that made this worth fixing ───────────────────────────────
def test_rf3100_batch_subjects_are_now_ship_records_on_the_real_history():
    """CAPABILITY: run the real scanner over the real git history and assert the
    batch members land in the SUBJECT bucket, not the review pile."""
    from aria_service.intel import r_number_registry as reg
    by_subject, by_body = reg.scan_shipped_r_numbers()
    for num in ("R-F1561", "R-F1562", "R-F1566", "R-F1571"):   # 73c1d07e batch
        assert num in by_subject, f"{num} is named in a batch SUBJECT line"
        assert num not in by_body
    for num in ("R-F2913", "R-F2914", "R-F2917"):              # 5efd5044 batch
        assert num in by_subject
    for num in ("R-F1104", "R-F1105", "R-F1106"):              # e46245c6 range
        assert num in by_subject
