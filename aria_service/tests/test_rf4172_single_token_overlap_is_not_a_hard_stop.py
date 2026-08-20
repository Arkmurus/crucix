"""C-186 - a single shared generic token produced a HARD STOP with a SAR
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

**UPDATE — R-F4177 (C-189) closed the DELIVERED case, by a third route
neither of these described.** `is_corroborated_match()` — whose docstring reads
"True iff `match` may drive a BLOCKING verdict" — was already enforced by
`derive_verified_sources` and never consulted by `classify_match`. The delivered
match carries a measured similarity of 0.4 against a declared floor of 0.50, so
the module had already judged it uncorroborated while still hard-stopping on it.
That separates the false positive from Modirum/Rosoboronexport where SHAPE
cannot, because it separates on EVIDENCE.

**What remains open here** is narrower and still a policy question: a lone
shared generic token on a match carrying NO measurable similarity. R-F2840
deliberately lets an unmeasurable match stand ("could not measure" is never
"measured and clear"), so the corroboration gate cannot reach it. The xfails
below cover exactly that residue.

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

**The severity policy itself was an operator decision** because it carries SAR
and defamation exposure. The operator's explicit instruction to pursue the
remaining work resolved that disposition in favour of the conservative AMBER
boundary below; it was not inferred silently.

R-F4172 implements the conservative policy choice: a curated common token may
not be the sole identity evidence for refusal when similarity is unmeasured.
The candidate remains visible at AMBER; unknown and distinctive tokens retain
the established never-false-clean behaviour.
"""
from __future__ import annotations

import pytest

from aria_service.intel import _sanctions_classify as sc
from aria_service.intel import engine_wiring


def _match(**kw) -> dict:
    """A match with NO `string_similarity` — i.e. one R-F4177's corroboration
    gate cannot exclude (R-F2840: "could not measure" is never "measured and
    clear"). This is the class C-186 is still open about.

    The first draft of this file put `string_similarity: 0.4` in the base and
    used it for the must-escalate cases too, which was unrealistic — a real
    Rosoboronexport/ROSOBORONEKSPORT pair measures ~0.9 (R-F569.5) — and made
    those cases fail against a correct fix. Fixture, not fix.
    """
    base = {
        "score": 0.85,
        "topics": ["sanction", "debarment"],
        "lists": ["BIS / US Trade Sanctions"],
        "name": "Black Shield Company for General Trading LLC",
    }
    base.update(kw)
    return base


def _delivered() -> dict:
    """The match as it reached the customer in dd_0d94ba69f415, INCLUDING its
    measured-low similarity — which is what R-F4177 (C-189) now acts on."""
    return _match(lists=["us_trade"], string_similarity=0.4)


# ── THE MEASUREMENT: what the delivered report actually did ─────────────────

def test_the_delivered_report_is_no_longer_a_hard_stop():
    """UPDATED BY R-F4177 (C-189), which is what this test was written to
    prompt: it used to assert `hard_stop`, the behaviour the customer received.

    C-189 did not answer C-186's policy question. It applied the module's OWN
    blocking gate (`is_corroborated_match`, `_MIN_BLOCK_SIMILARITY = 0.50`),
    which `derive_verified_sources` already enforced and `classify_match` did
    not. The delivered match carries a measured similarity of 0.4, so the two
    paths now agree instead of shipping opposite conclusions."""
    assert sc.classify_match(_delivered(), "BLACK ROSE SECURITY LTD") == "amber"
    vs = sc.derive_verified_sources([_delivered()], screen_succeeded=True)
    assert not any(r.get("status") == "HIT" for r in vs.values()), (
        "the per-source table and the severity verdict disagree again"
    )
    # The tokenisation that produced it.
    q = sc._tokenize_entity_name("BLACK ROSE SECURITY LTD")
    c = sc._tokenize_entity_name("Black Shield Company for General Trading LLC")
    assert q & c == {"black"}, f"expected the lone shared token 'black', got {q & c}"
    assert len(only := (q & c)) == 1 and len(next(iter(only))) == 5, (
        "the shared token is exactly 5 chars, which is why R-F351's <5 rule "
        "did not fire"
    )
    aggregate = sc.classify_matches([_delivered()], "BLACK ROSE SECURITY LTD")
    assert aggregate["per_match"][0]["low_distinctiveness_capped"] is False


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


# ── THE R-F4172 POLICY BOUNDARY ─────────────────────────────────────────────


def test_the_black_rose_report_is_not_a_hard_stop_anymore():
    """Was xfail; CLOSED by R-F4177 (C-189) for THIS match, because it carries a
    measured-low similarity. The residual class — a lone generic token on a
    match with NO measurable similarity — is still open below."""
    assert sc.classify_match(_delivered(), "BLACK ROSE SECURITY LTD") != "hard_stop"


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
    assert sc.classify_match(_match(name=candidate), query) == "amber"


def test_a_hard_stop_should_not_cite_a_list_the_screen_called_CLEAN():
    """Candidate direction 2, stated as an executable expectation. The report
    asserted "BIS Entity List - CLEAN" and hard-stopped citing BIS in the same
    document; that is decidable without any policy judgement."""
    verdict = sc.classify_match(_match(lists=["BIS / US Trade Sanctions"]),
                                "BLACK ROSE SECURITY LTD")
    assert verdict == "amber"


def test_capability_unmeasured_generic_overlap_cannot_drive_refusal():
    """Drive the aggregate used by DD and prove the customer-visible tier."""
    result = sc.classify_matches(
        [_match(lists=["us_trade"])],
        query_name="BLACK ROSE SECURITY LTD",
    )

    assert result["worst_severity"] == "amber"
    assert result["blocking_source_ids"] == []
    assert result["per_match"][0]["severity"] == "amber"
    assert result["per_match"][0]["noise_filtered"] is True
    assert result["per_match"][0]["low_distinctiveness_capped"] is True


def test_unknown_single_token_keeps_never_false_clean_policy():
    """The curated set must not silently generalise to distinctive names."""
    assert sc.classify_match(
        _match(name="Aurelium Defence Industries"),
        "Aurelium Security Ltd",
    ) == "hard_stop"


def test_malformed_similarity_is_unmeasurable_not_corroboration():
    assert sc.classify_match(
        _match(name="Black Shield Industries", string_similarity="unknown"),
        "Black Rose Security Ltd",
    ) == "amber"


def test_common_token_as_the_whole_name_keeps_escalating():
    assert sc.classify_match(
        _match(name="Black", score=0.9),
        "Black",
    ) == "hard_stop"


def test_classifier_failure_reaches_brain_wiring(monkeypatch):
    class BrokenMatch(dict):
        def get(self, *args, **kwargs):
            raise RuntimeError("corrupt provider match")

    failures = []
    monkeypatch.setattr(
        engine_wiring,
        "wire_failure",
        lambda **kwargs: failures.append(kwargs),
    )

    with pytest.raises(RuntimeError, match="corrupt provider match"):
        sc.classify_match(BrokenMatch(), "Black Rose Security Ltd")

    assert failures and failures[0]["module"] == "_sanctions_classify"
    assert "corrupt provider match" in failures[0]["detail"]
