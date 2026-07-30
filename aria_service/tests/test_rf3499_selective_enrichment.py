"""R-F3499 — selective deep enrichment: fetch bodies only where they change the answer.

Normal RSS ingestion keeps a maximum 500-character feed description
(news_monitor.py:569,607) and never fetches the article body. Downstream the
intel ledger keeps 500 chars and the brain absorbs 200 (intel_ledger.py:608).

That is enough for headline monitoring, coarse classification and fast alerts.
It is NOT enough to know who made a claim, to separate reported fact from quoted
allegation, to see the caveats, or to find the supporting numbers — and those are
exactly the distinctions this product sells.

Deep-fetching EVERYTHING is the wrong answer in the other direction:

  * cost and latency scale with volume, not with value
  * storing full bodies for every publisher raises real copyright and
    personal-data retention exposure (CLAUDE.md §18 already treats source
    licensing as a first-class constraint, not an afterthought)
  * most feed items never influence any assessment

So enrichment is SELECTIVE and the decision is deterministic, explainable and
recorded — never "whatever the crawler felt like". A record carries its
``extraction_status`` so every downstream consumer can tell a headline from a
read article, and the archive's ``body_ref`` keeps the retention decision
governed per source rather than inlining text by default.

The honesty property that matters most: a shallow headline must never be
presentable as if the article had been read.
"""
from __future__ import annotations

import pytest

from aria_service.intel import news_enrichment as ne, news_archive


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(news_archive, "_DB_PATH", tmp_path / "news_archive.db")
    news_archive._reset_for_tests()
    yield
    news_archive._reset_for_tests()


def _article(**kw):
    a = {"url": "https://janes.com/a1", "title": "Poland orders K2 tanks",
         "summary": "Warsaw signed a contract.", "source": "Janes",
         "category": "global_defence", "tier": "tier_1a"}
    a.update(kw)
    return a


class TestTheDecisionIsDeterministicAndExplained:

    def test_tier_1a_qualifies(self):
        ok, reason = ne.should_enrich(_article(tier="tier_1a"))
        assert ok is True
        assert reason, "an enrichment decision must carry its reason"

    def test_ordinary_tier_2_headline_does_not_qualify(self):
        ok, reason = ne.should_enrich(_article(tier="tier_2", relevance_score=0.1))
        assert ok is False
        assert reason

    def test_watched_entity_qualifies_even_at_low_tier(self):
        ok, reason = ne.should_enrich(
            _article(tier="tier_2", title="Rheinmetall wins Baltic contract"),
            watched_entities={"rheinmetall"})
        assert ok is True
        assert "watch" in reason.lower()

    def test_high_relevance_qualifies(self):
        ok, _ = ne.should_enrich(_article(tier="tier_2", relevance_score=0.95))
        assert ok is True

    def test_decision_is_stable_for_the_same_input(self):
        """Deterministic: the same article must not enrich only sometimes."""
        art = _article(tier="tier_2", relevance_score=0.4)
        assert {ne.should_enrich(art)[0] for _ in range(5)} == {ne.should_enrich(art)[0]}

    def test_every_refusal_names_a_reason(self):
        for art in (_article(tier="tier_2", relevance_score=0.0),
                    _article(tier="", relevance_score=None)):
            ok, reason = ne.should_enrich(art)
            if not ok:
                assert reason.strip(), "a silent refusal is unauditable"


class TestEnrichmentIsBudgeted:
    """Cost must scale with value, not with feed volume (§17)."""

    def test_a_budget_of_zero_enriches_nothing(self):
        assert ne.should_enrich(_article(tier="tier_1a"), budget_remaining=0)[0] is False

    def test_the_budget_reason_is_explicit(self):
        ok, reason = ne.should_enrich(_article(tier="tier_1a"), budget_remaining=0)
        assert ok is False and "budget" in reason.lower()


class TestExtractionStatusIsRecordedAndHonest:

    @pytest.mark.asyncio
    async def test_a_feed_only_record_says_so(self):
        res = await news_archive.archive_article(_article())
        rec = await news_archive.get_article(res["article_id"])
        assert rec["extraction_status"] == "feed_only", (
            "a record must state that only the feed summary was seen"
        )

    @pytest.mark.asyncio
    async def test_successful_enrichment_upgrades_the_status(self, monkeypatch):
        res = await news_archive.archive_article(_article())

        async def _fetch(_url):
            return "The full article body, considerably longer than the feed summary. " * 5

        monkeypatch.setattr(ne, "_fetch_body", _fetch)
        out = await ne.enrich_archived_article(res["article_id"])
        assert out["enriched"] is True
        rec = await news_archive.get_article(res["article_id"])
        assert rec["extraction_status"] == "enriched"

    @pytest.mark.asyncio
    async def test_a_failed_fetch_does_NOT_claim_enrichment(self, monkeypatch):
        """The honesty floor: a failed read must never look like a read article."""
        res = await news_archive.archive_article(_article())

        async def _boom(_url):
            raise RuntimeError("paywall")

        monkeypatch.setattr(ne, "_fetch_body", _boom)
        out = await ne.enrich_archived_article(res["article_id"])
        assert out["enriched"] is False
        rec = await news_archive.get_article(res["article_id"])
        assert rec["extraction_status"] != "enriched", (
            "a failed fetch left the record claiming the body had been read"
        )
        assert rec["extraction_status"] == "enrichment_failed"

    @pytest.mark.asyncio
    async def test_an_empty_body_is_not_enrichment(self, monkeypatch):
        res = await news_archive.archive_article(_article())

        async def _empty(_url):
            return "   "

        monkeypatch.setattr(ne, "_fetch_body", _empty)
        out = await ne.enrich_archived_article(res["article_id"])
        assert out["enriched"] is False
        rec = await news_archive.get_article(res["article_id"])
        assert rec["extraction_status"] != "enriched"


class TestShallowRecordsCannotPassAsRead:
    """The USP property. A headline must not be presentable as a read article."""

    def test_confidence_is_capped_for_feed_only_evidence(self):
        capped = ne.cap_confidence_for_extraction("HIGH", "feed_only")
        assert capped in {"LOW", "MEDIUM"}, (
            f"the cap must stay inside the caller's vocabulary, got {capped!r}")
        assert capped != "HIGH", (
            "a 500-char feed summary was allowed to carry HIGH confidence"
        )

    def test_enriched_evidence_keeps_its_confidence(self):
        assert ne.cap_confidence_for_extraction("HIGH", "enriched") == "HIGH"

    def test_failed_enrichment_is_capped_like_feed_only(self):
        assert ne.cap_confidence_for_extraction("HIGH", "enrichment_failed") != "HIGH"

    def test_unknown_status_is_treated_conservatively(self):
        """Never assume a record was read just because the status is unfamiliar."""
        assert ne.cap_confidence_for_extraction("HIGH", "") != "HIGH"
        assert ne.cap_confidence_for_extraction("HIGH", "something_new") != "HIGH"
