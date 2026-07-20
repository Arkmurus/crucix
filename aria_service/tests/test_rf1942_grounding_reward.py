"""R-F1942 — the verifiable grounding reward must reward grounded/honest answers
and punish fabricated sources, objectively (the reasoning analog of tests-pass)."""
from __future__ import annotations

from aria_service.intel import grounding_reward as gr

_CTX = (
    "[RAG RETRIEVED]\n"
    "- Asset freezes apply to designated entities. [Source: web_search:EU sanctions]\n"
    "- The UK uses SAMLA 2018. [Source: intlaw:sanctions_law]\n"
)


def test_fully_grounded_scores_high():
    ans = ("The EU applies asset freezes [Source: web_search:EU sanctions]; "
           "the UK uses SAMLA 2018 [Source: intlaw:sanctions_law].")
    b = gr.score(ans, _CTX)
    assert b.citation_precision == 1.0
    assert b.fabricated_citations == 0
    assert b.score >= 0.95
    assert "fully_grounded" in b.reasons


def test_fabricated_citation_is_punished():
    ans = ("The EU froze $4.2B in assets [Source: web_search:EU sanctions] "
           "per the 2019 Treaty of Lisbon Annex [Source: treaty:lisbon_annex_7].")
    b = gr.score(ans, _CTX)
    assert b.fabricated_citations == 1          # the lisbon annex is NOT in context
    assert b.score < 0.6                        # heavily penalised vs fully-grounded
    assert any("fabricated" in r for r in b.reasons)


def test_all_fabricated_scores_near_zero():
    ans = "Per [Source: fake:doc1] and [Source: fake:doc2], the answer is X."
    b = gr.score(ans, _CTX)
    assert b.grounded_citations == 0
    assert b.score <= 0.05


def test_correct_abstention_no_context_scores_high():
    b = gr.score("The provided context does not contain information to answer this.", "[RAG RETRIEVED]\n(no sources)")
    assert b.abstained is True
    assert b.context_has_sources is False
    assert b.score == 1.0


def test_fabricating_when_no_context_scores_zero():
    b = gr.score("The answer is 42 [Source: made:up].", "[RAG RETRIEVED]\n(no sources)")
    assert b.score == 0.0


def test_answer_without_citation_scores_low():
    b = gr.score("The EU and UK both impose asset freezes on designated persons.", _CTX)
    assert b.total_citations == 0
    assert b.score <= 0.2


def test_scalar_reward_orders_grounded_above_fabricated():
    good = gr.reward("Asset freezes apply [Source: web_search:EU sanctions].", _CTX)
    bad = gr.reward("Asset freezes apply [Source: invented:source].", _CTX)
    assert good > bad


# --- R-F2788 (cycle 6) — additive-capped RECALL BONUS lever ------------------
# The reward CTX above carries two grounded facts (asset freezes, SAMLA 2018).
_KW = ["asset freezes", "samla 2018"]
_SUBSTANTIVE = ("The EU applies asset freezes and the UK uses SAMLA 2018 "
                "[Source: web_search:EU sanctions].")   # states both keywords, grounded
_TERSE = "The position is as noted [Source: web_search:EU sanctions]."  # grounded, 0 keywords


def test_recall_bonus_default_off_is_byte_identical():
    # With the knob at its default (0.0) the headroom branch must NOT fire — score()
    # must equal the pre-R-F2788 reallocation-blend behaviour exactly, so the frozen
    # objective eval stays comparable.
    for ans in (_SUBSTANTIVE, _TERSE):
        default = gr.score(ans, _CTX, answerable=True, expected_keywords=_KW).score
        explicit0 = gr.score(ans, _CTX, answerable=True, expected_keywords=_KW,
                             recall_bonus_weight=0.0).score
        assert default == explicit0
    # And the terse grounded answer still gets the OLD harsh blend (pw=0.5) when off.
    terse_off = gr.score(_TERSE, _CTX, answerable=True, expected_keywords=_KW).score
    assert abs(terse_off - 0.5) < 1e-9   # 0.5*precision(1.0) + 0.5*recall(0.0)


def test_recall_bonus_on_substantive_beats_terse_without_trading_precision():
    on = dict(answerable=True, expected_keywords=_KW, recall_bonus_weight=0.15)
    sub = gr.score(_SUBSTANTIVE, _CTX, **on)
    terse = gr.score(_TERSE, _CTX, **on)
    # (b) substantive-grounded strictly beats terse-grounded even though BOTH are
    # fully grounded (precision saturates at 1.0) — the saturation problem is solved.
    assert sub.score > terse.score
    assert sub.citation_precision == 1.0 and terse.citation_precision == 1.0
    # precision PRESERVED, not traded: the terse floor is (1-rbw)*precision = 0.85,
    # far above the reallocation blend's 0.5 — recall is an additive band, not a swap.
    assert abs(terse.score - 0.85) < 1e-9
    assert abs(sub.score - 1.0) < 1e-9
    assert sub.recall_bonus > 0.0


def test_recall_bonus_cannot_farm_fabricated_or_uncited():
    on = dict(answerable=True, expected_keywords=_KW, recall_bonus_weight=0.15)
    # (c1) an uncited keyword-stuffed answer (no grounded citation) cannot earn the
    # bonus — the lever is gated on grounded_citations > 0.
    stuffed_uncited = gr.score("The EU applies asset freezes and the UK uses SAMLA 2018.",
                               _CTX, **on)
    assert stuffed_uncited.recall_bonus == 0.0
    assert stuffed_uncited.score <= 0.2
    # (c2) a keyword-stuffed answer whose ONLY citation is fabricated stays near zero
    # (no grounded citation -> capped at 0.05); the recall term never rescues it.
    stuffed_fab = gr.score("The EU applies asset freezes and the UK uses SAMLA 2018 "
                           "[Source: invented:doc].", _CTX, **on)
    assert stuffed_fab.grounded_citations == 0
    assert stuffed_fab.score <= 0.05
    # A fully-substantive-and-grounded answer must still clearly beat both.
    good = gr.score(_SUBSTANTIVE, _CTX, **on)
    assert good.score > stuffed_uncited.score and good.score > stuffed_fab.score


def test_recall_bonus_cap_bounds_recall_share():
    # recall_bonus_cap hard-ceilings rbw: even a huge weight can't push the terse
    # (zero-recall) floor below (1 - cap)*precision, so recall can never dominate.
    terse = gr.score(_TERSE, _CTX, answerable=True, expected_keywords=_KW,
                     recall_bonus_weight=5.0, recall_bonus_cap=0.25)
    assert abs(terse.score - 0.75) < 1e-9   # (1 - 0.25)*precision(1.0)


# --- R-F2805 (cycle 7) — GROUNDED recall knob ---------------------------------
# _CTX contains "asset freezes" and "SAMLA 2018"; "SIPRI"/"OIEL" are NOT in it
# (they are ungrounded expert domain vocab — the ~75% of eval gold keywords that
# are not in the retrieved context and whose raw-recall reward = a fabrication push).
_KW_MIXED = ["asset freezes", "SIPRI"]   # 1 grounded, 1 ungrounded
_ANS_GROUNDED = "Asset freezes apply. [Source: web_search:EU sanctions]"
_ANS_UNGROUNDED = "Asset freezes apply per SIPRI. [Source: web_search:EU sanctions]"


def test_grounded_recall_default_off_is_raw():
    # Default (flag off): keyword_recall is the ORIGINAL raw recall, so stating the
    # UNGROUNDED "SIPRI" RAISES recall — exactly the mis-incentive cycles 5/6 hit.
    g = gr.score(_ANS_GROUNDED, _CTX, answerable=True, expected_keywords=_KW_MIXED)
    u = gr.score(_ANS_UNGROUNDED, _CTX, answerable=True, expected_keywords=_KW_MIXED)
    assert abs(g.keyword_recall - 0.5) < 1e-9
    assert abs(u.keyword_recall - 1.0) < 1e-9
    assert gr.score(_ANS_GROUNDED, _CTX, answerable=True, expected_keywords=_KW_MIXED,
                    grounded_recall_only=False).score == g.score   # byte-identical when off


def test_grounded_recall_ignores_ungrounded_keywords():
    # Flag ON: recall scored ONLY over in-context keywords -> stating ungrounded
    # "SIPRI" earns NO extra recall; the fabrication incentive is removed.
    on = dict(answerable=True, expected_keywords=_KW_MIXED, grounded_recall_only=True)
    g = gr.score(_ANS_GROUNDED, _CTX, **on)
    u = gr.score(_ANS_UNGROUNDED, _CTX, **on)
    assert abs(g.keyword_recall - 1.0) < 1e-9      # grounded kws = ["asset freezes"] -> 1/1
    assert u.keyword_recall == g.keyword_recall    # ungrounded SIPRI adds nothing
    assert gr.reward(_ANS_UNGROUNDED, _CTX, expected_keywords=_KW_MIXED,
                     answerable=True, grounded_recall_only=True) == u.score


def test_grounded_recall_no_incontext_keywords_is_zero():
    # All gold keywords ungrounded -> grounded recall 0.0 (nothing extractable; the
    # recall term is neutral, so there is no push to invent them).
    b = gr.score("SIPRI and OIEL matter. [Source: web_search:EU sanctions]", _CTX,
                 answerable=True, expected_keywords=["SIPRI", "OIEL"],
                 grounded_recall_only=True)
    assert b.keyword_recall == 0.0
