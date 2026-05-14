"""R-F504 — chat-time wire-up of the internal search index.

Unit + capability tests covering:
  - `_query_internal_index()` returns [] when the search-index db is
    not connected (degrades silently — chat keeps working).
  - `_query_internal_index()` returns dicts in the unified `link/title/
    snippet/source` shape consumed by the rest of the researcher.py
    pipeline.
  - `_web_search()` merges internal and external results by URL,
    with internal preferred on duplicate.
  - `_web_search()` returns internal-only results when external errors.

Capability test (the R-F504 user-visible promise): given the actual
Modirum-Gespi homograph scenario, _web_search must return the internal
press release ahead of the academic-noise external results.
"""
from __future__ import annotations

import asyncio
import pytest
import time

from aria_service.search_index import db, indexer
from aria_service.intel import researcher


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
# _query_internal_index
# ─────────────────────────────────────────────────────────────────

def test_internal_query_returns_empty_when_db_unready():
    """If the chat path runs before lifespan has opened the index,
    the internal branch must NOT raise — it must return []."""
    _run(db._reset())
    async def go():
        out = await researcher._query_internal_index("anything")
        assert out == []
    _run(go())


def test_internal_query_returns_unified_dict_shape(db_ready):
    async def go():
        await db.register_domain("press.en", tier=2, sector="news",
                                 language="en")
        await indexer.index_fetch_result({
            "url": "https://press.en/x", "domain": "press.en",
            "title": "Defence contract awarded today",
            "headings": "",
            "body": ("A major defence contract has been awarded "
                     "today by the ministry to a domestic supplier "
                     "following a competitive tender process spanning "
                     "several months of detailed evaluation."),
            "language": "en", "source_tier": 2,
            "http_status": 200, "fetched_at": time.time(),
        })
        out = await researcher._query_internal_index("defence contract")
        assert out, "internal index must return at least one hit"
        h = out[0]
        # Shape parity with the external branch:
        for key in ("title", "link", "snippet", "source"):
            assert key in h, f"missing key {key}"
        assert h["link"].startswith("https://press.en/")
        assert h["source"] in ("aria_internal_index",)
        assert h.get("_internal") is True
    _run(go())


# ─────────────────────────────────────────────────────────────────
# Merge behaviour in _web_search
# ─────────────────────────────────────────────────────────────────

def test_web_search_merges_internal_and_external(db_ready, monkeypatch):
    """Both branches return results, with NO overlap — final list has
    both."""
    async def go():
        await db.register_domain("press.en", tier=2, sector="news",
                                 language="en")
        await indexer.index_fetch_result({
            "url": "https://press.en/internal-only",
            "domain": "press.en",
            "title": "Modirum Gespi awarded contract",
            "headings": "",
            "body": ("Modirum Gespi has been awarded a defence systems "
                     "integration contract for the Angolan Ministry "
                     "of Defence. The company, headquartered in Luanda "
                     "with a Finnish parent, will deliver tactical "
                     "communications and battlefield management "
                     "systems over three years."),
            "language": "en", "source_tier": 2,
            "http_status": 200, "fetched_at": time.time(),
        })

        # Stub the external ws.search_multilingual to return a distinct
        # external URL — no overlap with internal.
        from types import SimpleNamespace
        async def fake_ext(query, languages=None, max_results=30):
            return [SimpleNamespace(
                title="external news",
                url="https://external.example/news/1",
                snippet="external snippet",
                source="duckduckgo",
                credibility_tier=4,
                language="en",
                relevance_score=0.5,
            )]
        from aria_service.intel import web_search as _ws
        monkeypatch.setattr(_ws, "search_multilingual", fake_ext)

        results = await researcher._web_search("Modirum Gespi contract")
        links = [r["link"] for r in results]
        assert "https://press.en/internal-only" in links
        assert "https://external.example/news/1" in links
        # Internal is first because it gets prepended in the merge.
        assert links[0] == "https://press.en/internal-only"
    _run(go())


def test_web_search_dedupes_when_url_overlaps(db_ready, monkeypatch):
    """If both internal AND external have the same URL, internal wins
    (keeps tier metadata) and the URL appears only once."""
    async def go():
        await db.register_domain("press.en", tier=2, sector="news",
                                 language="en")
        url = "https://press.en/shared-story"
        await indexer.index_fetch_result({
            "url": url, "domain": "press.en",
            "title": "Modirum Gespi awarded contract",
            "headings": "",
            "body": ("Modirum Gespi has been awarded a defence systems "
                     "integration contract for the Angolan Ministry "
                     "of Defence. The contract is valued at an "
                     "undisclosed sum and runs for three years."),
            "language": "en", "source_tier": 2,
            "http_status": 200, "fetched_at": time.time(),
        })

        from types import SimpleNamespace
        async def fake_ext(query, languages=None, max_results=30):
            return [SimpleNamespace(
                title="external view of the same story",
                url=url,  # SAME URL
                snippet="external snippet",
                source="duckduckgo",
                credibility_tier=4,
                language="en",
                relevance_score=0.4,
            )]
        from aria_service.intel import web_search as _ws
        monkeypatch.setattr(_ws, "search_multilingual", fake_ext)

        results = await researcher._web_search("Modirum Gespi contract")
        # Only one row for the shared URL.
        same = [r for r in results if r["link"] == url]
        assert len(same) == 1
        # And it's the internal one (has _internal=True).
        assert same[0].get("_internal") is True
        assert same[0]["title"] == "Modirum Gespi awarded contract"
    _run(go())


def test_web_search_returns_internal_only_when_external_errors(
    db_ready, monkeypatch,
):
    """If web_search.search_multilingual blows up, internal hits still
    surface — independence path proven."""
    async def go():
        await db.register_domain("press.en", tier=2, sector="news",
                                 language="en")
        await indexer.index_fetch_result({
            "url": "https://press.en/x", "domain": "press.en",
            "title": "Defence contract", "headings": "",
            "body": ("A defence contract has been awarded today to a "
                     "supplier following a multi-month tender process "
                     "evaluation by the ministry of defence team."),
            "language": "en", "source_tier": 2,
            "http_status": 200, "fetched_at": time.time(),
        })

        async def boom(query, languages=None, max_results=30):
            raise RuntimeError("Brave key invalid, SearXNG empty, all down")
        from aria_service.intel import web_search as _ws
        monkeypatch.setattr(_ws, "search_multilingual", boom)

        results = await researcher._web_search("defence contract")
        assert any(r["link"].startswith("https://press.en/") for r in results)
        assert all(r.get("_internal") for r in results), (
            "when external errors, every result should be internal"
        )
    _run(go())


# ─────────────────────────────────────────────────────────────────
# Capability — the Modirum Gespi end-to-end story
# ─────────────────────────────────────────────────────────────────

def test_rf504_capability_modirum_gespi_internal_beats_homograph(
    db_ready, monkeypatch,
):
    """Reproduces the operator-reported failure:
      - External search (stubbed) returns 5 academic noise URLs that
        contain 'modirum' as a code variable.
      - Internal index has 1 Modirum Gespi corporate press release.

    Pre-R-F504 chat would see only the 5 academic URLs and report
    'no contract wins found'. Post-R-F504 chat sees the corporate
    press release FIRST in the merged list because it's internal-only
    and prepended to the result list.
    """
    async def go():
        # Internal: register press domain + index the Modirum press release.
        await db.register_domain("press.en", tier=2, sector="news",
                                 language="en")
        await indexer.index_fetch_result({
            "url": "https://press.en/modirum-award",
            "domain": "press.en",
            "title": "Modirum Gespi awarded defence integration contract",
            "headings": "Modirum Gespi · Angola · contract win",
            "body": ("Modirum Gespi has been awarded a defence systems "
                     "integration contract for the Angolan Ministry of "
                     "Defence. The company, headquartered in Luanda with "
                     "a Finnish parent, will deliver tactical "
                     "communications and battlefield management systems. "
                     "The contract is valued at an undisclosed sum and "
                     "runs for three years."),
            "language": "en", "source_tier": 2,
            "http_status": 200, "fetched_at": time.time(),
        })

        # External: academic homograph noise — what really happened
        # in the live failure (Semantic Scholar returning crypto papers).
        from types import SimpleNamespace
        async def fake_ext(query, languages=None, max_results=30):
            return [SimpleNamespace(
                title=f"On elliptic curves and modirum variables (paper {i})",
                url=f"https://semanticscholar.example/paper-{i}",
                snippet=("Let modirum be a variable in the algorithm "
                         "pseudocode. Theorem: discrete log over F_p..."),
                source="academic",
                credibility_tier=4,
                language="en",
                relevance_score=0.3,
            ) for i in range(5)]
        from aria_service.intel import web_search as _ws
        monkeypatch.setattr(_ws, "search_multilingual", fake_ext)

        results = await researcher._web_search("Modirum Gespi contract")
        assert results, "merged results must not be empty"

        # The single user-visible promise of the whole search-engine
        # build: the corporate press release ranks ABOVE the academic
        # noise even when the external backend is poisoned with
        # homograph results.
        first = results[0]
        assert first["link"] == "https://press.en/modirum-award", (
            f"Modirum Gespi press release must be the first merged "
            f"result. Got: {[r['link'] for r in results[:3]]}"
        )
        assert first.get("_internal") is True
        assert "Modirum Gespi" in first["title"]
    _run(go())


def test_rf504_lifespan_smoke_imports():
    """Lifespan smoke test — importing the search modules must not
    raise. This is the past-incident defence (2026-04-27 PM 5-min
    outage from a lifespan import error)."""
    from aria_service.search_index import db as _d  # noqa
    from aria_service.search_index import indexer as _i  # noqa
    from aria_service.search_engine import internal_search as _s  # noqa
    from aria_service.crawler import seed_list as _sl  # noqa
    from aria_service.crawler import fetcher as _f  # noqa
    from aria_service.crawler import politeness as _p  # noqa
    from aria_service.crawler import runner as _r  # noqa
    # Each module's public functions must be importable.
    assert callable(_d.connect)
    assert callable(_d.close)
    assert callable(_s.search)
    assert callable(_sl.seed_all)
    assert callable(_f.fetch_for_index)
    assert callable(_p.acquire)
    assert callable(_r.crawl_seed_homepages)
