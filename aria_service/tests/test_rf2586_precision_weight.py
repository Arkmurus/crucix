"""R-F2586 — GRPO cycle 3 precision lever: precision_weight tunes the composite
precision/recall split. Default 0.5 = v2 (R-F2558) behaviour (backward-compatible);
cycle 3 raises it so the reward gradient favours citation precision (the only gap
left to a decisive composite win — recall proved inert in v2).

Proves the weight moves the score in BOTH directions and leaves the default alone.
"""
from aria_service.intel import grounding_reward as gr

_CTX = "Context:\n↳ source: acme-annual-report-2026\nThe company reported figures.\n"


def _score(answer, pw):
    return gr.score(answer, _CTX, answerable=True,
                    expected_keywords=["revenue", "margin"], precision_weight=pw)


def test_higher_precision_weight_rewards_high_precision_low_recall():
    # 1 grounded citation, no keywords present → precision_score 1.0, recall 0.0.
    ans = "Per the filing [Source: acme-annual-report-2026], the entity is assessed."
    b_half = _score(ans, 0.5)
    b_hi = _score(ans, 0.75)
    assert b_half.citation_precision == 1.0 and b_half.keyword_recall == 0.0
    # precision > recall → weighting precision MORE raises the score.
    assert b_hi.score > b_half.score
    assert abs(b_half.score - 0.5) < 1e-6      # 0.5*1.0 + 0.5*0.0
    assert abs(b_hi.score - 0.75) < 1e-6       # 0.75*1.0 + 0.25*0.0


def test_higher_precision_weight_penalises_low_precision_high_recall():
    # 1 grounded + 1 fabricated citation (precision 0.5), both keywords present.
    ans = ("Revenue and margin per [Source: acme-annual-report-2026] "
           "and [Source: fabricated-source-xyz].")
    b_half = _score(ans, 0.5)
    b_hi = _score(ans, 0.75)
    assert b_half.citation_precision == 0.5 and b_half.keyword_recall == 1.0
    # recall > precision_score → weighting precision MORE lowers the score.
    assert b_hi.score < b_half.score


def test_default_precision_weight_is_unchanged():
    # Omitting precision_weight must reproduce the exact 0.5/0.5 v2 behaviour.
    ans = "Per [Source: acme-annual-report-2026] revenue is noted."
    default = gr.score(ans, _CTX, answerable=True, expected_keywords=["revenue", "margin"])
    explicit = _score(ans, 0.5)
    assert abs(default.score - explicit.score) < 1e-9


def test_reward_scalar_passes_precision_weight_through():
    ans = "Per the filing [Source: acme-annual-report-2026], assessed."
    r_half = gr.reward(ans, _CTX, answerable=True, expected_keywords=["revenue", "margin"], precision_weight=0.5)
    r_hi = gr.reward(ans, _CTX, answerable=True, expected_keywords=["revenue", "margin"], precision_weight=0.75)
    assert r_hi > r_half
