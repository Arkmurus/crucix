"""R-F1672 — chunked vision extraction must run CONCURRENTLY (bounded), in order.

The large/scanned-PDF path (_read_pdf_chunked) ran chunks strictly sequentially:
up to 30 chunks × ~60-120s = 30-60 min for a max-size doc, far beyond the WA
15-min poll window → large docs NEVER delivered. R-F1672 runs up to
ARIA_VISION_CHUNK_CONCURRENCY chunks at once while preserving page order. These
drive the REAL _read_pdf_chunked with mocked page-count + chunk worker.
"""
import asyncio
import inspect

import pytest

from aria_service.intel import document_reader as dr


def test_chunked_uses_bounded_concurrent_gather():
    src = inspect.getsource(dr._read_pdf_chunked)
    assert "asyncio.gather(" in src, (
        "R-F1672: _read_pdf_chunked must gather chunks concurrently, not loop serially."
    )
    assert "Semaphore(" in src, "R-F1672: concurrency must be bounded by a semaphore."


@pytest.mark.asyncio
async def test_chunks_run_concurrently_and_preserve_page_order(monkeypatch):
    # 60 pages -> 6 chunks of 10 (>VISION_LARGE_DOC_THRESHOLD so it chunks).
    monkeypatch.setattr(dr, "_fitz_page_count", lambda p: 60)
    monkeypatch.setattr(dr, "PYMUPDF_AVAILABLE", True, raising=False)

    state = {"cur": 0, "max": 0}

    async def fake_chunk(filepath, start_page, end_page, total_pages, llm, query=""):
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        await asyncio.sleep(0.05)  # simulate the LLM call
        state["cur"] -= 1
        return dr.ExtractionResult(
            text=f"PAGES {start_page + 1}-{end_page}",
            method="VISION_CHUNKED", confidence=0.8,
            pages_extracted=end_page - start_page, total_pages=total_pages,
        )

    monkeypatch.setattr(dr, "_vision_single_chunk", fake_chunk)

    res = await dr._read_pdf_chunked("/tmp/fake.pdf", llm=object(), query="terms", max_pages=300)

    # (1) Concurrency actually happened (was strictly 1 before).
    assert state["max"] > 1, (
        f"R-F1672: chunks ran sequentially (max concurrency={state['max']})."
    )
    # (2) Page ORDER preserved in the merged text despite out-of-order completion.
    t = res.text
    assert t.index("PAGES 1-10") < t.index("PAGES 11-20") < t.index("PAGES 51-60"), (
        "R-F1672: merged text must stay in page order after concurrent extraction."
    )
    # (3) All 6 chunks merged.
    assert res.text.count("PAGES ") == 6
