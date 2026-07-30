"""R-F3475 — CPU-bound HTML extraction ran on the event loop.

Fourth distinct stall cause named by R-F3464's attribution, live 2026-07-30 on
afbb28c8, immediately after R-F3473 shipped:

    "last_stall_loop_stack": [
      "trafilatura/main_extractor.py:_extract:610",
      "trafilatura/main_extractor.py:extract_content:657",
      "trafilatura/core.py:trafilatura_sequence:103" ],
    "last_stall_threads": {"total": 27, "parked": 24, "aiosqlite_workers": 9,
                           "pool_workers": 11, "running": 3}

A NEW class. The three before it were blocking syscalls — a module import
(R-F3467), a sqlite commit/fsync (R-F3468), a DNS resolve (R-F3473). This one is
pure CPU: trafilatura parses the DOM, scores blocks and runs main-content
detection, and `researcher._extract_structured_html` additionally runs a dozen
regex passes over the whole document (R-F2204 raised its output cap to 30k chars,
so the inputs are large by design).

`_extract_structured_html` is SYNC, and an AST sweep found all five of its call
sites sit inside ASYNC functions:

    crawl_enhancements.py:656,691   async fetch_with_fallbacks
    deep_researcher.py:187          async _fetch_page_with_links
    researcher.py:3506,3520         async extract_url_text

So every page a crawl touches parsed its HTML on the loop. CPU-bound work cannot
be made non-blocking in place — it has to move to a thread.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import time

import pytest

from aria_service.intel import researcher


_FILES = {
    "intel/crawl_enhancements.py": None,
    "intel/deep_researcher.py": None,
    "intel/researcher.py": None,
}
_ROOT = pathlib.Path(__file__).resolve().parents[1]


async def _loop_latency_while(coro, samples: list[float]):
    state = {"stop": False}

    async def _ticker():
        last = time.perf_counter()
        while not state["stop"]:
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            samples.append(now - last)
            last = now

    t = asyncio.create_task(_ticker())
    try:
        return await coro
    finally:
        state["stop"] = True
        await asyncio.sleep(0.02)
        t.cancel()


class TestExtractionIsOffloaded:

    @pytest.mark.asyncio
    async def test_async_wrapper_keeps_the_loop_responsive(self, monkeypatch):
        """The capability property: a slow extraction must not park the loop."""
        def _slow(html):
            time.sleep(0.4)          # stands in for trafilatura on a large DOM
            return {"text": "ok"}

        monkeypatch.setattr(researcher, "_extract_structured_html", _slow)
        samples: list[float] = []
        result = await _loop_latency_while(
            researcher.extract_structured_html_async("<html></html>"), samples,
        )
        assert result == {"text": "ok"}, "the offloaded call lost its result"
        assert samples, "loop-latency ticker never ran"
        worst = max(samples)
        assert worst < 0.25, (
            f"the event loop was blocked for {worst:.2f}s during HTML extraction"
        )

    @pytest.mark.asyncio
    async def test_wrapper_returns_the_same_shape_as_the_sync_function(self):
        """Offloading must not change the contract callers depend on."""
        html = (
            "<html><head><title>T</title></head><body>"
            "<h1>Heading</h1><p>" + ("body text " * 40) + "</p></body></html>"
        )
        sync_result = researcher._extract_structured_html(html)
        async_result = await researcher.extract_structured_html_async(html)
        assert isinstance(async_result, dict)
        assert set(async_result.keys()) == set(sync_result.keys())
        assert async_result.get("title") == sync_result.get("title")

    @pytest.mark.asyncio
    async def test_wrapper_propagates_exceptions(self, monkeypatch):
        def _boom(_html):
            raise ValueError("extractor exploded")

        monkeypatch.setattr(researcher, "_extract_structured_html", _boom)
        with pytest.raises(ValueError, match="extractor exploded"):
            await researcher.extract_structured_html_async("<html></html>")


class TestTheClassCannotGrow:
    """R-F3449/R-F3468 pattern — fail the build if a new async caller blocks."""

    def test_no_async_function_calls_the_sync_extractor(self):
        offenders: list[str] = []
        for rel in _FILES:
            path = _ROOT / rel
            tree = ast.parse(path.read_text(encoding="utf-8"))

            class _V(ast.NodeVisitor):
                def __init__(self) -> None:
                    self.fn: str | None = None

                def visit_AsyncFunctionDef(self, node):
                    prev, self.fn = self.fn, node.name
                    self.generic_visit(node)
                    self.fn = prev

                def visit_FunctionDef(self, node):
                    prev, self.fn = self.fn, None
                    self.generic_visit(node)
                    self.fn = prev

                def visit_Call(self, node):
                    name = (getattr(node.func, "id", None)
                            or getattr(node.func, "attr", None))
                    if self.fn and name == "_extract_structured_html":
                        offenders.append(f"{rel}:{node.lineno} in async {self.fn}()")
                    self.generic_visit(node)

            _V().visit(tree)

        assert not offenders, (
            "CPU-bound HTML extraction called directly from an async function — "
            "await extract_structured_html_async() instead:\n  "
            + "\n  ".join(offenders)
        )
