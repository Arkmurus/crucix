"""R-F2033 — answerable-aware grounding reward kills the abstention-gaming hole.

The 2026-06-27 GRPO run drove in-training grounding_reward to 1.0 but held-out
grounding stayed at the SFT 0.21 — because the model learned to ABSTAIN ~91% of
the time: the old reward gave a flat 0.5 for "abstained despite context" with no
penalty for refusing an ANSWERABLE question. These tests pin the fix: abstaining
on an answerable question is now low; grounded answering wins; honest abstention
on unanswerable questions still scores high.
"""
from aria_service.intel import grounding_reward as gr

_CTX = ("[RAG RETRIEVED]\nThe FAA had ~107,000 active personnel in 2024. [Source: sipri:2024]\n"
        "Procurement increased 12%. [Source: registry:mod]\n")
_GROUNDED = "The FAA has ~107,000 active personnel [Source: sipri:2024]."
_GROUNDED_NO_FACT = "There is relevant information here [Source: sipri:2024]."
_FABRICATED = "The FAA has 999,999 troops [Source: made_up:99]."
_ABSTAIN = "Based solely on the context, I cannot confirm the FAA personnel figure."


def test_abstaining_on_answerable_is_penalized():
    # the gaming hole: this used to score 0.5 — now it must be low.
    b = gr.score(_ABSTAIN, _CTX, answerable=True)
    assert b.score <= 0.15, f"abstain on answerable must be penalized, got {b.score}"
    assert "abstained_on_answerable" in b.reasons


def test_honest_abstention_on_unanswerable_still_rewarded():
    b = gr.score(_ABSTAIN, _CTX, answerable=False)
    assert b.score >= 0.9, f"honest abstention must stay high, got {b.score}"
    assert "correct_abstention_unanswerable" in b.reasons


def test_grounded_answer_beats_abstaining_on_answerable():
    # THE ranking GRPO will now optimize: answer-with-grounding >> abstain.
    answered = gr.reward(_GROUNDED, _CTX, answerable=True,
                         expected_keywords=["107,000", "FAA"])
    abstained = gr.reward(_ABSTAIN, _CTX, answerable=True)
    fabricated = gr.reward(_FABRICATED, _CTX, answerable=True,
                           expected_keywords=["107,000", "FAA"])
    assert answered > abstained, f"grounded answer ({answered}) must beat abstain ({abstained})"
    assert answered > fabricated, f"grounded answer ({answered}) must beat fabricated ({fabricated})"
    assert abstained > fabricated or fabricated <= 0.1, "fabrication is the worst"


def test_keyword_substance_bonus_rewards_real_answering():
    # both grounded, but one actually states the expected fact — it should win.
    with_fact = gr.reward(_GROUNDED, _CTX, answerable=True, expected_keywords=["107,000", "FAA"])
    without_fact = gr.reward(_GROUNDED_NO_FACT, _CTX, answerable=True, expected_keywords=["107,000", "FAA"])
    assert with_fact > without_fact, f"stating the fact ({with_fact}) must beat empty grounding ({without_fact})"


def test_keyword_bonus_never_rewards_fabrication():
    # a fabricated answer that happens to contain the keyword must NOT get the bonus.
    fab = gr.score(_FABRICATED, _CTX, answerable=True, expected_keywords=["FAA"])
    assert fab.score <= 0.1, f"fabrication must stay near 0 regardless of keywords, got {fab.score}"


def test_answering_when_should_abstain_is_capped():
    # symmetry: on an UNANSWERABLE question, abstaining must clearly beat answering.
    answered = gr.reward(_GROUNDED, _CTX, answerable=False)   # grounded but shouldn't have answered
    abstained = gr.reward(_ABSTAIN, _CTX, answerable=False)
    assert abstained > answered, f"abstain ({abstained}) must beat over-claiming ({answered})"
    assert answered <= 0.25, f"answering a should-abstain Q must be capped, got {answered}"


def test_backward_compat_unknown_answerability_unchanged():
    # answerable=None (the default) preserves the original 0.5 for abstain-despite-context.
    b = gr.score(_ABSTAIN, _CTX)
    assert abs(b.score - 0.5) < 1e-9 and "abstained_despite_context" in b.reasons
