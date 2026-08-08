"""R-F3130 — a homonym dragged a FTSE-250 defence group to RED.

LIVE on the Babcock DD (dd_8c7242c2b45b, 2026-07-26):

    RED  Site-extracted person Kevin Smith → sanctions red
         KEVIN SMITH (score 1.00, topics: debarment,
          lists: us_hhs_exclusions,us_sam_exclusions, matched_via=primary_name)

    verdict: RED — "very likely unsuitable for onboarding in current form"
    scorecard: 4/5 answered — identity OK, sanctions OK, adverse media OK, ownership OK

"Kevin Smith" clears the R-F3126 gates and SHOULD: it is a real name shape and it does
share distinctive tokens with the listed entry. Those gates were never the problem.
The problem is that a bare COMMON name screened against a debarment list will always
hit, and `score 1.00` means the STRING matched — not that it is the same human.
`matched_via=primary_name` says so outright: no DOB, no nationality, no document
number corroborated it.

A person scraped off a website and matched by NAME ALONE is not evidence about the
COUNTERPARTY. It is a lead requiring identity verification. The R-F2828 principle —
resolve by identifier, never by name — applied to people.

The finding is still surfaced (silence would be R-F1696 in reverse) but capped at
INFO, labelled an unconfirmed name match, and barred from moving the verdict.
"""
import inspect

from aria_service.intel import dd_orchestrator as ddo

# R-F3783/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def _block() -> str:
    src = module_source(ddo)
    i = src.index("R-F3130 — A HOMONYM IS NOT AN IDENTIFICATION")
    return src[i:i + 6500]   # must span the comment AND the detail strings


def _code() -> str:
    """Comment-blind: a guard that matches its own explanatory comment proves
    nothing (hit twice today — R-F3092 and R-F3129)."""
    return "\n".join(l for l in _block().splitlines() if not l.strip().startswith("#"))


def test_rf3130_name_only_match_cannot_keep_its_severity():
    code = _code()
    assert '_eff_worst = _worst if _identified else "info"' in code, (
        "R-F3130 REGRESSION: a name-only person match can drive the entity verdict again")


def test_rf3130_identification_requires_more_than_a_name():
    """primary_name / alias / weak_match are all NAME matching. Only a genuine
    secondary identifier counts as an identification."""
    code = _code()
    for weak in ("primary_name", "alias", "weak_match"):
        assert weak in code, f"{weak} must be treated as name-only"
    assert "_identified = any(" in code


def test_rf3130_a_real_identification_keeps_its_severity():
    """The fix must not blind the screen to a genuine hit — that would trade a false
    positive for a false negative, which is worse on a compliance product."""
    code = _code()
    assert "_eff_worst = _worst if _identified" in code
    assert '_fconf = {' in code and '"hard_stop": "CONFIRMED"' in code


def test_rf3130_the_unconfirmed_case_is_still_surfaced():
    """Going silent is the R-F1696 defect in reverse: an absent finding reads as
    'nothing was screened'."""
    code = _code()
    assert "or not _identified:" in code, "the INFO case must still emit a finding"
    assert "identity NOT confirmed" in code


def test_rf3130_the_detail_states_why_it_is_not_a_finding():
    code = _code()
    joined = " ".join(code.split()).replace('" "', "")
    assert "MATCHED ON NAME ALONE" in joined
    assert "does not affect the risk classification" in joined
    assert "LEAD to verify, NOT a finding against this counterparty" in joined


def test_rf3130_confidence_is_unverified_when_unidentified():
    code = _code()
    assert '"UNVERIFIED"' in code


def test_rf3130_rf3126_gates_are_still_in_force():
    """R-F3130 sits BEHIND R-F3126, it does not replace it: 'Senior Vice' must still
    never reach a screen at all."""
    from aria_service.intel.dd_orchestrator import _is_screenable_person_name as ok
    assert ok("Senior Vice") is False
    assert ok("Kevin Smith") is True, (
        "a real name must still be screenable — R-F3130 governs what the MATCH means")
