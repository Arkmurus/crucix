"""R-F4177 / C-189 - `classify_match` issues BLOCKING verdicts without consulting
the module's own gate for what may block.

`is_corroborated_match()` says exactly what it is for, in its first line:

    \"\"\"True iff `match` may drive a BLOCKING verdict.

    Deliberately strict and deliberately shared. A match that fails this is
    still reported - as a related-name observation, not as a designation.
    \"\"\"

`derive_verified_sources` obeys it: an uncorroborated match is not counted as a
HIT, so the per-source table reports CLEAN. `classify_match` - the function that
decides `hard_stop` - never calls it. So one function in this module declares
what may block and the function that blocks ignores it.

**Measured, on the exact match from the delivered Black Rose report**
(`dd_0d94ba69f415`, 2026-08-19):

    is_corroborated_match(m)                     -> False
    derive_verified_sources([m]) -> BIS ...      -> CLEAN
    classify_match(m, "BLACK ROSE SECURITY LTD") -> hard_stop

That disagreement is what the customer received: page 1 "HARD STOP - mandatory
refusal ... File SAR", page 4 "BIS Entity List - CLEAN". Both derived from the
same match, in the same run, by the same module.

**This is not the C-186 policy question.** C-186 asks whether a lone shared
generic token should ever compel a refusal - a compliance judgement with legal
exposure both ways, and the operator's to make. This fix makes no new judgement:
it applies a threshold the module ALREADY sets (`_MIN_BLOCK_SIMILARITY = 0.50`)
and already enforces on the per-source path.

**It cannot manufacture a false clean**, and that is load-bearing. R-F2840
deliberately made an ABSENT `string_similarity` non-excluding - "only a
MEASURED-low similarity may exclude a match ... 'could not measure' is never
'measured and clear'". Every case the suite requires to escalate
(Modirum, Rosoboronexport, Putin) carries no similarity field, so it passes the
gate untouched. That is exactly why this succeeds where the C-186 shape-based
attempt failed: it separates on evidence, not on token counts.

**Capped at AMBER, not demoted to info**, in the words of the gate's own
docstring: "still reported - as a related-name observation, not as a
designation". Same remedy as R-F434's brandified-hostname cap.
"""
from __future__ import annotations

import pytest

from aria_service.intel import _sanctions_classify as sc


def _match(**kw) -> dict:
    base = {
        "score": 0.85,
        "topics": ["sanction", "debarment"],
        "lists": ["us_trade"],
        "name": "Black Shield Company for General Trading LLC",
        "string_similarity": 0.4,          # MEASURED low
    }
    base.update(kw)
    return base


# ── THE CAPABILITY TEST ─────────────────────────────────────────────────────

def test_an_uncorroborated_match_does_not_compel_a_refusal():
    """The delivered Black Rose match. The module already judged it
    uncorroborated; the severity path ignored that."""
    m = _match()
    assert sc.is_corroborated_match(m) is False, "fixture drifted"

    verdict = sc.classify_match(m, "BLACK ROSE SECURITY LTD")
    assert verdict != "hard_stop", (
        "a match the module itself refuses to count as a HIT still compels a "
        "mandatory refusal and a SAR recommendation"
    )
    assert verdict == "amber"


def test_the_two_paths_no_longer_disagree():
    """THE CONTRADICTION, asserted directly: the per-source table and the
    severity verdict must not describe the same match differently."""
    m = _match()
    vs = sc.derive_verified_sources([m], screen_succeeded=True)
    any_hit = any(r.get("status") == "HIT" for r in vs.values())
    blocking = sc.SEVERITY_RANK[sc.classify_match(m, "BLACK ROSE SECURITY LTD")] >= \
        sc.SEVERITY_RANK["red"]

    assert not (blocking and not any_hit), (
        "a BLOCKING verdict was issued while every canonical list reports "
        "CLEAN - the exact pair the Black Rose report shipped"
    )


def test_the_match_is_still_reported():
    """THE OVER-CORRECTION GUARD. The gate's docstring says a failing match is
    "still reported - as a related-name observation". Burying it at `info`
    would trade a false refusal for a quiet miss."""
    v = sc.classify_match(_match(), "BLACK ROSE SECURITY LTD")
    assert sc.SEVERITY_RANK[v] >= sc.SEVERITY_RANK["amber"]


# ── IT MUST NOT MANUFACTURE A FALSE CLEAN (R-F2840) ─────────────────────────

def test_an_ABSENT_similarity_still_escalates():
    """R-F2840's rule, and the reason this fix is safe where a shape-based one
    was not: an unmeasurable match is not a disproved one."""
    m = _match(name="Rosoboronexport JSC", score=0.95, lists=["us_ofac_sdn"])
    m.pop("string_similarity")
    assert sc.is_corroborated_match(m) is True
    assert sc.classify_match(m, "Rosoboronexport") == "hard_stop"


def test_an_unparseable_similarity_still_escalates():
    m = _match(name="Rosoboronexport JSC", lists=["us_ofac_sdn"],
               string_similarity="n/a")
    assert sc.classify_match(m, "Rosoboronexport") == "hard_stop"


@pytest.mark.parametrize("query,candidate", [
    ("Modirum Gespi Industries", "Vladimir Modirum"),
    ("Modirum Gespi", "Modirum Defence Ltd"),
    ("Vladimir Putin", "Vladimir Vladimirovich Putin"),
    ("Gazprom Neft Limited", "GAZPROM NEFT PJSC"),
])
def test_every_must_escalate_case_is_untouched(query, candidate):
    """The cases the suite already pins, including the one named
    never-false-clean. None carries a similarity field, so none is gated."""
    m = _match(name=candidate, score=0.9, lists=["us_ofac_sdn"])
    m.pop("string_similarity")
    assert sc.classify_match(m, query) == "hard_stop"


def test_a_high_similarity_match_still_escalates():
    """Corroborated AND measured: nothing about this path changes."""
    m = _match(name="GAZPROM NEFT PJSC", lists=["us_ofac_sdn"],
               string_similarity=0.92)
    assert sc.is_corroborated_match(m) is True
    assert sc.classify_match(m, "Gazprom Neft Limited") == "hard_stop"


def test_the_threshold_is_the_one_the_module_already_sets():
    """No new number is introduced. If someone retunes blocking similarity,
    both paths move together."""
    assert sc._MIN_BLOCK_SIMILARITY == 0.50
    just_under = _match(lists=["us_ofac_sdn"], string_similarity=0.49)
    just_over = _match(lists=["us_ofac_sdn"], string_similarity=0.51)
    assert sc.classify_match(just_under, "BLACK ROSE SECURITY LTD") == "amber"
    assert sc.classify_match(just_over, "BLACK ROSE SECURITY LTD") == "hard_stop"


# ── the two list tables disagree, and that is recorded, not silently fixed ──

def test_no_escalating_slug_is_orphaned_from_the_canonical_registry():
    """`_DEFENCE_LIST_LABELS` decides severity; `_CANONICAL_SANCTIONS_SOURCES`
    decides the per-source table. A slug in the first and absent from the second
    escalates while every list reports CLEAN - which is how the Black Rose
    report came to assert both.

    Measured 2026-08-19: 3 of 19 defence slugs are orphaned. They are baselined
    here rather than papered over, because making the tables agree in the HIT
    direction would make an uncorroborated match look MORE credible, and
    `us_unverified` (BIS Unverified List) is a genuinely distinct list that
    needs its own canonical entry - a change with report-shape consequences.

    SHRINK-ONLY, like `LEGACY_COLLISIONS`: a FOURTH orphan fails this test.
    """
    known = {"cmic", "us_trade", "us_unverified"}
    orphans = {
        slug for slug in sc._DEFENCE_LIST_LABELS
        if not any(
            any(s in slug for s in slugs)
            for _lbl, slugs in sc._CANONICAL_SANCTIONS_SOURCES.values()
        )
    }
    assert orphans <= known, (
        f"a NEW escalating slug maps to no canonical source: {sorted(orphans - known)}. "
        f"It can compel a refusal while the per-source table reports every list CLEAN."
    )
