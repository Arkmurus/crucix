"""R-F3129 — the sanctions CLEAN finding named five authorities it could not evidence.

LIVE, on the QinetiQ DD (dd_a56444e7647e) and the Mitie report before it. The same
page carried, simultaneously:

    INFO  Sanctions screen CLEAN
          "no matches across OFAC SDN, UK OFSI, EU Consolidated, UN 1267, or
           OpenSanctions datasets … treat as clearance under standard commercial DD"
          (confidence: CONFIRMED)

    ✗     Sanctions and export-control exposure — UNRESOLVED
          "sanctions and export-control checks are not both evidenced"

Both cannot be true. The scorecard's `sanctions_verified` requires
`verified_sources` (dd_schema.py) — the per-list record of which authorities actually
answered. That structure was EMPTY on both reports, which is why neither rendered a
per-list block. The detail text, meanwhile, HARDCODED the five authority names
regardless.

THE SCORECARD WAS RIGHT; THE PROSE WAS THE OVERCLAIM — the same shape as R-F3089's
"subject-named" assertion that nothing ever established. Offering "clearance" on
coverage you cannot show is the false clean this product exists to prevent.

A clean screen is still reported (going silent would be R-F1696 in reverse), but it
now states exactly which lists answered and withholds the clearance language when it
cannot.
"""
import inspect

from aria_service.intel import dd_orchestrator as ddo

# R-F3783/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def _clean_branch(code_only: bool = False) -> str:
    """The R-F3129 branch. `code_only` strips comment lines.

    A source-grep guard that matches its OWN explanatory comment proves nothing —
    hit twice today (R-F3092's slice check did the same). Stripping comments makes
    the assertion about the CODE, which is what it is meant to constrain."""
    src = module_source(ddo)
    i = src.index("R-F3129 — NAME ONLY THE LISTS YOU CAN EVIDENCE")
    window = src[i:i + 3200]
    if not code_only:
        return window
    lines = window.splitlines()
    return "\n".join(l for l in lines if not l.strip().startswith("#"))


def test_rf3129_the_hardcoded_authority_list_is_gone():
    """THE DEFECT: five named authorities in a string, independent of the evidence."""
    src = module_source(ddo)
    assert "no matches across OFAC SDN, UK OFSI, EU Consolidated" not in src, (
        "R-F3129 REGRESSION: the CLEAN finding is again naming authorities it cannot "
        "show were queried")


def test_rf3129_clearance_language_requires_evidence():
    """'treat as clearance under standard commercial DD' may only appear when the
    per-list record exists."""
    # Collapse whitespace: the detail is built from adjacent string literals, so a
    # phrase can span a source line break and a naive substring test misses it.
    code = " ".join(_clean_branch(code_only=True).split())
    code = code.replace('" "', "")          # join the concatenated literals
    assert "treat as clearance under standard commercial DD" not in code, (
        "clearance must not be offered from a hardcoded string")
    assert "NOT YET EVIDENCED" in code, (
        "the no-evidence path must say so explicitly")
    assert "rather than a clearance" in code, (
        "the unevidenced branch must explicitly deny clearance, not merely omit it")


def test_rf3129_named_lists_come_from_verified_sources():
    branch = _clean_branch()
    assert 'screen.get("verified_sources")' in branch
    assert '"CLEAN"' in branch, "only lists that answered CLEAN may be named"
    assert "screened_at" in branch, "a screen without a date is an assertion (R-F3019)"


def test_rf3129_confidence_is_demoted_without_evidence():
    """A CONFIRMED tag on unevidenced coverage is what made this dangerous."""
    branch = _clean_branch()
    assert '_conf = "UNVERIFIED"' in branch
    assert '_conf = "CONFIRMED"' in branch
    assert branch.index('_conf = "CONFIRMED"') < branch.index('_conf = "UNVERIFIED"'), (
        "CONFIRMED belongs to the evidenced branch only")


def test_rf3129_a_clean_screen_is_still_reported():
    """Going silent on a clean screen is the R-F1696 defect in reverse: consumers
    read the absence of a finding as 'the screen never ran'."""
    branch = _clean_branch()
    assert "report.identity.findings.append(" in branch
    assert "Sanctions screen CLEAN" in branch


def test_rf3129_title_states_which_case_it_is():
    """A reader scanning titles must be able to tell an evidenced clean from an
    unevidenced one without opening the detail."""
    branch = _clean_branch()
    assert "list coverage NOT evidenced" in branch


def test_rf3129_still_distinguishes_source_unavailable():
    """R-F1696's guard must survive: a screen that could not run is a different
    statement again, and must not be folded into either clean branch."""
    # R-F3280 — assert the PROPERTY, not the wording. This pinned the literal
    # "Sanctions screen NOT performed — source unavailable", which was reworded to
    # "... — UNVERIFIED" (dd_orchestrator.py:4237). The R-F1696 guarantee did not
    # weaken; it got STRONGER, moving from prose into a structured field that
    # downstream code actually reads:
    #     "source_unavailable": _sanctions_unverified          (:1891, the writer)
    #     _scr.get("screened") is False or _scr.get("source_unavailable")  (:1861)
    # A string match would also have gone red on the em-dash sweep (R-F3278)
    # without anything about the behaviour changing at all.
    src = module_source(ddo)
    assert "Sanctions screen NOT performed" in src, (
        "the unscreenable-entity finding must still be raised"
    )
    assert '"source_unavailable"' in src, (
        "the source-unavailable state must remain a FIELD, not just prose"
    )
    assert 'get("source_unavailable")' in src, (
        "a field nothing reads is not a guard: the distinction must be consumed"
    )
