"""GRPO cycle-4 — COVERAGE lever for grounding_reward.

Confirmed diagnosis (objective eval v1→v3): the sovereign model's precision-WHEN-
it-cites rose (0.85→0.93, beating DeepSeek) but the share of answerable rows it
cited on FELL (zero-citation rows 12.7%→22.4%), so overall precision AND recall
slipped below DeepSeek despite record-low fabrication. Root cause = COVERAGE: the
reward taught cite-perfectly-or-not-at-all. The cycle-4 change adds a coverage
bonus for GROUNDED citation breadth (double-gated on grounding + precision so it
can never reward fabrication) and a configurable answerable-no-citation penalty,
and restores precision_weight to 0.5.

These tests prove the NEW reward:
  1. scores a well-grounded answerable answer HIGHER than a 0-citation one,
  2. rewards citing MORE grounded facts (coverage) on answerable rows,
  3. does NOT reward fabrication (adding a fabricated citation strictly lowers score),
  4. keeps honest silence >= fabrication (never inverts the honesty ordering),
  5. is DEFAULT-UNCHANGED (coverage off by default → objective eval stays comparable).
"""
from aria_service.intel import grounding_reward as gr

# Two distinct grounded source labels present in the context.
_CTX = (
    "Context:\n"
    "↳ source: acme-annual-report-2026\n"
    "The company reported revenue growth.\n"
    "↳ source: ofsi-consolidated-list-2026\n"
    "The entity appears on the sanctions list.\n"
)

# Cycle-4 candidate config (what the launch will set).
_COV_W = 0.15
_TGT = 2.0


def _sc(answer, **kw):
    return gr.score(answer, _CTX, answerable=True, **kw)


def test_grounded_answer_beats_zero_citation_on_answerable():
    grounded = "Revenue grew [Source: acme-annual-report-2026]."
    zero_cite = "Revenue grew, but no source is cited here."
    b_g = _sc(grounded, coverage_weight=_COV_W, coverage_target=_TGT)
    b_z = _sc(zero_cite, coverage_weight=_COV_W, coverage_target=_TGT)
    assert b_g.score > b_z.score
    # the 0-citation answerable answer is the coverage failure we penalise
    assert "answered_answerable_without_citation" in b_z.reasons


def test_coverage_bonus_rewards_more_grounded_citations():
    one = "Revenue grew [Source: acme-annual-report-2026]."
    two = ("Revenue grew [Source: acme-annual-report-2026]; the entity is "
           "listed [Source: ofsi-consolidated-list-2026].")
    b_one = _sc(one, coverage_weight=_COV_W, coverage_target=_TGT)
    b_two = _sc(two, coverage_weight=_COV_W, coverage_target=_TGT)
    assert b_two.grounded_citations == 2 and b_one.grounded_citations == 1
    assert b_two.score > b_one.score, "citing a 2nd grounded fact must pay (coverage)"
    # reallocation blend: more coverage => higher relative score (delta ordering)
    assert b_two.coverage_bonus > b_one.coverage_bonus


def test_coverage_bonus_never_rewards_fabrication():
    # 2 grounded citations (clean) vs 2 grounded + 1 FABRICATED. Adding a fabricated
    # citation to farm coverage must LOWER the score, never raise it.
    clean = ("Revenue grew [Source: acme-annual-report-2026]; listed "
             "[Source: ofsi-consolidated-list-2026].")
    farmed = ("Revenue grew [Source: acme-annual-report-2026]; listed "
              "[Source: ofsi-consolidated-list-2026]; also "
              "[Source: totally-made-up-source-xyz].")
    b_clean = _sc(clean, coverage_weight=_COV_W, coverage_target=_TGT)
    b_farm = _sc(farmed, coverage_weight=_COV_W, coverage_target=_TGT)
    assert b_farm.fabricated_citations == 1
    assert b_farm.score < b_clean.score, "adding a fabricated citation must not pay"


def test_honest_silence_never_below_fabrication():
    # A fabricated-only answer must not score above answering-without-citation.
    fabricated = "The figure is 999 [Source: invented-source-abc]."
    silent = "Revenue grew, but no source is cited."
    b_fab = _sc(fabricated, coverage_weight=_COV_W, coverage_target=_TGT)
    b_silent = _sc(silent, coverage_weight=_COV_W, coverage_target=_TGT)
    assert b_silent.score >= b_fab.score


def test_default_is_unchanged_backward_compatible():
    # With coverage OFF (default), the score must equal the R-F2586 baseline exactly.
    ans = "Revenue grew [Source: acme-annual-report-2026]."
    default = gr.score(ans, _CTX, answerable=True, expected_keywords=["revenue"])
    explicit_off = gr.score(ans, _CTX, answerable=True, expected_keywords=["revenue"],
                            coverage_weight=0.0, coverage_target=2.0,
                            answerable_nocite_penalty=0.1, precision_weight=0.5)
    assert abs(default.score - explicit_off.score) < 1e-9
    assert default.coverage_bonus == 0.0
    # 0-citation answerable default stays the historical 0.1 (not the new lower penalty)
    b_zero = gr.score("Revenue grew, no source.", _CTX, answerable=True)
    assert abs(b_zero.score - 0.1) < 1e-9


def test_reward_scalar_threads_coverage_params():
    two = ("Revenue grew [Source: acme-annual-report-2026]; listed "
           "[Source: ofsi-consolidated-list-2026].")
    one = "Revenue grew [Source: acme-annual-report-2026]."
    r_two = gr.reward(two, _CTX, answerable=True, coverage_weight=_COV_W, coverage_target=_TGT)
    r_one = gr.reward(one, _CTX, answerable=True, coverage_weight=_COV_W, coverage_target=_TGT)
    r_two_off = gr.reward(two, _CTX, answerable=True)
    r_one_off = gr.reward(one, _CTX, answerable=True)
    assert r_two > r_one            # coverage lever live through the scalar path
    assert abs(r_two_off - r_one_off) < 1e-9  # off: 1 vs 2 clean citations both precision 1.0


def test_lower_nocite_penalty_still_floored_at_fabrication_floor():
    # Even if a cycle sets the penalty below 0.05, score() floors it at 0.05 so honest
    # silence can never be scored below the fabrication floor.
    b = gr.score("Revenue grew, no source.", _CTX, answerable=True,
                 answerable_nocite_penalty=0.0)
    assert b.score >= 0.05
