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
