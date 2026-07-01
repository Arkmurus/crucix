"""R-F2221 — Reciprocal Rank Fusion in web_search.search().

The multi-backend fusion used a BINARY +0.3 triangulation bonus (found by 2+
backends → +0.3, else 0), blind to WHERE each backend ranked a result. RRF
adds sum(1/(k+rank)) across backends so a result several engines rank HIGH
beats one a single engine ranked low. These tests drive the REAL search()
with mocked backends and assert the rank-aware score contribution — which the
old binary bonus did not produce.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import web_search as ws
from aria_service.intel.web_search import SearchResult


def _mk(title, url, source="duckduckgo", tier=3):
    # Identical query-term overlap + credibility + web source_mult across
    # fixtures so the ONLY differentiator is rank/agreement (i.e. RRF).
    return SearchResult(title="alpha bravo " + title, url=url, snippet="alpha bravo",
                        source=source, credibility_tier=tier)


def _run(monkeypatch, *, searxng=None, ddg=None, rrf="1"):
    """Drive the real search() with all backends mocked and cache bypassed."""
    monkeypatch.setenv("ARIA_RRF_ENABLED", rrf)

    async def _fake_searxng(*a, **k):
        return list(searxng or [])

    async def _fake_ddg(*a, **k):
        return list(ddg or [])

    async def _empty(*a, **k):
        return []

    monkeypatch.setattr(ws, "_search_searxng", _fake_searxng)
    monkeypatch.setattr(ws, "_search_duckduckgo", _fake_ddg)
    monkeypatch.setattr(ws, "_search_google_news", _empty)
    monkeypatch.setattr(ws, "_search_bing_news", _empty)
    monkeypatch.setattr(ws, "_search_academic", _empty)
    monkeypatch.setattr(ws, "_search_defence_event", _empty)
    monkeypatch.setattr(ws, "_search_cache_get", lambda *a, **k: None)
    monkeypatch.setattr(ws, "_search_cache_set", lambda *a, **k: None)
    monkeypatch.setattr(ws, "_detect_query_languages", lambda *a, **k: [])
    monkeypatch.setattr(ws, "_detect_defence_event", lambda *a, **k: None)

    return asyncio.run(ws.search("alpha bravo", max_results=10, min_credibility=6))


def _by_url(results):
    return {r.url: r for r in results}


class TestR_F2221_RRF:
    def test_rank_aware_ordering_within_one_backend(self, monkeypatch):
        """One backend returns [A(rank0), B(rank1)] with identical base score;
        RRF must score the higher-ranked A above B."""
        A, B = _mk("a", "https://x.com/a"), _mk("b", "https://x.com/b")
        res = _run(monkeypatch, searxng=[A, B], rrf="1")
        m = _by_url(res)
        assert "https://x.com/a" in m and "https://x.com/b" in m
        assert m["https://x.com/a"].relevance_score > m["https://x.com/b"].relevance_score, (
            "RRF must rank the higher-ranked result above the lower one"
        )
        assert res[0].url == "https://x.com/a"

    def test_multibackend_top_beats_single_backend(self, monkeypatch):
        """A found top by 2 backends must outrank B found by 1 backend."""
        A_sx, A_dd = _mk("a", "https://x.com/a", source="searxng"), _mk("a", "https://x.com/a", source="duckduckgo")
        B = _mk("b", "https://x.com/b", source="searxng")
        res = _run(monkeypatch, searxng=[B, A_sx], ddg=[A_dd], rrf="1")
        m = _by_url(res)
        assert m["https://x.com/a"].relevance_score > m["https://x.com/b"].relevance_score

    def test_disabled_falls_back_to_legacy_binary(self, monkeypatch):
        """With RRF off, two single-backend results (no 2+ agreement) get NO
        agreement bonus → equal base score (the pre-R-F2221 behaviour). This is
        the discriminator: the rank-aware inequality only appears with RRF on."""
        A, B = _mk("a", "https://x.com/a"), _mk("b", "https://x.com/b")
        res = _run(monkeypatch, searxng=[A, B], rrf="0")
        m = _by_url(res)
        assert m["https://x.com/a"].relevance_score == pytest.approx(
            m["https://x.com/b"].relevance_score
        ), "legacy binary bonus must not distinguish two single-backend results by rank"

    def test_rrf_on_beats_off_for_top_result(self, monkeypatch):
        """The same top result scores strictly higher with RRF on than off
        (the added 1/(k+rank) term)."""
        A = _mk("a", "https://x.com/a")
        on = _by_url(_run(monkeypatch, searxng=[A], rrf="1"))["https://x.com/a"].relevance_score
        B = _mk("a", "https://x.com/a")
        off = _by_url(_run(monkeypatch, searxng=[B], rrf="0"))["https://x.com/a"].relevance_score
        assert on > off, "RRF-on must add a positive rank term the legacy path lacked"
