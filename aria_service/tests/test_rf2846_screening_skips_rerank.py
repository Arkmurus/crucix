"""R-F2846 — screening searches must skip the cross-encoder re-rank.

MEASURED ON THE BRAIN, not inferred. Phase-timing a single production search:

    total 119.92s
      _query_memory             4.96s    4%
      _gather_search_backends   8.72s    7%   <- the actual searching
      rerank_results           94.40s   79%   <- cross-encoder

One component was 79% of every search in the product. With ARIA_RERANK_ENABLED=0 the
same probe returned total 12.88s — a 9.3x speedup — and `rerank_results` vanished from
the phase list.

WHAT THAT BROKE. run_adverse_media_deep_search gives each template a 10s budget
(R-F2832) inside a 180s deadline. At ~120s per search NOTHING could complete: the live
SOCAR run recorded templates_run 18, `templates_searched: 0`,
`search_backends_answered: False` — i.e. adverse media, 20% of the decision scorecard,
returned zero evidence on every run. It also degraded chat and research, which share
this path.

WHY A GLOBAL OFF-SWITCH IS THE WRONG END STATE. Re-ranking is a genuine relevance
feature; someone enabled it deliberately and main.py pre-warms it (R-F2259). Leaving it
globally disabled trades a real capability for latency across the board.

The honest split is by PURPOSE. Adverse-media screening asks "does this entity appear in
adverse press?" — titles and snippets answer that, and the ORDER of 30 candidate hits is
irrelevant because every one is inspected downstream. Deep research and chat, where a
human reads the top few, are exactly where re-rank earns its cost. So screening opts OUT
by design, and the feature can then be safely re-enabled for the paths that benefit.

THE SAFETY CONSTRAINT. `screening=True` must never change WHICH results are found, only
whether they are re-ordered. Skipping a re-rank must not become a quiet recall cut — a
screen that silently searches less would be the false-clean family all over again.
"""
import asyncio
import inspect

import pytest

from aria_service.intel import web_search as ws
from aria_service.intel import researcher as R

# R-F3772/§16 — NOT inspect.getsource: it slices at line numbers captured AT
# IMPORT, so a mid-run edit silently returns a DIFFERENT function's body. A CLASS
# target scopes the lookup to that class's own body (R-F3771).
from ._source_probe import function_source


def test_search_accepts_a_screening_flag():
    sig = inspect.signature(ws.search)
    assert "screening" in sig.parameters, (
        "ws.search() must accept screening=True so a caller can opt out of re-rank "
        "without disabling the feature globally"
    )
    assert sig.parameters["screening"].default is False, (
        "screening must default False — existing callers keep re-ranking"
    )


def test_search_multilingual_forwards_the_flag():
    sig = inspect.signature(ws.search_multilingual)
    assert "screening" in sig.parameters
    src = function_source(ws, "search_multilingual")
    assert "screening=screening" in src, (
        "search_multilingual must FORWARD the flag to each per-language search(); "
        "accepting it and dropping it is the half-wire that makes a fix look done"
    )


def test_web_search_exposes_it_too():
    sig = inspect.signature(R._web_search)
    assert "screening" in sig.parameters


def test_the_adverse_media_loop_opts_out():
    """The caller that motivated this must actually use it."""
    src = function_source(R, "run_adverse_media_deep_search")
    assert "screening=True" in src, (
        "run_adverse_media_deep_search must request screening=True — at ~120s per "
        "re-ranked search it completed ZERO of 34 templates in its 180s budget"
    )


@pytest.mark.asyncio
async def test_screening_skips_the_reranker(monkeypatch):
    """CAPABILITY: with screening=True the cross-encoder is never invoked."""
    from aria_service.intel import reranker as _rr
    called = {"n": 0}

    async def _spy(query, results):
        called["n"] += 1
        return results

    monkeypatch.setattr(_rr, "is_enabled", lambda: True, raising=False)
    monkeypatch.setattr(_rr, "rerank_results", _spy, raising=False)
    _stub_backends(monkeypatch)

    await ws.search("adverse media probe", max_results=10, language="en", screening=True)
    assert called["n"] == 0, (
        "screening=True still invoked the cross-encoder — the 94s cost remains"
    )


@pytest.mark.asyncio
async def test_non_screening_still_reranks(monkeypatch):
    """ANTI-REGRESSION: the relevance feature must survive for the paths that want it."""
    from aria_service.intel import reranker as _rr
    called = {"n": 0}

    async def _spy(query, results):
        called["n"] += 1
        return results

    monkeypatch.setattr(_rr, "is_enabled", lambda: True, raising=False)
    monkeypatch.setattr(_rr, "rerank_results", _spy, raising=False)
    _stub_backends(monkeypatch)

    await ws.search("deep research probe", max_results=10, language="en")
    assert called["n"] == 1, (
        "a normal search must still re-rank — this fix is a purpose split, not a "
        "global disable"
    )


@pytest.mark.asyncio
async def test_screening_does_not_reduce_what_is_FOUND(monkeypatch):
    """SAFETY: skipping a re-order must never become a silent recall cut.

    A screen that quietly searches less would be the false-clean family again.
    """
    _stub_backends(monkeypatch, n=6)
    plain = await ws.search("q", max_results=10, language="en")
    _stub_backends(monkeypatch, n=6)
    screened = await ws.search("q", max_results=10, language="en", screening=True)
    assert {r.url for r in screened} == {r.url for r in plain}, (
        "screening changed WHICH results came back; it may only change their ORDER"
    )


def _stub_backends(monkeypatch, n: int = 3):
    """Hermetic: no network. Returns n SearchResult-shaped hits from one backend."""
    from aria_service.intel.web_search import SearchResult

    async def _hits(query, max_results=10, language="en"):
        return [
            SearchResult(
                title=f"hit {i}", url=f"https://example.test/{i}", snippet="s",
                source="stub", credibility_tier=1, relevance_score=0.5, language="en",
            ) for i in range(n)
        ]

    async def _none(*a, **k):
        return []

    for name in ("_search_brave", "_search_searxng", "_search_google_news", "_search_bing_news"):
        if hasattr(ws, name):
            monkeypatch.setattr(ws, name, _hits if name == "_search_searxng" else _none, raising=False)
    monkeypatch.setattr(ws, "_query_memory", _none, raising=False)
