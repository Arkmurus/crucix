"""Regression guard for F26 — hypothesis-validation domain filter.

Live observation 2026-04-27 19:31:55-19:32:25: hypothesis "MQ-25A
Stingray first flight marks a critical milestone" went through
validate_hypothesis. CrossRef returned DOI matches by keyword overlap
across ALL its indexed papers — medical-research and literature-review
DOIs ranked equal-weight to defence ones. ARIA then Lightpanda-
rendered casemedicalresearch.com (×2) and bloomsburycollections.com,
ingesting medical-research text into RAG as "defence evidence".

Fix: _is_plausible_defence_domain() rejects URLs on a denylist of
medical/literature/legal/lifestyle aggregators before _fetch_article_text
runs. Unknown domains pass through unchanged (denylist, not allowlist).
"""
from __future__ import annotations

from aria_service.intel.researcher import _is_plausible_defence_domain


# ── The exact 2026-04-27 false positives ───────────────────────────────────

def test_casemedicalresearch_blocked():
    """Live evidence URL — must be rejected."""
    assert not _is_plausible_defence_domain(
        "https://my.casemedicalresearch.com/api/v2/doi/10.31525/cmr-24aaf8a"
    )
    assert not _is_plausible_defence_domain("http://ww1.casemedicalresearch.com")
    assert not _is_plausible_defence_domain("http://alpha.casemedicalresearch.com/x")


def test_bloomsburycollections_blocked():
    """Literature aggregator from same incident."""
    assert not _is_plausible_defence_domain(
        "https://www.bloomsburycollections.com/monograph-detail?docid=b-9781350469631&tocid=b-9781350469631-chapter6"
    )


# ── Other implausible domains caught by the denylist ─────────────────────

def test_medical_research_aggregators_blocked():
    for url in [
        "https://pubmed.ncbi.nlm.nih.gov/12345",
        "https://www.nejm.org/doi/full/10.1056/abc",
        "https://www.thelancet.com/journals/lancet/article",
        "https://www.bmj.com/content/abc",
    ]:
        assert not _is_plausible_defence_domain(url), url


def test_literature_humanities_aggregators_blocked():
    assert not _is_plausible_defence_domain("https://www.jstor.org/stable/12345")
    assert not _is_plausible_defence_domain("https://muse.jhu.edu/article/123")


def test_lifestyle_retail_blocked():
    assert not _is_plausible_defence_domain("https://www.amazon.com/dp/B07XYZ")
    assert not _is_plausible_defence_domain("https://www.tripadvisor.com/Hotel_Review")
    assert not _is_plausible_defence_domain("https://www.allrecipes.com/recipe/12345")


# ── Defence-plausible domains pass through ────────────────────────────────

def test_defence_news_passes():
    for url in [
        "https://www.defensenews.com/article",
        "https://breakingdefense.com/2026/01/article",
        "https://www.janes.com/some-article",
        "https://warontherocks.com/article-on-isr",
        "https://www.shephardmedia.com/news/defence-notes/article",
        "https://www.naval-technology.com/projects/abc",
    ]:
        assert _is_plausible_defence_domain(url), url


def test_official_sources_pass():
    for url in [
        "https://www.dsca.mil/notification/abc",
        "https://www.bis.doc.gov/notice",
        "https://www.gov.uk/government/news/defence",
        "https://ec.europa.eu/sanctions",
        "https://eur-lex.europa.eu/eli/dec/2025/abc/oj",
    ]:
        assert _is_plausible_defence_domain(url), url


def test_unknown_domain_passes_through():
    """Denylist not allowlist — unknown domains pass."""
    assert _is_plausible_defence_domain("https://random-defence-blog.example/post")


def test_empty_url_rejected():
    """Empty URL is not plausible — caller should not fetch nothing."""
    assert not _is_plausible_defence_domain("")
    assert not _is_plausible_defence_domain(None)  # type: ignore[arg-type]
