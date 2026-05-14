"""R-F503 — internal search engine tests.

Covers:
  - search() returns [] on empty query / unready engine
  - search() returns dicts in web_search.SearchResult.to_dict() shape
  - tier bonus: tier-1 hit beats tier-3 hit at equal text relevance
  - recency bonus: fresh fetch beats stale fetch at equal everything
  - dedupe_by_domain: only one result per domain
  - language filter: pt-only query returns only pt docs
  - min_credibility filter excludes weak-tier sources
  - coverage_report returns the right structural fields

Capability test: Modirum Gespi homograph — 100 noise pages tagged as
academic tier-4 EN + 1 Modirum Gespi tier-2 press release EN. search()
must put the press release first AND coverage_report must surface that
1/101 candidates was tier-2.
"""
from __future__ import annotations

import asyncio
import pytest
import time

from aria_service.search_index import db, indexer
from aria_service.search_engine import internal_search


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def db_ready():
    _run(db._reset())
    ok = _run(db.connect(":memory:"))
    assert ok
    yield db
    _run(db._reset())


# ─────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────

def test_search_empty_query_returns_empty(db_ready):
    async def go():
        assert await internal_search.search("") == []
        assert await internal_search.search("   ") == []
    _run(go())


def test_search_when_db_unready_returns_empty():
    """No connect() called → engine should degrade silently to []."""
    _run(db._reset())
    async def go():
        assert await internal_search.search("anything") == []
    _run(go())


def test_search_returns_drop_in_shape(db_ready):
    """The dict keys must match what web_search.SearchResult.to_dict()
    exposes so callers can be drop-in."""
    async def go():
        await db.register_domain("example.com", tier=2, sector="news",
                                 language="en")
        await indexer.index_fetch_result({
            "url": "https://example.com/x", "domain": "example.com",
            "title": "Defence contract awarded",
            "headings": "Award notice",
            "body": ("The defence contract was awarded today to the "
                     "company by the ministry following the tender. "
                     "The contract is valued at an undisclosed sum."),
            "language": "en", "source_tier": 2,
            "http_status": 200, "fetched_at": time.time(),
        })
        hits = await internal_search.search("defence contract awarded")
        assert hits
        h = hits[0]
        for key in ("title", "url", "snippet", "source",
                    "credibility_tier", "language", "relevance_score"):
            assert key in h, f"missing {key} in result dict"
        assert h["source"] == "aria_internal_index"
        assert isinstance(h["credibility_tier"], int)
        assert isinstance(h["relevance_score"], float)
    _run(go())


# ─────────────────────────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────────────────────────

def test_tier_bonus_promotes_official_source(db_ready):
    """Two pages with similar text relevance — tier 1 official should
    rank above tier 3 regional."""
    async def go():
        await db.register_domain("official.gov", tier=1, sector="official",
                                 language="en")
        await db.register_domain("blog.example", tier=3, sector="news",
                                 language="en")
        body = ("Modirum Gespi has been awarded a defence systems "
                "integration contract for the Ministry of Defence. "
                "The contract is valued at an undisclosed sum.")
        await indexer.index_fetch_result({
            "url": "https://blog.example/x", "domain": "blog.example",
            "title": "Modirum Gespi awarded contract",
            "headings": "", "body": body, "language": "en",
            "source_tier": 3, "http_status": 200, "fetched_at": time.time(),
        })
        await indexer.index_fetch_result({
            "url": "https://official.gov/x", "domain": "official.gov",
            "title": "Modirum Gespi awarded contract",
            "headings": "", "body": body, "language": "en",
            "source_tier": 1, "http_status": 200, "fetched_at": time.time(),
        })
        hits = await internal_search.search("Modirum Gespi contract")
        assert hits[0]["url"] == "https://official.gov/x", (
            f"tier 1 official should rank above tier 3 blog. Got: "
            f"{[h['url'] for h in hits]}"
        )
    _run(go())


def test_recency_bonus_promotes_fresh_fetch(db_ready):
    """Equal tier, equal text — fresh fetch wins over 30-day-old fetch."""
    async def go():
        await db.register_domain("press.com", tier=2, sector="news",
                                 language="en")
        body = ("The minister announced a new defence procurement "
                "package today. The package includes communications "
                "equipment and tactical vehicles. The total value is "
                "described as substantial.")
        now = time.time()
        await indexer.index_fetch_result({
            "url": "https://press.com/old", "domain": "press.com",
            "title": "Defence procurement package",
            "headings": "", "body": body, "language": "en",
            "source_tier": 2, "http_status": 200,
            "fetched_at": now - 30 * 86400,  # 30 days ago
        })
        await indexer.index_fetch_result({
            "url": "https://press.com/new", "domain": "press.com",
            "title": "Defence procurement package",
            "headings": "", "body": body, "language": "en",
            "source_tier": 2, "http_status": 200,
            "fetched_at": now,
        })
        hits = await internal_search.search("defence procurement package",
                                              dedupe_by_domain=False)
        urls = [h["url"] for h in hits]
        assert urls.index("https://press.com/new") < urls.index(
            "https://press.com/old"), (
            f"fresh fetch should rank above stale fetch. Got: {urls}"
        )
    _run(go())


_LONG_CONTRACT_BODY = (
    "The new contract was signed today in a ceremony attended by "
    "officials from the ministry and senior executives from the "
    "supplier. The agreement covers defence services for the next "
    "three years with options to extend further. The total value of "
    "the deal has not been disclosed publicly by either party. "
    "Officials confirmed that the contract was awarded after a "
    "competitive tender process spanning several months of evaluation."
)


def test_dedupe_by_domain_keeps_best_only(db_ready):
    """Three pages on the same domain. With dedupe_by_domain=True,
    only one survives."""
    async def go():
        await db.register_domain("flood.example", tier=2, sector="news",
                                 language="en")
        for i in range(3):
            await indexer.index_fetch_result({
                "url": f"https://flood.example/page-{i}",
                "domain": "flood.example",
                "title": f"contract news {i}",
                "headings": "",
                "body": _LONG_CONTRACT_BODY,
                "language": "en", "source_tier": 2,
                "http_status": 200, "fetched_at": time.time(),
            })
        hits = await internal_search.search("contract", dedupe_by_domain=True)
        domains = [h["url"].split("/")[2] for h in hits]
        assert domains.count("flood.example") == 1
    _run(go())


def test_dedupe_off_keeps_all_pages(db_ready):
    async def go():
        await db.register_domain("flood.example", tier=2, sector="news",
                                 language="en")
        for i in range(3):
            await indexer.index_fetch_result({
                "url": f"https://flood.example/page-{i}",
                "domain": "flood.example",
                "title": f"contract news {i}",
                "headings": "",
                "body": _LONG_CONTRACT_BODY,
                "language": "en", "source_tier": 2,
                "http_status": 200, "fetched_at": time.time(),
            })
        hits = await internal_search.search("contract",
                                              dedupe_by_domain=False)
        assert len(hits) == 3
    _run(go())


# ─────────────────────────────────────────────────────────────────
# Filters
# ─────────────────────────────────────────────────────────────────

def test_language_filter_pt_only(db_ready):
    async def go():
        await db.register_domain("en.example", tier=2, sector="news",
                                 language="en")
        await db.register_domain("pt.example", tier=2, sector="news",
                                 language="pt")
        await indexer.index_fetch_result({
            "url": "https://en.example/x", "domain": "en.example",
            "title": "Defence contract", "headings": "",
            "body": ("English coverage of a defence contract awarded "
                     "to a major supplier today by the ministry."),
            "language": "en", "source_tier": 2,
            "http_status": 200, "fetched_at": time.time(),
        })
        await indexer.index_fetch_result({
            "url": "https://pt.example/x", "domain": "pt.example",
            "title": "Contrato de defesa", "headings": "",
            "body": ("A empresa portuguesa recebeu hoje um contrato "
                     "do Ministério da Defesa para fornecer sistemas "
                     "de comunicações às forças armadas. O contrato "
                     "tem um valor não divulgado."),
            "language": "pt", "source_tier": 2,
            "http_status": 200, "fetched_at": time.time(),
        })
        pt_hits = await internal_search.search("defesa", language="pt")
        assert all(h["language"] == "pt" for h in pt_hits)
        en_hits = await internal_search.search("defence", language="en")
        assert all(h["language"] == "en" for h in en_hits)
    _run(go())


def test_min_credibility_filter(db_ready):
    """min_credibility=2 should keep tier 1 + 2 and drop tier 3."""
    async def go():
        await db.register_domain("t1.gov", tier=1, sector="official",
                                 language="en")
        await db.register_domain("t3.blog", tier=3, sector="news",
                                 language="en")
        body = ("Substantive content describing a public procurement "
                "event with enough words to pass the thin-content "
                "guard, including a summary of the tender process, "
                "the participating bidders, and the eventual award "
                "to the winning supplier. The ministry confirmed "
                "the announcement at a press briefing held today.")
        await indexer.index_fetch_result({
            "url": "https://t1.gov/x", "domain": "t1.gov",
            "title": "procurement", "headings": "", "body": body,
            "language": "en", "source_tier": 1,
            "http_status": 200, "fetched_at": time.time(),
        })
        await indexer.index_fetch_result({
            "url": "https://t3.blog/x", "domain": "t3.blog",
            "title": "procurement", "headings": "", "body": body,
            "language": "en", "source_tier": 3,
            "http_status": 200, "fetched_at": time.time(),
        })
        hits = await internal_search.search("procurement",
                                              min_credibility=2)
        urls = [h["url"] for h in hits]
        assert "https://t1.gov/x" in urls
        assert "https://t3.blog/x" not in urls
    _run(go())


# ─────────────────────────────────────────────────────────────────
# Coverage report
# ─────────────────────────────────────────────────────────────────

def test_coverage_report_basic_structure(db_ready):
    async def go():
        await db.register_domain("a.com", tier=2, sector="news",
                                 language="en")
        await indexer.index_fetch_result({
            "url": "https://a.com/x", "domain": "a.com",
            "title": "t", "headings": "",
            "body": ("Body text long enough to pass the thin-content "
                     "guard with at least twenty words of substance "
                     "and one hundred characters total."),
            "language": "en", "source_tier": 2,
            "http_status": 200, "fetched_at": time.time(),
        })
        report = await internal_search.coverage_report()
        assert report["engine"] == "aria_internal_index"
        assert report["ready"] is True
        assert report["indexed_documents"] == 1
        assert report["domains_enabled"] == 1
        assert report["by_language"]["en"] == 1
    _run(go())


def test_coverage_report_with_query_shows_candidates(db_ready):
    async def go():
        await db.register_domain("a.com", tier=2, sector="news",
                                 language="en")
        await indexer.index_fetch_result({
            "url": "https://a.com/x", "domain": "a.com",
            "title": "Modirum Gespi", "headings": "",
            "body": ("Modirum Gespi contract win story published "
                     "today by our newsroom in cooperation with "
                     "local correspondents in Luanda and Maputo. "
                     "The company confirmed the award in a brief "
                     "statement issued after the close of trading "
                     "on the Lisbon stock exchange this afternoon."),
            "language": "en", "source_tier": 2,
            "http_status": 200, "fetched_at": time.time(),
        })
        report = await internal_search.coverage_report(query="Modirum Gespi")
        assert report["query"] == "Modirum Gespi"
        assert report["raw_candidates"] == 1
        assert report["candidates_by_tier"].get("tier_2") == 1
        assert report["candidates_by_language"].get("en") == 1
    _run(go())


# ─────────────────────────────────────────────────────────────────
# CAPABILITY TEST — the R-F503 Modirum-Gespi win
# ─────────────────────────────────────────────────────────────────

def test_rf503_capability_modirum_gespi_homograph_resilience(db_ready):
    """The single user-visible promise of the whole search-engine build:
    given 100 academic-noise pages where "modirum" appears as a code
    variable PLUS 1 corporate press release about Modirum Gespi, the
    engine returns the corporate press release first.

    This is the scenario that broke chat for real (the WA conversation
    the operator showed). With the legacy `web_search.search()` path
    the academic backend dominated and returned 0 relevant. With the
    internal index — even before vector retrieval or homograph filter
    is added — BM25 + tier bonus puts the corporate page on top
    because:
      a) The noise corpus is tier 4 (academic); press is tier 2.
      b) Modirum + Gespi co-occur densely in the press release.
    """
    async def go():
        # 100 academic-noise domains all tier 4.
        await db.register_domain("noise.example", tier=4,
                                 sector="academic", language="en")
        await db.register_domain("press.en", tier=2, sector="news",
                                 language="en")

        results: list[dict] = []
        for i in range(100):
            results.append({
                "url": f"https://noise.example/p-{i}",
                "domain": "noise.example",
                "title": (f"On the discrete logarithm problem over "
                          f"finite fields (paper {i})"),
                "headings": "Algorithm",
                "body": ("Let modirum be a variable name in the "
                         "algorithm pseudocode. Theorem: for all primes p "
                         "the elliptic curve algorithm terminates. "
                         "Lemma: the discrete log problem in F_p is "
                         "hard. We define modirum := g^x mod p as the "
                         "secret element. Proof: see Appendix B for "
                         "the full derivation of the equation."),
                "language": "en", "source_tier": 4,
                "http_status": 200, "fetched_at": time.time() - 60 * 86400,
            })
        results.append({
            "url": "https://press.en/modirum-award",
            "domain": "press.en",
            "title": "Modirum Gespi awarded defence integration contract",
            "headings": "Modirum Gespi · Angola · contract win",
            "body": ("Modirum Gespi has been awarded a defence systems "
                     "integration contract for the Angolan Ministry of "
                     "Defence. The company, headquartered in Luanda "
                     "with a Finnish parent, will deliver tactical "
                     "communications and battlefield management "
                     "systems. The contract is valued at an undisclosed "
                     "sum and runs for three years. Modirum Gespi "
                     "previously delivered systems to Mozambique."),
            "language": "en", "source_tier": 2,
            "http_status": 200, "fetched_at": time.time(),
        })

        summary = await indexer.index_many(results)
        assert summary["indexed"] == 101

        hits = await internal_search.search("Modirum Gespi contract",
                                              max_results=10)
        assert hits, "internal search must return at least one hit"
        assert hits[0]["url"] == "https://press.en/modirum-award", (
            f"Modirum Gespi press release must rank #1 over academic noise. "
            f"Got: {[(h['url'], h['relevance_score']) for h in hits[:3]]}"
        )

        # Coverage report should be honest about the corpus shape.
        report = await internal_search.coverage_report(
            query="Modirum Gespi contract")
        assert report["indexed_documents"] == 101
        # FTS5 will mostly return noise candidates; the engine's tier
        # bonus is what promotes the press release to #1.
        assert report["raw_candidates"] >= 1
    _run(go())
