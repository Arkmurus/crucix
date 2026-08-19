"""C-186 (OPEN) - a single shared generic token produced a HARD STOP with a SAR
recommendation against a live UK company.

**Found in a DELIVERED DD report**, `dd_0d94ba69f415`, generated 2026-08-19
13:19 UTC, on **Black Rose Security Limited** (UK 14244249).

    RISK SIGNAL CLEARANCE : NOT CLEARED / HARD STOP
    ASSESSMENT           : "HARD STOP - BLACK ROSE SECURITY LTD triggers a
                            mandatory refusal. Do NOT proceed."
    Recommendation       : "Refuse the engagement. File SAR if reporting
                            thresholds are met."

The entire basis:

    Black Shield Company for General Trading LLC (score 0.85,
    topics: sanction,debarment, lists: BIS / US Trade Sanctions,
    matched_via=primary_name='BLACK SHIELD COMPANY LTD.')
    HARD_STOP - CONFIRMED - source: sanctions.screen_with_aliases:R-F3219

The subject is a UK security-services company. The match is a Middle East
general-trading LLC on the BIS Entity List. After suffix/stopword stripping they
share exactly ONE token - **"black"**:

    BLACK ROSE SECURITY LTD              -> {black, rose, security}
    Black Shield Company for General ... -> {black, general, shield}

**The report contradicts itself**: page 4 lists "US Commerce - Bureau of
Industry and Security Entity List - CLEAN" while page 1 hard-stops on a match
whose list is "BIS / US Trade Sanctions". Both cannot be true.

**Why the existing guard missed it.** R-F351 identified this exact failure class
and its cost - "Cost of false-positive HARD_STOP (defamation, SAR mis-filing) >>
cost of false-negative demote-to-info" - and chose token LENGTH as the proxy for
distinctiveness: a lone shared token is demoted only when `< 5` characters (the
ADSM/ARMS/CORE acronym class). `"black"` is exactly 5. So are "royal", "crown",
"prime", "delta", "atlas".

═══════════════════════════════════════════════════════════════════════════════
WHY THIS IS STILL OPEN, AND WHAT WAS TRIED
═══════════════════════════════════════════════════════════════════════════════

The obvious fix - cap severity at AMBER when one shared token joins two names
that EACH still carry >=2 meaningful tokens - was implemented, went green here,
and was **reverted**, because it broke two existing tests that pin the opposite
and one of them is named *never-false-clean*:

    test_rf335_sanctions_match_path::test_rf351_long_token_single_overlap_preserved
    test_dd_honesty_and_sanctions_noise_rf2361_rf2362::
        test_rf2362_real_name_overlap_match_survives_and_escalates

They are right, and the reason is measured below in
`test_the_defect_and_a_real_hit_are_structurally_IDENTICAL`: "Modirum Gespi" vs
"Modirum Defence Ltd" has the **same shape** as Black Rose vs Black Shield - one
shared token, both sides multi-token. What separates them is that "modirum" is
coined and "black" is ubiquitous. That is DISTINCTIVENESS, and no rule built on
token counts, lengths or ratios can see it.

**Two candidate directions, neither shipped:**

1. *A curated low-entropy token set*, the way R-F277 added `_GEOGRAPHIC_TOKENS`
   after country-name overlap caused the same class of false positive. Proven
   pattern in this very module, fails safe (an unlisted word behaves exactly as
   today), but permanently incomplete - the next report is a different noun.

2. *Cross-check the fuzzy verdict against the canonical per-source screen* -
   and this is the promising one, because it needs no judgement call and it
   separates the two cases correctly. This report asserted BIS Entity List
   CLEAN and hard-stopped citing BIS in the same document. A hard_stop whose
   named list the canonical screen reports CLEAN is internally contradictory on
   its face. A real Modirum/OFAC hit would show that list as HIT, not CLEAN, so
   the rule leaves it alone. It lives in the screen/`derive_verified_sources`
   layer rather than here.

**The severity policy itself is an operator decision** (CLAUDE.md section 21e:
legal exposure - SAR filing and defamation - is escalated, not coded). Which
error we prefer on the weakest class of overlap is not Claude's call to make
silently.

The `xfail(strict=True)` markers below are deliberate: they state the intended
behaviour, they are not a standing red test, and they will FAIL LOUDLY the
moment someone fixes this - which is the prompt to delete the marker.
"""
from __future__ import annotations

import pytest

from aria_service.intel import _sanctions_classify as sc


def _match(**kw) -> dict:
    base = {
        "score": 0.85,
        "topics": ["sanction", "debarment"],
        "lists": ["BIS / US Trade Sanctions"],
        "name": "Black Shield Company for General Trading LLC",
        "string_similarity": 0.4,
    }
    base.update(kw)
    return base


# ── THE MEASUREMENT: what the delivered report actually did ─────────────────

def test_the_delivered_report_is_reproduced_exactly():
    """Not xfailed - this PASSES today and is the evidence. It pins the live
    behaviour so the defect cannot be quietly disputed, and it will need
    updating by whoever fixes it (which is the point)."""
    assert sc.classify_match(_match(), "BLACK ROSE SECURITY LTD") == "hard_stop"
    # The tokenisation that produced it.
    q = sc._tokenize_entity_name("BLACK ROSE SECURITY LTD")
    c = sc._tokenize_entity_name("Black Shield Company for General Trading LLC")
    assert q & c == {"black"}, f"expected the lone shared token 'black', got {q & c}"
    assert len(only := (q & c)) == 1 and len(next(iter(only))) == 5, (
        "the shared token is exactly 5 chars, which is why R-F351's <5 rule "
        "did not fire"
    )


def test_the_defect_and_a_real_hit_are_structurally_IDENTICAL():
    """THE FINDING THAT BLOCKS THE OBVIOUS FIX.

    Measured, not argued: the false positive and the two cases the suite
    requires to escalate have the same token shape. Any rule phrased in counts,
    lengths or ratios must treat them the same way."""
    cases = {
        "FALSE POSITIVE (Black Rose)":
            ("BLACK ROSE SECURITY LTD",
             "Black Shield Company for General Trading LLC"),
        "MUST ESCALATE (rf351)":
            ("Modirum Gespi Industries", "Vladimir Modirum"),
        "MUST ESCALATE (rf2362, never-false-clean)":
            ("Modirum Gespi", "Modirum Defence Ltd"),
    }
    shapes = {}
    for label, (q, c) in cases.items():
        qt, ct = sc._tokenize_entity_name(q), sc._tokenize_entity_name(c)
        shared = qt & ct
        shapes[label] = (len(shared), len(qt) >= 2, len(ct) >= 2,
                         len(next(iter(shared))) >= 5)

    distinct = set(shapes.values())
    assert len(distinct) == 1, (
        "the shapes differ, so a structural rule COULD separate them - "
        f"re-open the shape-based fix: {shapes}"
    )


# ── WHAT ANY FIX MUST PRESERVE (these pass today and must keep passing) ─────

def test_a_single_token_that_IS_the_whole_name_must_keep_escalating():
    """Modirum alone: the shared token is the identity, not a modifier."""
    assert sc.classify_match(_match(name="Modirum", score=0.9),
                             "Modirum Gespi Ltd") == "hard_stop"


def test_rosoboronexport_must_keep_escalating():
    """The live case CLAUDE.md section 18 pins as must-work."""
    assert sc.classify_match(_match(name="JSC ROSOBORONEXPORT", score=0.9),
                             "Rosoboronexport") == "hard_stop"


def test_two_shared_tokens_must_keep_escalating():
    assert sc.classify_match(_match(name="Vladimir Vladimirovich Putin",
                                    score=0.9), "Vladimir Putin") == "hard_stop"


def test_the_near_exact_transliteration_bypass_must_survive():
    """R-F569/R-F569.5 - score>=0.95 AND string_similarity>=0.50 bypasses the
    overlap discipline entirely."""
    assert sc.classify_match(
        _match(name="ROSOBORONEKSPORT OAO", score=0.97, string_similarity=0.9),
        "Rosoboronexport") == "hard_stop"


def test_zero_overlap_is_still_demoted_to_info():
    assert sc.classify_match(_match(name="SHAZAND PETROCHEMICAL COMPANY"),
                             "ADSM Saudi Arabia") == "info"


def test_the_short_acronym_demotion_is_unchanged():
    """R-F351's <5-char class still goes to info."""
    assert sc.classify_match(_match(name="ARM Holdings Group"),
                             "ARM Defence Systems") == "info"


def test_a_score_below_the_floor_is_still_info():
    assert sc.classify_match(_match(score=0.5),
                             "BLACK ROSE SECURITY LTD") == "info"


# ── THE INTENDED BEHAVIOUR (xfail until the policy call is made) ────────────

_REASON = ("C-186 OPEN: a lone shared generic token still compels a refusal. "
           "The shape-based fix was reverted because it also demotes real hits "
           "(see test_the_defect_and_a_real_hit_are_structurally_IDENTICAL). "
           "Needs either a curated low-entropy token set or the canonical "
           "per-source cross-check, plus an operator decision on severity "
           "policy (SAR/defamation exposure, CLAUDE.md 21e).")


@pytest.mark.xfail(strict=True, reason=_REASON)
def test_the_black_rose_report_should_not_be_a_hard_stop():
    assert sc.classify_match(_match(), "BLACK ROSE SECURITY LTD") != "hard_stop"


@pytest.mark.xfail(strict=True, reason=_REASON)
@pytest.mark.parametrize("word", ["black", "royal", "crown", "prime",
                                  "delta", "atlas"])
def test_a_lone_generic_token_should_not_compel_a_refusal(word):
    """Each is >=5 chars, so R-F351's length proxy passes it, and each survives
    the suffix/stopword/geographic filters. Deliberately excluded because the
    existing rules already handle them: "global"/"united" (stripped as
    stopword/geographic by R-F277) and "star" (4 chars, R-F351)."""
    query, candidate = f"{word} Rose Security Ltd", f"{word} Shield Petrochemical Industries"
    assert len(sc._tokenize_entity_name(query)
               & sc._tokenize_entity_name(candidate)) == 1
    assert sc.classify_match(_match(name=candidate), query) != "hard_stop"


@pytest.mark.xfail(strict=True, reason=_REASON)
def test_a_hard_stop_should_not_cite_a_list_the_screen_called_CLEAN():
    """Candidate direction 2, stated as an executable expectation. The report
    asserted "BIS Entity List - CLEAN" and hard-stopped citing BIS in the same
    document; that is decidable without any policy judgement."""
    verdict = sc.classify_match(_match(lists=["BIS / US Trade Sanctions"]),
                                "BLACK ROSE SECURITY LTD")
    assert verdict != "hard_stop"
