"""R-F4269 / C-230 - the exclusion ledger silently lost nine of 139 items.

THE LIVE SYMPTOM, from ``ARIA_DD_Vigilo_Solutions_Limited_dd_9fe0e61e4a0c.pdf``.
One sweep, one materiality dict, two renderings, two different accounts.

Page 2, ``dd_orchestrator._adverse_media_key_finding`` - complete and reconciling::

    139 raw item(s); 125 duplicate URL(s) collapsed; 5 of ARIA's own memory
    records excluded; 8 returned from a domain other than the one the query
    targeted; 1 did not name this entity.        125+5+8+1 = 139, leaving 0.

Page 5, ``dd_schema._render_adverse_media`` - incomplete::

    0 credible adverse item(s) from 139 raw hit(s)
    (125 duplicate, 5 self-referential, 0 non-adverse)      139-125-5-0 = 9 lost.

Nine items left the funnel with no reason given, on the page a reader checks to
decide how much the "nothing found" verdict is worth. That code's own comment
forbids exactly this: *"Report every non-zero reason; a dropped item that is never
accounted for reads as an item that was never found."*

TWO CAUSES, and only one of them is a missing line.

  1. CONFIRMED by code read: ``class_contradicted_dropped`` - the R-F3023 stage that
     drops a result whose domain contradicts the class its query template asserted -
     was absent from ``_excl`` altogether. That stage could never be reported here,
     for any run.

  2. MEASURED, cause NOT established: rendering the ledger from the page-2 dict
     produces "1 not naming this entity", which the delivered page 5 does not carry.
     So that surface received a materiality dict MISSING a key the sweep always
     returns - a legacy or divergently-merged blob. Inventing a mechanism for it
     would be a guess (S22).

SO THE FIX IS RECONCILIATION, NOT ONE MORE KEY. Adding
``class_contradicted_dropped`` closes cause 1 and leaves cause 2 free to lose items
silently the next time a key is absent. The ledger now checks its own arithmetic -
``raw == sum(exclusions) + items_for_review`` - and STATES any residual instead of
letting the reader subtract and come up short. A ledger that cannot account for its
own items has to say so; that is the whole difference between an audit trail and a
list of numbers.
"""
from __future__ import annotations

from aria_service.intel import dd_schema as ds


def _live_mat() -> dict:
    """The funnel exactly as page 2 of the delivered report stated it."""
    return {
        "raw_count": 139, "credible_count": 0,
        "duplicates_dropped": 125, "self_references_dropped": 5,
        "class_contradicted_dropped": 8, "index_pages_dropped": 0,
        "subject_unnamed_dropped": 1, "non_adverse_dropped": 0,
        "material": False, "official": 0, "subject_attribution": "verified",
    }


def _render(mat: dict) -> str:
    am = {"materiality": mat, "findings": [{"x": 1}] * mat["raw_count"],
          "findings_for_review": [], "status": "completed"}
    return "\n".join(ds._render_adverse_media(am, "company"))


def _ledger_line(text: str) -> str:
    for line in text.splitlines():
        if "de-duplication and filtering" in line:
            return line
    raise AssertionError(f"no exclusion ledger rendered:\n{text}")


def test_the_ledger_names_the_class_contradicted_stage():
    """Cause 1. Eight items were dropped by a stage the ledger could not name."""
    line = _ledger_line(_render(_live_mat()))
    assert "8" in line, (
        "the R-F3023 wrong-domain stage dropped 8 of 139 items and the ledger does "
        f"not report it: {line!r}"
    )


def test_the_live_funnel_reconciles_and_says_nothing_extra():
    """The healthy path must stay quiet. A warning that always fires is noise."""
    out = _render(_live_mat())
    assert "unaccounted" not in out.lower(), (
        f"the live funnel reconciles (125+5+8+1+0 = 139, 0 for review) yet the "
        f"renderer complained:\n{out}"
    )


def test_a_dict_missing_a_stage_declares_the_shortfall():
    """Cause 2 - the delivered page-5 shape, whose mechanism is NOT established.

    Reconciliation closes this without needing to know why the key was absent: the
    reader is told the funnel does not add up rather than silently losing 9 items.
    """
    mat = _live_mat()
    del mat["class_contradicted_dropped"]
    del mat["subject_unnamed_dropped"]
    out = _render(mat)
    assert "unaccounted" in out.lower(), (
        "9 of 139 items left the funnel with no reason given and the ledger did not "
        f"say so:\n{out}"
    )
    assert "9" in _ledger_line(out), f"the shortfall must be counted: {_ledger_line(out)!r}"


def test_items_held_for_review_are_not_counted_as_a_shortfall():
    """Survivors are accounted for by the review line, not by an exclusion.

    Without this the check would fire on every run that actually found something -
    the loudest possible false alarm, on the reports that matter most.
    """
    mat = _live_mat()
    mat["duplicates_dropped"] = 120          # free five items to survive
    am = {"materiality": mat, "findings": [{"x": 1}] * 139,
          "findings_for_review": [{"y": 1}] * 5, "status": "completed"}
    out = "\n".join(ds._render_adverse_media(am, "company"))
    assert "unaccounted" not in out.lower(), (
        f"5 surviving items were miscounted as lost:\n{out}"
    )
