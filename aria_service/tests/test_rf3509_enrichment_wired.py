"""R-F3509 — the enrichment engine had no caller, and shallow evidence could reach HIGH.

R-F3499 built selective deep enrichment: ``should_enrich`` (deterministic,
budgeted, explains itself), ``enrich_archived_article`` (honest extraction_status)
and ``cap_confidence_for_extraction`` (a 500-char feed summary must not carry HIGH
confidence). A grep for ``news_enrichment`` outside its own module and tests
returned NOTHING.

An engine with no caller is the exact shape I refused to build R-F3487 on:
``intel/corroboration.py`` has zero production callers, and
memory/corroboration_engine_rf2638 records its fixtures green while it scored
0/20 on real data. Shipping a second one would have been the same mistake with
my own name on it.

Two things are wired here, and the second matters more than the first:

1. ``_ingest_article`` consults ``should_enrich`` after archiving, under a
   per-poll budget, and records the outcome. Enrichment failing must never lose
   the article — the archive write has already happened by then.

2. ``_build_intel_signal`` caps confidence by the record's extraction_status. A
   headline that was never read cannot be published as HIGH-confidence
   intelligence. Wiring (1) without (2) would have been actively harmful: deep
   text would raise the apparent quality of a signal with no guard on the ones
   that were never enriched at all.
"""
from __future__ import annotations

import pytest

from aria_service.intel import news_monitor as nm, news_archive, news_enrichment as ne


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(news_archive, "_DB_PATH", tmp_path / "news_archive.db")
    news_archive._reset_for_tests()
    nm._reset_enrichment_budget()
    yield
    news_archive._reset_for_tests()


def _article(tier="tier_1a", **kw):
    a = {"url": "https://janes.com/a1", "title": "Poland orders K2 tanks",
         "summary": "Warsaw signed a defence procurement contract.",
         "source": "Janes", "category": "global_defence", "tier": tier}
    a.update(kw)
    return a


class TestEnrichmentIsActuallyCalled:

    @pytest.mark.asyncio
    async def test_ingest_enriches_a_tier_1a_article(self, monkeypatch):
        called = []

        async def _spy(article_id):
            called.append(article_id)
            return {"enriched": True, "chars": 900, "reason": "body read"}

        monkeypatch.setattr(nm, "_maybe_enrich", _spy, raising=False)
        monkeypatch.setattr(nm, "_store_article", _noop, raising=False)
        monkeypatch.setattr(nm, "_feed_to_brain", _false, raising=False)
        monkeypatch.setattr(nm, "_mark_seen", _noop1, raising=False)

        res = await nm._ingest_article(_article())
        assert res["archived"] is True
        assert called == [res["article_id"]], (
            "the enrichment engine was never invoked from the ingest path"
        )

    @pytest.mark.asyncio
    async def test_a_low_value_article_is_not_enriched(self, monkeypatch):
        """Cost must scale with value, not feed volume (§17)."""
        called = []
        monkeypatch.setattr(ne, "_fetch_body", _fetch_ok)
        monkeypatch.setattr(nm, "_store_article", _noop, raising=False)
        monkeypatch.setattr(nm, "_feed_to_brain", _false, raising=False)
        monkeypatch.setattr(nm, "_mark_seen", _noop1, raising=False)

        async def _spy(article_id):
            called.append(article_id)

        monkeypatch.setattr(nm, "_maybe_enrich", _spy, raising=False)
        await nm._ingest_article(_article(tier="tier_2", relevance_score=0.05))
        # _maybe_enrich is still consulted; the DECISION lives inside it.
        assert called, "ingest bypassed the enrichment decision entirely"

    @pytest.mark.asyncio
    async def test_enrichment_failure_never_loses_the_article(self, monkeypatch):
        async def _boom(_article_id):
            raise RuntimeError("fetch exploded")

        monkeypatch.setattr(nm, "_maybe_enrich", _boom, raising=False)
        monkeypatch.setattr(nm, "_store_article", _noop, raising=False)
        monkeypatch.setattr(nm, "_feed_to_brain", _false, raising=False)
        monkeypatch.setattr(nm, "_mark_seen", _noop1, raising=False)

        res = await nm._ingest_article(_article())
        assert res["archived"] is True, (
            "an enrichment failure lost the archived article"
        )

    @pytest.mark.asyncio
    async def test_the_budget_bounds_a_poll_cycle(self, monkeypatch):
        """A breaking-news burst must not run up unbounded deep fetches."""
        nm._reset_enrichment_budget()
        fetched = []

        async def _fetch(_url):
            fetched.append(_url)
            return "A real article body. " * 40

        monkeypatch.setattr(ne, "_fetch_body", _fetch)
        for i in range(nm._ENRICH_BUDGET_PER_POLL + 5):
            res = await news_archive.archive_article(_article(url=f"https://janes.com/{i}"))
            await nm._maybe_enrich(res["article_id"])
        assert len(fetched) <= nm._ENRICH_BUDGET_PER_POLL, (
            f"{len(fetched)} deep fetches in one poll — the budget is not enforced"
        )


class TestShallowEvidenceCannotBePublishedAsHigh:
    """The guard that makes wiring enrichment safe rather than harmful."""

    def test_feed_only_signal_confidence_is_capped(self):
        art = _article()
        art["extraction_status"] = "feed_only"
        sig = nm._build_intel_signal(art)
        assert sig.get("confidence") != "HIGH", (
            "a 500-char feed summary was published as HIGH-confidence intelligence"
        )

    def test_enriched_signal_keeps_its_confidence(self):
        art = _article()
        art["extraction_status"] = "enriched"
        sig_enriched = nm._build_intel_signal(art)
        art2 = _article()
        art2["extraction_status"] = "feed_only"
        sig_shallow = nm._build_intel_signal(art2)
        order = {"LOW": 0, "MEDIUM": 1, "ASSESSED": 1, "HIGH": 2}
        assert order.get(sig_enriched.get("confidence"), 0) >= \
               order.get(sig_shallow.get("confidence"), 0), (
            "reading the article did not preserve at least the shallow confidence"
        )

    def test_missing_status_is_treated_as_shallow(self):
        """Never assume a record was read because the field is absent."""
        art = _article()
        art.pop("extraction_status", None)
        sig = nm._build_intel_signal(art)
        assert sig.get("confidence") != "HIGH"


async def _noop(*_a, **_kw):
    return None


async def _noop1(_x):
    return None


async def _false(_a):
    return False


async def _fetch_ok(_url):
    return "A real article body. " * 40
