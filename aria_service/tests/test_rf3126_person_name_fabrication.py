"""R-F3126 — a job title was screened as a person and promoted to RED.

LIVE DEFECT, on the QinetiQ deep DD (dd_a56444e7647e, 2026-07-26). The report carried:

    RED  Site-extracted person Senior Vice → sanctions red
         Name 'Senior Vice' was extracted from QinetiQ Group plc's own site …
         Senior Vice President of Physician Services (score 0.77,
         topics: debarment, lists: us_ca_med_exclusions)

"Senior Vice" is not a person. It is a fragment of "Senior Vice President" scraped
off the leadership page, mislabelled `person_name`, screened, and matched against a
CALIFORNIA MEDICAL EXCLUSIONS entry on the shared role words {senior, vice,
president}. A FTSE-250 UK defence group was handed a sanctions-adjacent RED because
of a job title — and that finding reached the verdict.

On a compliance product ONE false positive destroys the guarantee. Two independent
gates, because either alone stops it and a single guard is a single point of failure:

  D1 `_is_screenable_person_name` — a candidate must carry TWO distinctive,
     non-role tokens before anyone can be accused
  D2 `_person_match_is_coincidence` — the match must share a distinctive token,
     the same rule R-F2994 applies to companies and R-F3089 to adverse media.
     Person screening was the ONE path without it.
"""
import pytest

from aria_service.intel.dd_orchestrator import (
    _is_screenable_person_name as screenable,
    _person_distinctive_tokens as tokens,
    _person_match_is_coincidence as coincidence,
)

# R-F3785/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


# ── D1 — a job title is not a person ───────────────────────────────────────
@pytest.mark.parametrize("value", [
    "Senior Vice",                 # THE live defect
    "Senior Vice President",
    "Chief Executive Officer",
    "Head of Operations",
    "Our Leadership Team",
    "Group Finance Director",
    "",
    "Cooper",                      # single token — too weak to accuse anyone
    "A1 Smith",                    # digits are not a name
])
def test_rf3126_d1_rejects_non_names(value):
    assert screenable(value) is False, f"{value!r} must never reach a sanctions screen"


@pytest.mark.parametrize("value", [
    "Shonaid Jemmett-Page",
    "Gordon Messenger",
    "Neil Anthony Johnson",
    "Roger Arnold Krone",
    "Dina Knight",
])
def test_rf3126_d1_keeps_real_people(value):
    """The gate must not blind the screen to actual directors — that would trade a
    false positive for a false negative, which is worse on a compliance product."""
    assert screenable(value) is True


def test_rf3126_role_words_carry_no_identity():
    assert tokens("Senior Vice President") == set()
    assert tokens("Gordon Messenger") == {"gordon", "messenger"}


# ── D2 — the match must share a distinctive token ──────────────────────────
def test_rf3126_d2_the_live_match_is_a_coincidence():
    assert coincidence(
        "Senior Vice", "Senior Vice President of Physician Services") is True, (
        "R-F3126 REGRESSION: a role-word-only overlap is being treated as a hit")


def test_rf3126_d2_keeps_a_genuine_person_match():
    assert coincidence("Gordon Messenger", "Gordon Kenneth Messenger") is False
    assert coincidence("Roger Krone", "Roger Arnold Krone") is False


def test_rf3126_d2_drops_an_unrelated_person():
    assert coincidence("Dina Knight", "Vladimir Petrov") is True


def test_rf3126_d2_is_asymmetric_by_design():
    """A token-less QUERY is not a name, so nothing is attributable to it. A
    token-less MATCHED entry is merely unjudgeable, so we keep it.

    The first cut failed open on BOTH and returned False for the exact 'Senior Vice'
    case — D1 masked it, which is precisely why the second guard has to be correct
    on its own."""
    assert coincidence("Senior Vice", "Anyone Real") is True      # query not a name
    assert coincidence("Neil Johnson", "") is False               # cannot judge entry


# ── the wiring: both gates on the live path ────────────────────────────────
def test_rf3126_both_gates_are_applied_at_the_screening_site():
    import inspect
    from aria_service.intel import dd_orchestrator as ddo
    src = module_source(ddo)
    assert "if not _is_screenable_person_name(_v):" in src, (
        "D1 must filter candidates BEFORE they are screened")
    assert "_person_match_is_coincidence(" in src, (
        "D2 must filter matches BEFORE they are classified")
    # and the drop must be counted, never silent
    assert "dropped %d name-coincidence match(es)" in src
