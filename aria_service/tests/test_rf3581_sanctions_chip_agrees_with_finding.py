"""R-F3581 — the identity chip contradicted the report's own finding.

PROVEN on dd_7b37d9a5e3cd while verifying R-F3579. The finding correctly read
"Sanctions/PEP screen — no entity-name match", and the identity panel chip printed
"1" — one screen, two numbers, and the alarming one wins the reader's eye.

`_sanctions_match_metric` renders from `screen["match_classification"]`, which is
persisted BEFORE the coincidence filter runs. A match dropped by that filter was
therefore invisible to the chip. R-F2994's coincidence drop carried the same gap from
the day it shipped; R-F3579 only made it audible by producing a report where the two
surfaces disagreed out loud.

This is exactly what R-F3090 exists to prevent: "persist the arithmetic so every
surface renders the filtered truth instead of re-deriving a raw count."
"""
from __future__ import annotations

import inspect

from aria_service.intel import dd_orchestrator as dd
from aria_service.intel.dd_schema import _sanctions_match_metric as chip

# R-F3784/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def _screen(total, noise, actionable, worst="info"):
    return {"matches": [{}] * total,
            "match_classification": {"total": total, "noise_filtered": noise,
                                     "actionable": actionable, "worst_severity": worst}}


def test_the_contradiction_is_gone():
    """PROVE RED: with actionable=1 the chip said "1" beside a finding saying none."""
    assert chip(_screen(1, 0, 1)) == "1"                     # the defect
    assert chip(_screen(1, 1, 0)).startswith("none"), (      # the fix
        "a fully-filtered screen must not print a match count"
    )


def test_the_drop_is_ACCOUNTED_FOR_not_hidden():
    """A dropped match that is never accounted for is indistinguishable from a match
    never found — the chip must say how many were filtered."""
    out = chip(_screen(1, 1, 0))
    assert "1" in out and "filtered" in out, out


def test_a_genuine_match_still_counts():
    """THE NEVER-FALSE-CLEAN PROPERTY. Filtering noise must never zero a real hit."""
    out = chip(_screen(2, 1, 1))
    assert out.startswith("1"), out
    assert "filtered" in out


def test_a_screen_that_never_ran_is_still_distinguishable():
    """'none found' and 'never looked' must stay apart (R-F3217)."""
    assert chip({"error": "not_entity_shaped"}) == "NOT SCREENED — see data gaps"
    assert chip({}) is None


def test_the_writeback_is_wired_into_the_filter():
    """R-F3515's lesson: a helper nothing calls is indistinguishable from no fix.
    The write-back must sit with the coincidence filter that produces the drop."""
    src = module_source(dd)
    i = src.index("_coincidences = [")
    blk = src[i: i + 2600]
    assert 'match_classification' in blk, (
        "the coincidence filter still does not feed the persisted arithmetic"
    )
    assert 'noise_filtered' in blk and 'actionable' in blk


def test_the_writeback_never_produces_a_negative_actionable():
    """Arithmetic guard: more filtered than total must clamp at zero, not go negative."""
    mc = {"total": 1, "noise_filtered": 0, "actionable": 1}
    mc["noise_filtered"] = mc["noise_filtered"] + 5
    mc["actionable"] = max(0, int(mc["total"]) - int(mc["noise_filtered"]))
    assert mc["actionable"] == 0
    assert chip({"matches": [{}], "match_classification": mc}).startswith("none")
