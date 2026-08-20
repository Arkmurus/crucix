"""R-F1666 — doc-read must NOT block the event loop.

Root cause (2026-06-18 contract-review outage, §22 wedge stack: main thread
blocked in read_document): the VISION PDF path ran sync fitz.open / get_text /
get_pixmap rasterisation ON the event loop. Rendering a large/scanned PDF froze
the whole single-process brain — the doc read AND every other request (WA
brain-fetch, /api/aria/health, state_store) timed out together. The fix moves
all blocking fitz work into asyncio.to_thread helpers.

These tests drive the REAL path: the source guard proves the async vision fns
no longer call fitz inline, and the behavioural test proves a concurrent
coroutine keeps running WHILE a (synthetic) multi-page PDF is extracted.
"""
import asyncio
import inspect

import pytest

from aria_service.intel import document_reader as dr
from ._env_probe import requires_module


# ── Source guard: the async vision fns must not call fitz inline ─────────────

@pytest.mark.parametrize("fn_name", [
    "_strategy_vision_pdf",
    "_vision_single_chunk",
    "_read_pdf_chunked",
])
def test_async_vision_fns_have_no_inline_fitz(fn_name):
    fn = getattr(dr, fn_name)
    src = inspect.getsource(fn)
    # The blocking fitz primitives must NOT appear in the async function body —
    # they belong only in the sync _fitz_* helpers dispatched via to_thread.
    for blocker in ("fitz.open(", "get_pixmap(", ".get_text("):
        assert blocker not in src, (
            f"R-F1666: {fn_name} still calls {blocker} inline on the event loop. "
            f"Move it into a sync _fitz_* helper run via asyncio.to_thread."
        )
    assert "to_thread" in src, (
        f"R-F1666: {fn_name} must dispatch fitz work via asyncio.to_thread."
    )


def test_sync_fitz_helpers_exist():
    for h in ("_fitz_page_count", "_fitz_vision_pdf_summaries_sync",
              "_fitz_chunk_summaries_sync"):
        assert hasattr(dr, h), f"R-F1666: missing sync helper {h}"


# ── Behavioural: the loop stays responsive during a real vision extraction ───

@pytest.mark.asyncio
@requires_module("fitz")
async def test_vision_pdf_does_not_block_event_loop():
    import fitz  # type: ignore
    import tempfile, os

    # Build a small multi-page PDF with a text layer.
    doc = fitz.open()
    for i in range(5):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i+1} of the test fee agreement. " * 40)
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    doc.save(path)
    doc.close()

    # A heartbeat coroutine that must keep ticking WHILE extraction runs —
    # if the loop were blocked by sync fitz, this would stall.
    ticks = {"n": 0}
    stop = False

    async def heartbeat():
        while not stop:
            ticks["n"] += 1
            await asyncio.sleep(0.005)

    class _MockLLM:
        async def complete(self, system, prompt, **kw):
            await asyncio.sleep(0.01)
            class _R:  # noqa
                text = "Extracted: " + prompt[:200]
            return _R()

    hb = asyncio.create_task(heartbeat())
    try:
        result = await dr._strategy_vision_pdf(path, _MockLLM(), query="fee terms")
    finally:
        stop = True
        await hb

    assert result is not None
    # The loop ticked during extraction — proves fitz ran off-loop.
    assert ticks["n"] > 3, (
        f"R-F1666: event loop appears blocked during extraction (only "
        f"{ticks['n']} heartbeat ticks) — fitz work is not off the loop."
    )
    try:
        os.unlink(path)
    except OSError:
        pass
