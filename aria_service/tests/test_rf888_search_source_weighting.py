"""R-F888 — web_search relevance demotes academic registries to fallback.

Live 2026-05-25 (operator "zero confidence"): "who is the current US president"
returned Crossref's "Who Was Who 2007" (stops at G.W. Bush) over DuckDuckGo's
live "President Donald Trump 2025-2029". The backends individually returned the
CORRECT answer — the ranking buried it: _score_relevance = term_overlap ×
credibility_mult, and academic (Crossref tier 1-2 → ×1.3-1.5) with keyword-dense
titles out-scored live web. Fix: source-type weighting (academic ×0.45 fallback,
live-web/news ×1.25). Verified locally — the query now returns live DuckDuckGo
results top, no Crossref.
"""
from __future__ import annotations

from aria_service.intel.web_search import SearchResult, _score_relevance

_Q = "who is the current president of the united states"


def _mk(source, tier, title):
    return SearchResult(title=title, url=f"https://{source}.example/x", snippet=title,
                        source=source, credibility_tier=tier)


def test_live_web_outranks_academic_on_equal_overlap():
    # both titles match the query strongly; academic even has the BETTER tier
    academic = _mk("crossref", 1, "Wilson, Woodrow — President of the United States")
    liveweb = _mk("duckduckgo", 3, "Donald Trump — current President of the United States 2025")
    assert _score_relevance(liveweb, _Q) > _score_relevance(academic, _Q)


def test_news_outranks_academic():
    academic = _mk("crossref", 2, "President of the United States: a biographical study")
    news = _mk("google_news", 4, "President Trump signs order — President of the United States")
    assert _score_relevance(news, _Q) > _score_relevance(academic, _Q)


def test_academic_still_scores_above_zero_when_only_source():
    # research queries where only academic returns still surface it (>0)
    academic = _mk("semantic_scholar", 1, "President of the United States — constitutional powers")
    assert _score_relevance(academic, _Q) > 0.0


def test_academic_penalised_relative_to_unknown_source():
    academic = _mk("openalex", 1, "President of the United States review article")
    generic = _mk("some_blog", 5, "President of the United States explainer")
    # academic (×0.45) should not beat a plain general source (×1.0) on equal overlap+
    assert _score_relevance(academic, _Q) < _score_relevance(generic, _Q)
