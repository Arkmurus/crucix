"""R-F2226 — search gather quorum+grace early-return.

The gather used to block on the SLOWEST backend for the full 12s per-backend
timeout (asyncio.wait_for(gather, timeout=REQUEST_TIMEOUT)). One slow
supplementary backend pinned every search at 12s even though the fast primary
backend had already returned. R-F2226 returns as soon as a QUORUM of backends
has useful results (+ a short GRACE for stragglers), but keeps waiting the full
budget when quorum isn't reached — so recall is preserved in the degraded case.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from aria_service.intel import web_search as ws
from aria_service.intel.web_search import SearchResult, _gather_search_backends


def _sr(u):
    return SearchResult(title="t", url=u, snippet="s", source="duckduckgo", credibility_tier=3)


class TestR_F2226_GatherHelper:
    """Drive the REAL _gather_search_backends helper with controlled tasks."""

    def test_returns_fast_on_quorum_not_slowest(self):
        async def _go():
            async def fast(u):
                return [_sr(u)]

            async def slow(u):
                await asyncio.sleep(30)  # would pin the old gather at the cap
                return [_sr(u)]

            tasks = [asyncio.create_task(fast("https://a")),
                     asyncio.create_task(fast("https://b")),
                     asyncio.create_task(slow("https://c"))]
            t0 = time.monotonic()
            out = await _gather_search_backends(tasks, budget=5.0, quorum=2, grace=0.3)
            return time.monotonic() - t0, out

        elapsed, out = asyncio.run(_go())
        assert elapsed < 2.0, f"quorum should return in ~grace, not block on the 30s task (took {elapsed:.2f}s)"
        # Order preserved; the two fast backends are present, the slow one is a sentinel.
        assert out[0][0].url == "https://a" and out[1][0].url == "https://b"
        assert isinstance(out[2], Exception), "the un-finished slow backend must be a TimeoutError sentinel"

    def test_recall_safe_when_quorum_not_met(self):
        """Only ONE backend has results (quorum=2 never met) → must wait the full
        budget and INCLUDE that backend's result, not cut it early."""
        async def _go():
            async def slowish_useful(u):
                await asyncio.sleep(0.5)
                return [_sr(u)]

            async def never(u):
                await asyncio.sleep(30)
                return [_sr(u)]

            tasks = [asyncio.create_task(slowish_useful("https://only")),
                     asyncio.create_task(never("https://x")),
                     asyncio.create_task(never("https://y"))]
            t0 = time.monotonic()
            out = await _gather_search_backends(tasks, budget=1.5, quorum=2, grace=0.3)
            return time.monotonic() - t0, out

        elapsed, out = asyncio.run(_go())
        assert elapsed >= 1.3, f"quorum not met → must wait the full budget (took {elapsed:.2f}s)"
        assert elapsed < 4.0, "must not wait for the 30s backends"
        assert out[0][0].url == "https://only", "the single useful backend must be preserved (recall-safe)"
        assert isinstance(out[1], Exception) and isinstance(out[2], Exception)

    def test_order_preserved_with_mixed_results(self):
        async def _go():
            async def ok(u):
                return [_sr(u)]

            async def empty(u):
                return []

            async def boom(u):
                raise RuntimeError("backend down")

            tasks = [asyncio.create_task(ok("https://1")),
                     asyncio.create_task(empty("https://2")),
                     asyncio.create_task(boom("https://3")),
                     asyncio.create_task(ok("https://4"))]
            return await _gather_search_backends(tasks, budget=3.0, quorum=2, grace=0.3)

        out = asyncio.run(_go())
        assert out[0][0].url == "https://1"
        assert out[1] == []                      # empty backend, order kept
        assert isinstance(out[2], Exception)     # raised backend surfaced in place
        assert out[3][0].url == "https://4"


class TestR_F2226_SearchIntegration:
    """Drive the REAL search() with one hung backend — the operator's path."""

    def test_search_does_not_block_on_hung_backend(self, monkeypatch):
        # Small, fast gather budget for the test.
        monkeypatch.setattr(ws, "SEARCH_GATHER_BUDGET", 4.0)
        monkeypatch.setattr(ws, "SEARCH_GATHER_QUORUM", 2)
        monkeypatch.setattr(ws, "SEARCH_GATHER_GRACE", 0.3)

        async def fast_sx(*a, **k):
            return [_sr("https://sx/1")]

        async def fast_ddg(*a, **k):
            return [_sr("https://ddg/1")]

        async def hung(*a, **k):
            await asyncio.sleep(30)
            return [_sr("https://slow/1")]

        async def empty_mem(*a, **k):
            return []

        # _query_memory is search()'s first task; unmocked it cold-loads the
        # embedder and blocks the loop (~4s) in the test env — a fixture artifact,
        # not gather behaviour. In prod the embedder is baked+warm.
        monkeypatch.setattr(ws, "_query_memory", empty_mem)
        monkeypatch.setattr(ws, "_search_searxng", fast_sx)
        monkeypatch.setattr(ws, "_search_duckduckgo", fast_ddg)
        monkeypatch.setattr(ws, "_search_google_news", hung)
        monkeypatch.setattr(ws, "_search_bing_news", hung)
        monkeypatch.setattr(ws, "_search_academic", hung)
        monkeypatch.setattr(ws, "_search_defence_event", hung)
        monkeypatch.setattr(ws, "_search_cache_get", lambda *a, **k: None)
        monkeypatch.setattr(ws, "_search_cache_set", lambda *a, **k: None)
        monkeypatch.setattr(ws, "_detect_query_languages", lambda *a, **k: [])
        monkeypatch.setattr(ws, "_detect_defence_event", lambda *a, **k: None)

        async def _go():
            t0 = time.monotonic()
            res = await ws.search("hung-backend-probe", max_results=10, min_credibility=6)
            return time.monotonic() - t0, res

        elapsed, res = asyncio.run(_go())
        assert elapsed < 3.0, f"search() must not block on the 30s backends (took {elapsed:.2f}s)"
        urls = {r.url for r in res}
        assert "https://sx/1" in urls and "https://ddg/1" in urls, "fast backends' results must survive"
